"""Offline synthetic adapter exercising extraction, checks, decisions and conflicts.

Only authored synthetic evidence files are accepted. This is a service-level smoke
corpus, not measured provider accuracy or a substitute for a scratch DB lifecycle run.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from trade_extract.domain.fields import Field
from trade_extract.domain.hashing import document_hash
from trade_extract.domain.inference import ProviderResult
from trade_extract.domain.provenance import BBox
from trade_extract.domain.readers import PageFeatures, PageInput, TextCharacter
from trade_extract.providers.mock import MockProvider
from trade_extract.schemas.base import EnvelopeSource
from trade_extract.schemas.registry import get_schema
from trade_extract.services.checks import run_invoice_checks
from trade_extract.services.conflicts import detect_conflicts
from trade_extract.services.decisions import decide_document
from trade_extract.services.evaluation import LabelJob, safe_path
from trade_extract.services.extraction import extract
from trade_extract.services.readers.text_layer import TextLayerReader


def run(job: LabelJob, root: Path) -> dict[str, Any]:
    if job.origin != "synthetic":
        raise ValueError("synthetic adapter refuses real corpus")
    documents = []
    for label in job.documents:
        path = safe_path(root, label.file)
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("origin") != "synthetic":
            raise ValueError("evidence must declare synthetic origin")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        source = EnvelopeSource(
            filename=path.name,
            content_type="application/x-synthetic-evidence",
            file_hash=digest,
            document_hash=document_hash(
                source_file_sha256=digest,
                page_start=0,
                page_end=0,
                document_type=label.document_type,
            ),
            page_start=0,
            page_end=0,
        )
        fields, characters = {}, []
        schema = get_schema(label.document_type)
        for index, (name, value) in enumerate(raw["fields"].items()):
            spec = schema.model.field_set.resolve(name)
            if spec is None:
                raise ValueError("unknown synthetic evidence field")
            box = BBox(x0=0.1, y0=0.02 + index * 0.04, x1=0.8, y1=0.04 + index * 0.04)
            for offset, char in enumerate(value):
                characters.append(
                    TextCharacter(
                        char,
                        BBox(
                            x0=box.x0 + (box.x1 - box.x0) * offset / len(value),
                            y0=box.y0,
                            x1=box.x0 + (box.x1 - box.x0) * (offset + 1) / len(value),
                            y1=box.y1,
                        ),
                    )
                )
            characters.append(TextCharacter("\n", box))
            fields[name] = Field.model_validate(
                {
                    "kind": "scalar",
                    "value": value,
                    "value_type": spec.value_type,
                    "agreement": "single_reader",
                    "provenance": {
                        "page_index": 0,
                        "bbox": box.model_dump(),
                        "coordinate_space": "normalized_top_left",
                        "reader": "text_layer",
                        "document_hash": source.document_hash,
                    },
                }
            ).model_dump(mode="json")
        provider = MockProvider(
            {
                (label.document_type, source.document_hash): [
                    ProviderResult(
                        {"source": source.model_dump(), "fields": fields}, "scripted-synthetic"
                    )
                ]
            }
        )
        page = PageInput(
            0,
            characters=tuple(characters),
            features=PageFeatures(good_text=True, dense_table=False),
        )
        outcome = extract(
            provider,
            document_type=label.document_type,
            source=source,
            pages=(page,),
            readers=(TextLayerReader(),),
        )
        checks = run_invoice_checks(outcome.document) if outcome.document else []
        status = "failed" if outcome.status == "extraction_failed" else outcome.status
        decision = decide_document(
            document_type=label.document_type,
            status=status,
            checks=checks,
            policy_id="synthetic-default",
            policy_version=1,
            config={},
        )
        documents.append(
            {
                "document_id": label.document_id,
                "document_type": label.document_type,
                "status": status,
                "fields": outcome.document.model_dump(mode="json")["fields"]
                if outcome.document
                else {},
                "tables": {},
                "decision": decision,
            }
        )
    return {
        "documents": documents,
        "conflicts": [c.canonical_key for c in detect_conflicts(job.job_id, documents)],
    }
