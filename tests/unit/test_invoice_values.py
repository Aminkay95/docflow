import pytest

from tests.unit.test_extraction import fixture
from trade_extract.domain.fields import Field
from trade_extract.domain.provenance import ReaderKind
from trade_extract.services.invoice_values import normalize_invoice_field


@pytest.mark.parametrize(
    ("path", "printed", "expected"),
    [
        ("totals.payable_amount", "$346.50", "346.50"),
        ("totals.payable_amount", "USD 1,234.50", "1234.50"),
        ("totals.payable_amount", "1,234", "1,234"),
        ("totals.payable_amount", "€1.234,50", "1234.50"),
        ("tax_breakdown[0].tax_rate", "7.5%", "7.5"),
        ("invoice.issue_date", "18/03/2024", "2024-03-18"),
        ("invoice.issue_date", "03/18/2024", "2024-03-18"),
        ("invoice.issue_date", "2 August 2025", "2025-08-02"),
        ("invoice.issue_date", "August 2, 2025", "2025-08-02"),
        ("invoice.issue_date", "03/12/2024", "03/12/2024"),
        ("invoice.issue_date", "31/02/2024", "31/02/2024"),
    ],
)
def test_invoice_values_preserve_evidence_and_leave_ambiguous_formats_for_review(
    tmp_path, path, printed, expected
):
    _, _, payload = fixture(tmp_path)
    fields = payload(ReaderKind.TEXT_LAYER)["fields"]
    field = Field.model_validate(fields.get(path, fields["totals.payable_amount"]))
    source = field.model_copy(update={"value": printed, "raw_text": printed})
    result = normalize_invoice_field(path, source)
    assert result.value == expected
    assert result.raw_text == printed
    assert result.provenance == source.provenance


@pytest.mark.parametrize(
    ("path", "guessed", "printed"),
    [
        ("invoice.issue_date", "2024-03-05", "03/05/2024"),
        ("invoice.currency", "USD", "$"),
    ],
)
def test_provider_guess_does_not_override_ambiguous_printed_evidence(
    tmp_path, path, guessed, printed
):
    _, _, payload = fixture(tmp_path)
    field = Field.model_validate(payload(ReaderKind.VISION)["fields"][path])
    claimed = field.model_copy(update={"value": guessed, "raw_text": printed})
    observed = normalize_invoice_field(path, claimed)
    assert observed.value == printed
    assert observed.raw_text == printed
    assert observed.provenance == claimed.provenance
