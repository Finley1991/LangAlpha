"""Closing evidence is research input only, never a TrustedQuote for execution."""

import asyncio
import hashlib
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import httpx

from src.config.settings import get_simulation_closing_token
from src.data_client.sina import normalize_ts_code
from src.data_client.tushare import TushareError, request_table

CHINA = ZoneInfo("Asia/Shanghai")
CALENDAR_REFERENCE = "https://tushare.pro/document/2?doc_id=26"
DAILY_REFERENCE = "https://tushare.pro/document/2?doc_id=27"
MAX_POSITIONS = 100


class ClosingDataError(Exception):
    pass


def parse_day(value: str) -> date:
    if (
        not isinstance(value, str)
        or len(value) != 8
        or not value.isascii()
        or not value.isdigit()
    ):
        raise ClosingDataError
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        raise ClosingDataError from None


def latest_closed(rows: list[dict], exchange: str, cutoff: datetime) -> date:
    local = cutoff.astimezone(CHINA)
    start = local.date() - timedelta(days=45)
    expected = {start + timedelta(days=i) for i in range(46)}
    seen: set[date] = set()
    closed = []
    for row in rows:
        day = parse_day(row.get("cal_date"))
        if (
            day in seen
            or day not in expected
            or row.get("exchange") != exchange
            or type(row.get("is_open")) not in (int, str)
            or str(row["is_open"]) not in ("0", "1")
        ):
            raise ClosingDataError
        seen.add(day)
        if (
            str(row["is_open"]) == "1"
            and datetime.combine(day, time(15), CHINA) <= local
        ):
            closed.append(day)
    if seen != expected or not closed:
        raise ClosingDataError
    return max(closed)


def closing_prices(rows: list[dict], codes: set[str], day: date) -> dict[str, str]:
    prices = {}
    try:
        for row in rows:
            code = row["ts_code"]
            if (
                code not in codes
                or code in prices
                or parse_day(row["trade_date"]) != day
            ):
                raise ClosingDataError
            value = row["close"]
            if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
                raise ClosingDataError
            price = Decimal(value)
            if (
                not price.is_finite()
                or not 0 < price <= 1_000_000
                or price != price.quantize(Decimal(".01"))
            ):
                raise ClosingDataError
            prices[code] = format(price, ".2f")
    except (KeyError, TypeError, InvalidOperation, ValueError):
        raise ClosingDataError from None
    return prices


def source(
    api: str, params: dict, rows: list[dict], fetched: datetime, as_of: datetime
) -> dict:
    evidence = {"api": api, "params": params, "rows": rows}
    # Store selected public fields, not provider envelopes or credentials.
    wire = json.dumps(
        evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return {
        "source_id": "tushare-" + hashlib.sha256(wire.encode()).hexdigest(),
        "kind": "closing_calendar" if api == "trade_cal" else "closing_price",
        "title": "Tushare 交易日历"
        if api == "trade_cal"
        else "Tushare 未复权日线收盘价",
        "reference": CALENDAR_REFERENCE if api == "trade_cal" else DAILY_REFERENCE,
        "as_of": as_of.isoformat(),
        "fetched_at": fetched.isoformat(),
        "evidence": json.loads(wire),
    }


async def collect(positions: list[dict], cutoff: datetime) -> dict:
    result: dict = {"prices": {}, "sources": [], "issues": {}}
    token = get_simulation_closing_token()
    if token is None or not positions:
        return result
    if len(positions) > MAX_POSITIONS:
        return {**result, "issues": {"all": "持仓数量超出单次收盘检查上限"}}
    codes_by_exchange: dict[str, set[str]] = {}
    for p in positions:
        code = normalize_ts_code(p["code"] + "." + p["exchange"])
        if p["exchange"] not in ("SH", "SZ"):
            result["issues"][code] = "该交易所收盘日历尚未接入"
        else:
            codes_by_exchange.setdefault(p["exchange"], set()).add(code)
    try:
        async with asyncio.timeout(25):
            async with httpx.AsyncClient(
                timeout=5, trust_env=False, follow_redirects=False
            ) as client:
                for exchange, codes in codes_by_exchange.items():
                    try:
                        cal_params = {
                            "exchange": "SSE" if exchange == "SH" else "SZSE",
                            "start_date": (
                                cutoff.astimezone(CHINA).date() - timedelta(days=45)
                            ).strftime("%Y%m%d"),
                            "end_date": cutoff.astimezone(CHINA).strftime("%Y%m%d"),
                        }
                        calendar = await request_table(
                            client, token, "trade_cal", cal_params
                        )
                        fetched = datetime.now(CHINA)
                        day = latest_closed(calendar, cal_params["exchange"], cutoff)
                        stamp = datetime.combine(day, time(15), CHINA)
                        cal_source = source(
                            "trade_cal", cal_params, calendar, fetched, stamp
                        )
                        result["sources"].append(cal_source)
                        await asyncio.sleep(1.3)
                        params = {
                            "ts_code": ",".join(sorted(codes)),
                            "trade_date": day.strftime("%Y%m%d"),
                        }
                        rows = await request_table(client, token, "daily", params)
                        fetched = datetime.now(CHINA)
                        prices = closing_prices(rows, codes, day)
                        price_source = source("daily", params, rows, fetched, stamp)
                        result["sources"].append(price_source)
                        for code in codes:
                            if code not in prices:
                                result["issues"][code] = (
                                    "目标交易日无日线记录（未发布或停牌等原因待核实），不回退旧收盘价"
                                )
                            else:
                                result["prices"][code] = {
                                    "trade_date": day.isoformat(),
                                    "close": prices[code],
                                    "as_of": stamp.isoformat(),
                                    "fetched_at": fetched.isoformat(),
                                    "source_ids": [
                                        cal_source["source_id"],
                                        price_source["source_id"],
                                    ],
                                }
                    except (TushareError, ClosingDataError) as exc:
                        reason = (
                            exc.status
                            if isinstance(exc, TushareError)
                            else "invalid_data"
                        )
                        for code in codes:
                            result["issues"][code] = "收盘证据不可用：" + reason
                    await asyncio.sleep(1.3)
    except TimeoutError:
        result["issues"]["all"] = "收盘证据获取超时，未完成的证券保留待核实"
    return result
