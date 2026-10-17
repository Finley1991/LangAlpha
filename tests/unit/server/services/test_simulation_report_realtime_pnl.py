from src.server.services.simulation_reports import realtime_pnl


def position(**changes):
    return {"quantity": 250, "total_cost": "1234.56780001", **changes}


def quote(**changes):
    return {
        "price": "11.41",
        "currency": "CNY",
        "freshness": "fresh",
        "as_of": "2026-10-16T01:59:40Z",
        "source_ids": ["sina-quote-fictional"],
        **changes,
    }


def test_fresh_quote_calculates_position_value_and_unrealized_pnl_to_cents():
    assert realtime_pnl(position(), quote(), None) == {
        "status": "calculated",
        "market_value": "2852.50",
        "unrealized_pnl": "1617.93",
        "currency": "CNY",
        "as_of": "2026-10-16T01:59:40Z",
        "reason": None,
        "source_ids": ["sina-quote-fictional"],
    }


def test_stale_quote_keeps_pnl_unavailable_and_preserves_quote_evidence():
    result = realtime_pnl(position(), quote(freshness="stale"), None)

    assert result["status"] == "pending_verification"
    assert result["market_value"] is None
    assert result["unrealized_pnl"] is None
    assert result["reason"] == "实时行情已过期，盈亏待核实"
    assert result["source_ids"] == ["sina-quote-fictional"]


def test_missing_quote_is_explicitly_pending():
    result = realtime_pnl(position(), None, None)

    assert result["status"] == "pending_verification"
    assert result["unrealized_pnl"] is None
    assert result["reason"] == "本次未取得实时行情，盈亏待核实"


def test_invalid_quote_or_cost_is_not_reported_as_calculated():
    result = realtime_pnl(position(total_cost="not-a-number"), quote(), None)

    assert result["status"] == "pending_verification"
    assert result["unrealized_pnl"] is None
    assert result["reason"] == "报价或账本成本格式异常，盈亏待核实"
