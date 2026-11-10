"""ICM stage files: registry-authority parity and classify service behavior.

Stage files are auditable contract views of the registry; required paths
listed there must match the schema. These tests keep the two from drifting.
"""

from __future__ import annotations

import json

import pytest

from trade_extract.config import Settings
from trade_extract.domain.inference import ProviderResult
from trade_extract.providers.mock import MockProvider
from trade_extract.schemas.registry import DOCUMENT_SCHEMAS
from trade_extract.services import stages


@pytest.fixture
def classify_settings():
    return Settings(llm_classification_enabled=True)


def test_every_registered_type_has_a_stage_file_and_prompt_name():
    for document_type, spec in DOCUMENT_SCHEMAS.items():
        stage_file = stages.TYPE_STAGE_FILES[document_type]
        assert stage_file == spec.prompt
        assert (stages.STAGE_DIR / stage_file).is_file(), stage_file


def test_stage_file_required_paths_match_registry():
    """Every required non-indexed path named in a stage file must be required
    in the registry. Keeps contract-view prose from inventing requiredness."""
    for document_type, spec in DOCUMENT_SCHEMAS.items():
        text = stages.load_stage_text(spec.prompt)
        for spec_field in spec.model.field_set.specs:
            if not spec_field.required or spec_field.indexed:
                continue
            assert spec_field.path in text, (document_type, spec_field.path)


def test_classify_prompt_loads_only_stage01_and_stays_stable():
    text = stages.classify_system_prompt(_classify_request("a" * 64))
    assert "Stage 01" in text
    assert "commercial_invoice" in text
    assert "Stage 02" not in text
    # Same file content -> same system prompt; evidence is excluded from the prompt.
    assert text == stages.classify_system_prompt(_classify_request("b" * 64))


def _classify_request(source_hash):
    from trade_extract.domain.inference import InferenceRequest

    return InferenceRequest(
        document_type=stages.CLASSIFICATION_DOCUMENT_TYPE,
        source_hash=source_hash,
        schema_json=stages.CLASSIFICATION_SCHEMA,
        evidence="private document",
        stage="classify",
        page_count=1,
    )


def test_classify_segment_confident_type_passthrough():
    provider = MockProvider(
        {
            (
                stages.CLASSIFICATION_DOCUMENT_TYPE,
                "a" * 64,
            ): [
                ProviderResult(
                    raw_json=json.dumps(
                        {
                            "document_type": "commercial_invoice",
                            "confidence": 0.92,
                            "reason": "invoice",
                        }
                    ),
                    model="mock",
                )
            ]
        }
    )
    verdict = stages.classify_segment(
        provider,
        evidence="invoice text",
        source_hash="a" * 64,
        segment_id="seg_1",
        settings=Settings(),
    )
    assert verdict.document_type == "commercial_invoice"
    assert verdict.confidence == pytest.approx(0.92)


def test_classify_segment_low_confidence_becomes_unknown():
    provider = MockProvider(
        {
            (
                stages.CLASSIFICATION_DOCUMENT_TYPE,
                "a" * 64,
            ): [
                ProviderResult(
                    raw_json=json.dumps(
                        {"document_type": "bill_of_lading", "confidence": 0.3, "reason": "weak"}
                    ),
                    model="mock",
                )
            ]
        }
    )
    verdict = stages.classify_segment(
        provider,
        evidence="text",
        source_hash="a" * 64,
        segment_id="seg_1",
        settings=Settings(),
    )
    assert verdict.document_type == "unknown"


def test_classify_segment_invalid_type_raises():
    provider = MockProvider(
        {
            (
                stages.CLASSIFICATION_DOCUMENT_TYPE,
                "a" * 64,
            ): [
                ProviderResult(
                    raw_json=json.dumps(
                        {"document_type": "nonsense", "confidence": 0.9, "reason": "x"}
                    ),
                    model="mock",
                )
            ]
        }
    )
    with pytest.raises(ValueError, match="classification_invalid_type"):
        stages.classify_segment(
            provider,
            evidence="text",
            source_hash="a" * 64,
            segment_id="seg_1",
            settings=Settings(),
        )


def test_type_prompt_contains_contract_and_excludes_classify():
    text = stages.type_system_prompt("commercial_invoice", {"a": 1})
    assert "Stage 02" in text
    assert "commercial_invoice" in text
    assert "Stage 01 —" not in text


def test_stage_directory_is_unchanged_on_disk():
    files = {p.name for p in stages.STAGE_DIR.iterdir() if p.suffix == ".md"}
    assert files == {
        stages.CLASSIFY_STAGE_FILE,
        *stages.TYPE_STAGE_FILES.values(),
    }
