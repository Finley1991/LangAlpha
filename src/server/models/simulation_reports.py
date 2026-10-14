"""Reports describe frozen ledger evidence; no quote or trading input is accepted."""

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
    kind: Literal["ledger_snapshot"]
    title: str
    reference: str
    as_of: datetime
    snapshot_version: int


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
