from datetime import datetime
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
)

Price = Annotated[Decimal, Field(gt=0, le=1_000_000, decimal_places=2)]


class SuggestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_request_id: UUID
    account_version: StrictInt = Field(gt=0)
    code: str = Field(pattern=r"^[0-9]{6}$")
    exchange: Literal["SH", "SZ"]
    name: str = Field(min_length=1, max_length=100)
    side: Literal["buy", "sell"]
    quantity: StrictInt = Field(gt=0, le=1_000_000)
    limit_price: Price
    reason: str = Field(min_length=1, max_length=2000)
    counterargument: str = Field(min_length=1, max_length=2000)
    expires_at: datetime

    @field_validator("limit_price", mode="before")
    @classmethod
    def price_string(cls, value):
        if not isinstance(value, str):
            raise ValueError("价格必须是十进制字符串")
        return value

    @field_validator("expires_at")
    @classmethod
    def aware_expiry(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("有效期必须包含时区")
        return value


class ConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suggestion_version: StrictInt = Field(gt=0)
    account_version: StrictInt = Field(gt=0)
    acknowledge_simulation: StrictBool


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    suggestion_version: StrictInt = Field(gt=0)
    account_version: StrictInt = Field(gt=0)
    quantity: StrictInt = Field(gt=0, le=1_000_000)
    limit_price: Price

    @field_validator("limit_price", mode="before")
    @classmethod
    def price_string(cls, value):
        if not isinstance(value, str):
            raise ValueError("价格必须是十进制字符串")
        return value


class TrustedQuote(BaseModel):
    """Internal adapter output; never accepted by a user-facing route."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str = Field(pattern=r"^[0-9]{6}$")
    exchange: Literal["SH", "SZ"]
    price: Price
    as_of: datetime
    fetched_at: datetime
    source: str = Field(min_length=1, max_length=200)
    source_kind: Literal["provider", "test"]
    evidence_id: str = Field(min_length=1, max_length=200)
    instrument_verified: StrictBool = False
    calendar_verified: StrictBool = False
    trading_day: StrictBool = False
    suspended: StrictBool = True
    rule_version: Literal["sim-mainland-main-v1"]
    lower_limit: Price | None = None
    upper_limit: Price | None = None

    @field_validator("as_of", "fetched_at")
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("行情时间必须包含时区")
        return value


class FeeAssumptions(BaseModel):
    version: str
    commission_rate: str
    minimum_commission: str
    sell_tax_rate: str
    transfer_rate: str
    slippage_bps: str
    description: str


class FeeBreakdown(BaseModel):
    commission: str
    transfer: str
    sell_tax: str
    total: str
    version: str


class SuggestionData(SuggestionRequest):
    suggestion_version: StrictInt = Field(gt=0)
    replaces_suggestion_id: UUID | None = None
    fees: FeeAssumptions
    rule_version: Literal["sim-mainland-main-v1"]
    origin: Literal["manual_research"]
    reference_status: Literal["unverified"]
    estimated_cash_delta: str
    estimated_cash_after: str
    estimated_fees: FeeBreakdown


class SuggestionRecord(SuggestionData):
    suggestion_id: UUID
    status: Literal["pending", "confirmed", "cancelled", "expired"]
    created_at: datetime


class OrderRecord(SuggestionData):
    order_id: UUID
    suggestion_id: UUID
    status: Literal["pending", "filled", "cancelled", "expired"]
    reserved_cash: str
    confirmed_at: datetime
    confirmed_by: str
    confirmed_account_version: int
    execution_status: Literal[
        "awaiting_verified_quote", "filled", "cancelled", "expired"
    ]


class FillRecord(BaseModel):
    fill_id: UUID
    order_id: UUID
    suggestion_id: UUID
    code: str
    exchange: Literal["SH", "SZ"]
    name: str
    side: Literal["buy", "sell"]
    quantity: int
    price: str
    gross: str
    fees: FeeBreakdown
    fee_assumptions: FeeAssumptions
    cash_delta: str
    cash_after: str
    removed_cost: str
    realized_pnl: str
    cost_method: Literal["moving_weighted_average"]
    account_version: int
    confirmed_at: datetime
    created_at: datetime
    quote: TrustedQuote
    rule_version: Literal["sim-mainland-main-v1"]


class PositionLot(BaseModel):
    quantity: int
    acquired_on: str
    date_basis: Literal["known_batch", "latest_known_conservative", "simulation_fill"]


class LedgerPosition(BaseModel):
    code: str
    exchange: Literal["SH", "SZ", "BJ"]
    name: str
    quantity: int
    available_quantity: int
    total_cost: str
    lots: list[PositionLot]


class Ledger(BaseModel):
    account_id: UUID
    version: int
    mode: Literal["simulation"]
    currency: Literal["CNY"]
    as_of: datetime
    cash: str
    available_cash: str
    reserved_cash: str
    realized_pnl: str
    positions: list[LedgerPosition]
    suggestions: list[SuggestionRecord]
    orders: list[OrderRecord]
    fills: list[FillRecord]
    fees: FeeAssumptions
    rule_version: Literal["sim-mainland-main-v1"]
    valuation_status: Literal["unverified"]
    market_data_status: Literal["unverified"]
    history_limit: int
    sellable_basis: str


class LedgerResponse(BaseModel):
    ledger: Ledger | None


class LedgerValuationQuote(BaseModel):
    price: str
    currency: Literal["CNY"]
    as_of: datetime
    fetched_at: datetime
    provider_updated_at: datetime | None = None
    freshness: Literal["fresh", "stale", "future"]
    freshness_as_of: datetime
    source_ids: list[str]


class LedgerPositionPnl(BaseModel):
    status: Literal["calculated", "pending_verification"]
    market_value: str | None = None
    unrealized_pnl: str | None = None
    currency: Literal["CNY"] = "CNY"
    as_of: datetime | None = None
    reason: str | None = None
    source_ids: list[str] = Field(default_factory=list)


class LedgerPositionValuation(BaseModel):
    code: str
    exchange: Literal["SH", "SZ", "BJ"]
    name: str
    quantity: int
    total_cost: str
    current_quote: LedgerValuationQuote | None = None
    realtime_pnl: LedgerPositionPnl


class LedgerValuationResponse(BaseModel):
    account_version: int
    fetched_at: datetime
    positions: list[LedgerPositionValuation]


class ActionResponse(BaseModel):
    result: SuggestionRecord | OrderRecord | FillRecord


class EventsResponse(BaseModel):
    events: list[dict]
    next_cursor: int | None
