"""Versioned invoice gate configuration contract."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class GatePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    invoice_pass_enabled: StrictBool = False
    review_depth: Literal["full", "checks"] = "full"
    arithmetic_tolerance: Decimal = Field(default=Decimal("0.01"), ge=0, le=1, allow_inf_nan=False)


class LegacyReviewDepth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    commercial_invoice: Literal["gate", "full"]
    proforma_invoice: Literal["full"]
    bill_of_lading: Literal["full"]
    delivery_order: Literal["full"]
    port_pass: Literal["full"]
    custom_entry: Literal["full"]
    verification_memo: Literal["full"]
    packing_list: Literal["full"]


class LegacyLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_documents_per_group: int = Field(strict=True, gt=0)
    max_file_bytes: int = Field(strict=True, gt=0)
    max_pages_per_file: int = Field(strict=True, gt=0)
    max_pages_per_submission: int = Field(strict=True, gt=0)


class LegacyGatePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    invoice_pass_enabled: StrictBool
    trust_ramp_enabled: Literal[False]
    review_depth: LegacyReviewDepth
    limits: LegacyLimits

    def canonical(self) -> GatePolicy:
        return GatePolicy(
            invoice_pass_enabled=self.invoice_pass_enabled,
            review_depth="checks" if self.review_depth.commercial_invoice == "gate" else "full",
        )


def parse_gate_policy(config: dict) -> GatePolicy:
    if not isinstance(config, dict):
        raise ValueError("invalid_policy_configuration")
    if isinstance(config.get("review_depth"), dict):
        # StrictBool rejects integers even though Literal[False] would accept zero.
        if config.get("trust_ramp_enabled") is not False:
            raise ValueError("unsupported_legacy_trust_ramp")
        return LegacyGatePolicy.model_validate(config).canonical()
    return GatePolicy.model_validate(config)
