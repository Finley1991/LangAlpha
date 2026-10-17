"""Bounded reads from Sina's public stock-news list and article endpoints."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import httpx

from src.data_client.sina import normalize_ts_code, to_sina_symbol

LIST_ENDPOINT = "https://recommend.cj.sina.cn/app/v1/stock_news/get"
DETAIL_ENDPOINT = "https://interface.sina.cn/finance/article.d.json"
MAX_RESPONSE_BYTES = 524_288
MAX_LIST_ROWS = 10
MAX_SUMMARY_CHARS = 320
CHINA = ZoneInfo("Asia/Shanghai")
_DOC_ID = re.compile(r"comos:([A-Za-z0-9_-]{1,80})")
_SINA_HOSTS = {"finance.sina.com.cn", "finance.sina.cn", "cj.sina.cn"}


class SinaStockNewsError(Exception):
    """Stable, sanitized error category for the Sina news endpoints."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._ignored += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._ignored:
            self._ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored:
            self.parts.append(data)


def _decode(raw: bytes) -> object:
    if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
        raise SinaStockNewsError("response_too_large")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise SinaStockNewsError("invalid_response") from None


def _sina_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = httpx.URL(value)
        host = parsed.host.lower().rstrip(".") if parsed.host else ""
        if (
            parsed.scheme == "https"
            and host in _SINA_HOSTS
            and parsed.port in {None, 443}
            and not parsed.username
            and not parsed.password
        ):
            return str(parsed)
    except (httpx.InvalidURL, ValueError):
        pass
    return None


def _published(date: object, time: object) -> str | None:
    if not isinstance(date, str) or len(date) > 20:
        return None
    clock = time if isinstance(time, str) and len(time) <= 12 else "00:00:00"
    try:
        parsed = datetime.fromisoformat(f"{date} {clock}")
        return parsed.replace(tzinfo=CHINA).isoformat()
    except ValueError:
        return None


def parse_stock_news_list(raw: bytes, ts_code: str) -> list[dict]:
    normalize_ts_code(ts_code)
    payload = _decode(raw)
    try:
        result = payload["result"]
        status = result["status"]
        records = result["data"]
        if not isinstance(records, list) or len(records) > MAX_LIST_ROWS:
            raise ValueError
        if str(status["code"]) != "0":
            raise SinaStockNewsError("provider_error")
        output = []
        for row in records:
            if not isinstance(row, dict):
                continue
            match = _DOC_ID.fullmatch(row.get("docid", ""))
            url = _sina_url(row.get("url") or row.get("wapurl"))
            title = row.get("short_title") or row.get("title")
            if not match or not url or not isinstance(title, str) or not title.strip():
                continue
            if len(title) > 1000:
                continue
            publisher = row.get("media_source")
            output.append(
                {
                    "doc_id": match[1],
                    "title": title.strip()[:300],
                    "published_at": _published(
                        row.get("create_date"), row.get("create_time")
                    ),
                    "publisher": publisher.strip()[:120]
                    if isinstance(publisher, str) and publisher.strip()
                    else "新浪财经",
                    "url": url,
                    "category": "news",
                }
            )
        return output
    except SinaStockNewsError:
        raise
    except (KeyError, TypeError, ValueError):
        raise SinaStockNewsError("invalid_response") from None


def parse_article_detail(raw: bytes) -> str | None:
    payload = _decode(raw)
    try:
        content = payload.get("content") if isinstance(payload, dict) else None
        parts: list[str] = []
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content[:20]:
                if isinstance(block, dict):
                    value = block.get("content") or block.get("content_")
                    if isinstance(value, str):
                        parts.append(value)
        parser = _TextExtractor()
        parser.feed(" ".join(parts))
        text = " ".join(" ".join(parser.parts).split())
        return text[:MAX_SUMMARY_CHARS] or None
    except (AttributeError, TypeError, ValueError):
        raise SinaStockNewsError("invalid_response") from None


class SinaStockNewsClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=6, trust_env=False, follow_redirects=False
        )

    async def __aenter__(self) -> SinaStockNewsClient:
        return self

    async def __aexit__(self, *_args) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def _get_json(self, endpoint: str, params: dict[str, str]) -> bytes:
        try:
            async with asyncio.timeout(8):
                async with self._client.stream(
                    "GET",
                    endpoint,
                    params=params,
                    headers={
                        "accept": "application/json",
                        "user-agent": "Mozilla/5.0 (compatible; LangAlphaLocalReport/1.0)",
                    },
                ) as response:
                    if response.status_code != 200:
                        raise SinaStockNewsError("http_error")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_RESPONSE_BYTES:
                            raise SinaStockNewsError("response_too_large")
            return bytes(raw)
        except SinaStockNewsError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise SinaStockNewsError("timeout") from None
        except httpx.HTTPError:
            raise SinaStockNewsError("transport_error") from None

    async def get_stock_news(self, ts_code: str, detail_limit: int = 3) -> list[dict]:
        ts_code = normalize_ts_code(ts_code)
        if not 0 <= detail_limit <= 5:
            raise ValueError("detail_limit must be between 0 and 5")
        list_raw = await self._get_json(
            LIST_ENDPOINT,
            {
                "market": "cn",
                "symbol": to_sina_symbol(ts_code),
                "num": str(MAX_LIST_ROWS),
                "page": "1",
            },
        )
        rows = parse_stock_news_list(list_raw, ts_code)

        async def enrich(row: dict) -> dict:
            raw = await self._get_json(DETAIL_ENDPOINT, {"docID": row["doc_id"]})
            return {**row, "summary": parse_article_detail(raw)}

        detailed = await asyncio.gather(
            *(enrich(row) for row in rows[:detail_limit]), return_exceptions=True
        )
        summaries = {
            rows[index]["doc_id"]: value
            for index, value in enumerate(detailed)
            if isinstance(value, dict)
        }
        return [
            {
                "id": "sina-" + row["doc_id"],
                "title": row["title"],
                "summary": summaries.get(row["doc_id"], {}).get("summary"),
                "published_at": row["published_at"],
                "publisher": row["publisher"],
                "url": row["url"],
                "category": row["category"],
            }
            for row in rows[:MAX_LIST_ROWS]
        ]
