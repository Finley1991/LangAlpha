"""Bounded, metadata-only reads from Sina's public announcement and research APIs."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from typing import Any

import httpx

from src.data_client.sina import normalize_ts_code, to_sina_symbol

ANNOUNCEMENT_ENDPOINT = (
    "https://money.finance.sina.com.cn/corp/api/openapi.php/"
    "CB_AllService.getBulletinList"
)
RESEARCH_ENDPOINT = (
    "https://stock.finance.sina.com.cn/stock/api/openapi.php/ReportService.getList"
)
MAX_RESPONSE_BYTES = 262_144
MAX_ROWS = 20


class SinaResearchError(Exception):
    def __init__(self, code: str, provider_code: int | None = None):
        super().__init__(code)
        self.code = code
        self.provider_code = provider_code


def _text(value: Any, limit: int = 500) -> str | None:
    if value is None:
        return None
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError
    text = str(value)
    if len(text) > limit:
        raise ValueError
    return text or None


def _date(value: Any, limit: int = 32) -> str | None:
    text = _text(value, limit)
    if text is None:
        return None
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        raise ValueError from None


def _rows(raw: bytes, *, expected_symbol: str) -> tuple[dict, list[dict]]:
    if len(raw) > MAX_RESPONSE_BYTES:
        raise SinaResearchError("response_too_large")
    try:
        result = json.loads(raw)
        status = result["result"]["status"]
        data = result["result"]["data"]
        if type(status["code"]) is not int:
            raise ValueError
        if status["code"] != 0:
            raise SinaResearchError("provider_error", status["code"])
        records = data["data"]
        if not isinstance(records, list) or len(records) > MAX_ROWS:
            raise ValueError
        if any(not isinstance(row, dict) for row in records):
            raise ValueError
        if expected_symbol.startswith(("sh", "sz", "bj")):
            more = data.get("more") or {}
            if more.get("SYMBOL") != expected_symbol:
                raise SinaResearchError("instrument_mismatch")
            if any(row.get("symbol") != expected_symbol for row in records):
                raise SinaResearchError("instrument_mismatch")
        else:
            other = data.get("otherinfo") or {}
            if other.get("PaperCode") != expected_symbol:
                raise SinaResearchError("instrument_mismatch")
            for row in records:
                code = row.get("PaperCode")
                if code is not None and code != expected_symbol:
                    raise SinaResearchError("instrument_mismatch")
        return data, records
    except SinaResearchError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise SinaResearchError("invalid_response") from None


def parse_announcements(raw: bytes, ts_code: str, fetched_at: datetime) -> list[dict]:
    del (
        fetched_at
    )  # Retained in the call contract; timestamps belong to frozen sources.
    symbol = normalize_ts_code(ts_code).split(".")[0]
    _, records = _rows(raw, expected_symbol=symbol)
    try:
        output = []
        for row in records:
            ann_date = _date(row.get("date"))
            output.append(
                {
                    "id": _text(row.get("ID"), 80),
                    "paper_code": symbol,
                    "ann_date": ann_date,
                    "end_date": _text(row.get("EndDate"), 16),
                    # In the supplied dictionary response `type` is ANNTYPE and
                    # `type2` is LEVEL1; API query filters use SubType2/ANNTYPE
                    # and SubType/LEVEL1 respectively.
                    "ann_type": _text(row.get("type"), 80),
                    "event_category": _text(row.get("type2"), 250),
                    "title": _text(row.get("title"), 300),
                    "url": _text(row.get("url"), 2048),
                    "pdf_path": _text(row.get("pdf_path"), 2048),
                }
            )
        return output
    except (TypeError, ValueError):
        raise SinaResearchError("invalid_response") from None


def parse_research_reports(
    raw: bytes, ts_code: str, fetched_at: datetime
) -> list[dict]:
    del fetched_at
    expected_symbol = to_sina_symbol(ts_code)
    data, records = _rows(raw, expected_symbol=expected_symbol)
    if data.get("total") is not None:
        try:
            total = int(data["total"])
            if total < len(records) or total > 10_000_000:
                raise ValueError
        except (TypeError, ValueError):
            raise SinaResearchError("invalid_response") from None
    try:
        return [
            {
                "id": _text(row.get("id"), 80),
                "title": _text(row.get("title"), 300),
                "institution": _text(row.get("orgnamelong") or row.get("orgname"), 200),
                "authors": _text(row.get("author"), 200),
                "published_date": _date(row.get("adddate")),
                "report_type": _text(row.get("rpt_name"), 80),
                "rating": _text(row.get("rating"), 80),
                "target_price": _text(row.get("goal_price"), 32),
                "symbol": expected_symbol,
                "url": _text(row.get("detail_url"), 2048),
            }
            for row in records
        ]
    except (TypeError, ValueError):
        raise SinaResearchError("invalid_response") from None


class SinaResearchClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=5, trust_env=False, follow_redirects=False
        )

    async def __aenter__(self) -> SinaResearchClient:
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def get_announcements(
        self, ts_code: str, start_date: str, end_date: str
    ) -> tuple[bytes, dict]:
        symbol = normalize_ts_code(ts_code).split(".")[0]
        params = {
            "Display": "20",
            "Page": "1",
            "Type": "recommend_ai",
            "PaperCode": symbol,
            "StartDate": start_date,
            "EndDate": end_date,
        }
        return await self._read(ANNOUNCEMENT_ENDPOINT, params), params

    async def get_research_reports(
        self, ts_code: str, start_date: str, end_date: str
    ) -> tuple[bytes, dict]:
        ts_code = normalize_ts_code(ts_code)
        params = {
            "kind": "lastest",
            "t1": "2",
            "num": "10",
            "page": "1",
            "symbol": to_sina_symbol(ts_code),
            "start_date": f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}",
            "end_date": f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}",
            # Reports are used as attributed opinions; full bodies are neither
            # needed in the frozen evidence nor retained by this product.
            "get_content_flag": "0",
        }
        return await self._read(RESEARCH_ENDPOINT, params), params

    async def _read(self, url: str, params: dict[str, str]) -> bytes:
        try:
            async with asyncio.timeout(8):
                async with self._client.stream(
                    "GET", url, params=params, follow_redirects=False
                ) as response:
                    if response.status_code != 200:
                        raise SinaResearchError("http_error", response.status_code)
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_RESPONSE_BYTES:
                            raise SinaResearchError("response_too_large")
            return bytes(raw)
        except SinaResearchError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise SinaResearchError("timeout") from None
        except httpx.HTTPError:
            raise SinaResearchError("transport_error") from None
