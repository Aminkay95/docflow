from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import ValidationError

from trade_extract.api.app import create_app
from trade_extract.config import Settings
from trade_extract.domain.fields import Agreement, Field, FieldAlternative, ValueType
from trade_extract.domain.hashing import document_hash
from trade_extract.domain.provenance import BBox, CoordinateSpace, Provenance, ReaderKind
from trade_extract.schemas.base import (
    DocumentEnvelope,
    EnvelopeSource,
    FieldSet,
    FieldSpec,
    TableColumn,
    TableMeta,
    TableSpec,
)
from trade_extract.schemas.registry import (
    DOCUMENT_SCHEMAS,
    SchemaSpec,
    UnsupportedDocumentTypeError,
    get_schema,
)

pytestmark = pytest.mark.contract

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FILE_HASH = "a" * 64
OTHER_HASH = "b" * 64

REQUIRED_PATHS: dict[str, dict[str, ValueType]] = {
    "commercial_invoice": {
        "invoice.number": ValueType.STRING,
        "invoice.issue_date": ValueType.DATE,
        "invoice.currency": ValueType.ENUM,
        "seller.name": ValueType.STRING,
        "buyer.name": ValueType.STRING,
        "totals.line_extension_amount": ValueType.DECIMAL,
        "totals.tax_exclusive_amount": ValueType.DECIMAL,
        "totals.tax_inclusive_amount": ValueType.DECIMAL,
        "totals.payable_amount": ValueType.DECIMAL,
    },
    "proforma_invoice": {
        "proforma.number": ValueType.STRING,
        "proforma.issue_date": ValueType.DATE,
        "proforma.currency": ValueType.ENUM,
        "seller.name": ValueType.STRING,
        "buyer.name": ValueType.STRING,
        "totals.line_extension_amount": ValueType.DECIMAL,
        "totals.tax_exclusive_amount": ValueType.DECIMAL,
        "totals.tax_inclusive_amount": ValueType.DECIMAL,
        "totals.payable_amount": ValueType.DECIMAL,
    },
    "bill_of_lading": {
        "bl.number": ValueType.STRING,
        "bl.issue_date": ValueType.DATE,
        "carrier.name": ValueType.STRING,
        "shipper.name": ValueType.STRING,
        "consignee.name": ValueType.STRING,
        "routing.port_of_loading": ValueType.STRING,
        "routing.port_of_discharge": ValueType.STRING,
    },
    "delivery_order": {
        "delivery_order.number": ValueType.STRING,
        "delivery_order.issue_date": ValueType.DATE,
        "bill_of_lading.number": ValueType.STRING,
        "consignee.name": ValueType.STRING,
    },
    "port_pass": {
        "port_pass.number": ValueType.STRING,
        "port_pass.issue_date": ValueType.DATE,
    },
    "custom_entry": {
        "declaration.number": ValueType.STRING,
        "declaration.registration_date": ValueType.DATE,
        "importer.name": ValueType.STRING,
    },
    "verification_memo": {
        "memo.date": ValueType.DATE,
    },
}

# Required row cells are documented separately from scalar required paths: the
# scalar oracle feeds `required_fields` with template-free keys, while rows are
# emitted per concrete source row.
REQUIRED_ROW_PATHS: dict[str, set[str]] = {
    "commercial_invoice": {
        "line_items[].description",
        "line_items[].quantity",
        "line_items[].unit_price",
        "line_items[].line_extension_amount",
    },
    "proforma_invoice": {
        "line_items[].description",
        "line_items[].quantity",
        "line_items[].unit_price",
        "line_items[].line_extension_amount",
    },
    "custom_entry": {"goods_lines[].description"},
}


def _lines(text: str) -> list[str]:
    """Split a triple-quoted oracle block without tripping the str.split lint rule."""

    return text.split()


_INVOICE_PATHS = _lines(
    """
invoice.number
invoice.issue_date
invoice.due_date
invoice.type_code
invoice.currency
invoice.tax_point_date
invoice.note
seller.name
seller.identifier
seller.address.line1
seller.address.city
seller.address.country
buyer.name
buyer.identifier
buyer.address.line1
buyer.address.city
buyer.address.country
ship_to.name
ship_to.address.line1
ship_to.address.city
ship_to.address.country
order.reference
despatch_advice.reference
receiving_advice.reference
bill_of_lading.number
payment.terms
payment.means_code
payment.iban
incoterm.code
incoterm.location
shipment.gross_weight_kg
shipment.gross_weight_unit
shipment.net_weight_kg
shipment.net_weight_unit
shipment.package_count
shipment.package_type
shipment.container_numbers[]
shipment.seal_numbers[]
shipment.vessel
shipment.voyage
shipment.port_of_loading
shipment.port_of_discharge
shipment.country_of_origin
shipment.marks_and_numbers
line_items[].line_number
line_items[].description
line_items[].quantity
line_items[].unit_code
line_items[].unit_price
line_items[].line_extension_amount
line_items[].hs_code
line_items[].tax_category
line_items[].tax_rate
line_items[].tax_amount
line_items[].allowance_charge
line_items[].country_of_origin
line_items[].note
totals.line_extension_amount
totals.tax_exclusive_amount
totals.tax_amount
totals.tax_inclusive_amount
totals.allowance_total
totals.charge_total
totals.prepaid_amount
totals.payable_amount
totals.amount_in_words
tax_breakdown[].taxable_amount
tax_breakdown[].tax_amount
tax_breakdown[].tax_category
tax_breakdown[].tax_rate
"""
)

EXPECTED_PATHS: dict[str, set[str]] = {
    "commercial_invoice": set(_INVOICE_PATHS),
    "proforma_invoice": {
        path.replace("invoice.", "proforma.", 1)
        for path in _INVOICE_PATHS
        if path != "bill_of_lading.number"
    }
    | {"proforma.valid_until"},
    "bill_of_lading": set(
        _lines(
            """
bl.number
bl.form
bl.issue_date
bl.place_of_issue
bl.original_count
bl.terms
carrier.name
carrier.scac
shipper.name
shipper.address.line1
shipper.address.city
shipper.address.country
consignee.name
consignee.address.line1
consignee.address.city
consignee.address.country
notify_party.name
notify_party.address.line1
booking.number
service.contract
vessel.name
vessel.imo
voyage.number
routing.port_of_loading
routing.port_of_discharge
routing.place_of_receipt
routing.place_of_delivery
dates.shipped_on_board
dates.arrival_estimated
freight.terms
freight.amount
freight.currency
containers[].number
containers[].seal
containers[].type
containers[].package_count
containers[].package_type
containers[].gross_weight_kg
containers[].net_weight_kg
containers[].volume_m3
containers[].marks
containers[].description
containers[].hs_code
clauses[].text
signature.place
signature.date
signature.signed_by
agent.name
"""
        )
    ),
    "delivery_order": set(
        _lines(
            """
delivery_order.number
delivery_order.issue_date
delivery_order.valid_until
bill_of_lading.number
carrier.name
agent.name
consignee.name
consignee.address.line1
notify_party.name
release.terminal
release.warehouse
release.reference
release.free_time_days
containers[].number
containers[].seal
containers[].type
containers[].gross_weight_kg
containers[].package_count
containers[].package_type
cargo.description
cargo.marks
charges[].description
charges[].amount
charges[].currency
instructions
signature.name
"""
        )
    ),
    "port_pass": set(
        _lines(
            """
port_pass.number
port_pass.issue_date
port_pass.valid_from
port_pass.valid_to
port.name
port.terminal
port.gate
vehicle.plate
vehicle.type
driver.name
driver.license
transporter.name
container.number
container.seal
container.size_type
container.gross_weight_kg
cargo.description
release.reference
bill_of_lading.number
delivery_order.number
movement.direction
escort.required
remarks
"""
        )
    ),
    "custom_entry": set(
        _lines(
            """
declaration.number
declaration.type
declaration.registration_date
declaration.customs_office
declarant.agent_name
declarant.license
importer.name
importer.pin
importer.address.line1
consignment.bl_number
consignment.awb_number
consignment.vessel
consignment.flight
consignment.voyage
consignment.arrival_date
consignment.port_of_loading
consignment.port_of_discharge
consignment.country_of_origin
consignment.country_of_export
consignment.incoterm
consignment.invoice_number
consignment.invoice_date
consignment.invoice_currency
consignment.exchange_rate
consignment.fob_value
consignment.freight_value
consignment.insurance_value
consignment.cif_value
goods_lines[].item_number
goods_lines[].hs_code
goods_lines[].description
goods_lines[].quantity
goods_lines[].unit_of_measure
goods_lines[].gross_weight_kg
goods_lines[].net_weight_kg
goods_lines[].package_count
goods_lines[].package_type
goods_lines[].marks_and_numbers
goods_lines[].container_numbers[]
goods_lines[].seal_numbers[]
assessment[].line_value
assessment[].customs_value
assessment[].duty_rate
assessment[].duty_amount
assessment[].import_declaration_fee
assessment[].railway_development_levy
assessment[].excise
assessment[].vat
assessment[].total_payable
permits.idf_reference
permits.pvoc_reference
permits.import_permits[]
permits.certificate_of_origin
permits.preferential_treatment
"""
        )
    ),
    "verification_memo": set(
        _lines(
            """
memo.number
memo.date
memo.type
cfs.name
cfs.location
container.number
container.seal
bill_of_lading.number
delivery_order.number
packages.stated
packages.received
packages.damaged
packages.short
packages.over
condition.seal_intact
condition.cargo
findings[].text
officer.name
officer.signature
remarks
"""
        )
    ),
}

_VALUE_SAMPLES = {
    ValueType.STRING: "value",
    ValueType.DECIMAL: "1.00",
    ValueType.INTEGER: "1",
    ValueType.DATE: "2026-01-01",
    ValueType.DATETIME: "2026-01-01T00:00:00Z",
    ValueType.BOOLEAN: "true",
    ValueType.ENUM: "USD",
}


def identity(document_type: str, *, page_start: int = 0, page_end: int = 0) -> str:
    return document_hash(
        source_file_sha256=FILE_HASH,
        page_start=page_start,
        page_end=page_end,
        document_type=document_type,
    )


def provenance(
    *,
    document_hash_value: str,
    page_index: int = 0,
    reader: ReaderKind = ReaderKind.TEXT_LAYER,
    confidence: float = 1.0,
    bbox_values: dict[str, float] | None = None,
) -> Provenance:
    return Provenance(
        page_index=page_index,
        page_number=page_index + 1,
        bbox=BBox(**(bbox_values or {"x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.2})),
        coordinate_space=CoordinateSpace.NORMALIZED_TOP_LEFT,
        page_width_pt=595.0,
        page_height_pt=842.0,
        reader=reader,
        reader_version="test-reader-1.0",
        confidence=confidence,
        document_hash=f"sha256:{document_hash_value}",
    )


def leaf(
    value: str,
    *,
    document_hash_value: str,
    value_type: ValueType = ValueType.STRING,
    page_index: int = 0,
    reader: ReaderKind = ReaderKind.TEXT_LAYER,
    confidence: float = 1.0,
    bbox_values: dict[str, float] | None = None,
) -> Field:
    return Field(
        kind="scalar",
        value=value,
        value_type=value_type,
        provenance=provenance(
            document_hash_value=document_hash_value,
            page_index=page_index,
            reader=reader,
            confidence=confidence,
            bbox_values=bbox_values,
        ),
        agreement=Agreement.UNANIMOUS,
    )


def source(
    document_hash_value: str,
    *,
    page_start: int = 0,
    page_end: int = 0,
    filename: str = "document.pdf",
) -> EnvelopeSource:
    return EnvelopeSource(
        filename=filename,
        content_type="application/pdf",
        file_hash=f"sha256:{FILE_HASH}",
        document_hash=f"sha256:{document_hash_value}",
        page_start=page_start,
        page_end=page_end,
        page_count=page_end - page_start + 1,
    )


def table_meta(
    stated: int,
    extracted: int,
    *,
    unresolved: list[int] | None = None,
    columns: list[TableColumn] | None = None,
    truncated: bool = False,
) -> TableMeta:
    return TableMeta(
        columns=columns or [],
        row_count_stated=stated,
        row_count_extracted=extracted,
        truncated=truncated,
        unresolved_rows=unresolved or [],
    )


def payload(
    document_type: str,
    fields: dict[str, Field],
    *,
    tables: dict[str, TableMeta] | None = None,
    extension: dict[str, Field] | None = None,
    page_start: int = 0,
    page_end: int = 0,
    document_hash_value: str | None = None,
) -> dict[str, Any]:
    spec = DOCUMENT_SCHEMAS[document_type]
    digest = document_hash_value or identity(
        document_type, page_start=page_start, page_end=page_end
    )
    return {
        "document_type": document_type,
        "schema_version": spec.version,
        "source": source(digest, page_start=page_start, page_end=page_end).model_dump(),
        "fields": fields,
        "tables": tables or {},
        "extension": extension or {},
    }


def required_fields(document_type: str, *, page_index: int = 0) -> dict[str, Field]:
    digest = identity(document_type)
    return {
        path: leaf(
            _VALUE_SAMPLES[value_type],
            document_hash_value=digest,
            value_type=value_type,
            page_index=page_index,
        )
        for path, value_type in REQUIRED_PATHS[document_type].items()
    }


def invoice_with_lines(
    rows: tuple[int, ...] = (0,),
    *,
    stated: int | None = None,
    extracted: int | None = None,
    unresolved: tuple[int, ...] = (),
    include_table: bool = True,
    truncated: bool = False,
    extra_fields: dict[str, Field] | None = None,
    extension: dict[str, Field] | None = None,
    document_type: str = "commercial_invoice",
) -> dict[str, Any]:
    digest = identity(document_type)
    fields = required_fields(document_type)
    for row in rows:
        fields[f"line_items[{row}].description"] = leaf(f"Widget {row}", document_hash_value=digest)
        fields[f"line_items[{row}].quantity"] = leaf(
            "1", document_hash_value=digest, value_type=ValueType.DECIMAL
        )
        fields[f"line_items[{row}].unit_price"] = leaf(
            "10.00", document_hash_value=digest, value_type=ValueType.DECIMAL
        )
        fields[f"line_items[{row}].line_extension_amount"] = leaf(
            "10.00", document_hash_value=digest, value_type=ValueType.DECIMAL
        )
    if extra_fields:
        fields.update(extra_fields)
    tables: dict[str, TableMeta] = {}
    if include_table:
        tables["line_items"] = table_meta(
            stated if stated is not None else max(len(rows), 1),
            extracted if extracted is not None else len(rows),
            unresolved=list(unresolved),
            truncated=truncated,
        )
    return payload(document_type, fields, tables=tables, extension=extension)


# --- field-set oracle -------------------------------------------------------


@pytest.mark.parametrize("document_type", sorted(DOCUMENT_SCHEMAS))
def test_field_set_matches_the_independent_oracle(document_type: str) -> None:
    spec = DOCUMENT_SCHEMAS[document_type]
    declared = {field_spec.path for field_spec in spec.model.field_set.specs}
    assert declared == EXPECTED_PATHS[document_type]


@pytest.mark.parametrize("document_type", sorted(DOCUMENT_SCHEMAS))
def test_required_paths_match_the_independent_oracle(document_type: str) -> None:
    spec = DOCUMENT_SCHEMAS[document_type]
    required = {field_spec.path for field_spec in spec.model.field_set.specs if field_spec.required}
    expected = set(REQUIRED_PATHS[document_type]) | REQUIRED_ROW_PATHS.get(document_type, set())
    assert required == expected


def test_key_value_types_match_the_documented_spines() -> None:
    cases = {
        ("commercial_invoice", "invoice.issue_date"): ValueType.DATE,
        ("commercial_invoice", "invoice.currency"): ValueType.ENUM,
        ("commercial_invoice", "shipment.gross_weight_kg"): ValueType.DECIMAL,
        ("commercial_invoice", "shipment.package_count"): ValueType.INTEGER,
        ("proforma_invoice", "proforma.valid_until"): ValueType.DATE,
        ("bill_of_lading", "bl.original_count"): ValueType.INTEGER,
        ("bill_of_lading", "freight.amount"): ValueType.DECIMAL,
        ("delivery_order", "release.free_time_days"): ValueType.INTEGER,
        ("port_pass", "port_pass.valid_from"): ValueType.DATETIME,
        ("port_pass", "escort.required"): ValueType.BOOLEAN,
        ("custom_entry", "consignment.exchange_rate"): ValueType.DECIMAL,
        ("custom_entry", "goods_lines[].quantity"): ValueType.DECIMAL,
        ("verification_memo", "condition.seal_intact"): ValueType.BOOLEAN,
    }
    for (document_type, path), expected in cases.items():
        spec = DOCUMENT_SCHEMAS[document_type].model.field_set.resolve(path)
        assert spec is not None, path
        assert spec.value_type is expected, path


# --- registry ---------------------------------------------------------------


def test_registry_contains_exactly_the_seven_named_types() -> None:
    assert set(DOCUMENT_SCHEMAS) == {
        "commercial_invoice",
        "proforma_invoice",
        "bill_of_lading",
        "delivery_order",
        "port_pass",
        "custom_entry",
        "verification_memo",
    }
    assert "packing_list" not in DOCUMENT_SCHEMAS
    assert "unknown" not in DOCUMENT_SCHEMAS


@pytest.mark.parametrize("document_type", sorted(DOCUMENT_SCHEMAS))
def test_every_registry_entry_has_version_prompt_checks_and_tables(
    document_type: str,
) -> None:
    spec = DOCUMENT_SCHEMAS[document_type]
    assert spec.version
    assert spec.prompt.endswith(".md")
    assert spec.checks
    assert isinstance(spec.tables, frozenset)
    assert spec.tables == frozenset(table.name for table in spec.model.table_specs)


def test_custom_entry_is_the_only_provisional_entry() -> None:
    provisional = {
        document_type for document_type, spec in DOCUMENT_SCHEMAS.items() if spec.provisional
    }
    assert provisional == {"custom_entry"}
    from trade_extract.schemas.custom_entry import PROVISIONAL, PROVISIONAL_REASON

    assert PROVISIONAL is True
    assert PROVISIONAL_REASON


def test_unsupported_types_are_explicit() -> None:
    for document_type in ("unknown", "packing_list", "not_a_type", ""):
        with pytest.raises(UnsupportedDocumentTypeError, match="unsupported"):
            get_schema(document_type)


def test_an_eighth_type_is_additive_and_needs_no_pipeline_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Eighth(DocumentEnvelope):
        field_set = FieldSet(specs=(FieldSpec("eighth.number", ValueType.STRING, required=True),))
        table_specs = ()

        document_type: Literal["eighth_type"] = "eighth_type"
        schema_version: Literal["eighth.local-1.0"] = "eighth.local-1.0"

    spec = SchemaSpec(
        model=Eighth,
        version="eighth.local-1.0",
        prompt="eighth.md",
        checks="advisory",
        tables=frozenset(),
    )
    monkeypatch.setitem(DOCUMENT_SCHEMAS, "eighth_type", spec)
    assert get_schema("eighth_type") is spec
    assert "eighth.number" in spec.json_schema()["properties"]["fields"]["properties"]


def test_registry_rejects_a_version_that_disagrees_with_the_model() -> None:
    with pytest.raises(ValueError, match="schema_version"):
        SchemaSpec(
            model=DOCUMENT_SCHEMAS["commercial_invoice"].model,
            version="invoice.peppol-2.0",
            prompt="invoice.md",
            checks="invoice_full",
            tables=frozenset({"line_items"}),
        )


def test_registry_rejects_a_type_that_disagrees_with_the_entry() -> None:
    with pytest.raises(ValueError, match="document_type"):
        SchemaSpec(
            model=DOCUMENT_SCHEMAS["commercial_invoice"].model,
            version="invoice.peppol-1.0",
            prompt="invoice.md",
            checks="invoice_full",
            tables=frozenset({"line_items"}),
            document_type="proforma_invoice",
        )


def test_registry_rejects_a_table_set_that_disagrees_with_the_model() -> None:
    with pytest.raises(ValueError, match="declares tables"):
        SchemaSpec(
            model=DOCUMENT_SCHEMAS["commercial_invoice"].model,
            version="invoice.peppol-1.0",
            prompt="invoice.md",
            checks="invoice_full",
            tables=frozenset(),
            document_type="commercial_invoice",
        )


def test_registry_rejects_a_table_disconnected_from_the_field_set() -> None:
    class Disconnected(DocumentEnvelope):
        field_set = FieldSet(specs=(FieldSpec("disconnected.number", ValueType.STRING),))
        table_specs = (TableSpec(name="line_items"),)

        document_type: Literal["disconnected"] = "disconnected"
        schema_version: Literal["disconnected-1.0"] = "disconnected-1.0"

    with pytest.raises(ValueError, match="no 'line_items\\[\\]' paths|connected"):
        SchemaSpec(
            model=Disconnected,
            version="disconnected-1.0",
            prompt="disconnected.md",
            checks="advisory",
            tables=frozenset({"line_items"}),
        )


def test_registry_rejects_an_unpinned_model() -> None:
    class Unpinned(DocumentEnvelope):
        field_set = FieldSet(specs=(FieldSpec("unpinned.number", ValueType.STRING),))
        table_specs = ()

        document_type: str = "unpinned"
        schema_version: str = "unpinned-1.0"

    with pytest.raises(ValueError, match="Literal"):
        SchemaSpec(
            model=Unpinned,
            version="unpinned-1.0",
            prompt="unpinned.md",
            checks="advisory",
            tables=frozenset(),
        )


# --- round trips and the JSON Schema contract ------------------------------


@pytest.mark.parametrize("document_type", sorted(DOCUMENT_SCHEMAS))
def test_every_type_round_trips_without_altering_values(document_type: str) -> None:
    spec = DOCUMENT_SCHEMAS[document_type]
    fields = required_fields(document_type)
    document = spec.model.model_validate(payload(document_type, fields))
    dumped = document.model_dump()
    assert dumped["document_type"] == document_type
    assert dumped["schema_version"] == spec.version
    for path, emitted in fields.items():
        assert dumped["fields"][path]["value"] == emitted.value
        assert dumped["fields"][path]["provenance"]["page_index"] == emitted.provenance.page_index


def test_happy_path_is_a_valid_invoice_with_provenance() -> None:
    body = invoice_with_lines()
    document = DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)
    assert document.schema_version == "invoice.peppol-1.0"
    assert document.fields["line_items[0].description"].provenance.bbox.x0 == 0.1


def test_invoice_json_schema_is_a_per_path_provider_contract() -> None:
    schema = DOCUMENT_SCHEMAS["commercial_invoice"].json_schema()
    fields = schema["properties"]["fields"]
    assert "invoice.number" in fields["properties"]
    assert "invoice.number" in fields["required"]
    assert fields["additionalProperties"] is False
    patterns = fields["patternProperties"]
    assert any("line_items" in pattern and r"\d+" in pattern for pattern in patterns)
    assert any("quantity" in pattern for pattern in patterns)

    definitions = schema["$defs"]
    assert "Field" in definitions and "Provenance" in definitions
    assert "provenance" in definitions["Field"]["required"]
    assert definitions["Field"]["properties"]["provenance"]["$ref"].endswith("/Provenance")

    extension = schema["properties"]["extension"]
    assert extension["additionalProperties"] is False
    assert any("extension" in pattern for pattern in extension["patternProperties"])
    tables = schema["properties"]["tables"]
    assert "line_items" in tables["properties"]
    assert tables["additionalProperties"] is False


# --- the eleven matrix rows ------------------------------------------------


def test_missing_required_path_names_the_path() -> None:
    body = invoice_with_lines()
    del body["fields"]["invoice.number"]
    with pytest.raises(ValidationError, match="invoice.number"):
        DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)


def test_unknown_primary_path_is_rejected() -> None:
    body = invoice_with_lines()
    body["fields"]["invoice.kenya_vat"] = leaf(
        "X", document_hash_value=identity("commercial_invoice")
    )
    with pytest.raises(ValidationError, match=r"invoice\.kenya_vat"):
        DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)


def test_template_paths_may_not_be_emitted_as_concrete_fields() -> None:
    body = invoice_with_lines()
    body["fields"]["line_items[].description"] = leaf(
        "Widget", document_hash_value=identity("commercial_invoice")
    )
    with pytest.raises(ValidationError, match="template path"):
        DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)


def test_extension_namespaces_are_alpha_two_or_any() -> None:
    digest = identity("commercial_invoice")
    body = invoice_with_lines(
        extension={
            "extension.ke.pin": leaf("P051234567X", document_hash_value=digest),
            "extension.any.other": leaf("note", document_hash_value=digest),
        }
    )
    DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)

    body["extension"]["extension.kenya.pin"] = leaf("X", document_hash_value=digest)
    with pytest.raises(ValidationError, match=r"extension\.kenya\.pin"):
        DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)


def test_extension_values_are_strings_in_v1() -> None:
    digest = identity("commercial_invoice")
    body = invoice_with_lines(
        extension={
            "extension.ke.pin": leaf(
                "123", document_hash_value=digest, value_type=ValueType.INTEGER
            )
        }
    )
    with pytest.raises(ValidationError, match="expects 'string'"):
        DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)


def test_bad_bbox_coordinates_are_rejected() -> None:
    digest = identity("commercial_invoice")
    for values in (
        {"x0": 0.9, "y0": 0.1, "x1": 0.1, "y1": 0.2},
        {"x0": -0.1, "y0": 0.1, "x1": 0.2, "y1": 0.2},
        {"x0": 0.1, "y0": 0.1, "x1": 1.1, "y1": 0.2},
    ):
        with pytest.raises(ValidationError):
            leaf("INV-1", document_hash_value=digest, bbox_values=values)


def test_page_out_of_range_names_the_field_and_range() -> None:
    body = invoice_with_lines()
    body["fields"]["invoice.number"] = leaf(
        "INV-1",
        document_hash_value=identity("commercial_invoice"),
        page_index=1,
    )
    with pytest.raises(ValidationError, match=r"invoice\.number.*0\.\.0"):
        DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)


def test_type_mismatch_names_expected_and_actual() -> None:
    body = invoice_with_lines()
    body["fields"]["invoice.number"] = leaf(
        "1",
        document_hash_value=identity("commercial_invoice"),
        value_type=ValueType.INTEGER,
    )
    with pytest.raises(ValidationError, match="'integer'.*'string'"):
        DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)


def test_truncated_table_is_never_complete() -> None:
    body = invoice_with_lines(truncated=True)
    with pytest.raises(ValidationError, match="truncated"):
        DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)


def test_unresolved_rows_are_accepted_and_preserve_the_gap() -> None:
    body = invoice_with_lines(rows=(0, 2), stated=3, extracted=2, unresolved=(1,))
    document = DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)
    meta = document.tables["line_items"]
    assert meta.unresolved_rows == [1]
    assert "line_items[1].description" not in document.fields
    assert "line_items[2].description" in document.fields


def test_proforma_rejects_bill_of_lading_identity() -> None:
    body = invoice_with_lines(document_type="proforma_invoice")
    body["fields"]["bill_of_lading.number"] = leaf(
        "BL-1", document_hash_value=identity("proforma_invoice")
    )
    with pytest.raises(ValidationError, match=r"bill_of_lading\.number.*extension\.any"):
        DOCUMENT_SCHEMAS["proforma_invoice"].model.model_validate(body)


# --- table completeness ----------------------------------------------------


def test_metadata_claiming_rows_never_emitted_is_rejected() -> None:
    body = invoice_with_lines(rows=(), stated=2, extracted=2)
    with pytest.raises(ValidationError, match="row_count_extracted"):
        DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)


def test_unnamed_gap_is_rejected() -> None:
    body = invoice_with_lines(rows=(0, 2), stated=3, extracted=2, unresolved=())
    with pytest.raises(ValidationError, match="unresolved_rows"):
        DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)


def test_a_row_cannot_be_both_emitted_and_unresolved() -> None:
    body = invoice_with_lines(rows=(0, 1), stated=2, extracted=2, unresolved=(1,))
    with pytest.raises(ValidationError, match="both emitted and unresolved"):
        DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)


def test_emitted_row_count_must_match_metadata() -> None:
    body = invoice_with_lines(rows=(0, 1), stated=2, extracted=1)
    with pytest.raises(ValidationError, match="row_count_extracted"):
        DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)


def test_emitted_index_outside_the_stated_range_is_rejected() -> None:
    body = invoice_with_lines(rows=(1,), stated=1, extracted=1)
    with pytest.raises(ValidationError, match="outside the stated range"):
        DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)


def test_rows_without_table_metadata_are_rejected() -> None:
    body = invoice_with_lines(include_table=False)
    with pytest.raises(ValidationError, match="no TableMeta"):
        DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)


def test_unknown_table_metadata_is_rejected() -> None:
    body = invoice_with_lines()
    body["tables"]["invented"] = table_meta(0, 0)
    with pytest.raises(ValidationError, match="invented"):
        DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)


def test_required_row_cells_are_required_for_every_emitted_row() -> None:
    body = invoice_with_lines()
    del body["fields"]["line_items[0].line_extension_amount"]
    with pytest.raises(ValidationError, match=r"line_items\[0\]\.line_extension_amount"):
        DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)


def test_array_indices_without_a_table_stay_contiguous() -> None:
    digest = identity("commercial_invoice")
    body = invoice_with_lines(
        extra_fields={
            "tax_breakdown[0].taxable_amount": leaf(
                "10", document_hash_value=digest, value_type=ValueType.DECIMAL
            ),
            "tax_breakdown[2].taxable_amount": leaf(
                "10", document_hash_value=digest, value_type=ValueType.DECIMAL
            ),
        }
    )
    with pytest.raises(ValidationError, match="tax_breakdown"):
        DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)


@pytest.mark.parametrize("array_name", ["container_numbers", "seal_numbers"])
def test_nested_array_indices_are_contiguous_per_parent_row(array_name: str) -> None:
    document_type = "custom_entry"
    digest = identity(document_type)
    fields = required_fields(document_type)
    for row in (0, 1):
        fields[f"goods_lines[{row}].description"] = leaf(f"Goods {row}", document_hash_value=digest)
        fields[f"goods_lines[{row}].{array_name}[0]"] = leaf(
            f"Identifier {row}", document_hash_value=digest
        )
    body = payload(document_type, fields, tables={"goods_lines": table_meta(2, 2)})
    model = DOCUMENT_SCHEMAS[document_type].model

    document = model.model_validate(body)
    for row in (0, 1):
        assert document.fields[f"goods_lines[{row}].{array_name}[0]"].value == f"Identifier {row}"

    # Across both parents the indices are still {0, 1}, but the second row
    # independently lacks index zero.
    fields[f"goods_lines[1].{array_name}[1]"] = fields.pop(f"goods_lines[1].{array_name}[0]")
    with pytest.raises(ValidationError, match=rf"goods_lines\[1\]\.{array_name}.*not contiguous"):
        model.model_validate(body)


def test_huge_array_indices_are_rejected_without_materialising_a_range() -> None:
    digest = identity("commercial_invoice")
    body = invoice_with_lines(
        extra_fields={
            "tax_breakdown[1000000000].taxable_amount": leaf(
                "10", document_hash_value=digest, value_type=ValueType.DECIMAL
            )
        }
    )
    with pytest.raises(ValidationError, match="not contiguous"):
        DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)


# --- identity and required values -----------------------------------------


def test_source_hash_must_be_the_derived_segment_identity() -> None:
    body = invoice_with_lines()
    bogus = f"sha256:{OTHER_HASH}"
    body["source"]["document_hash"] = bogus
    body["fields"] = {
        path: field.model_copy(
            update={"provenance": field.provenance.model_copy(update={"document_hash": bogus})}
        )
        for path, field in body["fields"].items()
    }
    with pytest.raises(ValidationError, match="source.document_hash"):
        DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)


def test_a_field_hash_that_disagrees_with_the_source_is_rejected() -> None:
    body = invoice_with_lines()
    body["fields"]["invoice.number"] = leaf("INV-1", document_hash_value=OTHER_HASH)
    with pytest.raises(ValidationError, match=r"invoice\.number.*document_hash"):
        DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)


def test_required_value_may_not_be_empty_or_whitespace() -> None:
    for value in ("", "   "):
        body = invoice_with_lines()
        body["fields"]["invoice.number"] = leaf(
            value, document_hash_value=identity("commercial_invoice")
        )
        with pytest.raises(ValidationError, match="must not be empty"):
            DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)


def test_optional_values_are_emitted_untouched_even_when_blank() -> None:
    body = invoice_with_lines()
    body["fields"]["invoice.note"] = leaf("", document_hash_value=identity("commercial_invoice"))
    document = DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)
    assert document.fields["invoice.note"].value == ""


def test_disputed_alternatives_must_agree_with_the_source_identity() -> None:
    digest = identity("commercial_invoice")
    body = invoice_with_lines()
    body["fields"]["invoice.number"] = Field(
        kind="scalar",
        value="INV-1",
        value_type=ValueType.STRING,
        provenance=provenance(document_hash_value=digest),
        agreement=Agreement.DISPUTED,
        alternatives=[
            FieldAlternative(
                value="INV-2",
                reader=ReaderKind.VISION,
                confidence=0.8,
                provenance=provenance(
                    document_hash_value=OTHER_HASH,
                    reader=ReaderKind.VISION,
                    confidence=0.8,
                ),
            )
        ],
    )
    with pytest.raises(ValidationError, match="alternative provenance document_hash"):
        DOCUMENT_SCHEMAS["commercial_invoice"].model.model_validate(body)


# --- public API and import boundary ---------------------------------------


def _openapi(tmp_path: Path) -> dict[str, Any]:
    app = create_app(
        Settings(
            environment="test",
            database_url="postgresql+psycopg://trade:trade@localhost:5432/unused",
            secret_key="contract-secret",
            api_key_pepper="contract-pepper",
            storage_dir=tmp_path / "storage",
        )
    )
    try:
        return app.openapi()
    finally:
        app.state.engine.dispose()


def test_published_openapi_uses_the_shared_field_and_envelope_models(tmp_path: Path) -> None:
    schemas = _openapi(tmp_path)["components"]["schemas"]
    document = schemas["Document"]["properties"]

    for name in ("fields", "extension"):
        mapping = next(
            branch for branch in document[name]["anyOf"] if branch.get("type") == "object"
        )
        assert mapping["additionalProperties"]["$ref"].endswith("/Field")
    tables = next(
        branch for branch in document["tables"]["anyOf"] if branch.get("type") == "object"
    )
    assert tables["additionalProperties"]["$ref"].endswith("/TableMeta")
    assert document["source"]["anyOf"][0]["$ref"].endswith("/EnvelopeSource")

    field = schemas["Field"]
    assert "provenance" in field["required"]
    assert field["properties"]["provenance"]["$ref"].endswith("/Provenance")
    assert "page_index" in schemas["Provenance"]["required"]
    assert "row_count_stated" in schemas["TableMeta"]["required"]


def test_registry_import_does_not_reach_db_services_api_or_providers() -> None:
    forbidden = (
        "trade_extract.db",
        "trade_extract.services",
        "trade_extract.api",
        "trade_extract.providers",
    )
    code = (
        "import sys\n"
        "import trade_extract.schemas.registry\n"
        "names = [n for n in sys.modules if n.startswith(" + repr(forbidden) + ")]\n"
        "print(','.join(names))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    assert result.stdout.strip() == "", result.stderr


def test_provider_schema_requires_fields_and_declared_value_types() -> None:
    schema = get_schema("commercial_invoice").json_schema()
    assert "fields" in schema["required"]
    fields = schema["properties"]["fields"]
    assert fields["properties"]["invoice.issue_date"]["properties"]["value_type"] == {
        "const": "date"
    }
    assert all("value_type" in item["properties"] for item in fields["patternProperties"].values())
    extension = schema["properties"]["extension"]["patternProperties"]
    assert all(
        item["properties"]["value_type"] == {"const": "string"} for item in extension.values()
    )


def test_registry_rejects_undeclared_table_column() -> None:
    class BadColumn(DocumentEnvelope):
        field_set = FieldSet((FieldSpec("rows[].name", ValueType.STRING),))
        table_specs = (TableSpec("rows", (TableColumn("missing", "Missing"),)),)
        document_type: Literal["bad_column"] = "bad_column"
        schema_version: Literal["bad_column-1.0"] = "bad_column-1.0"

    with pytest.raises(ValueError, match="column 'missing'.*absent"):
        SchemaSpec(BadColumn, "bad_column-1.0", "test.md", "advisory", frozenset({"rows"}))


def test_leading_zero_indices_cannot_alias_a_cell() -> None:
    body = invoice_with_lines()
    body["fields"]["line_items[00].description"] = leaf(
        "different", document_hash_value=identity("commercial_invoice")
    )
    with pytest.raises(ValidationError, match="noncanonical index"):
        get_schema("commercial_invoice").model.model_validate(body)
