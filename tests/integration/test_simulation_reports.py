import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.server.app.simulation import router
from src.server.database import simulation_reports as db
from src.server.database.user import create_user
from src.server.models.simulation_reports import ReportRequest
from src.server.services import (
    simulation_orders as trading,
    simulation_reports as service,
)
from src.server.utils.api import get_current_user_id
from tests.integration.test_simulation_orders import (
    clock as clock,
    initialize,
    request as suggestion_request,
    confirmation,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def test_user_id():
    return "simulation-test-reports"


def request(version=1, **changes):
    return ReportRequest.model_validate(
        {"client_request_id": str(uuid4()), "account_version": version, **changes}
    )


async def test_parallel_retries_freeze_exact_evidence_without_any_trade(
    seed_user, patched_get_db_connection, clock
):
    user = seed_user["user_id"]
    await initialize(user)
    before = await trading.get_ledger(user)
    results = await asyncio.gather(
        *[service.generate(user, req) for req in [request()] * 12]
    )
    assert len({r["report"]["report_id"] for r in results}) == 1
    data = results[0]
    assert not data["is_stale"] and len(data["content_hash"]) == 64
    report = data["report"]
    assert report["status"] == "awaiting_data" and len(report["missing_data"]) == 4
    assert report["snapshot"]["positions"][0]["total_cost"] == "1234.56780001"
    assert report["checks"][0]["code"] == "000001"
    assert report["checks"][0]["source_ids"] == [report["sources"][0]["source_id"]]
    assert report["report_id"] in report["sources"][0]["reference"]
    assert await trading.get_ledger(user) == before
    assert len((await service.list_reports(user, None, 20))["items"]) == 1


async def test_changed_ledger_marks_old_report_stale_without_overwriting_it(
    seed_user, patched_get_db_connection, clock
):
    user = seed_user["user_id"]
    await initialize(user)
    req = request()
    old = await service.generate(user, req)
    card = await trading.suggest(user, suggestion_request())
    await trading.confirm(user, UUID(card["suggestion_id"]), confirmation())
    retried = await service.generate(user, req)
    assert (
        retried["report"] == old["report"]
        and retried["content_hash"] == old["content_hash"]
    )
    assert retried["is_stale"] and retried["current_version"] == 2
    fresh = await service.generate(user, request(2))
    assert fresh["report"]["snapshot"]["reserved_cash"] == "105.00"
    assert old["report"]["snapshot"]["reserved_cash"] == "0.00"
    with pytest.raises(trading.SimulationError, match="刷新"):
        await service.generate(user, request())
    with pytest.raises(trading.SimulationError, match="不同账本版本"):
        await service.generate(
            user, request(2, client_request_id=req.client_request_id)
        )


async def test_24_hour_expiry_and_local_date_are_explicit(
    seed_user, patched_get_db_connection, clock
):
    user = seed_user["user_id"]
    await initialize(user)
    clock[0] = clock[0].replace(hour=16, minute=1)
    old = await service.generate(user, request())
    assert old["report"]["report_date"] == "2026-10-15"
    clock[0] += timedelta(hours=24)
    read = await service.get_report(user, UUID(old["report"]["report_id"]))
    assert read["expired"] and read["is_stale"]
    assert read["report"] == old["report"]


async def test_next_local_date_invalidates_sellable_balance_even_without_version_change(
    seed_user, patched_get_db_connection, clock
):
    user = seed_user["user_id"]
    await initialize(user)
    clock[0] = clock[0].replace(hour=15, minute=59)
    old = await service.generate(user, request())
    clock[0] += timedelta(minutes=2)
    read = await service.get_report(user, UUID(old["report"]["report_id"]))
    assert read["current_version"] == old["current_version"]
    assert not read["expired"] and read["is_stale"]
    assert read["report"] == old["report"]


async def test_future_opening_and_empty_cash_report_do_not_invent_market_facts(
    seed_user, patched_get_db_connection, clock
):
    user = seed_user["user_id"]
    await initialize(user, opening_date="2026-10-15", holdings=[])
    report = (await service.generate(user, request()))["report"]
    assert report["opening_in_future"] and not report["checks"]
    assert "预设快照" in report["missing_data"][-1]
    assert report["snapshot"]["cash"] == "1000.00"
    assert not any(
        k in report["snapshot"]
        for k in ["market_value", "unrealized_pnl", "total_assets"]
    )


async def test_owner_isolation_and_stable_history_cursor(
    seed_user, patched_get_db_connection, clock
):
    user = seed_user["user_id"]
    await initialize(user)
    ids = [
        (await service.generate(user, request()))["report"]["report_id"]
        for _ in range(3)
    ]
    first = await service.list_reports(user, None, 2)
    assert [str(i["report_id"]) for i in first["items"]] == ids[::-1][:2]
    second = await service.list_reports(user, first["next_cursor"], 2)
    assert [str(i["report_id"]) for i in second["items"]] == ids[:1]
    assert second["next_cursor"] is None
    await create_user("simulation-test-report-other", "other", "other@example.invalid")
    await initialize("simulation-test-report-other")
    assert not (await service.list_reports("simulation-test-report-other", None, 20))[
        "items"
    ]
    with pytest.raises(trading.SimulationError) as error:
        await service.get_report("simulation-test-report-other", UUID(ids[0]))
    assert error.value.status == 404


async def test_failed_report_save_rolls_back_without_financial_events(
    seed_user, patched_get_db_connection, clock, monkeypatch
):
    user = seed_user["user_id"]
    await initialize(user)
    original = db.insert

    async def failing(*args):
        await original(*args)
        raise RuntimeError("fictional persistence failure")

    monkeypatch.setattr(db, "insert", failing)
    with pytest.raises(RuntimeError):
        await service.generate(user, request())
    assert not (await service.list_reports(user, None, 20))["items"]
    ledger = await trading.get_ledger(user)
    assert ledger["cash"] == "1000.00" and ledger["version"] == 1
    assert not ledger["suggestions"] and not ledger["orders"] and not ledger["fills"]


async def test_integrity_mismatch_stops_display_instead_of_repairing_data(
    seed_user, patched_get_db_connection, clock
):
    user = seed_user["user_id"]
    await initialize(user)
    result = await service.generate(user, request())
    async with patched_get_db_connection() as conn:
        await conn.execute(
            "UPDATE simulation_reports SET payload=jsonb_set(payload,'{summary}','\"fictional corruption\"') WHERE report_id=%s",
            (result["report"]["report_id"],),
        )
    with pytest.raises(trading.SimulationError) as error:
        await service.get_report(user, UUID(result["report"]["report_id"]))
    assert error.value.code == "REPORT_INTEGRITY"
    with pytest.raises(trading.SimulationError):
        await service.list_reports(user, None, 20)


async def test_http_contract_strict_inputs_no_store_and_auth(
    seed_user, patched_get_db_connection, clock, monkeypatch
):
    user = seed_user["user_id"]
    await initialize(user)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: user
    b = "/api/v1/users/me/simulation/reports"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(b, json=request().model_dump(mode="json"))
        assert (
            result.status_code == 200 and result.headers["cache-control"] == "no-store"
        )
        report_id = result.json()["report"]["report_id"]
        assert (await client.get(b + "/" + report_id)).status_code == 200
        for changes in [
            {"user_id": "other"},
            {"quotes": []},
            {"account_version": True},
            {"account_version": "1"},
            {"report_date": "2020-01-01"},
        ]:
            assert (
                await client.post(
                    b, json={**request().model_dump(mode="json"), **changes}
                )
            ).status_code == 422
        for query in ["?limit=0", "?limit=51", "?before=0"]:
            assert (await client.get(b + query)).status_code == 422
        assert (await client.get(b + "/" + str(uuid4()))).status_code == 404
        app.dependency_overrides.clear()
        monkeypatch.setattr("src.server.utils.api.HOST_MODE", "platform")
        assert (await client.get(b)).status_code == 401
        assert (
            await client.post(b, json=request().model_dump(mode="json"))
        ).status_code == 401
        assert (await client.get(b + "/" + report_id)).status_code == 401
