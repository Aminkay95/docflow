"""The document-type registry: one entry per registered schema, no pipeline branch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trade_extract.schemas.base import DocumentEnvelope, FieldSet, single_literal
from trade_extract.schemas.bill_of_lading import BillOfLading
from trade_extract.schemas.custom_entry import PROVISIONAL, CustomEntry
from trade_extract.schemas.delivery_order import DeliveryOrder
from trade_extract.schemas.invoice import CommercialInvoice
from trade_extract.schemas.port_pass import PortPass
from trade_extract.schemas.proforma import ProformaInvoice
from trade_extract.schemas.verification_memo import VerificationMemo


class UnsupportedDocumentTypeError(LookupError):
    """Raised when a document type has no registered schema."""


@dataclass(frozen=True)
class SchemaSpec:
    """A registered model plus the prompt, checks and tables that travel with it."""

    model: type[DocumentEnvelope]
    version: str
    prompt: str
    checks: str
    tables: frozenset[str] = frozenset()
    provisional: bool = False
    document_type: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.model, type) or not issubclass(self.model, DocumentEnvelope):
            raise TypeError("SchemaSpec.model must be a DocumentEnvelope subclass")
        if self.model is DocumentEnvelope:
            raise TypeError("SchemaSpec.model must be a concrete document model")
        object.__setattr__(self, "tables", frozenset(self.tables))
        for name, value in (
            ("version", self.version),
            ("prompt", self.prompt),
            ("checks", self.checks),
        ):
            if not value:
                raise ValueError(f"SchemaSpec.{name} must not be empty")

        declared_type = self._literal("document_type")
        declared_version = self._literal("schema_version")
        if declared_type is None:
            raise ValueError(
                f"{self.model.__name__}.document_type must be a single-value Literal so the "
                "registry can trust the emitted type"
            )
        if declared_version is None:
            raise ValueError(
                f"{self.model.__name__}.schema_version must be a single-value Literal so the "
                "registry can trust the emitted version"
            )
        if declared_version != self.version:
            raise ValueError(
                f"{self.model.__name__} declares schema_version '{declared_version}' but the "
                f"registry entry says '{self.version}'"
            )
        if self.document_type is not None and declared_type != self.document_type:
            raise ValueError(
                f"{self.model.__name__} declares document_type '{declared_type}' but the "
                f"registry entry says '{self.document_type}'"
            )

        declared_tables = frozenset(spec.name for spec in self.model.table_specs)
        if len(declared_tables) != len(self.model.table_specs):
            raise ValueError(f"{self.model.__name__} declares a table more than once")
        if declared_tables != self.tables:
            raise ValueError(
                f"{self.model.__name__} declares tables {sorted(declared_tables)} but the "
                f"registry entry says {sorted(self.tables)}"
            )
        field_set: FieldSet = self.model.field_set
        row_tables = field_set.table_names()
        for table in sorted(declared_tables):
            if table not in row_tables:
                raise ValueError(
                    f"{self.model.__name__} registers table '{table}' but its field set has no "
                    f"'{table}[]' paths; tables must be connected to the field set"
                )
        for table_spec in self.model.table_specs:
            row_paths = {spec.path for spec in field_set.row_specs(table_spec.name)}
            for column in table_spec.columns:
                prefix = f"{table_spec.name}[].{column.key}"
                if not any(
                    path == prefix or path.startswith((prefix + ".", prefix + "[]"))
                    for path in row_paths
                ):
                    raise ValueError(
                        f"table '{table_spec.name}' column '{column.key}' "
                        "is absent from its field set"
                    )

    def _literal(self, field_name: str) -> str | None:
        model_field = self.model.model_fields.get(field_name)
        if model_field is None:
            return None
        return single_literal(model_field.annotation)

    def json_schema(self) -> dict[str, Any]:
        """The provider output contract for this document type."""

        return self.model.json_schema()


DOCUMENT_SCHEMAS: dict[str, SchemaSpec] = {
    "commercial_invoice": SchemaSpec(
        model=CommercialInvoice,
        version="invoice.peppol-1.0",
        prompt="02-commercial-invoice.md",
        checks="invoice_full",
        tables=frozenset({"line_items"}),
        document_type="commercial_invoice",
    ),
    "proforma_invoice": SchemaSpec(
        model=ProformaInvoice,
        version="proforma.peppol-1.0",
        prompt="03-proforma-invoice.md",
        checks="advisory",
        tables=frozenset({"line_items"}),
        document_type="proforma_invoice",
    ),
    "bill_of_lading": SchemaSpec(
        model=BillOfLading,
        version="bl.dcsa-1.0",
        prompt="04-bill-of-lading.md",
        checks="advisory",
        tables=frozenset({"containers"}),
        document_type="bill_of_lading",
    ),
    "delivery_order": SchemaSpec(
        model=DeliveryOrder,
        version="delivery_order.local-1.0",
        prompt="05-delivery-order.md",
        checks="advisory",
        tables=frozenset({"containers"}),
        document_type="delivery_order",
    ),
    "port_pass": SchemaSpec(
        model=PortPass,
        version="port_pass.local-1.0",
        prompt="06-port-pass.md",
        checks="advisory",
        tables=frozenset(),
        document_type="port_pass",
    ),
    "custom_entry": SchemaSpec(
        model=CustomEntry,
        version="custom_entry.wco-1.0",
        prompt="07-custom-entry.md",
        checks="advisory",
        tables=frozenset({"goods_lines", "assessment"}),
        provisional=PROVISIONAL,
        document_type="custom_entry",
    ),
    "verification_memo": SchemaSpec(
        model=VerificationMemo,
        version="verification_memo.local-1.0",
        prompt="08-verification-memo.md",
        checks="advisory",
        tables=frozenset({"findings"}),
        document_type="verification_memo",
    ),
}

SUPPORTED_DOCUMENT_TYPES = frozenset(DOCUMENT_SCHEMAS)


def get_schema(document_type: str) -> SchemaSpec:
    """Look up one registered schema; anything else is explicitly unsupported."""

    try:
        return DOCUMENT_SCHEMAS[document_type]
    except KeyError:
        raise UnsupportedDocumentTypeError(
            f"document type '{document_type}' is unsupported; registered types are "
            f"{', '.join(sorted(DOCUMENT_SCHEMAS))}"
        ) from None


__all__ = [
    "DOCUMENT_SCHEMAS",
    "SUPPORTED_DOCUMENT_TYPES",
    "SchemaSpec",
    "UnsupportedDocumentTypeError",
    "get_schema",
]
