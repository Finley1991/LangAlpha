"""Opening balances are immutable facts, separate from subsequent trades."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

Money = Annotated[Decimal, Field(ge=0, max_digits=24, decimal_places=8)]


class OpeningHolding(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(pattern=r"^[0-9]{6}$")
    exchange: Literal["SH", "SZ", "BJ"]
    name: str = Field(min_length=1, max_length=100)
    quantity: StrictInt = Field(gt=0, le=1_000_000_000)
    total_cost: Money
    purchased_on: date
    acquisition_dates: list[date] = Field(default_factory=list, max_length=100)
    batch_details_known: StrictBool = True

    @field_validator("total_cost", mode="before")
    @classmethod
    def decimal_string(cls, value):
        if not isinstance(value, str):
            raise ValueError("金额必须是十进制字符串")
        return value

    @model_validator(mode="after")
    def validate_acquisitions(self):
        if self.acquisition_dates and self.purchased_on != min(self.acquisition_dates):
            raise ValueError("汇总持仓须保留最早已知买入日期")
        if not self.batch_details_known and not self.acquisition_dates:
            raise ValueError("批次明细未知时须保留已报告的买入日期")
        return self


class OpeningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opening_date: date
    initial_cash: Annotated[Decimal, Field(ge=0, max_digits=24, decimal_places=2)]
    cost_includes_fees: StrictBool
    holdings: list[OpeningHolding] = Field(max_length=100)

    @field_validator("initial_cash", mode="before")
    @classmethod
    def decimal_string(cls, value):
        if not isinstance(value, str):
            raise ValueError("现金必须是十进制字符串")
        return value

    @model_validator(mode="after")
    def validate_dates(self):
        if any(
            any(
                day > self.opening_date
                for day in [row.purchased_on, *row.acquisition_dates]
            )
            for row in self.holdings
        ):
            raise ValueError("买入日期不能晚于账本起算日期")
        return self


class OpeningSnapshot(OpeningRequest):
    account_id: UUID
    version: Literal[1] = 1
    currency: Literal["CNY"] = "CNY"
    mode: Literal["simulation"] = "simulation"
    created_at: datetime
    historical_fees: None = None
    valuation_status: Literal["unverified"] = "unverified"


class OpeningResponse(BaseModel):
    snapshot: OpeningSnapshot | None
