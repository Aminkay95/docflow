"""Vision batches carry only attached evidence and validated native crop citations."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from trade_extract.config import Settings
from trade_extract.domain.inference import InferenceRequest, PageImage, ProviderResult
from trade_extract.domain.provenance import BBox, ReaderKind
from trade_extract.domain.readers import NativeCrop, PageInput
from trade_extract.providers.base import ModelProvider
from trade_extract.services.readers.base import partial_model


class VisionReader:
    name = ReaderKind.VISION

    def __init__(self, *, crops: tuple[NativeCrop, ...] = (), settings: Settings | None = None):
        self.crops = crops
        self.settings = settings or Settings()

    def read(
        self, provider: ModelProvider, request: InferenceRequest, pages: tuple[PageInput, ...]
    ) -> ProviderResult | None:
        if any(p.image is not None and p.image.page_index != p.page_index for p in pages):
            raise ValueError("image_page_index_mismatch")
        images = tuple(p.image for p in pages if p.vision_ready and p.image)
        if not images:
            return None
        limit = self.settings.provider_max_images
        # One original page accompanies its row crops for scalar/header context.
        batches: list[tuple[PageImage, tuple[NativeCrop, ...]]] = []
        for image in images:
            crops = tuple(c for c in self.crops if c.row.page_index == image.page_index)
            if crops and limit < 2:
                raise ValueError("table_provider_image_budget_exhausted")
            if crops:
                batches.extend(
                    (image, crops[i : i + limit - 1]) for i in range(0, len(crops), limit - 1)
                )
            else:
                batches.append((image, ()))
        if len(batches) > self.settings.table_max_batches:
            raise ValueError("table_provider_batch_budget_exhausted")
        # Preserve the original one-call interface for a single whole-page batch.
        payload: dict[str, Any] | None = None
        model = ""
        for image, batch in batches:
            metadata = [
                {
                    "image_position": 0,
                    "page_index": image.page_index,
                    "coordinates": "normalized_page",
                }
            ]
            metadata.extend(
                {
                    "image_position": i + 1,
                    "page_index": c.row.page_index,
                    "crop_rectangle": c.rectangle,
                    "page_size": c.page_size,
                    "table_name": c.row.table_name,
                    "row_index": c.row.row_index,
                    "coordinates": "normalized_crop",
                }
                for i, c in enumerate(batch)
            )
            result = provider.vision_extract(
                replace(
                    request,
                    pages=(image, *(c.image for c in batch)),
                    evidence=request.evidence
                    + '\nRequired provenance.reader: "vision"'
                    + "\nScalar boxes use normalized_page. Table row boxes use normalized_crop."
                    + "\nOnly cite attached page/rows. Attached images: "
                    + json.dumps(metadata),
                )
            )
            if not self.crops and len(batches) == 1:
                return result
            raw = (
                json.loads(result.raw_json) if isinstance(result.raw_json, str) else result.raw_json
            )
            if not isinstance(raw, dict):
                raise ValueError("invalid_batch_object")
            doc = partial_model(request.document_type).model_validate(raw)
            expected_source = json.loads(request.evidence.split("\n", 1)[0])["source"]
            if doc.source.model_dump() != expected_source or doc.segment_id != request.segment_id:
                raise ValueError("batch_source_identity_mismatch")
            if any(
                getattr(doc, key) is not None
                for key in ("document_id", "result_id", "job_id", "job_ref", "file_id")
            ):
                raise ValueError("untrusted_batch_identity")
            current = doc.model_dump(mode="json")
            for section in ("fields", "extension"):
                for path, f in current[section].items():
                    provenance = f["provenance"]
                    if (
                        provenance["page_index"] != image.page_index
                        or provenance["reader"] != "vision"
                        or f["alternatives"]
                    ):
                        raise ValueError("batch_citation_not_attached")
                    match = re.match(r"([a-z_]+)\[(\d+)\]", path)
                    if match and self.crops:
                        relevant = [
                            c
                            for c in batch
                            if c.row.table_name == match.group(1)
                            and c.row.row_index == int(match.group(2))
                        ]
                        if len(relevant) != 1:
                            raise ValueError("batch_row_not_attached")
                        provenance["bbox"] = (
                            relevant[0]
                            .page_box(BBox.model_validate(provenance["bbox"]))
                            .model_dump()
                        )
            if payload is None:
                payload = current
            else:
                if any(
                    current[k] != payload[k]
                    for k in ("source", "segment_id", "document_type", "schema_version")
                ):
                    raise ValueError("batch_identity_mismatch")
                for section in ("fields", "extension"):
                    for path, f in current[section].items():
                        if path in payload[section] and payload[section][path] != f:
                            prior_field = payload[section][path]
                            if any(
                                prior_field[k] != f[k]
                                for k in ("value", "value_type", "raw_text", "kind")
                            ):
                                raise ValueError("batch_field_disagreement")
                            # Equal stated values have equivalent evidence; preserve a
                            # deterministic validated citation instead of requiring equal boxes.
                            f = min((prior_field, f), key=lambda v: json.dumps(v, sort_keys=True))
                        payload[section][path] = f
                for name, meta in current["tables"].items():
                    prior = payload["tables"].get(name)
                    if prior is None:
                        payload["tables"][name] = meta
                    else:
                        if (
                            prior["row_count_stated"] != meta["row_count_stated"]
                            or prior["columns"] != meta["columns"]
                        ):
                            raise ValueError("batch_table_count_disagreement")
                        prior["unresolved_rows"] = sorted(
                            set(prior["unresolved_rows"]) & set(meta["unresolved_rows"])
                        )
                        prior["row_count_extracted"] = prior["row_count_stated"] - len(
                            prior["unresolved_rows"]
                        )
            model = result.model
        return ProviderResult(payload or {}, model)
