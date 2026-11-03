"""Fail-closed product policy; confidence is deliberately absent from this API."""

from typing import Any

from pydantic import ValidationError

from trade_extract.domain.checks import CHECK_VERSION, CheckResult
from trade_extract.domain.policy import GatePolicy


def effective_policy(config: dict[str, Any]) -> tuple[GatePolicy, bool]:
    try:
        return GatePolicy.model_validate(config), True
    except (ValidationError, ValueError, TypeError):
        return GatePolicy(), False


def decide_document(
    *,
    document_type: str,
    status: str,
    checks: list[CheckResult],
    policy_id: str,
    policy_version: int,
    config: dict[str, Any],
    open_conflicts: list[str] | None = None,
    has_unreadable_page: bool = False,
) -> dict[str, Any]:
    policy, valid = effective_policy(config)
    reasons = [
        f"check_failed:{c.code}:{c.field_path or '$'}"
        for c in checks
        if c.severity == "blocking" and c.status in ("fail", "warn")
    ]
    conflicts = open_conflicts or []
    if not valid:
        reasons.append("policy:invalid_configuration")
    if status in ("failed", "unreadable"):
        value = status
        reasons.append("extraction_failed" if status == "failed" else "unreadable")
    else:
        if status != "extracted":
            reasons.append("extraction:not_complete")
        if document_type != "commercial_invoice" or policy.review_depth == "full":
            reasons.append("review_depth:full")
        if not policy.invoice_pass_enabled:
            reasons.append("policy:invoice_pass_disabled")
        if document_type == "commercial_invoice" and not {
            f"INV-{number:03}" for number in range(1, 15)
        }.issubset({check.code for check in checks}):
            reasons.append("checks:missing")
        if has_unreadable_page:
            reasons.append("unreadable_page")
        reasons.extend(f"conflict_open:{key}" for key in conflicts)
        value = "review" if reasons else "pass"
    return {
        "value": value,
        "route": "conflict" if conflicts else "extraction" if value != "pass" else "none",
        "reasons": sorted(set(reasons)),
        "policy_id": policy_id,
        "policy_version": policy_version,
        "policy_snapshot": policy.model_dump(mode="json"),
        "policy_valid": valid,
        "check_version": CHECK_VERSION,
    }
