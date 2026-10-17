"""Collect bounded Sina quote snapshots for manual reports, never for trading."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime

from src.config import settings
from src.data_client.sina import (
    CHINA,
    SinaQuoteClient,
    SinaQuoteError,
    normalize_ts_code,
    to_sina_symbol,
)

MAX_POSITIONS = 20
MAX_CONCURRENT_REQUESTS = 1
MIN_REQUEST_INTERVAL_SECONDS = 1.3
REFERENCE = "https://base.finance.sina.cn/stock/getHqInfo"


def _source(quote: dict) -> dict:
    evidence = {
        "api": "getHqInfo",
        "params": {"market": "cn", "symbol": to_sina_symbol(quote["ts_code"])},
        "rows": [quote],
    }
    wire = json.dumps(
        evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return {
        "source_id": "sina-quote-" + hashlib.sha256(wire.encode()).hexdigest(),
        "kind": "live_quote",
        "title": "新浪实时行情快照",
        "reference": REFERENCE,
        "as_of": quote["as_of"],
        "fetched_at": quote["fetched_at"],
        "evidence": json.loads(wire),
    }


async def collect(positions: list[dict], cutoff: datetime) -> dict:
    result: dict = {"quotes": {}, "sources": [], "issues": {}}
    if not settings.is_simulation_realtime_quotes_enabled() or not positions:
        return result
    if len(positions) > MAX_POSITIONS:
        return {**result, "issues": {"all": "持仓数量超出实时行情检查上限"}}

    last_fetched: datetime | None = None
    try:
        async with asyncio.timeout(25):
            async with SinaQuoteClient() as client:
                for index, position in enumerate(positions):
                    code = normalize_ts_code(
                        position["code"] + "." + position["exchange"]
                    )
                    if index and last_fetched is not None:
                        delay = (
                            MIN_REQUEST_INTERVAL_SECONDS
                            - (datetime.now(CHINA) - last_fetched).total_seconds()
                        )
                        if delay > 0:
                            await asyncio.sleep(delay)
                    try:
                        quote = await client.get_quote(code)
                        data = quote.as_dict()
                        last_fetched = datetime.now(CHINA)
                        result["quotes"][code] = {
                            key: data[key]
                            for key in (
                                "price",
                                "currency",
                                "as_of",
                                "fetched_at",
                                "provider_updated_at",
                                "freshness",
                                "freshness_as_of",
                            )
                        }
                        source = _source(data)
                        result["sources"].append(source)
                        result["quotes"][code]["source_ids"] = [source["source_id"]]
                        if data["freshness"] != "fresh":
                            reason = (
                                "报价时间在未来，待核实"
                                if data["freshness"] == "future"
                                else "报价已过期，待核实"
                            )
                            result["issues"][code] = reason
                    except SinaQuoteError as exc:
                        result["issues"][code] = "行情查询：" + exc.code
    except TimeoutError:
        result["issues"]["all"] = "实时行情查询超时，未完成项保留待核实"
    return result
