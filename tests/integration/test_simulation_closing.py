"""Real PostgreSQL checks for report evidence, concurrency and lock boundaries."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from src.server.services import simulation_closing as closing
from src.server.services import simulation_reports as reports
from src.server.services import simulation_orders as trading
from tests.integration.test_simulation_orders import clock as clock, initialize
from tests.integration.test_simulation_reports import request

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def test_user_id():
    return "simulation-test-closing"


def evidence():
    stamp = "2026-10-13T15:00:00+08:00"
    source = closing.source(
        "daily",
        {"ts_code": "000001.SZ", "trade_date": "20261013"},
        [{"ts_code": "000001.SZ", "trade_date": "20261013", "close": "10.23"}],
        datetime.fromisoformat("2026-10-14T08:00:00+08:00"),
        datetime.fromisoformat(stamp),
    )
    return {
        "sources": [source],
        "prices": {
            "000001.SZ": {
                "close": "10.23",
                "trade_date": "2026-10-13",
                "as_of": stamp,
                "fetched_at": "2026-10-14T08:00:00+08:00",
                "source_ids": [source["source_id"]],
            }
        },
        "issues": {},
    }


async def test_freezes_close_evidence_retry_does_not_refetch_or_change_ledger(
    seed_user, patched_get_db_connection, clock, monkeypatch
):
    user = seed_user["user_id"]
    await initialize(user)
    before = await trading.get_ledger(user)
    provider = AsyncMock(return_value=evidence())
    monkeypatch.setattr(closing, "collect", provider)
    req = request()
    result = await reports.generate(user, req)
    check = result["report"]["checks"][0]
    assert check["closing_price"]["close"] == "10.23"
    assert "不可用于当前成交" in check["fact"]
    assert len(result["report"]["missing_data"]) == 3
    assert result["report"]["status"] == "awaiting_data"
    assert (
        result["report"]["sources"][1]["evidence"]["params"]["trade_date"] == "20261013"
    )
    assert await reports.generate(user, req) == result
    provider.assert_awaited_once()
    assert await trading.get_ledger(user) == before
    clock[0] += timedelta(days=1)
    saved = await reports.get_report(user, UUID(result["report"]["report_id"]))
    assert saved["is_stale"] and saved["report"] == result["report"]


async def test_parallel_same_request_persists_one_report(
    seed_user, patched_get_db_connection, clock, monkeypatch
):
    user = seed_user["user_id"]
    await initialize(user)
    monkeypatch.setattr(closing, "collect", AsyncMock(return_value=evidence()))
    req = request()
    results = await asyncio.gather(*(reports.generate(user, req) for _ in range(12)))
    assert len({r["report"]["report_id"] for r in results}) == 1
    assert len((await reports.list_reports(user, None, 20))["items"]) == 1


async def test_network_releases_account_lock_and_version_race_rejects_save(
    seed_user, patched_get_db_connection, clock, monkeypatch
):
    user = seed_user["user_id"]
    await initialize(user)
    started, release = asyncio.Event(), asyncio.Event()

    async def slow(*args):
        started.set()
        await release.wait()
        return evidence()

    monkeypatch.setattr(closing, "collect", slow)
    task = asyncio.create_task(reports.generate(user, request()))
    await asyncio.wait_for(started.wait(), 3)
    try:
        async with asyncio.timeout(3):
            async with trading.db.transaction(user) as tx:
                await tx.bump()
    finally:
        release.set()
    with pytest.raises(trading.SimulationError) as exc:
        await task
    assert exc.value.code == "STALE_LEDGER"
    assert not (await reports.list_reports(user, None, 20))["items"]


async def test_cross_midnight_rejects_and_partial_failure_is_explicit(
    seed_user, patched_get_db_connection, clock, monkeypatch
):
    user = seed_user["user_id"]
    await initialize(user)

    async def cross_day(*args):
        clock[0] += timedelta(days=1)
        return evidence()

    monkeypatch.setattr(closing, "collect", cross_day)
    with pytest.raises(trading.SimulationError) as exc:
        await reports.generate(user, request())
    assert exc.value.code == "STALE_LEDGER"
    monkeypatch.setattr(
        closing,
        "collect",
        AsyncMock(
            return_value={
                "sources": [],
                "prices": {},
                "issues": {"000001.SZ": "收盘证据不可用：permission_denied"},
            }
        ),
    )
    result = await reports.generate(user, request())
    check = result["report"]["checks"][0]
    assert check["closing_price"] is None
    assert "permission_denied" in check["missing_data"][0]
    assert "permission_denied" in result["report"]["missing_data"][0]


async def test_network_delay_keeps_ledger_source_at_frozen_cutoff(
    seed_user, patched_get_db_connection, clock, monkeypatch
):
    user = seed_user["user_id"]
    await initialize(user)

    async def delay(*args):
        clock[0] += timedelta(seconds=20)
        return evidence()

    monkeypatch.setattr(closing, "collect", delay)
    report = (await reports.generate(user, request()))["report"]
    assert (
        report["sources"][0]["as_of"]
        == report["snapshot"]["as_of"]
        == report["cutoff_at"]
    )
    assert report["generated_at"] > report["cutoff_at"]


async def test_old_report_http_body_retains_original_hash(
    seed_user, patched_get_db_connection, clock
):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from psycopg.types.json import Jsonb
    from src.server.app.simulation import router
    from src.server.utils.api import get_current_user_id

    user = seed_user["user_id"]
    await initialize(user)
    saved = await reports.generate(user, request())
    old = saved["report"]
    for source in old["sources"]:
        source.pop("fetched_at", None)
        source.pop("evidence", None)
    for check in old["checks"]:
        check.pop("closing_price", None)
    old_hash = reports.digest(old)
    async with patched_get_db_connection() as conn:
        await conn.execute(
            "UPDATE simulation_reports SET payload=%s, content_hash=%s WHERE report_id=%s",
            (Jsonb(old), old_hash, old["report_id"]),
        )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: user
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/users/me/simulation/reports/" + old["report_id"]
        )
        assert response.status_code == 200
        body = response.json()
        assert body["report"] == old
        assert reports.digest(body["report"]) == body["content_hash"] == old_hash


async def test_new_report_http_json_hash_matches_frozen_content(
    seed_user, patched_get_db_connection, clock, monkeypatch
):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from src.server.app.simulation import router
    from src.server.utils.api import get_current_user_id

    user = seed_user["user_id"]
    await initialize(user)
    monkeypatch.setattr(closing, "collect", AsyncMock(return_value=evidence()))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: user
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/users/me/simulation/reports",
            json=request().model_dump(mode="json"),
        )
        assert response.status_code == 200
        body = response.json()
        assert reports.digest(body["report"]) == body["content_hash"]
        assert body["report"]["sources"][0]["as_of"] == body["report"]["cutoff_at"]
