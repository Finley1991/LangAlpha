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


async def test_sina_metadata_is_frozen_and_retry_does_not_query_provider_again(
    seed_user, patched_get_db_connection, clock, monkeypatch
):
    user = seed_user["user_id"]
    await initialize(user)
    before = await trading.get_ledger(user)
    calls = 0

    async def research_snapshot(positions, cutoff):
        nonlocal calls
        calls += 1
        code = positions[0]["code"] + "." + positions[0]["exchange"]
        return {
            "enabled": True,
            "announcements": {
                code: [
                    {
                        "id": "announcement-1",
                        "paper_code": "000001",
                        "title": "虚构公告",
                    }
                ]
            },
            "research_reports": {
                code: [{"id": "report-1", "symbol": "sz000001", "rating": "中性"}]
            },
            "sources": [
                {
                    "source_id": "sina-fictional",
                    "kind": "announcement",
                    "title": "新浪上市公司公告",
                    "reference": "https://money.finance.sina.com.cn/",
                    "as_of": cutoff.isoformat(),
                    "fetched_at": cutoff.isoformat(),
                    "evidence": {
                        "api": "getBulletinList",
                        "params": {"PaperCode": code[:6]},
                        "rows": [{"title": "虚构公告"}],
                    },
                },
                {
                    "source_id": "sina-research-fictional",
                    "kind": "analyst_research",
                    "title": "新浪机构研报元数据",
                    "reference": "https://stock.finance.sina.com.cn/",
                    "as_of": cutoff.isoformat(),
                    "fetched_at": cutoff.isoformat(),
                    "evidence": {
                        "api": "getList",
                        "params": {"symbol": "sz000001"},
                        "rows": [{"rating": "中性"}],
                    },
                },
            ],
            "issues": {},
        }

    async def no_closing(positions, cutoff):
        return {"prices": {}, "sources": [], "issues": {}}

    monkeypatch.setattr(service.simulation_research, "collect", research_snapshot)
    monkeypatch.setattr(service.simulation_closing, "collect", no_closing)
    req = request()
    saved = await service.generate(user, req)
    retried = await service.generate(user, req)
    report = saved["report"]
    check = report["checks"][0]
    assert check["announcements"][0]["title"] == "虚构公告"
    assert check["analyst_reports"][0]["rating"] == "中性"
    assert any(
        item.startswith("公告与财报正文尚未获取和核实")
        for item in check["missing_data"]
    )
    assert (
        len(
            [
                s
                for s in report["sources"]
                if s["kind"] in {"announcement", "analyst_research"}
            ]
        )
        == 2
    )
    assert retried["content_hash"] == saved["content_hash"] and calls == 1
    assert await trading.get_ledger(user) == before


async def test_news_sources_are_frozen_and_retry_does_not_query_provider_again(
    seed_user, patched_get_db_connection, clock, monkeypatch
):
    user = seed_user["user_id"]
    await initialize(user)
    before = await trading.get_ledger(user)
    calls = 0

    async def news_snapshot(positions, cutoff):
        nonlocal calls
        calls += 1
        code = positions[0]["code"] + "." + positions[0]["exchange"]
        item = {
            "id": "news-fictional",
            "title": "虚构新闻标题",
            "summary": "虚构新闻摘要",
            "published_at": cutoff.isoformat(),
            "publisher": "新浪财经",
            "url": "https://finance.sina.com.cn/news/example.shtml",
            "category": "news",
            "source_ids": ["news-fictional"],
        }
        source = {
            "source_id": "news-fictional",
            "kind": "stock_news",
            "title": "新浪财经个股新闻",
            "reference": "https://finance.sina.com.cn",
            "as_of": cutoff.isoformat(),
            "fetched_at": cutoff.isoformat(),
            "evidence": {"provider": "sina", "rows": [item]},
        }
        return {
            "enabled": True,
            "items": {code: [item]},
            "sources": [source],
            "issues": {},
        }

    async def no_closing(positions, cutoff):
        return {"prices": {}, "sources": [], "issues": {}}

    async def no_research(positions, cutoff):
        return {
            "enabled": False,
            "announcements": {},
            "research_reports": {},
            "sources": [],
            "issues": {},
        }

    async def no_quotes(positions, cutoff):
        return {"quotes": {}, "sources": [], "issues": {}}

    monkeypatch.setattr(service.simulation_news, "collect", news_snapshot)
    monkeypatch.setattr(service.simulation_closing, "collect", no_closing)
    monkeypatch.setattr(service.simulation_research, "collect", no_research)
    monkeypatch.setattr(service.simulation_quotes, "collect", no_quotes)
    req = request()
    saved = await service.generate(user, req)
    retried = await service.generate(user, req)

    report = saved["report"]
    assert report["checks"][0]["news"][0]["title"] == "虚构新闻标题"
    assert any(source["kind"] == "community_search" for source in report["sources"])
    assert retried["content_hash"] == saved["content_hash"] and calls == 1
    assert await trading.get_ledger(user) == before


async def test_realtime_quote_snapshot_is_frozen_and_retry_does_not_query_provider(
    seed_user, patched_get_db_connection, clock, monkeypatch
):
    user = seed_user["user_id"]
    await initialize(user)
    before = await trading.get_ledger(user)
    calls = 0

    async def quote_snapshot(positions, cutoff):
        nonlocal calls
        calls += 1
        code = positions[0]["code"] + "." + positions[0]["exchange"]
        quote = {
            "price": "11.41",
            "currency": "CNY",
            "as_of": cutoff.isoformat(),
            "fetched_at": cutoff.isoformat(),
            "freshness": "fresh",
            "freshness_as_of": cutoff.isoformat(),
            "source_ids": ["sina-quote-fictional"],
        }
        return {
            "quotes": {code: quote},
            "sources": [
                {
                    "source_id": "sina-quote-fictional",
                    "kind": "live_quote",
                    "title": "新浪实时行情快照",
                    "reference": "https://base.finance.sina.cn/stock/getHqInfo",
                    "as_of": cutoff.isoformat(),
                    "fetched_at": cutoff.isoformat(),
                    "evidence": {
                        "api": "getHqInfo",
                        "params": {"symbol": "sz000001"},
                        "rows": [{"price": "11.41", "currency": "CNY"}],
                    },
                }
            ],
            "issues": {},
        }

    async def no_closing(positions, cutoff):
        return {"prices": {}, "sources": [], "issues": {}}

    async def no_research(positions, cutoff):
        return {
            "enabled": False,
            "announcements": {},
            "research_reports": {},
            "sources": [],
            "issues": {},
        }

    monkeypatch.setattr(service.simulation_quotes, "collect", quote_snapshot)
    monkeypatch.setattr(service.simulation_closing, "collect", no_closing)
    monkeypatch.setattr(service.simulation_research, "collect", no_research)
    req = request()
    saved = await service.generate(user, req)
    retried = await service.generate(user, req)

    report = saved["report"]
    assert report["checks"][0]["current_quote"]["price"] == "11.41"
    assert report["checks"][0]["realtime_pnl"] == {
        "status": "calculated",
        "market_value": "2852.50",
        "unrealized_pnl": "1617.93",
        "currency": "CNY",
        "as_of": clock[0].isoformat(),
        "reason": None,
        "source_ids": ["sina-quote-fictional"],
    }
    assert report["checks"][0]["closing_price"] is None
    assert report["checks"][0]["source_ids"][-1] == "sina-quote-fictional"
    assert any(source["kind"] == "live_quote" for source in report["sources"])
    assert retried["content_hash"] == saved["content_hash"] and calls == 1
    assert await trading.get_ledger(user) == before


async def test_stale_realtime_quote_does_not_produce_a_valid_pnl(
    seed_user, patched_get_db_connection, clock, monkeypatch
):
    user = seed_user["user_id"]
    await initialize(user)

    async def stale_quote(positions, cutoff):
        code = positions[0]["code"] + "." + positions[0]["exchange"]
        return {
            "quotes": {
                code: {
                    "price": "11.41",
                    "currency": "CNY",
                    "as_of": (cutoff - timedelta(minutes=10)).isoformat(),
                    "fetched_at": cutoff.isoformat(),
                    "freshness": "stale",
                    "freshness_as_of": cutoff.isoformat(),
                    "source_ids": ["sina-stale-fictional"],
                }
            },
            "sources": [],
            "issues": {},
        }

    async def no_closing(positions, cutoff):
        return {"prices": {}, "sources": [], "issues": {}}

    async def no_research(positions, cutoff):
        return {"enabled": False, "announcements": {}, "research_reports": {}, "sources": [], "issues": {}}

    monkeypatch.setattr(service.simulation_quotes, "collect", stale_quote)
    monkeypatch.setattr(service.simulation_closing, "collect", no_closing)
    monkeypatch.setattr(service.simulation_research, "collect", no_research)
    report = (await service.generate(user, request()))["report"]

    assert report["checks"][0]["realtime_pnl"] == {
        "status": "pending_verification",
        "market_value": None,
        "unrealized_pnl": None,
        "currency": "CNY",
        "as_of": None,
        "reason": "实时行情已过期，盈亏待核实",
        "source_ids": ["sina-stale-fictional"],
    }


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
