"""Opt-in, read-only Tushare canary; never writes routing or trading state."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from dotenv import dotenv_values

SHANGHAI = ZoneInfo("Asia/Shanghai")
from src.data_client.tushare import (  # noqa: E402
    ENDPOINT,
    FIELDS,
    MAX_BYTES as MAX_BYTES,
    decode_table as decode_table,
    TushareError as ProbeError,
    request_table,
)

SAMPLE = "600000.SH"


def day(value) -> date:
    if not isinstance(value, str) or len(value) != 8 or not value.isdigit():
        raise ValueError
    return datetime.strptime(value, "%Y%m%d").date()


def positive_price(value) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError
    result = Decimal(value)
    if not result.is_finite() or not 0 < result <= 1_000_000:
        raise ValueError
    if result != result.quantize(Decimal("0.01")):
        raise ValueError
    return result


def continuous_session(now: datetime) -> bool:
    clock = now.astimezone(SHANGHAI).time()
    return time(9, 30) <= clock < time(11, 30) or time(13) <= clock < time(14, 57)


def summarize(api: str, rows: list[dict], now: datetime, expected_day: date | None):
    result: dict = {"status": "observed" if rows else "empty", "rows": len(rows)}
    if not rows:
        return result
    today = now.astimezone(SHANGHAI).date()
    try:
        if api == "trade_cal":
            dates = [day(row["cal_date"]) for row in rows]
            if len(set(dates)) != len(dates):
                raise ValueError
            for row, calendar_day in zip(rows, dates, strict=True):
                if (
                    row["exchange"] != "SSE"
                    or not today - timedelta(days=14) <= calendar_day <= today
                    or str(row["is_open"]) not in ("0", "1")
                ):
                    raise ValueError
            current = [row for row in rows if day(row["cal_date"]) == today]
            if len(current) != 1:
                raise ValueError
            result["sample_exchange_open_today"] = str(current[0]["is_open"]) == "1"
            past = [
                day(row["cal_date"])
                for row in rows
                if str(row["is_open"]) == "1" and day(row["cal_date"]) < today
            ]
            result["latest_observed_prior_session"] = (
                max(past).isoformat() if past else None
            )
        else:
            if any(row["ts_code"] != SAMPLE for row in rows):
                raise ValueError
            if api != "suspend_d" and len(rows) != 1:
                raise ValueError
            row = rows[0]
            if api == "stock_basic":
                if (
                    row["symbol"] != "600000"
                    or row["exchange"] != "SSE"
                    or row["market"] != "主板"
                    or row["curr_type"] != "CNY"
                    or row["list_status"] != "L"
                    or day(row["list_date"]) > today
                ):
                    raise ValueError
            elif api == "rt_k":
                positive_price(row["close"])
                stamp = row["trade_time"]
                if not isinstance(stamp, str):
                    raise ValueError
                # A time without its date cannot establish freshness across days.
                as_of = datetime.fromisoformat(stamp)
                if len(stamp) < 19:
                    raise ValueError
                if as_of.tzinfo is None:
                    as_of = as_of.replace(tzinfo=SHANGHAI)
                as_of = as_of.astimezone(SHANGHAI)
                lag = (now - as_of).total_seconds()
                result.update(
                    as_of=as_of.isoformat(),
                    age_seconds=round(lag, 3),
                    fresh_sample=0 <= lag <= 60
                    and as_of.date() == today
                    and continuous_session(as_of)
                    and continuous_session(now),
                )
            else:
                if expected_day is None or any(
                    day(r["trade_date"]) != expected_day for r in rows
                ):
                    raise ValueError
                result["observed_trade_date"] = expected_day.isoformat()
                if api == "daily":
                    positive_price(row["close"])
                elif api == "stk_limit":
                    if positive_price(row["down_limit"]) >= positive_price(
                        row["up_limit"]
                    ):
                        raise ValueError
                elif any(r["suspend_type"] not in ("S", "R") for r in rows):
                    raise ValueError
    except (ValueError, KeyError, TypeError, InvalidOperation, OverflowError):
        return {"status": "invalid_data", "rows": len(rows)}
    return result


async def probe(
    client: httpx.AsyncClient, token: str, *, pause=asyncio.sleep, clock=None
):
    clock = clock or (lambda: datetime.now(SHANGHAI))
    started = clock()
    today = started.astimezone(SHANGHAI).date()
    results: dict = {}

    async def check(api, params, expected_day=None):
        try:
            rows = await request_table(client, token, api, params)
            results[api] = summarize(api, rows, clock(), expected_day)
        except ProbeError as exc:
            results[api] = {"status": exc.status, "code": exc.code}
        results[api]["fetched_at"] = clock().isoformat()
        await pause(1.3)  # Stay below the documented 50/minute canary floor.

    await check("stock_basic", {"ts_code": SAMPLE})
    await check(
        "trade_cal",
        {
            "exchange": "SSE",
            "start_date": (today - timedelta(days=14)).strftime("%Y%m%d"),
            "end_date": today.strftime("%Y%m%d"),
        },
    )
    prior = results["trade_cal"].get("latest_observed_prior_session")
    if prior:
        prior_day = date.fromisoformat(prior)
        for api in ("daily", "stk_limit", "suspend_d"):
            await check(
                api,
                {"ts_code": SAMPLE, "trade_date": prior_day.strftime("%Y%m%d")},
                prior_day,
            )
    else:
        for api in ("daily", "stk_limit", "suspend_d"):
            results[api] = {"status": "blocked_by_calendar"}
    await check("rt_k", {"ts_code": SAMPLE})
    return {
        "probe_version": 1,
        "sample": SAMPLE,
        "started_at": started.isoformat(),
        "finished_at": clock().isoformat(),
        "results": results,
        "sample_freshness_observed": results["rt_k"].get("fresh_sample", False)
        and results["trade_cal"].get("sample_exchange_open_today", False),
        "execution_ready": False,
        "limits": [
            "one_public_sample_only",
            "no_order_or_ledger_access",
            "no_suspension_or_current_limit_verification",
            "no_announcements_or_data_redistribution_verification",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="store_true",
        help="perform up to six read-only public-sample calls",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="explicitly authorized absolute path; only read with --run",
    )
    args = parser.parse_args(argv)
    if not args.run:
        print(
            json.dumps(
                {
                    "mode": "plan",
                    "sample": SAMPLE,
                    "endpoint": ENDPOINT,
                    "apis": list(FIELDS),
                    "max_calls": 6,
                    "credentials_read": False,
                    "execution_ready": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.env_file is None or not args.env_file.is_absolute():
        parser.error("--run requires an explicitly authorized absolute --env-file path")
    # Suppress third-party diagnostic logging; never interpolate other env secrets.
    logging.disable(logging.CRITICAL)
    try:
        token = dotenv_values(args.env_file, interpolate=False, encoding="utf-8").get(
            "TUSHARE_TOKEN"
        )
        if not isinstance(token, str) or not token.strip():
            print('{"status":"missing_token"}')
            return 2

        async def run():
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10), trust_env=False, follow_redirects=False
            ) as client:
                return await probe(client, token.strip())

        result = asyncio.run(run())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return (
            0
            if all(
                row["status"] in ("observed", "empty")
                for row in result["results"].values()
            )
            else 1
        )
    except (OSError, UnicodeError):
        print('{"status":"credential_file_unreadable"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
