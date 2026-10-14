"""The opening path does not call the portfolio accumulation or brokerage APIs."""

import logging

from fastapi import APIRouter, HTTPException, Response

from src.server.database.simulation import OpeningConflict
from src.server.models.simulation import OpeningRequest, OpeningResponse
from src.server.services import simulation
from src.server.utils.api import CurrentUserId, handle_api_exceptions

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/users/me/simulation", tags=["Simulation"])


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
