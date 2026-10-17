"""Calculate a read-only valuation against a stable simulation ledger version."""

from datetime import datetime, timedelta, timezone

from src.server.services import simulation_quotes, simulation_reports, simulation_orders
from src.server.services.simulation_orders import SimulationError

MAX_ESTIMATED_QUOTE_AGE = timedelta(days=10)


def _position_pnl(position: dict, quote: dict | None, issue: str | None) -> dict:
    pnl = simulation_reports.realtime_pnl(position, quote, issue)
    if (
        quote is None
        or quote.get("freshness") != "stale"
        or issue not in (None, "报价已过期，待核实")
    ):
        return pnl

    try:
        as_of = datetime.fromisoformat(quote["as_of"])
        fetched_at = datetime.fromisoformat(quote["fetched_at"])
        checked_at = datetime.fromisoformat(quote["freshness_as_of"])
        if (
            as_of.tzinfo is None
            or fetched_at.tzinfo is None
            or checked_at.tzinfo is None
            or as_of > fetched_at
            or fetched_at > checked_at
            or checked_at - as_of > MAX_ESTIMATED_QUOTE_AGE
        ):
            return {**pnl, "reason": "最近报价超过10天或时间异常，盈亏待核实"}
    except (KeyError, TypeError, ValueError):
        return pnl

    estimate = simulation_reports.realtime_pnl(
        position, {**quote, "freshness": "fresh"}, None
    )
    if estimate["status"] != "calculated":
        return estimate
    return {
        **estimate,
        "status": "estimated",
        "reason": "按最近可用报价估算，非实时行情",
    }


async def refresh(user_id: str) -> dict:
    ledger = await simulation_orders.get_ledger(user_id)
    if ledger is None:
        raise SimulationError("OPENING_REQUIRED", "请先初始化模拟账本。", 404)

    quotes = await simulation_quotes.collect(
        ledger["positions"], datetime.now(timezone.utc)
    )
    latest_ledger = await simulation_orders.get_ledger(user_id)
    if latest_ledger is None or latest_ledger["version"] != ledger["version"]:
        raise SimulationError(
            "LEDGER_CHANGED", "刷新期间账本已变化，请重新查询行情。", 409
        )

    valued_positions = []
    for position in ledger["positions"]:
        code = f"{position['code']}.{position['exchange']}"
        quote = quotes["quotes"].get(code)
        issue = quotes["issues"].get(code) or quotes["issues"].get("all")
        valued_positions.append(
            {
                "code": position["code"],
                "exchange": position["exchange"],
                "name": position["name"],
                "quantity": position["quantity"],
                "total_cost": position["total_cost"],
                "current_quote": quote,
                "realtime_pnl": _position_pnl(position, quote, issue),
            }
        )

    return {
        "account_version": ledger["version"],
        "fetched_at": datetime.now(timezone.utc),
        "positions": valued_positions,
    }
