from datetime import timezone

import pytest

from src.server.services import simulation_valuation
from src.server.services.simulation_orders import SimulationError


def ledger(version: int = 3):
    return {
        "version": version,
        "positions": [{
            "code": "600000",
            "exchange": "SH",
            "name": "虚构测试证券",
            "quantity": 100,
            "total_cost": "1000.00",
        }],
    }


@pytest.mark.asyncio
async def test_refresh_returns_fresh_quote_valuation_without_changing_ledger(monkeypatch):
    snapshot = ledger()
    ledger_reads = 0
    quote_calls = []

    async def get_ledger(_user_id):
        nonlocal ledger_reads
        ledger_reads += 1
        return snapshot

    async def collect(positions, cutoff):
        quote_calls.append((positions, cutoff))
        return {
            "quotes": {
                "600000.SH": {
                    "price": "12.50",
                    "currency": "CNY",
                    "as_of": "2026-10-17T01:59:55Z",
                    "fetched_at": "2026-10-17T02:00:00Z",
                    "freshness": "fresh",
                    "freshness_as_of": "2026-10-17T02:00:00Z",
                    "source_ids": ["sina-quote-fictional"],
                }
            },
            "issues": {},
        }

    monkeypatch.setattr(simulation_valuation.simulation_orders, "get_ledger", get_ledger)
    monkeypatch.setattr(simulation_valuation.simulation_quotes, "collect", collect)

    result = await simulation_valuation.refresh("fictional-user")

    assert result["account_version"] == 3
    assert result["positions"][0]["current_quote"]["price"] == "12.50"
    assert result["positions"][0]["realtime_pnl"]["market_value"] == "1250.00"
    assert result["positions"][0]["realtime_pnl"]["unrealized_pnl"] == "250.00"
    assert result["positions"][0]["realtime_pnl"]["as_of"] == "2026-10-17T01:59:55Z"
    assert ledger_reads == 2
    assert len(quote_calls) == 1
    assert quote_calls[0][0] == snapshot["positions"]
    assert quote_calls[0][1].tzinfo == timezone.utc
    assert snapshot["positions"][0]["total_cost"] == "1000.00"


@pytest.mark.asyncio
async def test_refresh_rejects_valuation_if_ledger_changes_during_quote_request(monkeypatch):
    snapshots = iter([ledger(3), ledger(4)])

    async def get_ledger(_user_id):
        return next(snapshots)

    async def collect(_positions, _cutoff):
        return {"quotes": {}, "issues": {}}

    monkeypatch.setattr(simulation_valuation.simulation_orders, "get_ledger", get_ledger)
    monkeypatch.setattr(simulation_valuation.simulation_quotes, "collect", collect)

    with pytest.raises(SimulationError) as error:
        await simulation_valuation.refresh("fictional-user")

    assert error.value.code == "LEDGER_CHANGED"
    assert error.value.status == 409


@pytest.mark.asyncio
async def test_refresh_estimates_pnl_from_a_recent_quote_outside_the_live_window(monkeypatch):
    snapshot = ledger()

    async def get_ledger(_user_id):
        return snapshot

    async def collect(_positions, _cutoff):
        return {
            "quotes": {
                "600000.SH": {
                    "price": "12.50",
                    "currency": "CNY",
                    "as_of": "2026-10-16T01:59:55Z",
                    "fetched_at": "2026-10-17T02:00:00Z",
                    "freshness": "stale",
                    "freshness_as_of": "2026-10-17T02:00:00Z",
                    "source_ids": ["sina-quote-fictional"],
                }
            },
            "issues": {},
        }

    monkeypatch.setattr(simulation_valuation.simulation_orders, "get_ledger", get_ledger)
    monkeypatch.setattr(simulation_valuation.simulation_quotes, "collect", collect)

    result = await simulation_valuation.refresh("fictional-user")
    pnl = result["positions"][0]["realtime_pnl"]

    assert pnl["status"] == "estimated"
    assert pnl["market_value"] == "1250.00"
    assert pnl["unrealized_pnl"] == "250.00"
    assert pnl["as_of"] == "2026-10-16T01:59:55Z"
    assert pnl["reason"] == "按最近可用报价估算，非实时行情"


@pytest.mark.asyncio
async def test_refresh_keeps_quotes_older_than_ten_days_pending(monkeypatch):
    snapshot = ledger()

    async def get_ledger(_user_id):
        return snapshot

    async def collect(_positions, _cutoff):
        return {
            "quotes": {
                "600000.SH": {
                    "price": "12.50",
                    "currency": "CNY",
                    "as_of": "2026-10-01T01:59:55Z",
                    "fetched_at": "2026-10-17T02:00:00Z",
                    "freshness": "stale",
                    "freshness_as_of": "2026-10-17T02:00:00Z",
                    "source_ids": ["sina-quote-fictional"],
                }
            },
            "issues": {"600000.SH": "报价已过期，待核实"},
        }

    monkeypatch.setattr(simulation_valuation.simulation_orders, "get_ledger", get_ledger)
    monkeypatch.setattr(simulation_valuation.simulation_quotes, "collect", collect)

    result = await simulation_valuation.refresh("fictional-user")
    pnl = result["positions"][0]["realtime_pnl"]

    assert pnl["status"] == "pending_verification"
    assert pnl["market_value"] is None
    assert pnl["unrealized_pnl"] is None
    assert pnl["reason"] == "最近报价超过10天或时间异常，盈亏待核实"


@pytest.mark.asyncio
async def test_refresh_requires_an_initialized_ledger(monkeypatch):
    async def get_ledger(_user_id):
        return None

    monkeypatch.setattr(simulation_valuation.simulation_orders, "get_ledger", get_ledger)

    with pytest.raises(SimulationError) as error:
        await simulation_valuation.refresh("fictional-user")

    assert error.value.code == "OPENING_REQUIRED"
    assert error.value.status == 404
