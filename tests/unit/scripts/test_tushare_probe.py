import asyncio
import contextlib
import io
import json
import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

import httpx

from scripts.data_probe import tushare as p

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=p.SHANGHAI)
FAKE_TOKEN = "synthetic-sensitive-canary"


def table(fields, rows):
    return json.dumps(
        {"code": 0, "data": {"fields": fields.split(","), "items": rows}}
    ).encode()


class ContractTests(unittest.TestCase):
    def test_plan_never_loads_credentials(self):
        with patch.object(
            p, "dotenv_values", side_effect=AssertionError("must not read")
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(p.main(["--env-file", "/not-opened/.env"]), 0)
            self.assertFalse(json.loads(output.getvalue())["credentials_read"])

    def test_explicit_run_requires_absolute_path_before_read(self):
        with patch.object(
            p, "dotenv_values", side_effect=AssertionError("must not read")
        ):
            with (
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                p.main(["--run", "--env-file", "relative.env"])

    def test_money_is_exact_and_not_coerced_from_float_or_bool(self):
        row = p.decode_table(
            b'{"code":0,"data":{"fields":["close"],"items":[[10.21]]}}', "close"
        )
        self.assertEqual(row[0]["close"], Decimal("10.21"))
        for value in (True, 10.21, "NaN", "Infinity", "-1", "0", "10.001", "1e1000"):
            with (
                self.subTest(value=value),
                self.assertRaises((ValueError, ArithmeticError)),
            ):
                p.positive_price(value)

    def test_malformed_table_is_rejected(self):
        for raw in (
            b"[]",
            b'{"code":false}',
            b'{"code":0,"data":null}',
            table("close,close", [[1, 2]]),
            table("other", [[1]]),
            table("close", [[1, 2]]),
            table("close", [[1]] * 101),
        ):
            with self.subTest(raw=raw[:50]), self.assertRaises(p.ProbeError) as ctx:
                p.decode_table(raw, "close")
            self.assertEqual(ctx.exception.status, "invalid_response")

    def test_provider_message_is_not_exposed(self):
        raw = json.dumps({"code": 2002, "msg": FAKE_TOKEN}).encode()
        with self.assertRaises(p.ProbeError) as ctx:
            p.decode_table(raw, "close")
        self.assertEqual(str(ctx.exception), "permission_denied")
        self.assertNotIn(FAKE_TOKEN, repr(ctx.exception))

    def test_observed_permission_code_is_classified_without_guessing_other_errors(self):
        for message, status in (
            ("无接口访问权限 " + FAKE_TOKEN, "permission_denied"),
            ("unknown " + FAKE_TOKEN, "provider_error"),
        ):
            with self.subTest(status=status), self.assertRaises(p.ProbeError) as ctx:
                p.decode_table(
                    json.dumps({"code": 40203, "msg": message}).encode(), "close"
                )
            self.assertEqual(ctx.exception.status, status)
            self.assertNotIn(FAKE_TOKEN, str(ctx.exception))

    def test_realtime_dates_and_session_are_required(self):
        cases = [
            ("2026-10-15 09:59:00", True),
            ("2026-10-15 09:58:59", False),
            ("2026-10-15 10:00:01", False),
            ("2026-10-14 10:00:00", False),
            ("2026-10-15T01:59:30+00:00", True),
            ("09:59:30", None),
            (None, None),
        ]
        for stamp, expected in cases:
            with self.subTest(stamp=stamp):
                result = p.summarize(
                    "rt_k",
                    [{"ts_code": p.SAMPLE, "close": "10.00", "trade_time": stamp}],
                    NOW,
                    None,
                )
                self.assertEqual(result.get("fresh_sample"), expected)
        noon = NOW.replace(hour=12)
        row = {
            "ts_code": p.SAMPLE,
            "close": "10.00",
            "trade_time": "2026-10-15 12:00:00",
        }
        self.assertFalse(p.summarize("rt_k", [row], noon, None)["fresh_sample"])

    def test_wrong_instrument_or_trade_date_is_rejected(self):
        for row in (
            {"ts_code": "000001.SZ", "trade_date": "20261014", "close": "10.00"},
            {"ts_code": p.SAMPLE, "trade_date": "20261013", "close": "10.00"},
        ):
            self.assertEqual(
                p.summarize("daily", [row], NOW, date(2026, 10, 14))["status"],
                "invalid_data",
            )

    def test_calendar_requires_current_exchange_and_unique_dates(self):
        valid = {"exchange": "SSE", "cal_date": "20261015", "is_open": 1}
        for rows in (
            [{**valid, "exchange": "SZSE"}],
            [{**valid, "cal_date": "20261014"}],
            [valid, valid],
            [{**valid, "is_open": True}],
        ):
            self.assertEqual(
                p.summarize("trade_cal", rows, NOW, None)["status"], "invalid_data"
            )

    def test_empty_suspension_response_does_not_prove_trading_eligibility(self):
        self.assertEqual(
            p.summarize("suspend_d", [], NOW, date(2026, 10, 14)),
            {"status": "empty", "rows": 0},
        )


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_failures_and_redirects_are_not_followed(self):
        for code in (302, 429, 503):
            seen = []

            def respond(request):
                seen.append(str(request.url))
                return httpx.Response(
                    code, headers={"location": "https://other.invalid"}
                )

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(respond), follow_redirects=True
            ) as client:
                with self.assertRaises(p.ProbeError) as ctx:
                    await p.request_table(client, FAKE_TOKEN, "daily", {})
            self.assertEqual(ctx.exception.code, code)
            self.assertEqual(seen, [p.ENDPOINT])

    async def test_timeout_has_no_request_or_token_in_error(self):
        def respond(request):
            raise httpx.ReadTimeout(FAKE_TOKEN, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with self.assertRaises(p.ProbeError) as ctx:
                await p.request_table(client, FAKE_TOKEN, "daily", {})
        self.assertEqual(str(ctx.exception), "timeout")

    async def test_oversized_response_is_bounded(self):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, content=b"x" * (p.MAX_BYTES + 1))
            )
        ) as client:
            with self.assertRaises(p.ProbeError) as ctx:
                await p.request_table(client, FAKE_TOKEN, "daily", {})
        self.assertEqual(ctx.exception.status, "response_too_large")

    async def test_failed_calendar_blocks_dated_probes_and_redacts_output(self):
        seen, pauses = [], []

        def respond(request):
            body = json.loads(request.content)
            seen.append(body["api_name"])
            self.assertEqual(body["token"], FAKE_TOKEN)
            return httpx.Response(200, json={"code": 2002, "msg": FAKE_TOKEN})

        async def pause(seconds):
            pauses.append(seconds)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await p.probe(client, FAKE_TOKEN, pause=pause, clock=lambda: NOW)
        self.assertEqual(seen, ["stock_basic", "trade_cal", "rt_k"])
        self.assertEqual(pauses, [1.3] * 3)
        self.assertEqual(result["results"]["daily"]["status"], "blocked_by_calendar")
        self.assertFalse(result["sample_freshness_observed"])
        self.assertFalse(result["execution_ready"])
        self.assertNotIn(FAKE_TOKEN, json.dumps(result))

    async def test_closed_calendar_cannot_promote_fresh_timestamp(self):
        def respond(request):
            api = json.loads(request.content)["api_name"]
            rows = {
                "stock_basic": [],
                "trade_cal": [["SSE", "20261015", 0, "20261014"]],
                "rt_k": [[p.SAMPLE, "10.00", "2026-10-15 10:00:00"]],
            }
            return httpx.Response(200, content=table(p.FIELDS[api], rows[api]))

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await p.probe(
                client, FAKE_TOKEN, pause=lambda _: asyncio.sleep(0), clock=lambda: NOW
            )
        self.assertTrue(result["results"]["rt_k"]["fresh_sample"])
        self.assertFalse(result["sample_freshness_observed"])
        self.assertFalse(result["execution_ready"])


if __name__ == "__main__":
    unittest.main()
