"""Sina CN quote transport; public symbols use Tushare spelling, source stays Sina."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal
from zoneinfo import ZoneInfo

import httpx

ENDPOINT = "https://base.finance.sina.cn/stock/getHqInfo"
CHINA = ZoneInfo("Asia/Shanghai")
MAX_RESPONSE_BYTES = 65_536
_TS_CODE = re.compile(r"[0-9]{6}\.(SH|SZ|BJ)")
_SINA_SYMBOL = re.compile(r"(sh|sz|bj)[0-9]{6}")


def normalize_ts_code(value: str) -> str:
    if not isinstance(value, str) or not _TS_CODE.fullmatch(value.strip().upper()):
        raise ValueError("Expected a six-digit Tushare code with .SH, .SZ or .BJ")
    return value.strip().upper()


def to_sina_symbol(ts_code: str) -> str:
    code, exchange = normalize_ts_code(ts_code).split(".")
    return exchange.lower() + code


def from_sina_symbol(symbol: str) -> str:
    if not isinstance(symbol, str) or not _SINA_SYMBOL.fullmatch(symbol):
        raise ValueError("Invalid Sina CN symbol")
    return symbol[2:] + "." + symbol[:2].upper()


class SinaQuoteError(Exception):
    def __init__(self, code: str, provider_code: int | None = None):
        super().__init__(code)
        self.code = code
        self.provider_code = provider_code


@dataclass(frozen=True, slots=True)
class SinaQuote:
    ts_code: str
    price: Decimal
    as_of: datetime
    fetched_at: datetime
    provider_updated_at: datetime | None
    stop_status: str | None
    evidence_id: str

    def freshness(
        self, now: datetime | None = None
    ) -> Literal["fresh", "stale", "future"]:
        now = now or datetime.now(CHINA)
        _aware(now)
        if self.as_of > self.fetched_at or self.fetched_at > now:
            return "future"
        if now - self.as_of > timedelta(seconds=60) or (
            self.as_of.astimezone(CHINA).date() != now.astimezone(CHINA).date()
        ):
            return "stale"
        return "fresh"

    def as_dict(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(CHINA)
        return {
            "ts_code": self.ts_code,
            "price": format(self.price, ".2f"),
            "currency": "CNY",
            "source": "sina",
            "source_url": ENDPOINT,
            "as_of": self.as_of.isoformat(),
            "fetched_at": self.fetched_at.isoformat(),
            "provider_updated_at": self.provider_updated_at.isoformat()
            if self.provider_updated_at
            else None,
            "stop_status": self.stop_status,
            "freshness": self.freshness(now),
            "freshness_as_of": now.astimezone(CHINA).isoformat(),
            "evidence_id": self.evidence_id,
        }


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Quote times must include a timezone")


def _timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}", value
    ):
        raise ValueError
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CHINA)


def parse_quote(raw: bytes, ts_code: str, fetched_at: datetime) -> SinaQuote:
    ts_code = normalize_ts_code(ts_code)
    _aware(fetched_at)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise SinaQuoteError("response_too_large")
    try:
        payload = json.loads(raw, parse_float=Decimal)
        if not isinstance(payload, dict) or type(payload.get("code")) is not int:
            raise ValueError
        if payload["code"] != 0:
            raise SinaQuoteError("provider_error", payload["code"])
        data = payload["data"]
        if not isinstance(data, dict):
            raise ValueError
        if data.get("hqMarket") != "cn" or from_sina_symbol(data["symbol"]) != ts_code:
            raise SinaQuoteError("instrument_mismatch")
        price = data["price"]
        if isinstance(price, bool) or not isinstance(price, (Decimal, int, str)):
            raise ValueError
        price = Decimal(price)
        if (
            not price.is_finite()
            or not 0 < price <= 1_000_000
            or price != price.quantize(Decimal("0.01"))
        ):
            raise ValueError
        quote_date, quote_time = data["hqDate"], data["hqTime"]
        if not isinstance(quote_date, str) or not isinstance(quote_time, str):
            raise ValueError
        as_of = _timestamp(quote_date + " " + quote_time)
        # uptime is a provider update time, never a replacement for the quote time.
        updated = _timestamp(data["uptime"]) if data.get("uptime") else None
        stop_status = data.get("stopStatus")
        if stop_status is not None and (
            not isinstance(stop_status, str)
            or not re.fullmatch(r"[0-9A-Za-z]{1,16}", stop_status)
        ):
            raise ValueError
        return SinaQuote(
            ts_code=ts_code,
            price=price,
            as_of=as_of,
            fetched_at=fetched_at.astimezone(CHINA),
            provider_updated_at=updated,
            stop_status=stop_status,
            evidence_id="sha256:" + hashlib.sha256(raw).hexdigest(),
        )
    except (ValueError, KeyError, TypeError, InvalidOperation, OverflowError):
        raise SinaQuoteError("invalid_quote") from None


class SinaQuoteClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=10, trust_env=False, follow_redirects=False
        )

    async def __aenter__(self) -> SinaQuoteClient:
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def get_quote(self, ts_code: str) -> SinaQuote:
        ts_code = normalize_ts_code(ts_code)
        try:
            async with asyncio.timeout(15):
                async with self._client.stream(
                    "GET",
                    ENDPOINT,
                    params={"market": "cn", "symbol": to_sina_symbol(ts_code)},
                    follow_redirects=False,
                ) as response:
                    if response.status_code != 200:
                        raise SinaQuoteError("http_error", response.status_code)
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_RESPONSE_BYTES:
                            raise SinaQuoteError("response_too_large")
            fetched_at = datetime.now(CHINA)
            return parse_quote(bytes(raw), ts_code, fetched_at)
        except (TimeoutError, httpx.TimeoutException):
            raise SinaQuoteError("timeout") from None
        except httpx.HTTPError:
            raise SinaQuoteError("transport_error") from None
