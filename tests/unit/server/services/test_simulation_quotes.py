from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from src.data_client.sina import SinaQuoteError
from src.server.services import simulation_quotes

CHINA = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 16, 10, 0, tzinfo=CHINA)
POSITIONS = [
    {"code": "000001", "exchange": "SZ"},
    {"code": "600000", "exchange": "SH"},
]


class Quote:
    def __init__(self, ts_code: str, as_of: datetime):
        self.ts_code = ts_code
        self.price = Decimal("11.41")
        self.as_of = as_of
        self.fetched_at = NOW
        self.provider_updated_at = NOW
        self.stop_status = "00"
        self.evidence_id = "sha256:quote"

    def as_dict(self, now=None):
        return {
            "ts_code": self.ts_code,
            "price": format(self.price, ".2f"),
            "currency": "CNY",
            "source": "sina",
            "source_url": "https://base.finance.sina.cn/stock/getHqInfo",
            "as_of": self.as_of.isoformat(),
            "fetched_at": self.fetched_at.isoformat(),
            "provider_updated_at": self.provider_updated_at.isoformat(),
            "stop_status": self.stop_status,
            "freshness": "fresh",
            "freshness_as_of": (now or NOW).isoformat(),
            "evidence_id": self.evidence_id,
        }


@pytest.mark.asyncio
async def test_disabled_quote_collection_never_contacts_sina(monkeypatch):
    monkeypatch.delenv("SIMULATION_REALTIME_QUOTES_ENABLED", raising=False)
    client = AsyncMock(side_effect=AssertionError("must not query"))
    monkeypatch.setattr(simulation_quotes, "SinaQuoteClient", client)

    result = await simulation_quotes.collect(POSITIONS, NOW)

    assert result == {"quotes": {}, "sources": [], "issues": {}}
    client.assert_not_called()


@pytest.mark.asyncio
async def test_enabled_collection_freezes_quote_source_and_sanitizes_failures(
    monkeypatch,
):
    monkeypatch.setenv("SIMULATION_REALTIME_QUOTES_ENABLED", "1")

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get_quote(self, code):
            if code == "600000.SH":
                raise SinaQuoteError("provider_error", 503)
            return Quote(code, NOW - timedelta(seconds=20))

    monkeypatch.setattr(simulation_quotes, "SinaQuoteClient", Client)
    monkeypatch.setattr(simulation_quotes.asyncio, "sleep", AsyncMock())

    result = await simulation_quotes.collect(POSITIONS, NOW)

    assert result["quotes"]["000001.SZ"]["price"] == "11.41"
    assert result["quotes"]["000001.SZ"]["freshness"] == "fresh"
    assert result["issues"] == {"600000.SH": "行情查询：provider_error"}
    assert len(result["sources"]) == 1
    source = result["sources"][0]
    assert source["kind"] == "live_quote"
    assert source["evidence"]["rows"][0]["evidence_id"] == "sha256:quote"
    assert "503" not in str(result)


def test_report_content_separates_quote_snapshot_from_closing_price():
    from src.server.services.simulation_reports import content

    now = NOW
    position = {
        "code": "000001",
        "exchange": "SZ",
        "name": "虚构证券",
        "quantity": "100",
        "total_cost": "1000.00",
        "available_quantity": "100",
        "lots": [],
    }
    snapshot = {
        "account_id": "00000000-0000-0000-0000-000000000001",
        "version": 1,
        "as_of": NOW.isoformat(),
        "cash": "0.00",
        "available_cash": "0.00",
        "reserved_cash": "0.00",
        "realized_pnl": "0.00",
        "positions": [position],
        "sellable_basis": "虚构测试口径",
    }
    quote_source = {
        "source_id": "sina-quote-1",
        "kind": "live_quote",
        "title": "新浪实时行情快照",
        "reference": "https://base.finance.sina.cn/stock/getHqInfo",
        "as_of": NOW.isoformat(),
        "fetched_at": NOW.isoformat(),
        "evidence": {"params": {"symbol": "sz000001"}, "rows": []},
    }
    quotes = {
        "quotes": {
            "000001.SZ": {
                "price": "11.41",
                "currency": "CNY",
                "as_of": NOW.isoformat(),
                "fetched_at": NOW.isoformat(),
                "freshness": "fresh",
                "freshness_as_of": NOW.isoformat(),
                "source_ids": ["sina-quote-1"],
            }
        },
        "sources": [quote_source],
        "issues": {},
    }

    report = content(snapshot, now, "2020-01-01", quotes=quotes)

    check = report["checks"][0]
    assert check["current_quote"]["price"] == "11.41"
    assert check["closing_price"] is None
    assert "新浪实时行情快照" in check["fact"]
    assert "sina-quote-1" in check["source_ids"]
    assert "不用于成交" in report["scope"]
