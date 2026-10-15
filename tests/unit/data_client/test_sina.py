import contextlib
import io
import json
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import httpx

from scripts.data_probe import sina as probe
from src.data_client import sina

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=sina.CHINA)


def wire(**changes):
    data = {
        "symbol": "sz000001",
        "hqMarket": "cn",
        "price": "11.41",
        "hqDate": "2026-10-15",
        "hqTime": "09:59:30",
        "uptime": "2026-10-15 10:00:00",
        "stopStatus": "00",
    }
    data.update(changes)
    return json.dumps({"code": 0, "data": data}).encode()


class SymbolTests(unittest.TestCase):
    def test_exchange_is_explicit_and_leading_zero_is_preserved(self):
        for ts_code, symbol in (
            ("688001.SH", "sh688001"),
            ("600000.SH", "sh600000"),
            ("000001.SZ", "sz000001"),
            ("920019.BJ", "bj920019"),
            ("000001.SH", "sh000001"),
        ):
            with self.subTest(ts_code=ts_code):
                self.assertEqual(sina.to_sina_symbol(ts_code), symbol)
                self.assertEqual(sina.from_sina_symbol(symbol), ts_code)
        self.assertEqual(sina.normalize_ts_code(" 000001.sz "), "000001.SZ")

    def test_ambiguous_or_unsafe_codes_are_rejected(self):
        for value in (
            None,
            1,
            "000001",
            "sh600000",
            "600000.HK",
            "600000.XSHG",
            "１00000.SH",
            "60000.SH",
            "6000000.SH",
            "600000.SH\n&x=1",
            "https://localhost",
            "600000.SH/..",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                sina.to_sina_symbol(value)

    def test_vendor_symbols_are_strict(self):
        for value in (
            None,
            "SH688001",
            "sh68800",
            "sh688001&x=1",
            "hk00700",
            "688001.SH",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                sina.from_sina_symbol(value)


class QuoteTests(unittest.TestCase):
    def test_normalized_quote_preserves_source_precision_and_evidence(self):
        raw = wire()
        quote = sina.parse_quote(raw, "000001.SZ", NOW)
        self.assertEqual(quote.ts_code, "000001.SZ")
        self.assertEqual(quote.price, Decimal("11.41"))
        self.assertEqual(quote.as_of, NOW - timedelta(seconds=30))
        self.assertEqual(quote.provider_updated_at, NOW)
        self.assertEqual(quote.stop_status, "00")
        output = quote.as_dict(NOW)
        self.assertEqual(output["price"], "11.41")
        self.assertEqual(output["source"], "sina")
        self.assertEqual(output["freshness"], "fresh")
        self.assertTrue(output["evidence_id"].startswith("sha256:"))
        self.assertNotIn("instrument_verified", output)
        self.assertNotIn("suspended", output)
        self.assertNotEqual(
            quote.evidence_id,
            sina.parse_quote(wire(price="11.42"), "000001.SZ", NOW).evidence_id,
        )

    def test_json_fraction_is_parsed_as_exact_decimal(self):
        raw = wire().replace(b'"11.41"', b"11.41")
        self.assertEqual(
            sina.parse_quote(raw, "000001.SZ", NOW).price, Decimal("11.41")
        )

    def test_vendor_identity_must_match_request(self):
        for changes in (
            {"symbol": "sh000001"},
            {"symbol": "sz000002"},
            {"hqMarket": "hk"},
        ):
            with (
                self.subTest(changes=changes),
                self.assertRaises(sina.SinaQuoteError) as ctx,
            ):
                sina.parse_quote(wire(**changes), "000001.SZ", NOW)
            self.assertEqual(ctx.exception.code, "instrument_mismatch")

    def test_quote_time_cannot_be_replaced_by_uptime(self):
        for changes in (
            {"hqDate": None},
            {"hqTime": ""},
            {"hqTime": "09:59"},
            {"hqDate": "2026-02-30"},
            {"hqTime": "25:00:00"},
            {"uptime": "invalid"},
        ):
            with (
                self.subTest(changes=changes),
                self.assertRaises(sina.SinaQuoteError) as ctx,
            ):
                sina.parse_quote(wire(**changes), "000001.SZ", NOW)
            self.assertEqual(ctx.exception.code, "invalid_quote")
        quote = sina.parse_quote(wire(hqDate="2026-10-14"), "000001.SZ", NOW)
        self.assertEqual(quote.freshness(NOW), "stale")
        self.assertEqual(quote.provider_updated_at, NOW)

    def test_stale_and_future_quotes_remain_visible_but_are_not_fresh(self):
        for stamp, status in (
            ("09:59:00", "fresh"),
            ("09:58:59", "stale"),
            ("10:00:01", "future"),
        ):
            with self.subTest(stamp=stamp):
                quote = sina.parse_quote(wire(hqTime=stamp), "000001.SZ", NOW)
                self.assertEqual(quote.freshness(NOW), status)
        quote = sina.parse_quote(wire(), "000001.SZ", NOW)
        self.assertEqual(quote.freshness(NOW + timedelta(seconds=31)), "stale")
        self.assertEqual(quote.freshness(NOW - timedelta(seconds=1)), "future")

    def test_cross_day_is_stale_even_within_sixty_seconds(self):
        midnight = NOW.replace(hour=0, second=10)
        quote = sina.parse_quote(
            wire(hqDate="2026-10-14", hqTime="23:59:50"), "000001.SZ", midnight
        )
        self.assertEqual(quote.freshness(midnight), "stale")

    def test_cached_quote_rechecks_current_time(self):
        quote = sina.parse_quote(wire(), "000001.SZ", NOW)
        with patch.object(sina, "datetime") as clock:
            clock.now.return_value = NOW + timedelta(seconds=31)
            self.assertEqual(quote.freshness(), "stale")

    def test_naive_time_is_not_silently_assumed(self):
        with self.assertRaises(ValueError):
            sina.parse_quote(wire(), "000001.SZ", NOW.replace(tzinfo=None))
        quote = sina.parse_quote(wire(), "000001.SZ", NOW)
        with self.assertRaises(ValueError):
            quote.freshness(NOW.replace(tzinfo=None))

    def test_missing_optional_status_and_uptime_stay_unknown(self):
        quote = sina.parse_quote(wire(stopStatus=None, uptime=None), "000001.SZ", NOW)
        self.assertIsNone(quote.stop_status)
        self.assertIsNone(quote.provider_updated_at)
        self.assertEqual(
            sina.parse_quote(wire(stopStatus="99"), "000001.SZ", NOW).stop_status, "99"
        )

    def test_invalid_price_or_status_is_rejected(self):
        for changes in (
            {"price": None},
            {"price": True},
            {"price": "NaN"},
            {"price": "Infinity"},
            {"price": "0"},
            {"price": "-1"},
            {"price": "11.411"},
            {"price": "1000001"},
            {"stopStatus": {"value": "00"}},
            {"stopStatus": "<script>"},
        ):
            with self.subTest(changes=changes), self.assertRaises(sina.SinaQuoteError):
                sina.parse_quote(wire(**changes), "000001.SZ", NOW)

    def test_malformed_or_provider_failure_is_not_empty_success(self):
        for raw in (b"not-json", b"[]", b'{"code":true}', b'{"code":0,"data":null}'):
            with self.subTest(raw=raw), self.assertRaises(sina.SinaQuoteError) as ctx:
                sina.parse_quote(raw, "000001.SZ", NOW)
            self.assertEqual(ctx.exception.code, "invalid_quote")
        with self.assertRaises(sina.SinaQuoteError) as ctx:
            sina.parse_quote(
                b'{"code":-1,"message":"untrusted provider text"}', "000001.SZ", NOW
            )
        self.assertEqual(ctx.exception.provider_code, -1)
        self.assertEqual(str(ctx.exception), "provider_error")

    def test_response_size_is_bounded(self):
        with self.assertRaises(sina.SinaQuoteError) as ctx:
            sina.parse_quote(b"x" * (sina.MAX_RESPONSE_BYTES + 1), "000001.SZ", NOW)
        self.assertEqual(ctx.exception.code, "response_too_large")


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_uses_fixed_https_and_context_releases_client(self):
        seen = []

        def respond(request):
            seen.append(request)
            return httpx.Response(200, content=wire())

        transport = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch.object(sina.httpx, "AsyncClient", return_value=transport) as factory:
            async with sina.SinaQuoteClient() as client:
                quote = await client.get_quote(" 000001.sz ")
            factory.assert_called_once_with(
                timeout=10, trust_env=False, follow_redirects=False
            )
        self.assertTrue(transport.is_closed)
        self.assertEqual(quote.ts_code, "000001.SZ")
        self.assertEqual(str(seen[0].url), sina.ENDPOINT + "?market=cn&symbol=sz000001")
        self.assertNotIn("authorization", seen[0].headers)

    async def test_http_failures_are_not_followed_or_retried(self):
        for status in (302, 429, 503):
            seen = []

            def respond(request):
                seen.append(request)
                return httpx.Response(
                    status, headers={"location": "https://elsewhere.invalid"}
                )

            transport = httpx.AsyncClient(
                transport=httpx.MockTransport(respond), follow_redirects=True
            )
            with patch.object(sina.httpx, "AsyncClient", return_value=transport):
                async with sina.SinaQuoteClient() as client:
                    with self.assertRaises(sina.SinaQuoteError) as ctx:
                        await client.get_quote("000001.SZ")
            self.assertEqual(ctx.exception.code, "http_error")
            self.assertEqual(ctx.exception.provider_code, status)
            self.assertEqual(len(seen), 1)

    async def test_timeout_and_transport_error_are_classified(self):
        for error, code in (
            (httpx.ReadTimeout, "timeout"),
            (httpx.ConnectError, "transport_error"),
        ):

            def respond(request):
                raise error("raw transport details", request=request)

            transport = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            with patch.object(sina.httpx, "AsyncClient", return_value=transport):
                async with sina.SinaQuoteClient() as client:
                    with self.assertRaises(sina.SinaQuoteError) as ctx:
                        await client.get_quote("000001.SZ")
            self.assertEqual(str(ctx.exception), code)

    async def test_stream_size_limit_is_enforced(self):
        transport = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, content=b"x" * (sina.MAX_RESPONSE_BYTES + 1)
                )
            )
        )
        with patch.object(sina.httpx, "AsyncClient", return_value=transport):
            async with sina.SinaQuoteClient() as client:
                with self.assertRaises(sina.SinaQuoteError) as ctx:
                    await client.get_quote("000001.SZ")
        self.assertEqual(ctx.exception.code, "response_too_large")

    async def test_bad_symbol_fails_before_network(self):
        transport = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: self.fail("must not request"))
        )
        with patch.object(sina.httpx, "AsyncClient", return_value=transport):
            async with sina.SinaQuoteClient() as client:
                with self.assertRaises(ValueError):
                    await client.get_quote("000001")


class ProbeTests(unittest.TestCase):
    def test_plan_is_canonical_and_does_not_construct_client(self):
        output = io.StringIO()
        with (
            patch.dict("os.environ", {}, clear=False),
            patch.object(
                sina, "SinaQuoteClient", side_effect=AssertionError("must not connect")
            ),
        ):
            with contextlib.redirect_stdout(output):
                self.assertEqual(probe.main(["000001.sz", "000001.SZ"]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["symbols"], {"000001.SZ": "sz000001"})
        self.assertFalse(result["execution_ready"])

    def test_cli_rejects_invalid_or_excessive_inputs(self):
        for args in (["000001"], [f"{i:06d}.SH" for i in range(11)]):
            with (
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                probe.main(args)


if __name__ == "__main__":
    unittest.main()
