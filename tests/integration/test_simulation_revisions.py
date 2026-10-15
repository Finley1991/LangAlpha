import asyncio
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.server.app.simulation import router
from src.server.database import simulation_orders as db
from src.server.database.user import create_user
from src.server.models.simulation_orders import RevisionRequest
from src.server.services import simulation_orders as service
from src.server.utils.api import get_current_user_id
from tests.integration.test_simulation_orders import (
    confirmation,
    initialize,
    request,
)

from tests.integration.test_simulation_orders import clock as clock

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def test_user_id():
    return "simulation-test-revisions"


def revision(**changes):
    return RevisionRequest.model_validate(
        dict(
            client_request_id=str(uuid4()),
            suggestion_version=1,
            account_version=1,
            quantity=200,
            limit_price="1.50",
            **changes,
        )
    )


async def test_revision_is_atomic_idempotent_and_requires_new_confirmation(
    seed_user, patched_get_db_connection, clock
):
    user = seed_user["user_id"]
    await initialize(user)
    original = await service.suggest(user, request())
    original_id = UUID(original["suggestion_id"])
    before = await service.get_ledger(user)
    change = revision()
    results = await asyncio.gather(
        *(service.revise(user, original_id, change) for _ in range(12))
    )
    child = results[0]
    assert len({r["suggestion_id"] for r in results}) == 1
    assert child["quantity"] == 200 and child["suggestion_version"] == 2
    assert child["replaces_suggestion_id"] == str(original_id)
    assert child["estimated_cash_delta"] == "-305.00"
    after = await service.get_ledger(user)
    for key in (
        "version",
        "cash",
        "available_cash",
        "reserved_cash",
        "positions",
        "orders",
        "fills",
    ):
        assert before[key] == after[key]
    old = await service.get_record(user, "suggestions", original_id)
    assert old == {**original, "status": "cancelled"}
    with pytest.raises(service.SimulationError, match="已取消或到期"):
        await service.confirm(user, original_id, confirmation())
    with pytest.raises(service.SimulationError, match="当前版本"):
        await service.confirm(user, UUID(child["suggestion_id"]), confirmation())
    order = await service.confirm(
        user, UUID(child["suggestion_id"]), confirmation(suggestion_version=2)
    )
    assert order["quantity"] == 200 and order["reserved_cash"] == "305.00"
    assert (await service.revise(user, original_id, change))["status"] == "confirmed"
    with pytest.raises(service.SimulationError, match="同一请求"):
        await service.revise(
            user, original_id, change.model_copy(update={"quantity": 300})
        )


async def test_competing_edits_and_old_confirmation_cannot_both_win(
    seed_user, patched_get_db_connection, clock
):
    user = seed_user["user_id"]
    await initialize(user)
    original = await service.suggest(user, request())
    sid = UUID(original["suggestion_id"])
    results = await asyncio.gather(
        service.revise(user, sid, revision()),
        service.confirm(user, sid, confirmation()),
        return_exceptions=True,
    )
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert sum(isinstance(r, service.SimulationError) for r in results) == 1
    other = await service.suggest(
        user, request((await service.get_ledger(user))["version"])
    )
    v = other["account_version"]
    a = revision().model_copy(update={"account_version": v})
    b = revision().model_copy(update={"account_version": v, "quantity": 300})
    results = await asyncio.gather(
        service.revise(user, UUID(other["suggestion_id"]), a),
        service.revise(user, UUID(other["suggestion_id"]), b),
        return_exceptions=True,
    )
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert sum(isinstance(r, service.SimulationError) for r in results) == 1


async def test_revision_rolls_back_child_and_parent_on_audit_failure(
    seed_user, patched_get_db_connection, clock, monkeypatch
):
    user = seed_user["user_id"]
    await initialize(user)
    original = await service.suggest(user, request())
    before = await service.get_events(user)

    async def fail(*args, **kwargs):
        raise RuntimeError("synthetic audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(db.LedgerTransaction, "event", fail)
        with pytest.raises(RuntimeError, match="synthetic audit"):
            await service.revise(user, UUID(original["suggestion_id"]), revision())
    after = await service.get_ledger(user)
    assert after["suggestions"] == [original]
    assert await service.get_events(user) == before


async def test_revision_rejects_closed_stale_and_invalid_lots(
    seed_user, patched_get_db_connection, clock
):
    user = seed_user["user_id"]
    await initialize(user)
    original = await service.suggest(user, request())
    sid = UUID(original["suggestion_id"])
    for update, code in [
        ({"account_version": 2}, "STALE_LEDGER"),
        ({"suggestion_version": 2}, "STALE_SUGGESTION"),
        ({"quantity": 101}, "INVALID_LOT"),
    ]:
        with pytest.raises(service.SimulationError) as exc:
            await service.revise(user, sid, revision().model_copy(update=update))
        assert exc.value.code == code
    await service.cancel_suggestion(user, sid)
    with pytest.raises(service.SimulationError) as exc:
        await service.revise(user, sid, revision())
    assert exc.value.code == "SUGGESTION_CLOSED"
    assert len((await service.get_ledger(user))["suggestions"]) == 1


async def test_revised_sell_still_validates_sellable_on_confirmation(
    seed_user, patched_get_db_connection, clock
):
    user = seed_user["user_id"]
    await initialize(user)
    original = await service.suggest(
        user, request(code="000001", exchange="SZ", side="sell")
    )
    change = revision().model_copy(update={"quantity": 300})
    child = await service.revise(user, UUID(original["suggestion_id"]), change)
    with pytest.raises(service.SimulationError) as exc:
        await service.confirm(
            user, UUID(child["suggestion_id"]), confirmation(suggestion_version=2)
        )
    assert exc.value.code == "INSUFFICIENT_SELLABLE"
    assert not (await service.get_ledger(user))["orders"]


async def test_revision_http_contract_and_account_isolation(
    seed_user, patched_get_db_connection, clock
):
    user = seed_user["user_id"]
    await initialize(user)
    original = await service.suggest(user, request())
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: user
    path = f"/api/v1/users/me/simulation/suggestions/{original['suggestion_id']}/revise"
    body = revision().model_dump(mode="json")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        for changes in (
            {"quantity": True},
            {"quantity": "200"},
            {"limit_price": 1.5},
            {"side": "sell"},
            {"acknowledge_simulation": True},
        ):
            assert (await c.post(path, json={**body, **changes})).status_code == 422
        app.dependency_overrides[get_current_user_id] = lambda: (
            "simulation-test-uninitialized"
        )
        assert (await c.post(path, json=body)).status_code == 404
        await create_user(
            "simulation-test-revision-other", "other", "other@example.invalid"
        )
        await initialize("simulation-test-revision-other")
        app.dependency_overrides[get_current_user_id] = lambda: (
            "simulation-test-revision-other"
        )
        assert (await c.post(path, json=body)).status_code == 404
        app.dependency_overrides[get_current_user_id] = lambda: user
        r = await c.post(path, json=body)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        assert r.json()["result"]["suggestion_version"] == 2
