from copy import deepcopy
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.server.models.simulation import OpeningRequest
from src.server.services.simulation import canonical_opening
from src.server.services.simulation_import import (
    OpeningDataMismatch,
    adapt_private_opening,
)


def opening():
    return {
        "opening_date": "2026-10-13",
        "initial_cash": "1000.00",
        "cost_includes_fees": False,
        "holdings": [
            {
                "code": "000001",
                "exchange": "SZ",
                "name": "虚构测试持仓",
                "quantity": 100,
                "total_cost": "1234.5678",
                "purchased_on": "2026-10-12",
            }
        ],
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("initial_cash", 1000.0),
        ("initial_cash", "-1"),
        ("initial_cash", "0.001"),
        ("initial_cash", "NaN"),
        ("cost_includes_fees", "false"),
        ("opening_date", "2026-10-01"),
        ("user_id", "another-user"),
    ],
)
def test_rejects_invalid_opening(field, value):
    with pytest.raises(ValidationError):
        OpeningRequest.model_validate({**opening(), field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", True),
        ("quantity", 100.0),
        ("quantity", 0),
        ("total_cost", 12.3),
        ("total_cost", "0.123456789"),
        ("code", "1"),
        ("exchange", "NASDAQ"),
    ],
)
def test_rejects_invalid_holding(field, value):
    data = opening()
    data["holdings"][0][field] = value
    with pytest.raises(ValidationError):
        OpeningRequest.model_validate(data)


def test_retry_identity_ignores_row_order_and_decimal_zeroes():
    data = opening()
    data["holdings"].append({**data["holdings"][0], "code": "000002"})
    replay = deepcopy(data)
    replay["initial_cash"] = "1000"
    replay["holdings"].reverse()
    replay["holdings"][0]["total_cost"] = "1234.56780000"
    assert canonical_opening(OpeningRequest.model_validate(data)) == canonical_opening(
        OpeningRequest.model_validate(replay)
    )


def private_opening():
    return {
        "schema_version": 1,
        "record_type": "opening_snapshot",
        "account_type": "simulation",
        "currency": "CNY",
        "effective_date": "2026-10-13",
        "opening_cash": "1000.00",
        "position_cost_total": "30.001",
        "cash_plus_position_cost": "1030.001",
        "historical_fees_included": False,
        "historical_fees_amount": None,
        "positions": [
            {
                "code": "000001",
                "name": "虚构已知批次",
                "exchange": "SZSE",
                "quantity": 2,
                "cost_total": "20.001",
                "user_reported_purchases": [
                    {"date": "2026-10-11", "quantity": 1, "price": "10.001"},
                    {"date": "2026-10-12", "quantity": 1, "price": "10"},
                ],
            },
            {
                "code": "600001",
                "name": "虚构汇总持仓",
                "exchange": "SSE",
                "quantity": 2,
                "cost_total": "10",
                "user_reported_acquisition_dates": ["2026-10-10", "2026-10-12"],
            },
        ],
    }


def test_import_preserves_exact_cost_and_unknown_allocations():
    request = adapt_private_opening(private_opening())
    assert sum(row.total_cost for row in request.holdings) == Decimal("30.001")
    assert len(request.holdings) == 3
    aggregate = request.holdings[-1]
    assert aggregate.quantity == 2 and not aggregate.batch_details_known
    assert len(aggregate.acquisition_dates) == 2
    assert str(aggregate.purchased_on) == "2026-10-10"


@pytest.mark.parametrize(
    "change",
    ["cash", "cost", "batch_quantity", "batch_price", "missing_date", "future_date"],
)
def test_import_stops_on_inconsistent_source(change):
    data = private_opening()
    if change == "cash":
        data["opening_cash"] = "1001"
    if change == "cost":
        data["position_cost_total"] = "31"
    if change == "batch_quantity":
        data["positions"][0]["user_reported_purchases"][0]["quantity"] = 2
    if change == "batch_price":
        data["positions"][0]["user_reported_purchases"][0]["price"] = "11"
    if change == "missing_date":
        data["positions"][1]["user_reported_acquisition_dates"] = []
    if change == "future_date":
        data["positions"][1]["user_reported_acquisition_dates"].append("2026-10-14")
    with pytest.raises((OpeningDataMismatch, ValidationError)):
        adapt_private_opening(data)


@pytest.mark.parametrize(
    "change", ["numeric_price", "numeric_cash", "boolean_quantity", "real_account"]
)
def test_import_rejects_coercion_or_non_simulation_data(change):
    data = private_opening()
    if change == "numeric_price":
        data["positions"][0]["user_reported_purchases"][0]["price"] = 10.001
    if change == "numeric_cash":
        data["opening_cash"] = 1000.0
    if change == "boolean_quantity":
        data["positions"][0]["quantity"] = True
    if change == "real_account":
        data["account_type"] = "not_simulation"
    with pytest.raises((OpeningDataMismatch, ValidationError)):
        adapt_private_opening(data)


def test_import_cli_does_not_echo_invalid_input(tmp_path, monkeypatch, capsys):
    import json
    from scripts.utils.import_simulation_opening import main

    data = private_opening()
    data["positions"][0]["name"] = "PRIVATE_SENTINEL"
    data["positions"][0]["quantity"] = "PRIVATE_SENTINEL"
    source = tmp_path / "opening.json"
    source.write_text(json.dumps(data))
    monkeypatch.setattr("sys.argv", ["import", str(source)])
    assert main() == 2
    assert "PRIVATE_SENTINEL" not in capsys.readouterr().out


def test_import_cli_refuses_external_destination_before_reading(monkeypatch, capsys):
    from scripts.utils.import_simulation_opening import main

    monkeypatch.setattr(
        "sys.argv",
        ["import", "/does/not/exist", "--api-url", "https://example.com", "--apply"],
    )
    assert main() == 2
    assert "回环" in capsys.readouterr().out
