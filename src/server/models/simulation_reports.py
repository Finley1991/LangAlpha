"""Reports freeze ledger and market evidence; quotes remain report-only."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.server.models.simulation_orders import LedgerPosition


class ReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: UUID
    account_version: int = Field(strict=True, ge=1)


class ReportSnapshot(BaseModel):
    account_id: UUID
    version: int
    as_of: datetime
    cash: str
    available_cash: str
    reserved_cash: str
    realized_pnl: str
    positions: list[LedgerPosition]
    sellable_basis: str


class ReportSource(BaseModel):
    source_id: str
    kind: Literal[
        "ledger_snapshot",
        "closing_calendar",
        "closing_price",
        "live_quote",
        "announcement",
        "analyst_research",
        "stock_news",
        "community_search",
    ]
    title: str
    reference: str
    as_of: datetime
    snapshot_version: int | None = None
    fetched_at: datetime | None = None
    evidence: dict | None = None


class ClosingPrice(BaseModel):
    trade_date: date
    close: str
    as_of: datetime
    fetched_at: datetime
    source_ids: list[str]


class CurrentQuote(BaseModel):
    price: str
    currency: Literal["CNY"]
    as_of: datetime
    fetched_at: datetime
    provider_updated_at: datetime | None = None
    freshness: Literal["fresh", "stale", "future"]
    freshness_as_of: datetime
    source_ids: list[str]


class RealtimePnl(BaseModel):
    status: Literal["calculated", "pending_verification"]
    market_value: str | None = None
    unrealized_pnl: str | None = None
    currency: Literal["CNY"] = "CNY"
    as_of: datetime | None = None
    reason: str | None = None
    source_ids: list[str] = Field(default_factory=list)


class AnnouncementEvidence(BaseModel):
    id: str | None = None
    paper_code: str
    ann_date: str | None = None
    end_date: str | None = None
    ann_type: str | None = None
    event_category: str | None = None
    title: str | None = None
    url: str | None = None
    pdf_path: str | None = None


class AnalystReportEvidence(BaseModel):
    id: str | None = None
    title: str | None = None
    institution: str | None = None
    authors: str | None = None
    published_date: str | None = None
    report_type: str | None = None
    rating: str | None = None
    target_price: str | None = None
    symbol: str
    url: str | None = None


class StockNewsEvidence(BaseModel):
    id: str
    title: str
    summary: str | None = None
    published_at: datetime | None = None
    publisher: str | None = None
    url: str
    # Legacy categories remain readable for frozen reports; current collectors emit news only.
    category: Literal["news", "xueqiu", "tieba"]
    source_ids: list[str] = Field(default_factory=list)


class PositionCheck(BaseModel):
    code: str
    exchange: Literal["SH", "SZ", "BJ"]
    name: str
    fact: str
    source_ids: list[str]
    judgment: str
    counterargument: str
    trigger: str
    missing_data: list[str]
    closing_price: ClosingPrice | None = None
    current_quote: CurrentQuote | None = None
    realtime_pnl: RealtimePnl | None = None
    announcements: list[AnnouncementEvidence] | None = None
    analyst_reports: list[AnalystReportEvidence] | None = None
    news: list[StockNewsEvidence] | None = None


class ReportContent(BaseModel):
    schema_version: Literal["simulation-report-v1"]
    report_id: UUID
    report_date: date
    generated_at: datetime
    cutoff_at: datetime
    expires_at: datetime
    status: Literal["awaiting_data"]
    title: str
    summary: str
    validity: str
    opening_in_future: bool
    snapshot: ReportSnapshot
    sources: list[ReportSource]
    checks: list[PositionCheck]
    missing_data: list[str]
    scope: str


class ReportDetail(BaseModel):
    report: ReportContent
    sequence: int
    content_hash: str
    current_version: int
    is_stale: bool
    expired: bool


class ReportSummary(BaseModel):
    report_id: UUID
    sequence: int
    report_date: date
    generated_at: datetime
    expires_at: datetime
    snapshot_version: int
    title: str
    status: Literal["awaiting_data"]
    is_stale: bool
    expired: bool


class ReportPage(BaseModel):
    items: list[ReportSummary]
    next_cursor: int | None
