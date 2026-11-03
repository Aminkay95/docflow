"""Invoice checks compare stated values; computed amounts never leave outcomes."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Literal

from trade_extract.domain.checks import CheckResult
from trade_extract.domain.fields import Agreement, ValueType
from trade_extract.domain.numbers import decimal_value as decimal_value
from trade_extract.domain.numbers import exact_arithmetic
from trade_extract.schemas.invoice import INVOICE_FIELD_SET, CommercialInvoice

# ISO 4217 current alphabetic registry, SIX publication 2026-09-17.
# Source: https://www.six-group.com/dam/download/financial-information/data-center/
# iso-currrency/lists/list-one.xml
CURRENCY_REGISTRY_DATE = "2026-09-17"
CURRENCIES = frozenset(
    [
        "AED",
        "AFN",
        "ALL",
        "AMD",
        "AOA",
        "ARS",
        "AUD",
        "AWG",
        "AZN",
        "BAM",
        "BBD",
        "BDT",
        "BHD",
        "BIF",
        "BMD",
        "BND",
        "BOB",
        "BOV",
        "BRL",
        "BSD",
        "BTN",
        "BWP",
        "BYN",
        "BZD",
        "CAD",
        "CDF",
        "CHE",
        "CHF",
        "CHW",
        "CLF",
        "CLP",
        "CNY",
        "COP",
        "COU",
        "CRC",
        "CUP",
        "CVE",
        "CZK",
        "DJF",
        "DKK",
        "DOP",
        "DZD",
        "EGP",
        "ERN",
        "ETB",
        "EUR",
        "FJD",
        "FKP",
        "GBP",
        "GEL",
        "GHS",
        "GIP",
        "GMD",
        "GNF",
        "GTQ",
        "GYD",
        "HKD",
        "HNL",
        "HTG",
        "HUF",
        "IDR",
        "ILS",
        "INR",
        "IQD",
        "IRR",
        "ISK",
        "JMD",
        "JOD",
        "JPY",
        "KES",
        "KGS",
        "KHR",
        "KMF",
        "KPW",
        "KRW",
        "KWD",
        "KYD",
        "KZT",
        "LAK",
        "LBP",
        "LKR",
        "LRD",
        "LSL",
        "LYD",
        "MAD",
        "MDL",
        "MGA",
        "MKD",
        "MMK",
        "MNT",
        "MOP",
        "MRU",
        "MUR",
        "MVR",
        "MWK",
        "MXN",
        "MXV",
        "MYR",
        "MZN",
        "NAD",
        "NGN",
        "NIO",
        "NOK",
        "NPR",
        "NZD",
        "OMR",
        "PAB",
        "PEN",
        "PGK",
        "PHP",
        "PKR",
        "PLN",
        "PYG",
        "QAR",
        "RON",
        "RSD",
        "RUB",
        "RWF",
        "SAR",
        "SBD",
        "SCR",
        "SDG",
        "SEK",
        "SGD",
        "SHP",
        "SLE",
        "SOS",
        "SRD",
        "SSP",
        "STN",
        "SVC",
        "SYP",
        "SZL",
        "THB",
        "TJS",
        "TMT",
        "TND",
        "TOP",
        "TRY",
        "TTD",
        "TWD",
        "TZS",
        "UAH",
        "UGX",
        "USD",
        "USN",
        "UYI",
        "UYU",
        "UYW",
        "UZS",
        "VED",
        "VES",
        "VND",
        "VUV",
        "WST",
        "XAD",
        "XAF",
        "XAG",
        "XAU",
        "XBA",
        "XBB",
        "XBC",
        "XBD",
        "XCD",
        "XCG",
        "XDR",
        "XOF",
        "XPD",
        "XPF",
        "XPT",
        "XSU",
        "XTS",
        "XUA",
        "XXX",
        "YER",
        "ZAR",
        "ZMW",
        "ZWG",
    ]
)
INCOTERMS = frozenset(["EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP"])


def valid_container(value: str) -> bool:
    value = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}[UJZ]\d{7}", value):
        return False
    alphabet = {
        char: n
        for char, n in zip(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ", (n for n in range(10, 39) if n % 11), strict=True
        )
    }
    total = sum((int(c) if c.isdigit() else alphabet[c]) * (2**i) for i, c in enumerate(value[:10]))
    return total % 11 % 10 == int(value[-1])


def words_amount(value: str) -> Decimal | None:
    """Recognize a strict English grammar; unsupported forms are never guesses."""
    text = value.lower().strip().removesuffix(" only").strip()
    fraction = re.search(r"(?: and)? (\d{1,2})/100$", text)
    cents = Decimal(fraction[1]) / 100 if fraction else Decimal(0)
    if fraction:
        text = text[: fraction.start()].strip()
    text = re.sub(r" (?:dollars?|shillings?|euros?|pounds?)$", "", text)
    tokens = [t for t in text.replace("-", " ").split() if t != "and"]
    words = [
        "zero",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
    ]
    small = dict(zip(words, range(20), strict=True))
    tens = dict(
        zip(
            ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"],
            range(20, 100, 10),
            strict=True,
        )
    )

    def under_hundred(group: list[str]) -> int | None:
        if not group:
            return 0
        if len(group) == 1:
            return small.get(group[0], tens.get(group[0]))
        if len(group) == 2 and group[0] in tens and 1 <= small.get(group[1], -1) <= 9:
            return tens[group[0]] + small[group[1]]
        return None

    def under_thousand(group: list[str]) -> int | None:
        if "hundred" not in group:
            return under_hundred(group)
        if len(group) < 2 or group[1] != "hundred" or not 1 <= small.get(group[0], -1) <= 9:
            return None
        remainder = under_hundred(group[2:])
        return small[group[0]] * 100 + remainder if remainder is not None else None

    if not tokens:
        return None
    scales = {"billion": 1000000000, "million": 1000000, "thousand": 1000}
    previous_scale, total, start = 1000000000000, 0, 0
    for index, token in enumerate(tokens):
        if token not in scales:
            continue
        group = under_thousand(tokens[start:index])
        scale = scales[token]
        if not group or scale >= previous_scale:
            return None
        total += group * scale
        previous_scale, start = scale, index + 1
    remainder = under_thousand(tokens[start:])
    if remainder is None:
        return None
    return Decimal(total + remainder) + cents


@exact_arithmetic
def run_invoice_checks(
    doc: CommercialInvoice, *, tolerance: Decimal = Decimal("0.01")
) -> list[CheckResult]:
    if not tolerance.is_finite() or tolerance < 0 or tolerance > 1:
        raise ValueError("invalid_tolerance")
    fields = doc.fields
    output: list[CheckResult] = []

    def result(
        code: str,
        path: str,
        ok: bool | None,
        expected: object,
        actual: object,
        *,
        warning: bool = False,
    ) -> None:
        status: Literal["skipped", "pass", "warn", "fail"] = (
            "skipped" if ok is None else "pass" if ok else "warn" if warning else "fail"
        )
        output.append(
            CheckResult(
                code=code,
                status=status,
                field_path=path,
                expected=expected,
                actual=actual,
                severity="warning" if warning else "blocking",
                message=f"{code} {path}: expected {expected}; actual {actual}"
                if ok is not None
                else f"{code} {path}: optional inputs not stated",
            )
        )

    def arithmetic(
        code: str,
        target: str,
        operands: list[tuple[str, int]],
        *,
        optional_zero: tuple[str, ...] = (),
    ) -> None:
        needed = [target, *(p for p, _ in operands if p not in optional_zero)]
        if any(p not in fields for p in needed):
            result(code, target, None, "stated operands", None)
            return
        try:
            expected = sum(
                (
                    decimal_value(fields[p].value) * sign if p in fields else Decimal(0)
                    for p, sign in operands
                ),
                Decimal(0),
            )
            actual = decimal_value(fields[target].value)
            result(
                code,
                target,
                abs(expected - actual) <= tolerance,
                str(expected),
                fields[target].value,
            )
        except ValueError:
            result(
                code,
                target,
                False,
                "unambiguous decimal operands",
                {p: fields[p].value for p in needed},
            )

    missing = [
        s.path
        for s in INVOICE_FIELD_SET.specs
        if s.required
        and not s.indexed
        and (s.path not in fields or not fields[s.path].value.strip())
    ]
    rows = sorted({int(m[1]) for p in fields if (m := re.match(r"line_items\[(\d+)\]", p))})
    if not rows:
        missing.append("line_items")
    for row in rows:
        missing.extend(
            s.path.replace("[]", f"[{row}]")
            for s in INVOICE_FIELD_SET.specs
            if s.required
            and s.indexed
            and (
                s.path.replace("[]", f"[{row}]") not in fields
                or not fields[s.path.replace("[]", f"[{row}]")].value.strip()
            )
        )
    incomplete = [
        name
        for name, meta in doc.tables.items()
        if meta.truncated
        or meta.unresolved_rows
        or meta.row_count_stated != meta.row_count_extracted
    ]
    result(
        "INV-001",
        "$",
        not missing and not incomplete,
        "all required fields and complete rows",
        missing + incomplete,
    )
    for row in rows:
        prefix = f"line_items[{row}]"
        paths = [f"{prefix}.{p}" for p in ("quantity", "unit_price", "line_extension_amount")]
        if not all(p in fields for p in paths):
            result("INV-002", paths[-1], False, "required operands", None)
        else:
            try:
                quantity, price, amount = (decimal_value(fields[p].value) for p in paths)
                expected = quantity * price
                result(
                    "INV-002",
                    paths[-1],
                    abs(expected - amount) <= tolerance,
                    str(expected),
                    fields[paths[-1]].value,
                )
            except ValueError:
                result(
                    "INV-002",
                    paths[-1],
                    False,
                    "unambiguous decimals",
                    [fields[p].value for p in paths],
                )
    arithmetic(
        "INV-003",
        "totals.line_extension_amount",
        [(f"line_items[{r}].line_extension_amount", 1) for r in rows],
    )
    arithmetic(
        "INV-004",
        "totals.tax_inclusive_amount",
        [("totals.tax_exclusive_amount", 1), ("totals.tax_amount", 1)],
    )
    optional = ("totals.charge_total", "totals.allowance_total", "totals.prepaid_amount")
    arithmetic(
        "INV-005",
        "totals.payable_amount",
        [
            ("totals.tax_inclusive_amount", 1),
            (optional[0], 1),
            (optional[1], -1),
            (optional[2], -1),
        ],
        optional_zero=optional,
    )
    for code, path, valid in [
        ("INV-006", "invoice.currency", CURRENCIES),
        ("INV-007", "incoterm.code", INCOTERMS),
    ]:
        value = fields.get(path)
        result(
            code,
            path,
            value.value.strip().upper() in valid if value else None,
            "registered code",
            value.value if value else None,
        )
    dates = {p: f.value for p, f in fields.items() if f.value_type == ValueType.DATE}
    try:
        parsed = {p: date.fromisoformat(v) for p, v in dates.items()}
        ok = not (
            "invoice.due_date" in parsed
            and "invoice.issue_date" in parsed
            and parsed["invoice.issue_date"] > parsed["invoice.due_date"]
        )
        result(
            "INV-008", "invoice.issue_date", ok if dates else None, "ISO date; issue <= due", dates
        )
    except ValueError:
        result("INV-008", "invoice.issue_date", False, "unambiguous valid ISO dates", dates)
    for path, field in fields.items():
        if field.value_type in (ValueType.DECIMAL, ValueType.INTEGER):
            try:
                amount_value = decimal_value(field.value)
                ok = amount_value > 0 if path.endswith(".quantity") else amount_value >= 0
                if field.value_type == ValueType.INTEGER:
                    ok = ok and amount_value == amount_value.to_integral_value()
                result(
                    "INV-009",
                    path,
                    ok,
                    "> 0" if path.endswith(".quantity") else ">= 0",
                    field.value,
                )
            except ValueError:
                result("INV-009", path, False, "unambiguous nonnegative decimal", field.value)
    for code, pattern, validator in [
        (
            "INV-010",
            r"(?:shipment.container_numbers\[\d+\]|containers\[\d+\].number)",
            valid_container,
        ),
        (
            "INV-011",
            r".*\.hs_code",
            lambda v: re.fullmatch(r"\d{6}(?:\d{2})?(?:\d{2})?", v.strip()) is not None,
        ),
    ]:
        matches = [(p, f) for p, f in fields.items() if re.fullmatch(pattern, p)]
        for path, field in matches:
            result(code, path, validator(field.value), "valid identifier", field.value)
        if not matches:
            result(code, pattern, None, "valid identifier", None)
    # Critical identifiers, required cells and all arithmetic operands block pass.
    critical = {s.path for s in INVOICE_FIELD_SET.specs if s.required} | {
        "totals.tax_amount",
        "totals.charge_total",
        "totals.allowance_total",
        "totals.prepaid_amount",
        "tax_breakdown[].tax_amount",
        "tax_breakdown[].taxable_amount",
        "tax_breakdown[].tax_rate",
        "line_items[].tax_amount",
        "line_items[].tax_rate",
        "line_items[].allowance_charge",
        "bill_of_lading.number",
        "shipment.container_numbers[]",
    }
    disputed = [
        p
        for p, f in fields.items()
        if f.agreement == Agreement.DISPUTED and re.sub(r"\[\d+\]", "[]", p) in critical
    ]
    result("INV-012", "$", not disputed, "no critical reader disagreement", disputed)
    for path, field in fields.items():
        if field.agreement == Agreement.DISPUTED and path not in disputed:
            result(
                "INV-012",
                path,
                False,
                "reader agreement (noncritical advisory)",
                field.value,
                warning=True,
            )
    tax_paths = [(p, 1) for p in fields if re.fullmatch(r"tax_breakdown\[\d+\].tax_amount", p)]
    if tax_paths:
        arithmetic("INV-013", "totals.tax_amount", tax_paths)
    else:
        result("INV-013", "tax_breakdown", None, "stated tax breakdown", None)
    words = fields.get("totals.amount_in_words")
    payable = fields.get("totals.payable_amount")
    if words and payable:
        try:
            expected_words = words_amount(words.value)
            ok = (
                expected_words is not None
                and abs(expected_words - decimal_value(payable.value)) <= tolerance
            )
            result(
                "INV-014",
                "totals.amount_in_words",
                ok,
                str(expected_words)
                if expected_words is not None
                else "supported unambiguous word form",
                words.value,
                warning=True,
            )
        except ValueError:
            result(
                "INV-014",
                "totals.amount_in_words",
                False,
                "unambiguous payable amount",
                payable.value,
                warning=True,
            )
    else:
        result("INV-014", "totals.amount_in_words", None, "both amounts stated", None, warning=True)
    return output
