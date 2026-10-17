"""The opening path does not call the portfolio accumulation or brokerage APIs."""

import logging
from collections.abc import Awaitable
from uuid import UUID
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from src.server.database.simulation import OpeningConflict
from src.server.models.simulation import OpeningRequest, OpeningResponse
from src.server.models.simulation_orders import (
    ActionResponse,
    ConfirmationRequest,
    EventsResponse,
    LedgerResponse,
    RevisionRequest,
    SuggestionRequest,
)
from src.server.models.simulation_reports import ReportRequest, ReportDetail, ReportPage
from src.server.services import simulation, simulation_reports as reports
from src.server.services import simulation_orders as trading
from src.server.utils.api import CurrentUserId, handle_api_exceptions

logger = logging.getLogger(__name__)


def private_response(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(
    prefix="/api/v1/users/me/simulation",
    tags=["Simulation"],
    dependencies=[Depends(private_response)],
)


@router.get("/opening", response_model=OpeningResponse)
@handle_api_exceptions("read simulation opening", logger)
async def get_opening(user_id: CurrentUserId):
    return OpeningResponse(snapshot=await simulation.get_opening(user_id))


@router.post("/opening", response_model=OpeningResponse, status_code=201)
@handle_api_exceptions("initialize simulation opening", logger)
async def initialize(
    request: OpeningRequest, user_id: CurrentUserId, response: Response
):
    try:
        snapshot, created = await simulation.initialize(user_id, request)
    except OpeningConflict as exc:
        raise HTTPException(
            status_code=409, detail={"code": "OPENING_CONFLICT", "message": str(exc)}
        ) from exc
    if not created:
        response.status_code = 200
    return OpeningResponse(snapshot=snapshot)


async def financial_action(action: Awaitable):
    try:
        return await action
    except trading.SimulationError as exc:
        raise HTTPException(
            status_code=exc.status, detail={"code": exc.code, "message": str(exc)}
        ) from exc


@router.get("/ledger", response_model=LedgerResponse)
@handle_api_exceptions("read simulation ledger", logger)
async def ledger(user_id: CurrentUserId):
    return LedgerResponse(ledger=await financial_action(trading.get_ledger(user_id)))


@router.post("/suggestions", response_model=ActionResponse)
@handle_api_exceptions("save simulation suggestion", logger)
async def suggest(request: SuggestionRequest, user_id: CurrentUserId):
    return ActionResponse(
        result=await financial_action(trading.suggest(user_id, request))
    )


@router.post("/suggestions/{suggestion_id}/revise", response_model=ActionResponse)
@handle_api_exceptions("revise simulation suggestion", logger)
async def revise(suggestion_id: UUID, request: RevisionRequest, user_id: CurrentUserId):
    return ActionResponse(
        result=await financial_action(trading.revise(user_id, suggestion_id, request))
    )


@router.post("/suggestions/{suggestion_id}/confirm", response_model=ActionResponse)
@handle_api_exceptions("confirm simulation suggestion", logger)
async def confirm(
    suggestion_id: UUID, request: ConfirmationRequest, user_id: CurrentUserId
):
    return ActionResponse(
        result=await financial_action(trading.confirm(user_id, suggestion_id, request))
    )


@router.post("/suggestions/{suggestion_id}/cancel", response_model=ActionResponse)
@handle_api_exceptions("cancel simulation suggestion", logger)
async def cancel_suggestion(suggestion_id: UUID, user_id: CurrentUserId):
    return ActionResponse(
        result=await financial_action(trading.cancel_suggestion(user_id, suggestion_id))
    )


@router.post("/orders/{order_id}/cancel", response_model=ActionResponse)
@handle_api_exceptions("cancel simulation order", logger)
async def cancel_order(order_id: UUID, user_id: CurrentUserId):
    return ActionResponse(
        result=await financial_action(trading.cancel_order(user_id, order_id))
    )


@router.get("/events", response_model=EventsResponse)
@handle_api_exceptions("read simulation events", logger)
async def events(
    user_id: CurrentUserId,
    before: int | None = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    return EventsResponse(
        **await financial_action(trading.get_events(user_id, before, limit))
    )


@router.post("/reports", response_model=ReportDetail, response_model_exclude_unset=True)
@handle_api_exceptions("generate simulation report", logger)
async def create_report(request: ReportRequest, user_id: CurrentUserId):
    return await financial_action(reports.generate(user_id, request))


@router.get("/reports", response_model=ReportPage, response_model_exclude_unset=True)
@handle_api_exceptions("list simulation reports", logger)
async def report_list(
    user_id: CurrentUserId,
    before: int | None = Query(None, ge=1),
    limit: int = Query(20, ge=1, le=50),
):
    return await financial_action(reports.list_reports(user_id, before, limit))


@router.get(
    "/reports/{report_id}",
    response_model=ReportDetail,
    response_model_exclude_unset=True,
)
@handle_api_exceptions("read simulation report", logger)
async def report_detail(report_id: UUID, user_id: CurrentUserId):
    return await financial_action(reports.get_report(user_id, report_id))


@router.get("/{kind}/{record_id}", response_model=ActionResponse)
@handle_api_exceptions("read simulation record", logger)
async def record(
    kind: Literal["suggestions", "orders", "fills"],
    record_id: UUID,
    user_id: CurrentUserId,
):
    return ActionResponse(
        result=await financial_action(trading.get_record(user_id, kind, record_id))
    )
