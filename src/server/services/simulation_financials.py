"""Freeze recent, structured Tushare financial evidence into manual reports."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx

from src.config import settings
from src.data_client.tushare import TushareError, request_table
from src.server.services.simulation_orders import CHINA

MAX_POSITIONS = 20
MAX_CONCURRENT_REQUESTS = 4
MAX_QUERY_SECONDS = 30
MAX_HISTORY_DAYS = 3650
STATEMENT_APIS = ("income", "balancesheet", "cashflow", "fina_indicator")
API_REFERENCES = {
    "income": "https://tushare.pro/document/2?doc_id=33",
    "balancesheet": "https://tushare.pro/document/2?doc_id=36",
    "cashflow": "https://tushare.pro/document/2?doc_id=44",
    "fina_indicator": "https://tushare.pro/document/2?doc_id=79",
    "disclosure_date": "https://tushare.pro/document/2?doc_id=162",
}
METRIC_FIELDS = {
    "income": (
        "basic_eps", "total_revenue", "revenue", "operate_profit", "total_profit",
        "n_income", "n_income_attr_p",
    ),
    "balancesheet": (
        "total_cur_assets", "total_assets", "total_cur_liab", "total_liab",
        "total_hldr_eqy_exc_min_int", "money_cap",
    ),
    "cashflow": (
        "n_cashflow_act", "n_cashflow_inv_act", "n_cash_flows_fnc_act",
        "c_cash_equ_end_period",
    ),
    "fina_indicator": (
        "eps", "profit_dedt", "gross_margin", "current_ratio", "quick_ratio",
        "debt_to_assets", "roe", "roa", "ocfps",
    ),
}


def _date_value(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        if len(value) == 8 and value.isdigit():
            return datetime.strptime(value, "%Y%m%d").date()
        return date.fromisoformat(value)
    except ValueError:
        return None


def _date_text(value: object) -> str | None:
    parsed = _date_value(value)
    return parsed.isoformat() if parsed else None


def _quarter_end(day: date) -> str:
    quarter_ends = ((3, 31), (6, 30), (9, 30), (12, 31))
    for month, end_day in reversed(quarter_ends):
        candidate = date(day.year, month, end_day)
        if candidate <= day:
            return candidate.strftime("%Y%m%d")
    return date(day.year - 1, 12, 31).strftime("%Y%m%d")


def _metric(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    return format(number, "f")


def _latest_published(rows: list[dict], api: str, cutoff: date) -> dict | None:
    candidates = []
    for row in rows:
        if api != "fina_indicator" and str(row.get("report_type") or "1") != "1":
            continue
        period = _date_value(row.get("end_date"))
        announcement = _date_value(row.get("f_ann_date") or row.get("ann_date"))
        if period is None or announcement is None or period > cutoff or announcement > cutoff:
            continue
        candidates.append((period, announcement, row))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def _record(api: str, row: dict, source_id: str) -> dict:
    metrics = {}
    for field in METRIC_FIELDS[api]:
        value = _metric(row.get(field))
        if value is not None:
            metrics[field] = value
    record = {
        "table": api,
        "end_date": _date_text(row.get("end_date")),
        "ann_date": _date_text(row.get("ann_date")),
        # ann_date is the provider's publication date; only f_ann_date is the
        # separately reported actual announcement date.
        "actual_ann_date": _date_text(row.get("f_ann_date")),
        "report_type": str(row["report_type"]) if row.get("report_type") is not None else None,
        "metrics": metrics,
        "source_ids": [source_id],
    }
    return record


def _source(api: str, params: dict, row: dict | None, fetched_at: datetime) -> dict:
    evidence = {
        "provider": "tushare",
        "api": api,
        "params": params,
        "row_count": 1 if row else 0,
        "record_sha256": hashlib.sha256(
            json.dumps(row, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest() if row else None,
    }
    wire = json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        "source_id": "tushare-financial-" + hashlib.sha256(wire.encode()).hexdigest(),
        "kind": "financial_statement",
        "title": f"Tushare {api} 结构化财务数据",
        "reference": API_REFERENCES[api],
        "as_of": fetched_at.isoformat(),
        "fetched_at": fetched_at.isoformat(),
        "evidence": evidence,
    }


async def collect(positions: list[dict], cutoff: datetime) -> dict:
    result: dict = {"enabled": False, "financials": {}, "sources": [], "issues": {}}
    token = settings.get_simulation_financials_token()
    if token is None or not positions:
        return result
    result["enabled"] = True
    if len(positions) > MAX_POSITIONS:
        result["issues"]["all"] = "持仓数量超出单次财务数据查询上限"
        return result

    cutoff_date = cutoff.astimezone(CHINA).date()
    history_start = cutoff_date - timedelta(days=MAX_HISTORY_DAYS)
    period_end = _quarter_end(cutoff_date)
    period_start = history_start.strftime("%Y%m%d")
    cutoff_text = cutoff_date.strftime("%Y%m%d")
    source_by_code: dict[str, list[str]] = {}
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    async def fetch(client: httpx.AsyncClient, code: str, api: str, params: dict):
        async with semaphore:
            rows = await request_table(client, token, api, params)
        selected = None
        if api == "disclosure_date":
            selected = next(
                (row for row in rows if row.get("end_date") == params["end_date"]),
                None,
            )
        else:
            selected = _latest_published(rows, api, cutoff_date)
        fetched = datetime.now(CHINA)
        source = _source(api, params, selected, fetched)
        result["sources"].append(source)
        source_by_code.setdefault(code, []).append(source["source_id"])
        return selected, source

    async def fetch_position(client: httpx.AsyncClient, position: dict):
        code = f"{position['code']}.{position['exchange']}"
        if position["exchange"] not in ("SH", "SZ"):
            result["issues"][code] = "当前仅支持沪深 A 股财务数据，待核实"
            return
        tasks = []
        for api in STATEMENT_APIS:
            if api == "fina_indicator":
                params = {"ts_code": code, "start_date": period_start, "end_date": period_end}
            else:
                params = {
                    "ts_code": code,
                    "start_date": history_start.strftime("%Y%m%d"),
                    "end_date": cutoff_text,
                    "report_type": "1",
                }
            tasks.append((api, params, asyncio.create_task(fetch(client, code, api, params))))
        tables = {}
        disclosure = None
        failures = []
        latest_period = None
        for api, _params, task in tasks:
            try:
                row, source = await task
                if row:
                    tables[api] = _record(api, row, source["source_id"])
                    period = tables[api]["end_date"]
                    if latest_period is None or period > latest_period:
                        latest_period = period
                else:
                    failures.append(f"{api} 暂无已披露记录")
            except TushareError as exc:
                if exc.status == "permission_denied":
                    failures.append(f"{api} 权限不足")
                elif exc.status == "timeout":
                    failures.append(f"{api} 查询超时")
                else:
                    failures.append(f"{api} 查询失败")
            except httpx.HTTPError:
                failures.append(f"{api} 查询失败")
        disclosed_period = _date_value(latest_period) if latest_period else None
        disclosure_params = {
            "ts_code": code,
            "end_date": disclosed_period.strftime("%Y%m%d") if disclosed_period else period_end,
        }
        try:
            row, source = await fetch(client, code, "disclosure_date", disclosure_params)
            if row:
                actual_date = _date_value(row.get("actual_date"))
                disclosure = {
                    "end_date": _date_text(row.get("end_date")),
                    "ann_date": _date_text(row.get("ann_date")),
                    "planned_date": _date_text(row.get("pre_date")),
                    "actual_date": actual_date.isoformat()
                    if actual_date and actual_date <= cutoff_date else None,
                    "source_ids": [source["source_id"]],
                }
            else:
                failures.append("disclosure_date 暂无披露计划")
        except TushareError as exc:
            failures.append(
                "disclosure_date 权限不足"
                if exc.status == "permission_denied"
                else "disclosure_date 查询失败"
            )
        except httpx.HTTPError:
            failures.append("disclosure_date 查询失败")
        if tables or disclosure:
            result["financials"][code] = {
                "tables": tables,
                "disclosure": disclosure,
                "fetched_at": datetime.now(CHINA).isoformat(),
                "source_ids": source_by_code.get(code, []),
            }
        if failures:
            result["issues"][code] = "财务数据待核实：" + "；".join(failures)

    try:
        async with asyncio.timeout(MAX_QUERY_SECONDS):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(12, connect=4),
                trust_env=False,
                follow_redirects=False,
            ) as client:
                await asyncio.gather(*(fetch_position(client, position) for position in positions))
    except TimeoutError:
        result["issues"]["all"] = "财报数据查询超时，未完成项保留待核实"
    except (httpx.HTTPError, TushareError):
        result["issues"]["all"] = "财报数据服务暂不可用，未完成项保留待核实"
    return result
