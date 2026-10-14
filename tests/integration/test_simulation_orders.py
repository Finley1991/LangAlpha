import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.server.app.simulation import router
from src.server.database import simulation_orders as db
from src.server.database.user import create_user
from src.server.models.simulation import OpeningRequest
from src.server.models.simulation_orders import (
    ConfirmationRequest,
    SuggestionRequest,
    TrustedQuote,
)
from src.server.services import simulation, simulation_orders as service
from src.server.utils.api import get_current_user_id

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
NOW = datetime(2026, 10, 14, 2, tzinfo=timezone.utc)


@pytest.fixture
def test_user_id():
    return "simulation-test-orders"


@pytest.fixture
def clock(monkeypatch):
    current = [NOW]
    original = db.transaction

    @asynccontextmanager
    async def transaction(user_id):
        async with original(user_id) as tx:
            if tx:
                tx.now = current[0]
            yield tx

    monkeypatch.setattr(db, "transaction", transaction)
    return current


async def initialize(user_id, **changes):
    request = {
        "opening_date": "2026-10-13",
        "initial_cash": "1000.00",
        "cost_includes_fees": False,
        "holdings": [
            {
                "code": "000001",
                "exchange": "SZ",
                "name": "虚构期初",
                "quantity": 250,
                "total_cost": "1234.56780001",
                "purchased_on": "2026-10-12",
            }
        ],
        **changes,
    }
    return (
        await simulation.initialize(user_id, OpeningRequest.model_validate(request))
    )[0]


def request(version=1, **changes):
    return SuggestionRequest.model_validate(
        {
            "client_request_id": str(uuid4()),
            "account_version": version,
            "code": "600000",
            "exchange": "SH",
            "name": "虚构模拟",
            "side": "buy",
            "quantity": 100,
            "limit_price": "1.00",
            "reason": "虚构研究理由",
            "counterargument": "测试不具有投资意义",
            "expires_at": (NOW + timedelta(hours=1)).isoformat(),
            **changes,
        }
    )


async def confirmed(user_id, req=None):
    if req is None:
        ledger = await service.get_ledger(user_id)
        req = request(ledger["version"])
    suggestion = await service.suggest(user_id, req)
    order = await service.confirm(
        user_id,
        UUID(suggestion["suggestion_id"]),
        confirmation(suggestion["account_version"]),
    )
    return suggestion, order


def confirmation(version=1, **changes):
    return ConfirmationRequest.model_validate(
        {
            "suggestion_version": 1,
            "account_version": version,
            "acknowledge_simulation": True,
            **changes,
        }
    )


def quote(at=NOW + timedelta(seconds=1), **changes):
    return TrustedQuote.model_validate(
        {
            "code": "600000",
            "exchange": "SH",
            "price": "0.99",
            "as_of": at,
            "fetched_at": at,
            "source": "fictional-integration",
            "source_kind": "test",
            "evidence_id": "fictional-db-evidence",
            "instrument_verified": True,
            "calendar_verified": True,
            "trading_day": True,
            "suspended": False,
            "rule_version": service.RULE_VERSION,
            "lower_limit": "0.50",
            "upper_limit": "2.00",
            **changes,
        }
    )


async def test_draft_retries_are_idempotent_without_order_or_reservation(
    seed_user, patched_get_db_connection, clock
):
    user = seed_user["user_id"]
    await initialize(user)
    req = request()
    cards = await asyncio.gather(*(service.suggest(user, req) for _ in range(12)))
    assert len({s["suggestion_id"] for s in cards}) == 1
    ledger = await service.get_ledger(user)
    assert (
        ledger["orders"] == []
        and ledger["reserved_cash"] == "0.00"
        and ledger["version"] == 1
    )
    altered = request(client_request_id=req.client_request_id, reason="另一个理由")
    with pytest.raises(service.SimulationError, match="不同建议"):
        await service.suggest(user, altered)
    assert len((await service.get_events(user))["events"]) == 1


async def test_concurrent_confirmation_creates_one_order_and_checks_versions(
    seed_user, patched_get_db_connection, clock
):
    user = seed_user["user_id"]
    await initialize(user)
    card = await service.suggest(user, request())
    stale = await service.suggest(user, request())
    replies = await asyncio.gather(
        *(
            service.confirm(user, UUID(card["suggestion_id"]), confirmation())
            for _ in range(12)
        )
    )
    assert len({o["order_id"] for o in replies}) == 1
    ledger = await service.get_ledger(user)
    assert (
        len(ledger["orders"]) == 1
        and ledger["reserved_cash"] == "105.00"
        and ledger["version"] == 2
    )
    for body in [confirmation(2), confirmation(1)]:
        with pytest.raises(service.SimulationError) as exc:
            await service.confirm(user, UUID(stale["suggestion_id"]), body)
        assert exc.value.code == "STALE_LEDGER"
    with pytest.raises(service.SimulationError) as exc:
        await service.confirm(
            user, UUID(card["suggestion_id"]), confirmation(suggestion_version=2)
        )
    assert exc.value.code == "CONFIRMATION_REQUIRED"
    with pytest.raises(service.SimulationError):
        await service.confirm(
            user,
            UUID(card["suggestion_id"]),
            confirmation(acknowledge_simulation=False),
        )


async def test_competing_orders_cannot_overreserve_cash(
    seed_user, patched_get_db_connection, clock
):
    user = seed_user["user_id"]
    await initialize(user)
    cards = await asyncio.gather(
        *(service.suggest(user, request(limit_price="5")) for _ in range(2))
    )
    results = await asyncio.gather(
        *(
            service.confirm(user, UUID(s["suggestion_id"]), confirmation())
            for s in cards
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(r, dict) for r in results) == 1
    _, first = next(
        (cards[i], result)
        for i, result in enumerate(results)
        if isinstance(result, dict)
    )
    ledger = await service.get_ledger(user)
    card = await service.suggest(user, request(ledger["version"], limit_price="5"))
    with pytest.raises(service.SimulationError) as exc:
        await service.confirm(
            user, UUID(card["suggestion_id"]), confirmation(ledger["version"])
        )
    assert exc.value.code == "INSUFFICIENT_CASH"
    assert (await service.get_ledger(user))["available_cash"] == "494.99"
    await service.cancel_order(user, UUID(first["order_id"]))
    assert (await service.get_ledger(user))["available_cash"] == "1000.00"


async def test_sell_reservations_and_conservative_odd_lot_rule(
    seed_user, patched_get_db_connection, clock
):
    user = seed_user["user_id"]
    await initialize(user)
    _, order = await confirmed(
        user, request(side="sell", code="000001", exchange="SZ", quantity=200)
    )
    ledger = await service.get_ledger(user)
    assert ledger["positions"][0]["available_quantity"] == 50
    excessive = await service.suggest(
        user,
        request(
            ledger["version"], side="sell", code="000001", exchange="SZ", quantity=100
        ),
    )
    with pytest.raises(service.SimulationError) as exc:
        await service.confirm(
            user, UUID(excessive["suggestion_id"]), confirmation(ledger["version"])
        )
    assert exc.value.code == "INSUFFICIENT_SELLABLE"
    await confirmed(
        user,
        request(
            ledger["version"], side="sell", code="000001", exchange="SZ", quantity=50
        ),
    )
    assert (await service.get_ledger(user))["positions"][0]["available_quantity"] == 0
    await service.cancel_order(user, UUID(order["order_id"]))
    ledger = await service.get_ledger(user)
    invalid = await service.suggest(
        user,
        request(
            ledger["version"], side="sell", code="000001", exchange="SZ", quantity=150
        ),
    )
    with pytest.raises(service.SimulationError) as exc:
        await service.confirm(
            user, UUID(invalid["suggestion_id"]), confirmation(ledger["version"])
        )
    assert exc.value.code == "INVALID_LOT"


async def test_cancelled_unconfirmed_and_expired_never_fill_and_retries_do_not_resurrect(
    seed_user, patched_get_db_connection, clock
):
    user = seed_user["user_id"]
    await initialize(user)
    cancelled = await service.suggest(user, request())
    await service.cancel_suggestion(user, UUID(cancelled["suggestion_id"]))
    with pytest.raises(service.SimulationError) as exc:
        await service.confirm(user, UUID(cancelled["suggestion_id"]), confirmation())
    assert exc.value.code == "SUGGESTION_CLOSED"
    _, order = await confirmed(user)
    await service.cancel_order(user, UUID(order["order_id"]))
    clock[0] += timedelta(seconds=2)
    assert (
        await service.settle(user, UUID(order["order_id"]), quote(), allow_test=True)
        is None
    )
    card, expires = await confirmed(user)
    pending = await service.suggest(
        user, request((await service.get_ledger(user))["version"])
    )
    clock[0] = NOW + timedelta(hours=1)
    ledger = await service.get_ledger(user)
    assert ledger["reserved_cash"] == "0.00" and ledger["cash"] == "1000.00"
    assert (
        next(
            o["status"]
            for o in ledger["orders"]
            if o["order_id"] == expires["order_id"]
        )
        == "expired"
    )
    retried = await service.confirm(
        user, UUID(card["suggestion_id"]), confirmation(card["account_version"])
    )
    assert retried["order_id"] == expires["order_id"] and retried["status"] == "expired"
    with pytest.raises(service.SimulationError):
        await service.confirm(
            user,
            UUID(pending["suggestion_id"]),
            confirmation(pending["account_version"]),
        )
    assert not ledger["fills"]
    assert all(
        e["cash_delta"] == "0.00" for e in (await service.get_events(user))["events"]
    )


async def test_concurrent_fill_is_atomic_unique_reconciled_and_t_plus_one(
    seed_user, patched_get_db_connection, clock
):
    user = seed_user["user_id"]
    await initialize(user)
    _, order = await confirmed(user)
    clock[0] += timedelta(seconds=2)
    assert await service.settle(user, UUID(order["order_id"]), quote()) is None
    results = await asyncio.gather(
        *(
            service.settle(user, UUID(order["order_id"]), quote(), allow_test=True)
            for _ in range(12)
        )
    )
    assert len({f["fill_id"] for f in results}) == 1
    ledger = await service.get_ledger(user)
    assert (
        ledger["cash"] == "895.00"
        and ledger["reserved_cash"] == "0.00"
        and ledger["version"] == 3
    )
    new = next(p for p in ledger["positions"] if p["code"] == "600000")
    assert (
        new["quantity"] == 100
        and new["available_quantity"] == 0
        and new["total_cost"] == "105.00000000"
    )
    events = (await service.get_events(user))["events"]
    assert sum(e["event_type"] == "filled" for e in events) == 1
    assert Decimal("1000") + sum(Decimal(e["cash_delta"]) for e in events) == Decimal(
        ledger["cash"]
    )
    sell = await service.suggest(user, request(ledger["version"], side="sell"))
    with pytest.raises(service.SimulationError) as exc:
        await service.confirm(
            user, UUID(sell["suggestion_id"]), confirmation(ledger["version"])
        )
    assert exc.value.code == "INSUFFICIENT_SELLABLE"
    with pytest.raises(service.SimulationError) as exc:
        await service.cancel_order(user, UUID(order["order_id"]))
    assert exc.value.code == "ORDER_FILLED"
    assert (await simulation.get_opening(user)).initial_cash == Decimal("1000")


async def test_failure_after_fill_insert_rolls_back_all_financial_writes(
    seed_user, patched_get_db_connection, clock, monkeypatch
):
    user = seed_user["user_id"]
    await initialize(user)
    _, order = await confirmed(user)
    clock[0] += timedelta(seconds=2)
    original = db.LedgerTransaction.event

    async def fail_event(self, kind, payload, cash_delta=Decimal("0")):
        if kind == "filled":
            raise RuntimeError("injected audit storage failure")
        return await original(self, kind, payload, cash_delta)

    monkeypatch.setattr(db.LedgerTransaction, "event", fail_event)
    with pytest.raises(RuntimeError, match="audit storage"):
        await service.settle(user, UUID(order["order_id"]), quote(), allow_test=True)
    ledger = await service.get_ledger(user)
    assert (
        ledger["cash"] == "1000.00" and ledger["version"] == 2 and not ledger["fills"]
    )
    assert ledger["orders"][0]["status"] == "pending" and len(ledger["positions"]) == 1
    monkeypatch.setattr(db.LedgerTransaction, "event", original)
    assert await service.settle(user, UUID(order["order_id"]), quote(), allow_test=True)


async def test_sell_costs_cash_and_events_keep_exact_opening_cost(
    seed_user, patched_get_db_connection, clock
):
    user = seed_user["user_id"]
    await initialize(user)
    _, order = await confirmed(
        user,
        request(
            side="sell", code="000001", exchange="SZ", quantity=250, limit_price="10"
        ),
    )
    clock[0] += timedelta(seconds=2)
    fill = await service.settle(
        user,
        UUID(order["order_id"]),
        quote(
            code="000001", exchange="SZ", price="12", lower_limit="9", upper_limit="15"
        ),
        allow_test=True,
    )
    assert fill["removed_cost"] == "1234.56780001" and fill["price"] == "11.99"
    ledger = await service.get_ledger(user)
    assert not ledger["positions"] and Decimal(ledger["realized_pnl"]) == Decimal(
        fill["cash_delta"]
    ) - Decimal(fill["removed_cost"])
    assert Decimal(ledger["cash"]) == Decimal("1000") + Decimal(fill["cash_delta"])


async def test_api_owner_isolation_pagination_and_no_client_quote_path(
    seed_user, patched_get_db_connection, clock
):
    user = seed_user["user_id"]
    other = "simulation-test-other"
    await create_user(user_id=other, name="Fictitious other")
    await initialize(user)
    await initialize(other)
    req = request()
    card = await service.suggest(user, req)
    other_card = await service.suggest(other, req)
    assert card["suggestion_id"] != other_card["suggestion_id"]
    _, order = await confirmed(user)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: other
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        base = "/api/v1/users/me/simulation"
        for path in [
            f"/suggestions/{card['suggestion_id']}/confirm",
            f"/suggestions/{card['suggestion_id']}/cancel",
            f"/orders/{order['order_id']}/cancel",
        ]:
            r = await client.post(
                base + path,
                json=confirmation().model_dump(mode="json")
                if path.endswith("confirm")
                else None,
            )
            assert r.status_code == 404
        assert (
            await client.post(base + "/quotes", json=quote().model_dump(mode="json"))
        ).status_code == 404
        invalid = await client.post(
            base + "/suggestions", json={**req.model_dump(mode="json"), "user_id": user}
        )
        assert invalid.status_code == 422
        app.dependency_overrides[get_current_user_id] = lambda: user
        response = await client.get(base + "/ledger")
        assert response.headers["cache-control"] == "no-store"
        page = (await client.get(base + "/events?limit=1")).json()
        next_page = (
            await client.get(base + f"/events?limit=1&before={page['next_cursor']}")
        ).json()
        assert page["events"][0]["event_id"] > next_page["events"][0]["event_id"]
        assert (await client.get(base + "/events?limit=10000")).status_code == 422
        details = await client.get(base + f"/orders/{order['order_id']}")
        assert (
            details.status_code == 200
            and details.json()["result"]["reason"] == "虚构研究理由"
        )
        app.dependency_overrides[get_current_user_id] = lambda: other
        assert (
            await client.get(base + f"/orders/{order['order_id']}")
        ).status_code == 404


async def test_future_opening_and_invalid_expiry_cannot_create_order(
    seed_user, patched_get_db_connection, clock
):
    user = seed_user["user_id"]
    await initialize(user, opening_date="2026-10-15")
    for expires in [NOW, NOW + timedelta(days=8)]:
        with pytest.raises(service.SimulationError) as exc:
            await service.suggest(user, request(expires_at=expires.isoformat()))
        assert exc.value.code == "INVALID_EXPIRY"
    card = await service.suggest(user, request())
    with pytest.raises(service.SimulationError) as exc:
        await service.confirm(user, UUID(card["suggestion_id"]), confirmation())
    assert exc.value.code == "OPENING_IN_FUTURE"


@pytest.mark.parametrize(
    "changes",
    [
        {"calendar_verified": False},
        {"suspended": True},
        {"lower_limit": None},
        {"as_of": NOW - timedelta(seconds=1), "fetched_at": NOW},
        {"as_of": NOW + timedelta(seconds=3), "fetched_at": NOW + timedelta(seconds=3)},
        {"price": "1.00"},
    ],
)
async def test_blocked_quotes_do_not_write_a_fill_or_cash(
    seed_user, patched_get_db_connection, clock, changes
):
    user = seed_user["user_id"]
    await initialize(user)
    _, order = await confirmed(user)
    clock[0] += timedelta(seconds=2)
    assert (
        await service.settle(
            user, UUID(order["order_id"]), quote(**changes), allow_test=True
        )
        is None
    )
    ledger = await service.get_ledger(user)
    assert (
        not ledger["fills"] and ledger["cash"] == "1000.00" and ledger["version"] == 2
    )
    assert ledger["orders"][0]["status"] == "pending"


async def test_cancel_and_fill_race_has_one_terminal_outcome(
    seed_user, patched_get_db_connection, clock
):
    user = seed_user["user_id"]
    await initialize(user)
    _, order = await confirmed(user)
    clock[0] += timedelta(seconds=2)
    await asyncio.gather(
        service.cancel_order(user, UUID(order["order_id"])),
        service.settle(user, UUID(order["order_id"]), quote(), allow_test=True),
        return_exceptions=True,
    )
    ledger = await service.get_ledger(user)
    status = ledger["orders"][0]["status"]
    assert status in {"cancelled", "filled"}
    assert len(ledger["fills"]) == (1 if status == "filled" else 0)
    assert ledger["cash"] == ("895.00" if status == "filled" else "1000.00")
    assert ledger["reserved_cash"] == "0.00"


async def test_pending_records_remain_actionable_after_100_newer_terminal_records(
    seed_user, patched_get_db_connection, clock
):
    user = seed_user["user_id"]
    await initialize(user)
    _, old_order = await confirmed(user)
    old_card = await service.suggest(user, request(2))
    for _ in range(101):
        clock[0] += timedelta(seconds=1)
        _, newer = await confirmed(user)
        await service.cancel_order(user, UUID(newer["order_id"]))

    ledger = await service.get_ledger(user)
    assert old_order["order_id"] in {o["order_id"] for o in ledger["orders"]}
    assert old_card["suggestion_id"] in {
        s["suggestion_id"] for s in ledger["suggestions"]
    }
    assert sum(o["status"] != "pending" for o in ledger["orders"]) == 100
    assert ledger["reserved_cash"] == "105.00"
    await service.cancel_order(user, UUID(old_order["order_id"]))
    await service.cancel_suggestion(user, UUID(old_card["suggestion_id"]))
    assert (await service.get_ledger(user))["reserved_cash"] == "0.00"


async def test_unauthed_user_cannot_access_any_financial_path(monkeypatch):
    monkeypatch.setattr("src.server.utils.api.HOST_MODE", "platform")
    app = FastAPI()
    app.include_router(router)
    base = "/api/v1/users/me/simulation"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path in ["/ledger", "/events", f"/orders/{uuid4()}"]:
            assert (await client.get(base + path)).status_code == 401
        assert (
            await client.post(
                base + "/suggestions", json=request().model_dump(mode="json")
            )
        ).status_code == 401
