import json
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest

from src.server.services import simulation_closing as c

NOW = datetime(2026, 10, 15, 14, 59, tzinfo=c.CHINA)


def calendar(now=NOW, exchange="SSE"):
    start = now.date() - timedelta(days=45)
    return [
        {
            "exchange": exchange,
            "cal_date": (start + timedelta(days=i)).strftime("%Y%m%d"),
            "is_open": int((start + timedelta(days=i)).weekday() < 5),
            "pretrade_date": "20261014",
        }
        for i in range(46)
    ]


def test_session_cutoff_weekend_and_timezone():
    assert c.latest_closed(calendar(), "SSE", NOW).isoformat() == "2026-10-14"
    assert (
        c.latest_closed(calendar(), "SSE", NOW.replace(hour=15)).isoformat()
        == "2026-10-15"
    )
    weekend = NOW.replace(day=18)
    assert (
        c.latest_closed(calendar(weekend), "SSE", weekend).isoformat() == "2026-10-16"
    )


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "exchange", "open", "future", "invalid_day"]
)
def test_incomplete_or_wrong_calendar_never_falls_back(mutation):
    rows = calendar()
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows[-1] = rows[-2]
    elif mutation == "exchange":
        rows[-1]["exchange"] = "SZSE"
    elif mutation == "open":
        rows[-1]["is_open"] = True
    elif mutation == "future":
        rows[-1]["cal_date"] = "20261016"
    else:
        rows[-1]["cal_date"] = "20260230"
    with pytest.raises(c.ClosingDataError):
        c.latest_closed(rows, "SSE", NOW)


@pytest.mark.parametrize(
    "value", [True, 1.1, "NaN", "Infinity", "0", "-1", "10.231", "1e999"]
)
def test_bad_price_is_not_used(value):
    with pytest.raises(c.ClosingDataError):
        c.closing_prices(
            [{"ts_code": "000001.SZ", "trade_date": "20261014", "close": value}],
            {"000001.SZ"},
            NOW.date().replace(day=14),
        )


def test_price_identity_date_duplicates_and_decimal():
    row = {"ts_code": "000001.SZ", "trade_date": "20261014", "close": Decimal("10.230")}
    day = NOW.date().replace(day=14)
    assert c.closing_prices([row], {"000001.SZ"}, day) == {"000001.SZ": "10.23"}
    assert c.closing_prices([], {"000001.SZ"}, day) == {}
    for rows in (
        [row, row],
        [{**row, "ts_code": "600000.SH"}],
        [{**row, "trade_date": "20261013"}],
    ):
        with pytest.raises(c.ClosingDataError):
            c.closing_prices(rows, {"000001.SZ"}, day)


@pytest.mark.asyncio
async def test_disabled_never_calls_provider(monkeypatch):
    monkeypatch.delenv("SIMULATION_CLOSING_ENABLED", raising=False)
    provider = AsyncMock(side_effect=AssertionError("must not query"))
    monkeypatch.setattr(c, "request_table", provider)
    assert await c.collect([{"code": "000001", "exchange": "SZ"}], NOW) == {
        "prices": {},
        "sources": [],
        "issues": {},
    }


@pytest.mark.asyncio
async def test_transport_freezes_rows_and_missing_today_does_not_query_yesterday(
    monkeypatch,
):
    monkeypatch.setenv("SIMULATION_CLOSING_ENABLED", "1")
    monkeypatch.setenv("TUSHARE_TOKEN", "fictional-token")
    seen = []

    async def no_pause(_):
        pass

    monkeypatch.setattr(c.asyncio, "sleep", no_pause)

    def respond(request):
        body = json.loads(request.content)
        seen.append(body)
        api = body["api_name"]
        rows = calendar(NOW, "SZSE") if api == "trade_cal" else []
        fields = body["fields"].split(",")
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "fields": fields,
                    "items": [[row[f] for f in fields] for row in rows],
                },
            },
        )

    factory = httpx.AsyncClient
    monkeypatch.setattr(
        c.httpx,
        "AsyncClient",
        lambda **kwargs: factory(transport=httpx.MockTransport(respond), **kwargs),
    )
    result = await c.collect(
        [{"code": "000001", "exchange": "SZ"}], NOW.replace(hour=16)
    )
    assert len(seen) == 2
    assert seen[-1]["params"] == {"ts_code": "000001.SZ", "trade_date": "20261015"}
    assert not result["prices"] and "不回退" in result["issues"]["000001.SZ"]
    assert len(result["sources"]) == 2
    assert "fictional-token" not in json.dumps(result)
