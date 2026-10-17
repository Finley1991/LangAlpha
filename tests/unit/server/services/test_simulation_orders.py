from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.server.models.simulation_orders import (
    ConfirmationRequest,
    SuggestionRequest,
    TrustedQuote,
)
from src.server.services import simulation_orders as service

NOW = datetime(2026, 10, 14, 2, 0, 2, tzinfo=timezone.utc)


def payload(**changes):
    return {
        "code": "600000",
        "exchange": "SH",
        "name": "虚构证券",
        "quantity": 100,
        "side": "buy",
        "limit_price": "10.00",
        "fees": deepcopy(service.FEES),
        "rule_version": service.RULE_VERSION,
        **changes,
    }


def quote(**changes):
    return TrustedQuote.model_validate(
        {
            "code": "600000",
            "exchange": "SH",
            "price": "9.98",
            "as_of": NOW - timedelta(seconds=1),
            "fetched_at": NOW,
            "source": "fictional-unit",
            "source_kind": "test",
            "evidence_id": "fictional-1",
            "instrument_verified": True,
            "calendar_verified": True,
            "trading_day": True,
            "suspended": False,
            "rule_version": service.RULE_VERSION,
            "lower_limit": "9",
            "upper_limit": "11",
            **changes,
        }
    )


def order(**changes):
    return {
        "status": "pending",
        "payload": payload(),
        "confirmed_at": NOW - timedelta(seconds=2),
        "expires_at": NOW + timedelta(hours=1),
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"instrument_verified": False},
        {"calendar_verified": False},
        {"trading_day": False},
        {"suspended": True},
        {"lower_limit": None},
        {"upper_limit": None},
        {"lower_limit": "11", "upper_limit": "9"},
        {"code": "600001"},
        {"exchange": "SZ"},
        {"price": "11.01"},
        {"price": "8.99"},
        {"price": "10.00"},
        {"as_of": NOW - timedelta(seconds=2)},
        {"as_of": NOW - timedelta(seconds=61)},
        {"as_of": NOW + timedelta(seconds=1)},
        {"fetched_at": NOW + timedelta(seconds=1)},
        {"fetched_at": NOW - timedelta(seconds=2)},
    ],
)
def test_unverified_stale_future_preconfirmation_or_limit_quotes_cannot_fill(changes):
    assert (
        service.executable_price(order(), quote(**changes), NOW, allow_test=True)
        is None
    )


@pytest.mark.parametrize("status", ["filled", "cancelled", "expired"])
def test_terminal_orders_never_execute(status):
    assert (
        service.executable_price(order(status=status), quote(), NOW, allow_test=True)
        is None
    )


@pytest.mark.parametrize(
    "hour,minute,weekday_offset",
    [(1, 29, 0), (4, 0, 0), (6, 57, 0), (7, 0, 0), (2, 0, 3)],
)
def test_outside_continuous_session_and_weekends_are_blocked(
    hour, minute, weekday_offset
):
    now = NOW.replace(hour=hour, minute=minute) + timedelta(days=weekday_offset)
    q = quote(as_of=now - timedelta(seconds=1), fetched_at=now)
    o = order(
        confirmed_at=now - timedelta(seconds=2), expires_at=now + timedelta(hours=1)
    )
    assert service.executable_price(o, q, now, allow_test=True) is None


def test_test_quotes_require_explicit_opt_in_and_adverse_slippage_stays_in_limit():
    assert service.executable_price(order(), quote(), NOW) is None
    assert service.executable_price(order(), quote(), NOW, allow_test=True) == Decimal(
        "9.99"
    )
    sell = order(payload=payload(side="sell", limit_price="9.97"))
    assert service.executable_price(sell, quote(), NOW, allow_test=True) == Decimal(
        "9.97"
    )
    assert (
        service.executable_price(order(expires_at=NOW), quote(), NOW, allow_test=True)
        is None
    )
    assert (
        service.executable_price(
            order(), quote(upper_limit="9.98"), NOW, allow_test=True
        )
        is None
    )
    assert (
        service.executable_price(sell, quote(lower_limit="9.98"), NOW, allow_test=True)
        is None
    )


def test_execution_after_session_close_cannot_use_a_quote_at_session_end():
    now = NOW.replace(hour=3, minute=30, second=1)
    q = quote(as_of=now - timedelta(seconds=1), fetched_at=now)
    o = order(
        confirmed_at=now - timedelta(seconds=2), expires_at=now + timedelta(hours=1)
    )
    assert service.executable_price(o, q, now, allow_test=True) is None


def test_unknown_opening_batches_use_latest_known_day_without_inventing_allocations():
    account = {
        "ledger_state": None,
        "opening_payload": {
            "initial_cash": "1000",
            "holdings": [
                {
                    "code": "600000",
                    "exchange": "SH",
                    "name": "虚构汇总",
                    "quantity": 200,
                    "total_cost": "1234.5678",
                    "purchased_on": "2026-10-12",
                    "acquisition_dates": ["2026-10-12", "2026-10-14"],
                    "batch_details_known": False,
                }
            ],
        },
    }
    state = service.initial_state(account)
    assert service.resources(state, [], "2026-10-14")[1] == {"600000.SH": 0}
    assert service.resources(state, [], "2026-10-15")[1] == {"600000.SH": 200}
    assert state["positions"][0]["total_cost"] == "1234.5678"
    assert account["opening_payload"]["holdings"][0]["quantity"] == 200


def test_precision_cost_allocation_and_final_sell_leave_no_residual():
    state = {
        "cash": "1000",
        "realized_pnl": "0",
        "positions": [
            {
                "code": "600000",
                "exchange": "SH",
                "name": "虚构证券",
                "quantity": 300,
                "total_cost": "1000.00000001",
                "lots": [{"quantity": 300, "acquired_on": "2026-10-12"}],
            }
        ],
    }
    removed = []
    for _ in range(3):
        fill, _ = service.apply_fill(
            state, payload(side="sell"), Decimal("10"), "2026-10-14"
        )
        removed.append(Decimal(fill["removed_cost"]))
    assert sum(removed) == Decimal("1000.00000001")
    assert not state["positions"]
    assert Decimal(state["cash"]) == Decimal("1000") + Decimal("994.49") * 3
    assert Decimal(state["realized_pnl"]) == Decimal("994.49") * 3 - Decimal(
        "1000.00000001"
    )


def test_buy_cost_includes_simulated_fees_and_is_unavailable_same_day():
    state = {"cash": "10000.00", "realized_pnl": "0", "positions": []}
    fill, _ = service.apply_fill(state, payload(), Decimal("9.99"), "2026-10-14")
    assert fill["cash_delta"] == "-1004.01"
    assert state["positions"][0]["total_cost"] == "1004.01000000"
    assert service.resources(state, [], "2026-10-14")[1]["600000.SH"] == 0
    assert service.resources(state, [], "2026-10-15")[1]["600000.SH"] == 100


@pytest.mark.parametrize(
    "changes",
    [
        {"limit_price": 1.0},
        {"limit_price": "NaN"},
        {"limit_price": "Infinity"},
        {"limit_price": "0"},
        {"limit_price": "1.001"},
        {"quantity": True},
        {"quantity": "100"},
        {"quantity": 0},
        {"quantity": 1_000_001},
        {"code": "６０００００"},
        {"user_id": "other"},
        {"expires_at": "2026-10-14T15:00:00"},
        {"reason": " "},
        {"fees": service.FEES},
    ],
)
def test_invalid_browser_requests_are_rejected(changes):
    request = {
        **payload(),
        "client_request_id": str(uuid4()),
        "account_version": 1,
        "reason": "测试",
        "counterargument": "虚构",
        "expires_at": NOW.isoformat(),
    }
    request.pop("fees")
    request.pop("rule_version")
    with pytest.raises(ValidationError):
        SuggestionRequest.model_validate({**request, **changes})


def test_confirmation_requires_strict_fields_and_timestamp_awareness():
    with pytest.raises(ValidationError):
        ConfirmationRequest.model_validate(
            {
                "suggestion_version": 1,
                "account_version": 1,
                "acknowledge_simulation": "true",
            }
        )
    with pytest.raises(ValidationError):
        quote(as_of=datetime(2026, 10, 14, 2))


@pytest.mark.parametrize(
    "changes",
    [
        {"code": "688001"},
        {"exchange": "SZ", "code": "300001"},
        {"code": "600000", "quantity": 150},
    ],
)
def test_uncovered_boards_and_buy_odd_lots_are_not_silently_mapped(changes):
    with pytest.raises(service.SimulationError):
        service.validate_instrument(payload(**changes))
