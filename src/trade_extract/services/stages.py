"""ICM stage-file loader and classifier service (prompt-chaining at the
filesystem level: stage 01 classify, stage 02 extract per registered type).

The registry stays the authority on fields and JSON schema. Stage files under
``providers/prompts/`` are auditable contract views; the required-path parity
test keeps them in sync. The loader is import-lazy so the stable prompt hash
used by ``tests/unit/test_providers.py`` stays unchanged unless the pipeline is
explicitly enabled with ``LLM_CLASSIFICATION_ENABLED`` or
``STAGE_PROMPTS_ENABLED``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from trade_extract.config import Settings
from trade_extract.domain.inference import InferenceRequest
from trade_extract.providers.base import ModelProvider
from trade_extract.schemas.registry import DOCUMENT_SCHEMAS

STAGE_DIR = Path(__file__).resolve().parent.parent / "providers" / "prompts"

CLASSIFY_STAGE_FILE = "01-classify.md"

TYPE_STAGE_FILES: dict[str, str] = {
    "commercial_invoice": "02-commercial-invoice.md",
    "proforma_invoice": "03-proforma-invoice.md",
    "bill_of_lading": "04-bill-of-lading.md",
    "delivery_order": "05-delivery-order.md",
    "port_pass": "06-port-pass.md",
    "custom_entry": "07-custom-entry.md",
    "verification_memo": "08-verification-memo.md",
}

CLASSIFICATION_DOCUMENT_TYPE = "classify"

CLASSIFICATION_SCHEMA: dict[str, Any] = {
    "contract": "classification_v1",
    "type": "object",
    "properties": {
        "document_type": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reason": {"type": "string"},
    },
    "required": ["document_type", "confidence", "reason"],
    "additionalProperties": False,
}

ALLOWED_TYPES = frozenset(DOCUMENT_SCHEMAS) | {"unknown"}


@lru_cache(maxsize=8)
def load_stage_text(stage_file: str) -> str:
    """Read a stage file once; the byte-stable content anchors prompt caching."""
    path = STAGE_DIR / stage_file
    if not path.is_file():
        raise ValueError(f"missing_stage_file:{stage_file}")
    return path.read_text(encoding="utf-8")


def classify_system_prompt(request: InferenceRequest) -> str:
    """The classify contract only; extraction instructions never load here."""
    return (
        load_stage_text(CLASSIFY_STAGE_FILE)
        + "\n"
        + json.dumps(
            {"stage": "classify", "document_type": CLASSIFICATION_DOCUMENT_TYPE},
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def type_system_prompt(document_type: str, schema_json: dict[str, Any]) -> str:
    """The type extraction contract, gated on the prior stage-01 verdict."""
    stage_file = TYPE_STAGE_FILES.get(document_type)
    if stage_file is None:
        raise ValueError(f"unsupported_type_stage:{document_type}")
    return (
        load_stage_text(stage_file)
        + "\n"
        + json.dumps(
            {"document_type": document_type, "schema": schema_json},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    )


@dataclass(frozen=True)
class ClassificationVerdict:
    document_type: str
    confidence: float
    reason: str


def _parse_verdict(raw_json: str | dict[str, Any]) -> ClassificationVerdict:
    data = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
    if not isinstance(data, dict):
        raise ValueError("classification_not_object")
    document_type = str(data.get("document_type", "unknown")).strip()
    if document_type not in ALLOWED_TYPES:
        raise ValueError(f"classification_invalid_type:{document_type}")
    confidence = data.get("confidence", 0.0)
    confidence_f = float(confidence)
    if not (0.0 <= confidence_f <= 1.0):
        raise ValueError("classification_confidence_out_of_range")
    reason = str(data.get("reason", "")).strip()
    return ClassificationVerdict(document_type, confidence_f, reason)


def classify_segment(
    provider: ModelProvider,
    *,
    evidence: str,
    source_hash: str,
    segment_id: str,
    settings: Settings,
) -> ClassificationVerdict:
    """One text-only LLM call per segment. Low confidence stays unknown."""
    request = InferenceRequest(
        document_type=CLASSIFICATION_DOCUMENT_TYPE,
        source_hash=source_hash,
        schema_json=CLASSIFICATION_SCHEMA,
        evidence=evidence,
        segment_id=segment_id,
        stage="classify",
        page_count=1,
    )
    result = provider.text_reason(request)
    verdict = _parse_verdict(result.raw_json)
    if verdict.confidence < settings.segmentation_min_confidence:
        return ClassificationVerdict("unknown", verdict.confidence, verdict.reason)
    return verdict


__all__ = [
    "ALLOWED_TYPES",
    "CLASSIFICATION_DOCUMENT_TYPE",
    "CLASSIFICATION_SCHEMA",
    "CLASSIFY_STAGE_FILE",
    "TYPE_STAGE_FILES",
    "ClassificationVerdict",
    "classify_segment",
    "classify_system_prompt",
    "load_stage_text",
    "type_system_prompt",
]
