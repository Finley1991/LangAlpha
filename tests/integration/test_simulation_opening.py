import asyncio
from decimal import Decimal

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from psycopg.errors import CheckViolation

from src.server.app.simulation import router
from src.server.database import simulation as db
from src.server.database.user import create_user
from src.server.models.simulation import OpeningRequest
from src.server.services import simulation
from src.server.utils.api import get_current_user_id

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def opening():
    return OpeningRequest.model_validate(
        {
            "opening_date": "2026-10-13",
            "initial_cash": "1000.00",
            "cost_includes_fees": False,
            "holdings": [
                {
                    "code": "000001",
                    "exchange": "SZ",
                    "name": "虚构测试持仓",
                    "quantity": 100,
                    "total_cost": "1234.5678",
                    "purchased_on": "2026-10-12",
                }
            ],
        }
    )


async def test_concurrent_retries_create_one_opening_and_cash_event(
    seed_user, patched_get_db_connection
):
    user_id = seed_user["user_id"]
    results = await asyncio.gather(
        *(simulation.initialize(user_id, opening()) for _ in range(12))
    )
    assert sum(created for _, created in results) == 1
    assert len({snapshot.account_id for snapshot, _ in results}) == 1
    async with patched_get_db_connection() as conn:
        result = await conn.execute(
            "SELECT COUNT(*) AS n, SUM(amount) AS balance FROM simulation_cash_events"
        )
        row = await result.fetchone()
    assert row["n"] == 1 and row["balance"] == Decimal("1000.00")
    assert results[0][0].holdings[0].total_cost == Decimal("1234.5678")
    assert results[0][0].historical_fees is None


async def test_conflicting_request_cannot_change_opening(
    seed_user, patched_get_db_connection
):
    user_id = seed_user["user_id"]
    original, _ = await simulation.initialize(user_id, opening())
    changed = OpeningRequest.model_validate(
        {**opening().model_dump(mode="json"), "initial_cash": "2000"}
    )
    with pytest.raises(db.OpeningConflict):
        await simulation.initialize(user_id, changed)
    assert (await simulation.get_opening(user_id)).account_id == original.account_id
    assert (await simulation.get_opening(user_id)).initial_cash == Decimal("1000.00")


async def test_cash_event_failure_rolls_back_account(
    seed_user, patched_get_db_connection
):
    payload, digest = simulation.canonical_opening(opening())
    payload["initial_cash"] = "-1"
    with pytest.raises(CheckViolation):
        await db.initialize(seed_user["user_id"], payload, digest)
    assert await simulation.get_opening(seed_user["user_id"]) is None


async def test_api_uses_authenticated_owner_and_never_client_user_id(
    seed_user, patched_get_db_connection
):
    user_id = seed_user["user_id"]
    other_id = "simulation-other-test-user"
    await create_user(user_id=other_id, name="Fictitious other user")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        request = opening().model_dump(mode="json")
        assert (
            await client.post(
                "/api/v1/users/me/simulation/opening",
                json={**request, "user_id": other_id},
            )
        ).status_code == 422
        assert (
            await client.post("/api/v1/users/me/simulation/opening", json=request)
        ).status_code == 201
        assert (
            await client.post("/api/v1/users/me/simulation/opening", json=request)
        ).status_code == 200
        assert (
            await client.post(
                "/api/v1/users/me/simulation/opening",
                json={**request, "initial_cash": "2000"},
            )
        ).status_code == 409
        app.dependency_overrides[get_current_user_id] = lambda: other_id
        assert (await client.get("/api/v1/users/me/simulation/opening")).json() == {
            "snapshot": None
        }
        assert (
            await client.post("/api/v1/users/me/simulation/opening", json=request)
        ).status_code == 201
    assert (await simulation.get_opening(user_id)).account_id != (
        await simulation.get_opening(other_id)
    ).account_id


async def test_missing_authentication_cannot_read_or_write(monkeypatch):
    monkeypatch.setattr("src.server.utils.api.HOST_MODE", "platform")
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (
            await client.get("/api/v1/users/me/simulation/opening")
        ).status_code == 401
        assert (
            await client.post(
                "/api/v1/users/me/simulation/opening",
                json=opening().model_dump(mode="json"),
            )
        ).status_code == 401
