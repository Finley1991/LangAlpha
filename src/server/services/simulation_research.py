"""Freeze bounded public announcement and analyst-report metadata for a report."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime, timedelta

from src.config import settings
from src.data_client.sina import normalize_ts_code
from src.data_client.sina_research import (
    ANNOUNCEMENT_ENDPOINT,
    RESEARCH_ENDPOINT,
    SinaResearchClient,
    SinaResearchError,
    parse_announcements,
    parse_research_reports,
)
from src.server.services.simulation_orders import CHINA

MAX_POSITIONS = 20
MAX_CONCURRENT_REQUESTS = 4
ANNOUNCEMENT_REFERENCE = ANNOUNCEMENT_ENDPOINT
RESEARCH_REFERENCE = RESEARCH_ENDPOINT


def _validate_window(rows: list[dict], date_field: str, start: date, end: date) -> None:
    try:
        for row in rows:
            value = row.get(date_field)
            if value is not None and not start <= date.fromisoformat(value) <= end:
                raise ValueError
    except (TypeError, ValueError):
        raise SinaResearchError("date_out_of_range") from None


def _source(
    kind: str,
    api: str,
    reference: str,
    params: dict,
    rows: list[dict],
    fetched_at: datetime,
    cutoff: datetime,
) -> dict:
    evidence = {"api": api, "params": params, "rows": rows}
    wire = json.dumps(
        evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    dates = [
        row.get("ann_date") or row.get("published_date")
        for row in rows
        if row.get("ann_date") or row.get("published_date")
    ]
    as_of = max(dates) if dates else cutoff.astimezone(CHINA).isoformat()
    if dates:
        as_of = datetime.fromisoformat(as_of).replace(tzinfo=CHINA).isoformat()
    return {
        "source_id": "sina-" + hashlib.sha256(wire.encode()).hexdigest(),
        "kind": kind,
        "title": "新浪上市公司公告" if kind == "announcement" else "新浪机构研报元数据",
        "reference": reference,
        "as_of": as_of,
        "fetched_at": fetched_at.astimezone(CHINA).isoformat(),
        "evidence": json.loads(wire),
    }


async def collect(positions: list[dict], cutoff: datetime) -> dict:
    empty = {
        "enabled": settings.is_simulation_research_enabled() and bool(positions),
        "announcements": {},
        "research_reports": {},
        "sources": [],
        "issues": {},
    }
    if not empty["enabled"]:
        return empty
    if len(positions) > MAX_POSITIONS:
        return {**empty, "issues": {"all": "持仓数量超出公告和研报检查上限"}}

    end = cutoff.astimezone(CHINA).date()
    start = end - timedelta(days=365)
    start_ymd, end_ymd = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    result = {**empty, "enabled": True}
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    async def fetch_one(client: SinaResearchClient, position: dict) -> None:
        code = normalize_ts_code(position["code"] + "." + position["exchange"])

        async def announcements() -> None:
            try:
                async with semaphore:
                    raw, params = await client.get_announcements(
                        code, start_ymd, end_ymd
                    )
                fetched = datetime.now(CHINA)
                rows = parse_announcements(raw, code, fetched)
                _validate_window(rows, "ann_date", start, end)
                result["announcements"][code] = rows
                result["sources"].append(
                    _source(
                        "announcement",
                        "getBulletinList",
                        ANNOUNCEMENT_REFERENCE,
                        params,
                        rows,
                        fetched,
                        cutoff,
                    )
                )
            except SinaResearchError as exc:
                result["issues"].setdefault(code, []).append("公告查询：" + exc.code)

        async def reports() -> None:
            try:
                async with semaphore:
                    raw, params = await client.get_research_reports(
                        code, start_ymd, end_ymd
                    )
                fetched = datetime.now(CHINA)
                rows = parse_research_reports(raw, code, fetched)
                _validate_window(rows, "published_date", start, end)
                result["research_reports"][code] = rows
                result["sources"].append(
                    _source(
                        "analyst_research",
                        "getList",
                        RESEARCH_REFERENCE,
                        params,
                        rows,
                        fetched,
                        cutoff,
                    )
                )
            except SinaResearchError as exc:
                result["issues"].setdefault(code, []).append("研报查询：" + exc.code)

        await asyncio.gather(announcements(), reports())

    try:
        async with asyncio.timeout(25):
            async with SinaResearchClient() as client:
                await asyncio.gather(
                    *(fetch_one(client, position) for position in positions)
                )
    except TimeoutError:
        result["issues"].setdefault("all", []).append(
            "公告和研报查询超时，未完成项保留待核实"
        )
    return result
