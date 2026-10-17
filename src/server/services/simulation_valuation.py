"""Calculate a read-only valuation against a stable simulation ledger version."""

from datetime import datetime, timezone

from src.server.services import simulation_quotes, simulation_reports, simulation_orders
from src.server.services.simulation_orders import SimulationError


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
                "realtime_pnl": simulation_reports.realtime_pnl(
                    position, quote, issue
                ),
            }
        )

    return {
        "account_version": ledger["version"],
        "fetched_at": datetime.now(timezone.utc),
        "positions": valued_positions,
    }
