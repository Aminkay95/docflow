"""Portable, versioned deterministic check outcomes."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

CHECK_VERSION = "invoice.peppol-1.0/checks-1"


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    code: str
    tier: int = 1
    status: Literal["pass", "fail", "warn", "skipped"]
    field_path: str | None = None
    expected: Any = None
    actual: Any = None
    severity: Literal["info", "warning", "blocking"] = "blocking"
    message: str
    check_version: str = CHECK_VERSION
