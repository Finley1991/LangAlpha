"""Deterministic forward simulation; quotes cannot enter through browser APIs."""

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from uuid import UUID
from zoneinfo import ZoneInfo

from src.server.database import simulation_orders as db
from src.server.models.simulation_orders import (
    ConfirmationRequest,
    SuggestionRequest,
    TrustedQuote,
)

CHINA = ZoneInfo("Asia/Shanghai")
CENT = Decimal("0.01")
COST_UNIT = Decimal("0.00000001")
FEES = {
    "version": "sim-fees-v1",
    "commission_rate": "0.0003",
    "minimum_commission": "5.00",
    "sell_tax_rate": "0.0005",
    "transfer_rate": "0.00001",
    "slippage_bps": "5",
    "description": "实验费用假设，非券商实际费率；整单模拟，成交不代表真实市场可获得成交。",
}
RULE_VERSION = "sim-mainland-main-v1"


class SimulationError(ValueError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


def require_account(tx):
    if tx is None:
        raise SimulationError("OPENING_REQUIRED", "请先初始化模拟账本。", 404)


def initial_state(account: dict) -> dict:
    if account["ledger_state"] is not None:
        return account["ledger_state"]
    opening = account["opening_payload"]
    positions = {}
    for row in opening["holdings"]:
        key = f"{row['code']}.{row['exchange']}"
        position = positions.setdefault(
            key,
            {
                "code": row["code"],
                "exchange": row["exchange"],
                "name": row["name"],
                "quantity": 0,
                "total_cost": "0",
                "lots": [],
            },
        )
        position["quantity"] += row["quantity"]
        position["total_cost"] = format(
            Decimal(position["total_cost"]) + Decimal(row["total_cost"]), "f"
        )
        day = max([row["purchased_on"], *row.get("acquisition_dates", [])])
        position["lots"].append(
            {
                "quantity": row["quantity"],
                "acquired_on": day,
                "date_basis": "known_batch"
                if row.get("batch_details_known", True)
                else "latest_known_conservative",
            }
        )
    return {
        "cash": opening["initial_cash"],
        "realized_pnl": "0.00000000",
        "positions": list(positions.values()),
    }


def commission(gross: Decimal, side: str, fees: dict = FEES) -> dict:
    charges = {
        "commission": max(
            Decimal(fees["minimum_commission"]),
            (gross * Decimal(fees["commission_rate"])).quantize(
                CENT, rounding=ROUND_HALF_UP
            ),
        ),
        "transfer": (gross * Decimal(fees["transfer_rate"])).quantize(
            CENT, rounding=ROUND_HALF_UP
        ),
        "sell_tax": (gross * Decimal(fees["sell_tax_rate"])).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
        if side == "sell"
        else Decimal("0.00"),
    }
    return {
        **{key: format(value, ".2f") for key, value in charges.items()},
        "total": format(sum(charges.values()), ".2f"),
        "version": fees["version"],
    }


def cash_change(payload: dict, price: Decimal) -> tuple[Decimal, dict]:
    gross = price * payload["quantity"]
    fees = commission(gross, payload["side"], payload["fees"])
    total = Decimal(fees["total"])
    return (-(gross + total) if payload["side"] == "buy" else gross - total), fees


def position_for(state: dict, payload: dict) -> dict | None:
    return next(
        (
            p
            for p in state["positions"]
            if (p["code"], p["exchange"]) == (payload["code"], payload["exchange"])
        ),
        None,
    )


def resources(state: dict, pending: list[dict], day: str) -> tuple[Decimal, dict]:
    reserved = sum((order["reserved_cash"] for order in pending), Decimal("0"))
    selling = {}
    for order in pending:
        p = order["payload"]
        if p["side"] == "sell":
            key = f"{p['code']}.{p['exchange']}"
            selling[key] = selling.get(key, 0) + p["quantity"]
    available = {}
    for p in state["positions"]:
        key = f"{p['code']}.{p['exchange']}"
        settled = sum(lot["quantity"] for lot in p["lots"] if lot["acquired_on"] < day)
        available[key] = max(0, settled - selling.get(key, 0))
    return Decimal(state["cash"]) - reserved, available


def validate_instrument(payload: dict):
    prefixes = (
        ("600", "601", "603", "605")
        if payload["exchange"] == "SH"
        else ("000", "001", "002", "003")
    )
    if not payload["code"].startswith(prefixes):
        raise SimulationError(
            "UNSUPPORTED_RULE",
            "当前模拟规则仅覆盖沪深主板普通 A 股；其他品种等待规则核验。",
            422,
        )
    if payload["side"] == "buy" and payload["quantity"] % 100:
        raise SimulationError("INVALID_LOT", "当前买入数量必须为 100 股的整数倍。", 422)


def public_suggestion(row: dict) -> dict:
    return {
        **row["payload"],
        "suggestion_id": str(row["suggestion_id"]),
        "status": row["status"],
        "created_at": row["created_at"].isoformat(),
    }


def public_order(row: dict) -> dict:
    return {
        **row["payload"],
        "order_id": str(row["order_id"]),
        "status": row["status"],
        "reserved_cash": format(row["reserved_cash"], ".2f"),
        "confirmed_at": row["confirmed_at"].isoformat(),
        "execution_status": "awaiting_verified_quote"
        if row["status"] == "pending"
        else row["status"],
    }


def public_fill(row: dict) -> dict:
    return {
        **row["payload"],
        "fill_id": str(row["fill_id"]),
        "order_id": str(row["order_id"]),
        "created_at": row["created_at"].isoformat(),
    }


async def get_ledger(user_id: str) -> dict | None:
    async with db.transaction(user_id) as tx:
        if tx is None:
            return None
        await tx.expire()
        state = initial_state(tx.account)
        pending = await tx.pending_orders()
        cash, available = resources(
            state, pending, tx.now.astimezone(CHINA).date().isoformat()
        )
        return {
            "account_id": str(tx.account_id),
            "version": tx.account["ledger_version"],
            "mode": "simulation",
            "currency": "CNY",
            "as_of": tx.now.isoformat(),
            "cash": format(Decimal(state["cash"]), ".2f"),
            "available_cash": format(cash, ".2f"),
            "reserved_cash": format(Decimal(state["cash"]) - cash, ".2f"),
            "realized_pnl": state["realized_pnl"],
            "positions": [
                {**p, "available_quantity": available[f"{p['code']}.{p['exchange']}"]}
                for p in state["positions"]
            ],
            "suggestions": [public_suggestion(s) for s in await tx.suggestions()],
            "orders": [public_order(o) for o in await tx.orders()],
            "fills": [public_fill(f) for f in await tx.fills()],
            "fees": FEES,
            "rule_version": RULE_VERSION,
            "valuation_status": "unverified",
            "market_data_status": "unverified",
            "history_limit": 100,
            "sellable_basis": "按已知日期保守计算，成交时仍须核验交易日历与证券规则",
        }


async def suggest(user_id: str, request: SuggestionRequest) -> dict:
    payload = request.model_dump(mode="json")
    payload["limit_price"] = format(request.limit_price, ".2f")
    payload["expires_at"] = request.expires_at.astimezone(timezone.utc).isoformat()
    async with db.transaction(user_id) as tx:
        require_account(tx)
        await tx.expire()
        previous = await tx.by_client_request(request.client_request_id)
        if previous:
            if any(previous["payload"][key] != value for key, value in payload.items()):
                raise SimulationError(
                    "REQUEST_CONFLICT", "同一请求标识不能提交不同建议，请新建卡片。"
                )
            return public_suggestion(previous)
        validate_instrument(payload)
        if request.account_version != tx.account["ledger_version"]:
            raise SimulationError(
                "STALE_LEDGER", "账本或预留已变化，请刷新并重新生成建议。"
            )
        if not tx.now < request.expires_at <= tx.now + timedelta(days=7):
            raise SimulationError(
                "INVALID_EXPIRY", "建议有效期须晚于当前时间且不超过七天。", 422
            )
        if await tx.pending_suggestion_count() >= 100:
            raise SimulationError(
                "TOO_MANY_PENDING", "待确认建议已达上限，请取消旧建议。"
            )
        state = initial_state(tx.account)
        payload.update(
            {
                "suggestion_version": 1,
                "fees": FEES,
                "rule_version": RULE_VERSION,
                "origin": "manual_research",
                "reference_status": "unverified",
            }
        )
        delta, charges = cash_change(payload, request.limit_price)
        payload["estimated_cash_delta"] = format(delta, ".2f")
        payload["estimated_fees"] = charges
        payload["estimated_cash_after"] = format(Decimal(state["cash"]) + delta, ".2f")
        row = await tx.insert_suggestion(payload)
        await tx.event(
            "suggested",
            {
                "suggestion_id": str(row["suggestion_id"]),
                "account_version": request.account_version,
                "suggestion_version": 1,
                "actor": user_id,
            },
        )
        return public_suggestion(row)


async def confirm(
    user_id: str, suggestion_id: UUID, request: ConfirmationRequest
) -> dict:
    async with db.transaction(user_id) as tx:
        require_account(tx)
        await tx.expire()
        suggestion = await tx.suggestion(suggestion_id)
        if suggestion is None:
            raise SimulationError("NOT_FOUND", "找不到此模拟建议。", 404)
        p = suggestion["payload"]
        if (
            not request.acknowledge_simulation
            or request.suggestion_version != p["suggestion_version"]
        ):
            raise SimulationError(
                "CONFIRMATION_REQUIRED", "请核对并明确确认当前版本的模拟建议。", 422
            )
        previous = await tx.order_for_suggestion(suggestion_id)
        if previous:
            return public_order(previous)
        if suggestion["status"] != "pending":
            raise SimulationError(
                "SUGGESTION_CLOSED", "此建议已取消或到期，请重新生成。"
            )
        if (
            request.account_version != p["account_version"]
            or request.account_version != tx.account["ledger_version"]
        ):
            raise SimulationError(
                "STALE_LEDGER", "账本或预留已变化，请刷新并重新生成建议。"
            )
        day = tx.now.astimezone(CHINA).date().isoformat()
        if tx.account["opening_payload"]["opening_date"] > day:
            raise SimulationError("OPENING_IN_FUTURE", "账本起算日期尚未到达。")
        state = initial_state(tx.account)
        pending = await tx.pending_orders()
        if len(pending) >= 100:
            raise SimulationError("TOO_MANY_PENDING", "待执行订单已达上限。")
        available_cash, available = resources(state, pending, day)
        delta, _ = cash_change(p, Decimal(p["limit_price"]))
        reserved = -delta if p["side"] == "buy" else Decimal("0")
        if p["side"] == "buy" and reserved > available_cash:
            raise SimulationError(
                "INSUFFICIENT_CASH", "可用现金不足（已扣除其他订单预留和模拟费用）。"
            )
        if p["side"] == "sell":
            sellable = available.get(f"{p['code']}.{p['exchange']}", 0)
            if p["quantity"] > sellable:
                raise SimulationError(
                    "INSUFFICIENT_SELLABLE",
                    "可卖数量不足（已扣除当日买入及其他订单预留）。",
                )
            if p["quantity"] % 100 and p["quantity"] != sellable:
                raise SimulationError(
                    "INVALID_LOT", "零股模拟仅支持一次卖出全部可卖余额。", 422
                )
            if delta <= 0:
                raise SimulationError(
                    "NONPOSITIVE_PROCEEDS", "预计卖出净收入不足以覆盖模拟费用。", 422
                )
        order = await tx.insert_order(suggestion, reserved)
        await tx.suggestion_status(suggestion_id, "confirmed")
        await tx.bump()
        await tx.event(
            "confirmed",
            {
                "suggestion_id": str(suggestion_id),
                "order_id": str(order["order_id"]),
                "actor": user_id,
                "suggestion_version": request.suggestion_version,
                "confirmed_account_version": request.account_version,
                "account_version": tx.account["ledger_version"],
                "reserved_cash": format(reserved, ".2f"),
            },
        )
        return public_order(order)


async def cancel_suggestion(user_id: str, suggestion_id: UUID) -> dict:
    async with db.transaction(user_id) as tx:
        require_account(tx)
        await tx.expire()
        row = await tx.suggestion(suggestion_id)
        if row is None:
            raise SimulationError("NOT_FOUND", "找不到此模拟建议。", 404)
        if row["status"] == "confirmed":
            raise SimulationError(
                "USE_ORDER_CANCEL", "建议已生成订单，请在订单卡取消。"
            )
        if row["status"] == "pending":
            await tx.suggestion_status(suggestion_id, "cancelled")
            await tx.event(
                "suggestion_cancelled",
                {"suggestion_id": str(suggestion_id), "actor": user_id},
            )
            row["status"] = "cancelled"
        return public_suggestion(row)


async def cancel_order(user_id: str, order_id: UUID) -> dict:
    async with db.transaction(user_id) as tx:
        require_account(tx)
        await tx.expire()
        row = await tx.order(order_id)
        if row is None:
            raise SimulationError("NOT_FOUND", "找不到此模拟订单。", 404)
        if row["status"] == "filled":
            raise SimulationError(
                "ORDER_FILLED", "已成交订单不能取消或改写，请使用后续更正流程。"
            )
        if row["status"] == "pending":
            await tx.order_status(order_id, "cancelled")
            await tx.bump()
            await tx.event(
                "order_cancelled",
                {
                    "order_id": str(order_id),
                    "actor": user_id,
                    "account_version": tx.account["ledger_version"],
                },
            )
            row["status"] = "cancelled"
        return public_order(row)


def continuous_session(value: datetime) -> bool:
    local = value.astimezone(CHINA)
    return local.weekday() < 5 and (
        time(9, 30) <= local.time() <= time(11, 30)
        or time(13) <= local.time() < time(14, 57)
    )


def executable_price(
    order: dict, quote: TrustedQuote, now: datetime, *, allow_test: bool = False
) -> Decimal | None:
    p = order["payload"]
    if quote.source_kind == "test" and not allow_test:
        return None
    local = quote.as_of.astimezone(CHINA)
    current = now.astimezone(CHINA)
    if (
        order["status"] != "pending"
        or not quote.instrument_verified
        or not quote.calendar_verified
        or not quote.trading_day
        or quote.suspended
        or (quote.code, quote.exchange) != (p["code"], p["exchange"])
        or quote.rule_version != p["rule_version"]
        or quote.lower_limit is None
        or quote.upper_limit is None
        or not continuous_session(quote.as_of)
        or not continuous_session(now)
        or local.date() != current.date()
        or not order["confirmed_at"] < quote.as_of <= quote.fetched_at <= now
        or now - quote.as_of > timedelta(seconds=60)
        or now >= order["expires_at"]
        or not quote.lower_limit <= quote.price <= quote.upper_limit
    ):
        return None
    sign = 1 if p["side"] == "buy" else -1
    price = (
        quote.price * (1 + sign * Decimal(p["fees"]["slippage_bps"]) / 10_000)
    ).quantize(CENT, rounding=ROUND_CEILING if sign == 1 else ROUND_FLOOR)
    limit = Decimal(p["limit_price"])
    if (
        price <= 0
        or not quote.lower_limit <= price <= quote.upper_limit
        or (price > limit if sign == 1 else price < limit)
    ):
        return None
    return price


def apply_fill(state: dict, p: dict, price: Decimal, day: str) -> tuple[dict, Decimal]:
    delta, charges = cash_change(p, price)
    position = position_for(state, p)
    removed_cost = Decimal("0")
    pnl = Decimal("0")
    if p["side"] == "buy":
        if position is None:
            position = {
                "code": p["code"],
                "exchange": p["exchange"],
                "name": p["name"],
                "quantity": 0,
                "total_cost": "0",
                "lots": [],
            }
            state["positions"].append(position)
        position["quantity"] += p["quantity"]
        position["total_cost"] = format(Decimal(position["total_cost"]) - delta, ".8f")
        position["lots"].append(
            {
                "quantity": p["quantity"],
                "acquired_on": day,
                "date_basis": "simulation_fill",
            }
        )
    else:
        if position is None or p["quantity"] > position["quantity"]:
            raise SimulationError("INSUFFICIENT_SELLABLE", "成交前可卖数量不足。")
        remaining = p["quantity"]
        for lot in sorted(position["lots"], key=lambda lot: lot["acquired_on"]):
            if lot["acquired_on"] < day:
                taken = min(remaining, lot["quantity"])
                lot["quantity"] -= taken
                remaining -= taken
        if remaining:
            raise SimulationError(
                "INSUFFICIENT_SELLABLE", "成交前未满足 T+1 可卖条件。"
            )
        cost = Decimal(position["total_cost"])
        removed_cost = (
            cost
            if p["quantity"] == position["quantity"]
            else (cost * p["quantity"] / position["quantity"]).quantize(
                COST_UNIT, rounding=ROUND_HALF_UP
            )
        )
        position["quantity"] -= p["quantity"]
        position["total_cost"] = format(cost - removed_cost, ".8f")
        position["lots"] = [lot for lot in position["lots"] if lot["quantity"]]
        pnl = delta - removed_cost
        state["realized_pnl"] = format(Decimal(state["realized_pnl"]) + pnl, ".8f")
    state["positions"] = [
        position for position in state["positions"] if position["quantity"]
    ]
    state["cash"] = format(Decimal(state["cash"]) + delta, ".2f")
    if Decimal(state["cash"]) < 0 or delta <= 0 and p["side"] == "sell":
        raise SimulationError("INSUFFICIENT_CASH", "成交前现金或净收入校验失败。")
    return {
        "price": format(price, ".2f"),
        "quantity": p["quantity"],
        "code": p["code"],
        "exchange": p["exchange"],
        "name": p["name"],
        "side": p["side"],
        "gross": format(price * p["quantity"], ".2f"),
        "fees": charges,
        "fee_assumptions": p["fees"],
        "cash_delta": format(delta, ".2f"),
        "cash_after": state["cash"],
        "removed_cost": format(removed_cost, ".8f"),
        "realized_pnl": format(pnl, ".8f"),
        "cost_method": "moving_weighted_average",
    }, delta


async def settle(
    user_id: str, order_id: UUID, quote: TrustedQuote, *, allow_test: bool = False
) -> dict | None:
    """Worker entry only; R2 adapter must supply verified fresh market evidence."""
    if allow_test and not (
        user_id.startswith("simulation-test-") or user_id == "simaizhirong-smoke"
    ):
        raise SimulationError(
            "TEST_ACCOUNT_REQUIRED", "测试行情仅可用于隔离的虚构测试账户。", 422
        )
    async with db.transaction(user_id) as tx:
        require_account(tx)
        await tx.expire()
        order = await tx.order(order_id)
        if order is None:
            raise SimulationError("NOT_FOUND", "找不到此模拟订单。", 404)
        previous = await tx.fill(order_id)
        if previous:
            return public_fill(previous)
        price = executable_price(order, quote, tx.now, allow_test=allow_test)
        if price is None:
            return None
        state = initial_state(tx.account)
        p = order["payload"]
        pending = await tx.pending_orders()
        other_orders = [o for o in pending if o["order_id"] != order_id]
        cash, sellable = resources(
            state, other_orders, quote.as_of.astimezone(CHINA).date().isoformat()
        )
        delta, _ = cash_change(p, price)
        if p["side"] == "buy" and (-delta > cash or -delta > order["reserved_cash"]):
            return None
        if p["side"] == "sell" and p["quantity"] > sellable.get(
            f"{p['code']}.{p['exchange']}", 0
        ):
            return None
        payload, delta = apply_fill(
            state, p, price, quote.as_of.astimezone(CHINA).date().isoformat()
        )
        payload.update(
            {
                "quote": quote.model_dump(mode="json"),
                "confirmed_at": order["confirmed_at"].isoformat(),
                "suggestion_id": str(order["suggestion_id"]),
                "rule_version": p["rule_version"],
            }
        )
        await tx.bump(state)
        payload["account_version"] = tx.account["ledger_version"]
        fill = await tx.insert_fill(order_id, payload)
        await tx.order_status(order_id, "filled")
        await tx.event(
            "filled",
            {
                "order_id": str(order_id),
                "fill_id": str(fill["fill_id"]),
                "account_version": tx.account["ledger_version"],
                "cash_after": state["cash"],
                "quote_evidence_id": quote.evidence_id,
            },
            delta,
        )
        return public_fill(fill)


async def get_events(user_id: str, before: int | None = None, limit: int = 50) -> dict:
    async with db.transaction(user_id) as tx:
        require_account(tx)
        await tx.expire()
        rows = await tx.events(before, limit)
        page = rows[:limit]
        return {
            "events": [
                {
                    "event_id": row["event_id"],
                    "event_type": row["event_type"],
                    "payload": row["payload"],
                    "cash_delta": format(row["cash_delta"], ".2f"),
                    "created_at": row["created_at"].isoformat(),
                }
                for row in page
            ],
            "next_cursor": page[-1]["event_id"] if len(rows) > limit else None,
        }


async def get_record(user_id: str, kind: str, record_id: UUID) -> dict:
    async with db.transaction(user_id) as tx:
        require_account(tx)
        await tx.expire()
        if kind == "suggestions":
            row = await tx.suggestion(record_id)
            serialize = public_suggestion
        elif kind == "orders":
            row = await tx.order(record_id)
            serialize = public_order
        else:
            row = await tx.fill_by_id(record_id)
            serialize = public_fill
        if row is None:
            raise SimulationError("NOT_FOUND", "找不到此模拟记录。", 404)
        return serialize(row)
