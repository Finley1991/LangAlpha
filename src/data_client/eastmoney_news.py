"""Bounded Eastmoney stock-news reads, following the public AKShare endpoint."""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from src.data_client.sina import normalize_ts_code

ENDPOINT = "https://search-api-web.eastmoney.com/search/jsonp"
REFERENCE = "https://so.eastmoney.com/news/s"
MAX_RESPONSE_BYTES = 262_144
MAX_ROWS = 10
CHINA = ZoneInfo("Asia/Shanghai")
_CALLBACK = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]{0,80}")
_ARTICLE_ID = re.compile(r"[0-9]{10,32}")
_TAG = re.compile(r"<[^>]{0,200}>")


class EastmoneyNewsError(Exception):
    """A stable, sanitized failure category from the public news endpoint."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError
    cleaned = html.unescape(_TAG.sub("", value)).replace("\u3000", " ").strip()
    return cleaned


def _published(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=CHINA)
        return parsed.astimezone(CHINA).isoformat()
    except ValueError:
        raise ValueError from None


def parse_stock_news(raw: bytes, ts_code: str) -> list[dict]:
    """Parse only attributed news metadata and short provider snippets."""
    normalize_ts_code(ts_code)
    if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
        raise EastmoneyNewsError("response_too_large")
    try:
        text = raw.decode("utf-8")
        match = re.fullmatch(r"\s*([A-Za-z_$][A-Za-z0-9_$]{0,80})\((.*)\);?\s*", text, re.S)
        if not match or not _CALLBACK.fullmatch(match[1]):
            raise ValueError
        payload = json.loads(match[2])
        records = payload["result"]["cmsArticleWebOld"]
        if not isinstance(records, list) or len(records) > MAX_ROWS:
            raise ValueError
        output = []
        for record in records:
            if not isinstance(record, dict):
                raise ValueError
            article_id = record.get("code")
            if not isinstance(article_id, str) or not _ARTICLE_ID.fullmatch(article_id):
                raise ValueError
            title = _text(record.get("title"), 300)
            if not title:
                raise ValueError
            summary = _text(record.get("content", ""), 1200)
            publisher = _text(record.get("mediaName", ""), 120)
            output.append(
                {
                    "id": article_id,
                    "title": title,
                    "summary": summary or None,
                    "published_at": _published(record.get("date")),
                    "publisher": publisher or None,
                    "url": f"https://finance.eastmoney.com/a/{article_id}.html",
                    "category": "news",
                }
            )
        return output
    except EastmoneyNewsError:
        raise
    except (UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise EastmoneyNewsError("invalid_response") from None


class EastmoneyStockNewsClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=6, trust_env=False, follow_redirects=False
        )

    async def __aenter__(self) -> EastmoneyStockNewsClient:
        return self

    async def __aexit__(self, *_args) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def get_stock_news(self, ts_code: str) -> list[dict]:
        ts_code = normalize_ts_code(ts_code)
        callback = "jQuery_codex_" + hashlib.sha256(
            f"{ts_code}:{time.time_ns()}".encode()
        ).hexdigest()[:24]
        inner = {
            "uid": "",
            "keyword": ts_code.split(".")[0],
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": MAX_ROWS,
                    "preTag": "<em>",
                    "postTag": "</em>",
                }
            },
        }
        params = {
            "cb": callback,
            "param": json.dumps(inner, separators=(",", ":"), ensure_ascii=False),
            "_": str(time.time_ns() // 1_000_000),
        }
        try:
            async with asyncio.timeout(8):
                async with self._client.stream(
                    "GET",
                    ENDPOINT,
                    params=params,
                    headers={
                        "accept": "*/*",
                        "referer": f"{REFERENCE}?keyword={ts_code.split('.')[0]}",
                        "user-agent": "Mozilla/5.0 (compatible; LangAlphaLocalReport/1.0)",
                    },
                    follow_redirects=False,
                ) as response:
                    if response.status_code != 200:
                        raise EastmoneyNewsError("http_error")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_RESPONSE_BYTES:
                            raise EastmoneyNewsError("response_too_large")
            return parse_stock_news(bytes(raw), ts_code)
        except EastmoneyNewsError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise EastmoneyNewsError("timeout") from None
        except httpx.HTTPError:
            raise EastmoneyNewsError("transport_error") from None
