"""Versioned invoice gate configuration contract."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class GatePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    invoice_pass_enabled: StrictBool = False
    review_depth: Literal["full", "checks"] = "full"
    arithmetic_tolerance: Decimal = Field(default=Decimal("0.01"), ge=0, le=1, allow_inf_nan=False)
