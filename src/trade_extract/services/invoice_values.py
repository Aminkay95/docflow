"""Normalize unambiguous invoice numbers and dates without changing source evidence."""

import contextlib
import re
from datetime import date

from trade_extract.domain.fields import Field, ValueType
from trade_extract.domain.numbers import decimal_value

_MONEY = re.compile(
    r"(?P<sign>[+-]?)\s*(?P<prefix>US\$|USD|EUR|GBP|KES|[$€£])?\s*"
    r"(?P<number>(?:[0-9]{1,3}(?:\.[0-9]{3})+,[0-9]+|"
    r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?))"
    r"(?:\s*(?P<suffix>USD|EUR|GBP|KES))?",
    re.IGNORECASE,
)
_DATE = re.compile(r"([0-9]{1,2})([/-])([0-9]{1,2})\2([0-9]{4})")

_MONTHS = {
    name.casefold(): index
    for index, month in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        start=1,
    )
    for name in (month, month[:3])
}
_DAY_FIRST = re.compile(r"([0-9]{1,2})\s+([A-Za-z]+)\s+([0-9]{4})")
_MONTH_FIRST = re.compile(r"([A-Za-z]+)\s+([0-9]{1,2}),?\s+([0-9]{4})")


def _named_date(value: str) -> str | None:
    if match := _DAY_FIRST.fullmatch(value):
        day, name, year = match.groups()
    elif match := _MONTH_FIRST.fullmatch(value):
        name, day, year = match.groups()
    else:
        return None
    month = _MONTHS.get(name.casefold())
    if month is not None:
        with contextlib.suppress(ValueError):
            return date(int(year), month, int(day)).isoformat()
    return None


def normalize_invoice_field(path: str, field: Field) -> Field:
    printed = field.value
    raw = field.raw_text.strip() if field.raw_text else ""
    if field.value_type == ValueType.DATE and (source_date := _DATE.fullmatch(raw)):
        first, second = int(source_date[1]), int(source_date[3])
        if 1 <= first <= 12 and 1 <= second <= 12 and first != second:
            return field.model_copy(update={"value": raw})
    if path in ("invoice.currency", "proforma.currency") and raw in {"$", "£", "¥"}:
        return field.model_copy(update={"value": raw})
    value = printed.strip()
    normalized = None
    if field.value_type == ValueType.DECIMAL and path.endswith(".tax_rate"):
        with contextlib.suppress(ValueError):
            normalized = format(decimal_value(value.removesuffix("%")), "f")
    elif field.value_type == ValueType.DECIMAL:
        match = _MONEY.fullmatch(value)
        if match and not (match["prefix"] and match["suffix"]):
            number = match["number"]
            # A grouped value without a decimal point can
            # also be a decimal-comma value, such as 1,234.
            if "," not in number or "." in number:
                if "." in number and number.rfind(",") > number.rfind("."):
                    number = number.replace(".", "").replace(",", ".")
                else:
                    number = number.replace(",", "")
                with contextlib.suppress(ValueError):
                    normalized = format(decimal_value(match["sign"] + number), "f")
    elif field.value_type == ValueType.DATE and (match := _DATE.fullmatch(value)):
        first, second, year = int(match[1]), int(match[3]), int(match[4])
        if first > 12 or second > 12 or first == second:
            month, day = (second, first) if first > 12 else (first, second)
            with contextlib.suppress(ValueError):
                normalized = date(year, month, day).isoformat()
    elif field.value_type == ValueType.DATE:
        normalized = _named_date(raw) or _named_date(value)
    if normalized is None or normalized == printed:
        return field
    return field.model_copy(update={"value": normalized, "raw_text": field.raw_text or printed})
