"""Collect bounded per-position stock news evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from src.config import settings
from src.data_client.eastmoney_news import (
    ENDPOINT as EASTMONEY_ENDPOINT,
    EastmoneyNewsError,
    EastmoneyStockNewsClient,
)
from src.data_client.sina_stock_news import (
    LIST_ENDPOINT as SINA_STOCK_NEWS_ENDPOINT,
    SinaStockNewsClient,
    SinaStockNewsError,
)
from src.data_client.sina import CHINA, normalize_ts_code

MAX_POSITIONS = 20
MAX_CONCURRENT_REQUESTS = 4
MAX_ITEMS_PER_CATEGORY = 5
TOTAL_TIMEOUT_SECONDS = 25


def _digest(value: dict) -> str:
    wire = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(wire.encode()).hexdigest()


def _source(kind: str, title: str, reference: str, evidence: dict, fetched_at: datetime, cutoff: datetime) -> dict:
    rows = evidence.get("rows", [])
    dates = [row.get("published_at") for row in rows if row.get("published_at")]
    parsed = []
    for value in dates:
        try:
            parsed.append(datetime.fromisoformat(value))
        except (TypeError, ValueError):
            continue
    as_of = max(parsed).isoformat() if parsed else cutoff.astimezone(CHINA).isoformat()
    return {
        "source_id": f"{kind}-" + _digest(evidence),
        "kind": kind,
        "title": title,
        "reference": reference,
        "as_of": as_of,
        "fetched_at": fetched_at.astimezone(CHINA).isoformat(),
        "evidence": evidence,
    }


def _position_code(position: dict) -> str:
    return normalize_ts_code(position["code"] + "." + position["exchange"])


async def collect(positions: list[dict], cutoff: datetime) -> dict:
    empty = {"enabled": settings.is_simulation_news_enabled() and bool(positions), "items": {}, "sources": [], "issues": {}}
    if not empty["enabled"]:
        return empty
    if len(positions) > MAX_POSITIONS:
        return {**empty, "issues": {"all": ["持仓数量超出新闻搜索上限"]}}

    result = {**empty, "enabled": True}
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    async def fetch_one(client: EastmoneyStockNewsClient, position: dict) -> None:
        try:
            code = _position_code(position)
        except (KeyError, TypeError, ValueError):
            return
        result["items"].setdefault(code, [])
        try:
            async with semaphore:
                rows = await client.get_stock_news(code)
            now = datetime.now(CHINA)
            evidence = {
                "provider": "eastmoney",
                "api": "stock_news_em",
                "params": {"symbol": code.split(".")[0], "page_size": "10"},
                "retained_limit": MAX_ITEMS_PER_CATEGORY,
                "rows": rows[:MAX_ITEMS_PER_CATEGORY],
            }
            if rows:
                source = _source("stock_news", "东方财富个股新闻", EASTMONEY_ENDPOINT, evidence, now, cutoff)
                result["sources"].append(source)
                result["items"][code].extend(
                    {**row, "source_ids": [source["source_id"]]}
                    for row in rows[:MAX_ITEMS_PER_CATEGORY]
                )
            else:
                result["issues"].setdefault(code, []).append("东方财富个股新闻本次未返回记录；不代表没有相关新闻")
        except EastmoneyNewsError as exc:
            result["issues"].setdefault(code, []).append("东方财富个股新闻查询：" + exc.code)
        except Exception:
            result["issues"].setdefault(code, []).append("东方财富个股新闻接口暂不可用")

        try:
            async with semaphore:
                sina_rows = await sina_client.get_stock_news(code, detail_limit=3)
            now = datetime.now(CHINA)
            if sina_rows:
                evidence = {
                    "provider": "sina",
                    "api": "stock_news/get + article.d.json",
                    "params": {
                        "market": "cn",
                        "symbol": code.split(".")[1].lower() + code.split(".")[0],
                        "num": "10",
                        "page": "1",
                        "detail_limit": 3,
                    },
                    "retained_limit": MAX_ITEMS_PER_CATEGORY,
                    "rows": sina_rows[:MAX_ITEMS_PER_CATEGORY],
                }
                source = _source(
                    "stock_news", "新浪财经个股新闻", SINA_STOCK_NEWS_ENDPOINT,
                    evidence, now, cutoff
                )
                result["sources"].append(source)
                result["items"][code].extend(
                    {**row, "source_ids": [source["source_id"]]}
                    for row in sina_rows[:MAX_ITEMS_PER_CATEGORY]
                )
            else:
                result["issues"].setdefault(code, []).append("新浪财经个股新闻本次未返回可核对记录；不代表没有相关新闻")
        except SinaStockNewsError as exc:
            result["issues"].setdefault(code, []).append("新浪财经个股新闻查询：" + exc.code)
        except Exception:
            result["issues"].setdefault(code, []).append("新浪财经个股新闻接口暂不可用")

        if not result["items"][code]:
            result["items"].pop(code)

    try:
        async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
            async with EastmoneyStockNewsClient() as client, SinaStockNewsClient() as sina_client:
                await asyncio.gather(*(fetch_one(client, p) for p in positions))
    except TimeoutError:
        result["issues"].setdefault("all", []).append("个股新闻查询超时，未完成项保留待核实")
    return result
