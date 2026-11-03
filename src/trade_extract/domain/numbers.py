"""Strict stated-number parsing shared by deterministic comparisons."""

import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation, localcontext
from functools import wraps

MAX_DIGITS = 256


def exact_arithmetic[**P, T](function: Callable[P, T]) -> Callable[P, T]:
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        with localcontext() as context:
            context.prec = MAX_DIGITS * 8
            return function(*args, **kwargs)

    return wrapped


def decimal_value(value: str) -> Decimal:
    if len(value.strip()) > MAX_DIGITS + 2:
        raise ValueError("decimal_digit_budget_exhausted")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value.strip()):
        raise ValueError("invalid_or_ambiguous_decimal")
    if sum(char.isdigit() for char in value) > MAX_DIGITS:
        raise ValueError("decimal_digit_budget_exhausted")
    try:
        result = Decimal(value.strip())
    except InvalidOperation as exc:
        raise ValueError("invalid_decimal") from exc
    if not result.is_finite():
        raise ValueError("nonfinite_decimal")
    return result
