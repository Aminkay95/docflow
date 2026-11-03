from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from trade_extract.domain.fields import Agreement, Field, FieldAlternative, ValueType
from trade_extract.domain.hashing import document_hash
from trade_extract.domain.provenance import BBox, CoordinateSpace, Provenance, ReaderKind

FILE_HASH = "a" * 64
DOCUMENT_HASH = document_hash(
    source_file_sha256=FILE_HASH,
    page_start=0,
    page_end=2,
    document_type="commercial_invoice",
)


def bbox(**overrides: float) -> dict[str, float]:
    values = {"x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.2}
    values.update(overrides)
    return values


def provenance(**overrides: object) -> Provenance:
    values: dict[str, object] = {
        "page_index": 0,
        "page_number": 1,
        "bbox": bbox(),
        "coordinate_space": CoordinateSpace.NORMALIZED_TOP_LEFT,
        "page_width_pt": 595.0,
        "page_height_pt": 842.0,
        "reader": ReaderKind.TEXT_LAYER,
        "reader_version": "pypdfium2-4.30.0",
        "confidence": 1.0,
        "document_hash": DOCUMENT_HASH,
    }
    values.update(overrides)
    return Provenance.model_validate(values)


def test_bbox_accepts_an_ordered_normalised_box() -> None:
    box = BBox(**bbox())
    assert box.width == pytest.approx(0.1)
    assert box.height == pytest.approx(0.1)


@pytest.mark.parametrize(
    "overrides",
    [
        {"x0": 0.3},
        {"y0": 0.3},
        {"x0": -0.01},
        {"x1": 1.01},
        {"y0": -0.1},
        {"y1": 2.0},
    ],
)
def test_bbox_rejects_inverted_or_out_of_bounds_coordinates(overrides: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        BBox(**bbox(**overrides))


@pytest.mark.parametrize("coordinate", ["x0", "y0", "x1", "y1"])
def test_bbox_rejects_non_finite_coordinates(coordinate: str) -> None:
    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValidationError):
            BBox(**bbox(**{coordinate: value}))


def test_provenance_requires_page_number_to_match_page_index() -> None:
    assert provenance(page_index=2, page_number=3).page_number == 3
    with pytest.raises(ValidationError, match="page_number"):
        provenance(page_index=2, page_number=2)


def test_provenance_hash_accepts_bare_and_prefixed_forms() -> None:
    assert provenance(document_hash=DOCUMENT_HASH).document_hash_hex == DOCUMENT_HASH
    assert provenance(document_hash=f"sha256:{DOCUMENT_HASH}").document_hash_hex == DOCUMENT_HASH


@pytest.mark.parametrize("value", ["", "abc", "sha256:abc", "z" * 64, f"sha256:{'g' * 64}"])
def test_provenance_rejects_malformed_hashes(value: str) -> None:
    with pytest.raises(ValidationError):
        provenance(document_hash=value)


def test_provenance_hash_agreement_is_prefix_insensitive() -> None:
    item = provenance(document_hash=f"sha256:{DOCUMENT_HASH}")
    assert item.matches(DOCUMENT_HASH)
    assert item.matches(f"sha256:{DOCUMENT_HASH.upper()}")
    assert not item.matches("b" * 64)
    assert not item.matches("not-a-hash")


def test_provenance_page_range_is_inclusive() -> None:
    assert provenance(page_index=0, page_number=1).within(0, 2)
    assert provenance(page_index=2, page_number=3).within(0, 2)
    assert not provenance(page_index=3, page_number=4).within(0, 2)
    with pytest.raises(ValueError, match="invalid page range"):
        provenance().within(2, 1)


@pytest.mark.parametrize("confidence", [-0.01, 1.01, math.nan, math.inf])
def test_provenance_rejects_invalid_confidence(confidence: float) -> None:
    with pytest.raises(ValidationError):
        provenance(confidence=confidence)


def test_provenance_page_dimensions_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        provenance(page_width_pt=0.0)
    with pytest.raises(ValidationError):
        provenance(page_height_pt=-1.0)


def leaf(**overrides: object) -> Field:
    values: dict[str, object] = {
        "kind": "scalar",
        "value": "INV-1",
        "value_type": ValueType.STRING,
        "provenance": provenance(),
        "agreement": Agreement.UNANIMOUS,
    }
    values.update(overrides)
    return Field.model_validate(values)


def test_field_requires_provenance() -> None:
    values = leaf().model_dump()
    del values["provenance"]
    with pytest.raises(ValidationError, match="provenance"):
        Field.model_validate(values)


def test_disputed_field_requires_an_alternative() -> None:
    with pytest.raises(ValidationError, match="alternative"):
        leaf(agreement=Agreement.DISPUTED)
    alternative = FieldAlternative(
        value="INV-2",
        reader=ReaderKind.VISION,
        confidence=0.7,
        provenance=provenance(reader=ReaderKind.VISION, confidence=0.7),
    )
    assert leaf(agreement=Agreement.DISPUTED, alternatives=[alternative]).alternatives


def test_non_disputed_field_rejects_alternatives() -> None:
    alternative = FieldAlternative(
        value="INV-2",
        reader=ReaderKind.VISION,
        confidence=0.7,
        provenance=provenance(reader=ReaderKind.VISION, confidence=0.7),
    )
    with pytest.raises(ValidationError, match="only when agreement is 'disputed'"):
        leaf(alternatives=[alternative])


def test_alternative_reader_must_match_its_own_provenance() -> None:
    with pytest.raises(ValidationError, match="reader"):
        FieldAlternative(
            value="INV-2",
            reader=ReaderKind.VISION,
            confidence=1.0,
            provenance=provenance(reader=ReaderKind.TEXT_LAYER),
        )


def test_alternative_confidence_must_match_its_own_provenance() -> None:
    with pytest.raises(ValidationError, match="confidence"):
        FieldAlternative(
            value="INV-2",
            reader=ReaderKind.VISION,
            confidence=0.4,
            provenance=provenance(reader=ReaderKind.VISION, confidence=0.9),
        )
