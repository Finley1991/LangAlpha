"""Fixed-host public Sina quote probe; Tushare symbols in and out, no credentials."""

import argparse
import asyncio
import json
import os


def main(argv=None) -> int:
    # Importing the existing data-client package also imports configuration.
    # This standalone public probe must not discover an unrelated .env file.
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    from src.data_client.sina import (
        ENDPOINT,
        SinaQuoteClient,
        SinaQuoteError,
        normalize_ts_code,
        to_sina_symbol,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "ts_codes",
        nargs="*",
        default=["688001.SH", "600000.SH", "000001.SZ", "920019.BJ"],
    )
    parser.add_argument(
        "--run", action="store_true", help="fetch each public sample once, at most ten"
    )
    args = parser.parse_args(argv)
    try:
        symbols = list(dict.fromkeys(normalize_ts_code(code) for code in args.ts_codes))
    except ValueError:
        parser.error("use Tushare codes such as 688001.SH or 000001.SZ")
    if not 1 <= len(symbols) <= 10:
        parser.error("specify between one and ten unique public symbols")
    if not args.run:
        print(
            json.dumps(
                {
                    "mode": "plan",
                    "endpoint": ENDPOINT,
                    "symbols": {code: to_sina_symbol(code) for code in symbols},
                    "credentials_required": False,
                    "execution_ready": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    async def run():
        results = []
        async with SinaQuoteClient() as client:
            for index, code in enumerate(symbols):
                try:
                    quote = await client.get_quote(code)
                    results.append({"status": "observed", **quote.as_dict()})
                except SinaQuoteError as exc:
                    results.append(
                        {
                            "ts_code": code,
                            "status": exc.code,
                            "provider_code": exc.provider_code,
                        }
                    )
                if index < len(symbols) - 1:
                    await asyncio.sleep(1.3)
        return {
            "results": results,
            "execution_ready": False,
            "missing_execution_evidence": [
                "instrument_catalog",
                "verified_calendar",
                "verified_suspension",
                "current_price_limits",
            ],
        }

    result = asyncio.run(run())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all(row["status"] == "observed" for row in result["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
