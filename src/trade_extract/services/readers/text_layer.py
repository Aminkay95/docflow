import json
import re
from dataclasses import replace

from trade_extract.domain.fields import Agreement, Field
from trade_extract.domain.inference import InferenceRequest, ProviderResult
from trade_extract.domain.provenance import BBox, CoordinateSpace, Provenance, ReaderKind
from trade_extract.domain.readers import PageInput
from trade_extract.providers.base import ModelProvider
from trade_extract.schemas.registry import get_schema
from trade_extract.services.local_extraction import compact_evidence, evidence_lines


class TextLayerReader:
    name = ReaderKind.TEXT_LAYER

    def read(
        self, provider: ModelProvider, request: InferenceRequest, pages: tuple[PageInput, ...]
    ) -> ProviderResult | None:
        lines = evidence_lines(tuple(page for page in pages if page.characters))
        if not lines:
            return None
        # A reader may be used independently of the extraction service, so keep a
        # conservative fixed cap here. The service applies the configured per-page cap.
        evidence = compact_evidence(lines, max_characters=max(1, 24_000 * len(pages)))
        geometry = (
            ""
            if "Compact evidence index:\n" in request.evidence
            else "\nEvidence lines (id|page|normalized bbox|text):\n" + evidence
        )
        result = provider.text_reason(
            replace(
                request,
                pages=(),
                evidence=(
                    request.evidence + '\nRequired provenance.reader: "text_layer"' + geometry
                ),
            )
        )
        if request.schema_json.get("contract") != "evidence_values_v1":
            return result
        raw = json.loads(result.raw_json) if isinstance(result.raw_json, str) else result.raw_json
        if not isinstance(raw, dict) or "values" not in raw:
            if isinstance(raw, dict) and "fields" in raw and "source" in raw:
                return result
            raise ValueError("invalid_compact_provider_root")
        if request.source_json is None:
            raise ValueError("invalid_compact_provider_source")
        compact_values = raw.get("values")
        if not isinstance(compact_values, (list, dict)):
            raise ValueError("invalid_compact_provider_values")
        by_id = {line.evidence_id: line for line in lines}
        spec = get_schema(request.document_type)
        allowed = set(request.schema_json.get("allowed_scalar_paths", ()))
        fields = {}
        items = (
            [
                dict(item, path=path)
                for path, item in compact_values.items()
                if isinstance(item, dict)
            ]
            if isinstance(compact_values, dict)
            else compact_values
        )
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("invalid_compact_provider_item")
            try:
                path, value, evidence_id = item["path"], item["value"], item["evidence_id"]
            except KeyError:
                raise ValueError("invalid_compact_provider_item") from None
            if not all(isinstance(value, str) for value in (path, value, evidence_id)):
                raise ValueError("invalid_compact_provider_item")
            field_spec = spec.model.field_set.resolve(path)
            line = by_id.get(evidence_id)
            if path not in allowed:
                continue
            if field_spec is None or line is None or line.reader != ReaderKind.TEXT_LAYER:
                continue
            source_text = "".join(char.text for char in line.characters)
            matches = (
                list(re.finditer(re.escape(value), source_text, re.IGNORECASE)) if value else []
            )
            if len(matches) != 1:
                continue
            start, end = matches[0].span()
            stated_value = source_text[start:end]
            bbox = line.box_for(start, end)
            if bbox is None:
                continue
            fields[path] = Field(
                kind="scalar",
                value=stated_value,
                value_type=field_spec.value_type,
                raw_text=stated_value,
                agreement=Agreement.SINGLE_READER,
                provenance=Provenance(
                    page_index=line.page_index,
                    page_number=line.page_index + 1,
                    bbox=bbox,
                    coordinate_space=CoordinateSpace.NORMALIZED_TOP_LEFT,
                    reader=ReaderKind.TEXT_LAYER,
                    model=result.model,
                    document_hash=request.source_hash,
                ),
            ).model_dump(mode="json")
        payload = {
            "segment_id": request.segment_id,
            "document_type": request.document_type,
            "schema_version": spec.version,
            "source": request.source_json,
            "fields": fields,
            "tables": raw.get("tables", {}),
            "extension": {},
        }
        return replace(result, raw_json=payload, repair_json=raw)


def verify_text_box(page: PageInput, text: str, bbox: BBox) -> bool:
    """Require an actual contiguous character span and its exact source rectangle."""
    if not text:
        return False
    source = page.text
    start = source.find(text)
    while start >= 0:
        end = start + len(text)
        if (start and source[start - 1].isalnum() and text[0].isalnum()) or (
            end < len(source) and source[end].isalnum() and text[-1].isalnum()
        ):
            start = source.find(text, start + 1)
            continue
        chars = page.characters[start : start + len(text)]
        if len(chars) == len(text):
            expected = BBox(
                x0=min(c.bbox.x0 for c in chars),
                y0=min(c.bbox.y0 for c in chars),
                x1=max(c.bbox.x1 for c in chars),
                y1=max(c.bbox.y1 for c in chars),
            )
            if all(
                abs(getattr(expected, key) - getattr(bbox, key)) <= 0.00001
                for key in ("x0", "y0", "x1", "y1")
            ):
                return True
        start = source.find(text, start + 1)
    return False
