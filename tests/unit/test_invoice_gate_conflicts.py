from copy import deepcopy
from decimal import Decimal

import pytest

from tests.unit.test_extraction import fixture
from trade_extract.domain.fields import Agreement
from trade_extract.domain.provenance import ReaderKind
from trade_extract.schemas.invoice import CommercialInvoice
from trade_extract.services.checks import decimal_value, run_invoice_checks, valid_container
from trade_extract.services.conflicts import detect_conflicts
from trade_extract.services.decisions import decide_document


def invoice(tmp_path):
    _, _, payload = fixture(tmp_path)
    raw = payload(ReaderKind.TEXT_LAYER)
    exemplar = raw["fields"]["totals.payable_amount"]
    for path, value in {
        "line_items[0].description": "Goods",
        "line_items[0].quantity": "2",
        "line_items[0].unit_price": "5",
        "line_items[0].line_extension_amount": "10",
        "totals.tax_amount": "0",
    }.items():
        item = deepcopy(exemplar)
        item["value"] = value
        item["value_type"] = "string" if path.endswith("description") else "decimal"
        raw["fields"][path] = item
    raw["tables"] = {"line_items": {"row_count_stated": 1, "row_count_extracted": 1}}
    return CommercialInvoice.model_validate(raw)


def decision(doc, checks, config=None, **kwargs):
    return decide_document(
        document_type="commercial_invoice",
        status="extracted",
        checks=checks,
        policy_id="pol_1",
        policy_version=7,
        config=config or {},
        **kwargs,
    )


def test_checks_and_policy_preserve_stated_transposed_total(tmp_path):
    doc = invoice(tmp_path)
    checks = run_invoice_checks(doc)
    assert {c.code for c in checks} == {f"INV-{n:03}" for n in range(1, 15)}
    assert not [c for c in checks if c.status == "fail"]
    assert decision(doc, checks)["value"] == "review"
    config = {"invoice_pass_enabled": True, "review_depth": "checks"}
    passed = decision(doc, checks, config)
    assert passed["value"] == "pass" and passed["policy_version"] == 7
    bad = doc.model_copy(
        update={
            "fields": {
                **doc.fields,
                "totals.payable_amount": doc.fields["totals.payable_amount"].model_copy(
                    update={"value": "100"}
                ),
            }
        }
    )
    failures = run_invoice_checks(bad)
    check = next(c for c in failures if c.code == "INV-005")
    assert check.status == "fail" and check.expected == "10" and check.actual == "100"
    assert "INV-005" in check.message and "totals.payable_amount" in check.message
    assert bad.fields["totals.payable_amount"].value == "100"
    assert decision(bad, failures, config)["value"] == "review"
    assert decision(doc, checks, {"invoice_pass_enabled": "true"})["policy_valid"] is False
    assert decision(doc, checks, config, open_conflicts=["invoice.currency"])["route"] == "conflict"


@pytest.mark.parametrize("value", ["1,000", "1e2", "NaN", "Infinity", "1 000", "1,2"])
def test_ambiguous_operands_cannot_pass(value):
    with pytest.raises(ValueError):
        decimal_value(value)


def test_identifiers_dates_and_unreadable(tmp_path):
    doc = invoice(tmp_path)
    fields = dict(doc.fields)
    fields["invoice.due_date"] = doc.fields["invoice.issue_date"].model_copy(
        update={"value": "2026-01-01"}
    )
    fields["line_items[0].quantity"] = fields["line_items[0].quantity"].model_copy(
        update={"value": "0"}
    )
    checked = run_invoice_checks(doc.model_copy(update={"fields": fields}))
    assert {"INV-008", "INV-009"} <= {c.code for c in checked if c.status == "fail"}
    assert valid_container("CSQU3054383") and not valid_container("CSQU3054384")
    assert decision(doc, checked, has_unreadable_page=True)["value"] == "review"
    assert (
        decide_document(
            document_type="commercial_invoice",
            status="unreadable",
            checks=[],
            policy_id="pol",
            policy_version=1,
            config={"invoice_pass_enabled": True, "review_depth": "checks"},
        )["value"]
        == "unreadable"
    )


def test_conflicts_include_every_source_and_order_is_stable(tmp_path):
    doc = invoice(tmp_path)
    documents = []
    for index, value in enumerate(("USD", "EUR", "USD")):
        fields = {p: f.model_dump(mode="json") for p, f in doc.fields.items()}
        fields["invoice.currency"]["value"] = value
        documents.append(
            {"document_id": f"doc_{index}", "document_type": "commercial_invoice", "fields": fields}
        )
    before = deepcopy(documents)
    conflicts = detect_conflicts("job_1", documents)
    assert len(conflicts) == 1 and len(conflicts[0].sources) == 3
    assert detect_conflicts("job_1", list(reversed(documents))) == conflicts
    assert documents == before
    assert detect_conflicts("another_job", documents)[0].fingerprint != conflicts[0].fingerprint


def test_numeric_tolerance_and_incomparable_units(tmp_path):
    doc = invoice(tmp_path)
    fields = {p: f.model_dump(mode="json") for p, f in doc.fields.items()}
    fields["shipment.gross_weight_kg"] = deepcopy(fields["totals.payable_amount"])
    fields["shipment.gross_weight_kg"]["value"] = "10"
    a = {"document_id": "a", "document_type": "commercial_invoice", "fields": fields}
    b = deepcopy(a)
    b["document_id"] = "b"
    b["fields"]["shipment.gross_weight_kg"]["value"] = "10.001"
    assert detect_conflicts("j", [a, b], tolerance=Decimal(".01")) == []
    b["fields"]["shipment.gross_weight_unit"] = {"value": "LB"}
    conflicts = detect_conflicts("j", [a, b])
    assert conflicts[0].sources[1]["comparison_reason"] == "incomparable_unit"


@pytest.mark.parametrize(
    "words", ["unsupported wording", "one million widgets", "ten euros and twenty cents"]
)
def test_unsupported_amount_words_are_advisory_not_matches(tmp_path, words):
    doc = invoice(tmp_path)
    fields = dict(doc.fields)
    fields["totals.amount_in_words"] = fields["line_items[0].description"].model_copy(
        update={"value": words}
    )
    checked = run_invoice_checks(doc.model_copy(update={"fields": fields}))
    check = next(c for c in checked if c.code == "INV-014")
    assert check.status == "warn" and check.severity == "warning"
    assert check.actual == words


def test_canonical_aliases_compare_registered_document_paths_without_changing_values(tmp_path):
    doc = invoice(tmp_path)
    invoice_field = doc.fields["invoice.number"].model_dump(mode="json")
    bl_field = deepcopy(invoice_field)
    bl_field["value"] = "BL-OTHER"
    first = {
        "document_id": "invoice",
        "document_type": "commercial_invoice",
        "fields": {"bill_of_lading.number": invoice_field},
    }
    second = {
        "document_id": "bl",
        "document_type": "bill_of_lading",
        "fields": {"bl.number": bl_field},
    }
    result = detect_conflicts("asserted-group", [first, second])
    assert len(result) == 1 and result[0].canonical_key == "bill_of_lading.number"
    assert {s["field_path"] for s in result[0].sources} == {"bill_of_lading.number", "bl.number"}
    assert first["fields"]["bill_of_lading.number"]["value"] == invoice_field["value"]


@pytest.mark.parametrize(
    "value",
    [
        "one two",
        "twenty ten",
        "one hundred hundred",
        "one thousand one million",
        "one dollars hundred",
    ],
)
def test_invalid_word_grammar_is_not_invented(value):
    from trade_extract.services.checks import words_amount

    assert words_amount(value) is None


def test_supported_words_match_exact_amount():
    from trade_extract.services.checks import words_amount

    assert words_amount("One hundred and twenty-three dollars and 45/100 only") == Decimal("123.45")


@pytest.mark.parametrize(
    "path,value,code,expected,actual",
    [
        ("line_items[0].unit_price", "6", "INV-002", "12", "10"),
        ("totals.line_extension_amount", "11", "INV-003", "10", "11"),
        ("totals.tax_amount", "2", "INV-004", "12", "10"),
    ],
)
def test_arithmetic_mismatches_have_named_expected_actual_and_block_gate(
    tmp_path, path, value, code, expected, actual
):
    doc = invoice(tmp_path)
    fields = {**doc.fields, path: doc.fields[path].model_copy(update={"value": value})}
    changed = doc.model_copy(update={"fields": fields})
    checks = run_invoice_checks(changed)
    outcome = next(c for c in checks if c.code == code)
    assert (outcome.status, outcome.expected, outcome.actual) == ("fail", expected, actual)
    assert (
        decision(changed, checks, {"invoice_pass_enabled": True, "review_depth": "checks"})["value"]
        == "review"
    )


def test_long_decimal_work_is_exact_and_bounded(tmp_path):
    doc = invoice(tmp_path)
    fields = dict(doc.fields)
    amount = "123456789012345678901234567890.12"
    wrong = "123456789012345678901234567890.13"
    for path in (
        "line_items[0].unit_price",
        "line_items[0].line_extension_amount",
        "totals.line_extension_amount",
        "totals.tax_exclusive_amount",
        "totals.tax_inclusive_amount",
        "totals.payable_amount",
    ):
        fields[path] = fields[path].model_copy(update={"value": amount})
    fields["line_items[0].quantity"] = fields["line_items[0].quantity"].model_copy(
        update={"value": "1"}
    )
    fields["totals.payable_amount"] = fields["totals.payable_amount"].model_copy(
        update={"value": wrong}
    )
    changed = doc.model_copy(update={"fields": fields})
    checks = run_invoice_checks(changed, tolerance=Decimal("0"))
    outcome = next(c for c in checks if c.code == "INV-005")
    assert outcome.status == "fail" and outcome.expected == amount and outcome.actual == wrong
    assert (
        decision(changed, checks, {"invoice_pass_enabled": True, "review_depth": "checks"})["value"]
        == "review"
    )
    with pytest.raises(ValueError, match="digit_budget"):
        decimal_value("1" * 257)
    a = {
        "document_id": "a",
        "document_type": "commercial_invoice",
        "fields": {"shipment.gross_weight_kg": {"value": amount, "provenance": {}}},
    }
    b = deepcopy(a)
    b["document_id"] = "b"
    b["fields"]["shipment.gross_weight_kg"]["value"] = wrong
    assert len(detect_conflicts("j", [a, b], tolerance=Decimal("0"))) == 1


@pytest.mark.parametrize(
    "path,blocking", [("invoice.note", False), ("totals.payable_amount", True)]
)
def test_critical_disagreement_blocks_and_optional_note_is_advisory(tmp_path, path, blocking):
    from trade_extract.domain.fields import FieldAlternative

    doc = invoice(tmp_path)
    field = doc.fields.get(path, doc.fields["line_items[0].description"])
    provenance = field.provenance.model_copy(update={"reader": ReaderKind.VISION})
    alternative = FieldAlternative(
        value="other",
        reader=ReaderKind.VISION,
        provenance=provenance,
        confidence=provenance.confidence,
    )
    disputed = field.model_copy(
        update={"agreement": Agreement.DISPUTED, "alternatives": [alternative]}
    )
    changed = doc.model_copy(update={"fields": {**doc.fields, path: disputed}})
    checks = run_invoice_checks(changed)
    failures = [c for c in checks if c.code == "INV-012" and c.status == "fail"]
    assert bool(failures) == blocking
    assert decision(changed, checks, {"invoice_pass_enabled": True, "review_depth": "checks"})[
        "value"
    ] == ("review" if blocking else "pass")


def test_persisted_fields_extensions_and_alternatives_use_original_page_geometry(tmp_path):
    from trade_extract.domain.conditioning import CoordinateTransform
    from trade_extract.domain.fields import FieldAlternative
    from trade_extract.domain.provenance import BBox
    from trade_extract.domain.readers import PageInput
    from trade_extract.workers.handlers.extract_segment import source_snapshot

    doc = invoice(tmp_path)
    box = BBox(x0=0.3, y0=0.4, x1=0.5, y1=0.6)
    fields = {
        p: f.model_copy(update={"provenance": f.provenance.model_copy(update={"bbox": box})})
        for p, f in doc.fields.items()
    }
    field = fields["invoice.number"]
    alternative = FieldAlternative(
        value="other",
        reader=ReaderKind.VISION,
        provenance=field.provenance.model_copy(update={"reader": ReaderKind.VISION}),
        confidence=field.provenance.confidence,
    )
    fields["invoice.number"] = field.model_copy(
        update={"agreement": Agreement.DISPUTED, "alternatives": [alternative]}
    )
    changed = doc.model_copy(update={"fields": fields, "extension": {"extension.any.note": field}})
    transform = CoordinateTransform(
        source_size=(1000, 800),
        conditioned_size=(500, 400),
        forward=(0.5, 0, 100, 0, 0.5, 40, 0, 0, 1),
        inverse=(2, 0, -200, 0, 2, -80, 0, 0, 1),
    )
    output = source_snapshot(changed, [PageInput(0, transform=transform)])
    for item in [*output["fields"].values(), *output["extension"].values()]:
        for provenance in [item["provenance"], *[a["provenance"] for a in item["alternatives"]]]:
            assert provenance["bbox"] == pytest.approx(dict(x0=0.1, y0=0.3, x1=0.3, y1=0.5))
    assert output["fields"]["invoice.number"]["value"] == doc.fields["invoice.number"].value
    assert changed.fields["invoice.number"].provenance.bbox == box


@pytest.mark.parametrize(
    "currency,expected",
    [
        ("XCG", "pass"),
        ("XAD", "pass"),
        ("ZZZ", "fail"),
        ("ANG", "fail"),
        ("BGN", "fail"),
        ("CUC", "fail"),
        ("SLL", "fail"),
        ("ZWL", "fail"),
    ],
)
def test_currency_registry_matches_current_six_snapshot(tmp_path, currency, expected):
    doc = invoice(tmp_path)
    changed = doc.model_copy(
        update={
            "fields": {
                **doc.fields,
                "invoice.currency": doc.fields["invoice.currency"].model_copy(
                    update={"value": currency}
                ),
            }
        }
    )
    outcome = next(c for c in run_invoice_checks(changed) if c.code == "INV-006")
    assert outcome.status == expected
