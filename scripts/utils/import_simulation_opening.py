"""Validate privately by default; --apply posts only to a loopback product API."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from src.server.services.simulation_import import adapt_private_opening


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--api-url", default="http://127.0.0.1:18000")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    destination = urlsplit(args.api_url)
    if (
        destination.scheme != "http"
        or destination.hostname not in {"127.0.0.1", "localhost", "::1"}
        or destination.username
        or destination.password
        or destination.query
        or destination.fragment
        or destination.path not in {"", "/"}
    ):
        print("拒绝导入：只允许无凭据的本地回环 API 地址。")
        return 2
    try:
        if args.path.stat().st_size > 1_048_576:
            print("拒绝导入：期初文件超过 1 MiB。")
            return 2
        request = adapt_private_opening(
            json.loads(args.path.read_text(encoding="utf-8"))
        )
    except (ValueError, KeyError, TypeError, OSError, ArithmeticError):
        # ValidationError and JSONDecodeError can embed private input values.
        print("私有文件校验失败；数据处理已停止，请核对文件结构、日期与汇总口径。")
        return 2
    print("私有文件校验通过；未输出持仓或金额。")
    if not args.apply:
        print("仅校验，未写入运行时账本。")
        return 0
    try:
        with httpx.Client(
            timeout=20, follow_redirects=False, trust_env=False
        ) as client:
            response = client.post(
                args.api_url.rstrip("/") + "/api/v1/users/me/simulation/opening",
                json=request.model_dump(mode="json"),
            )
    except httpx.HTTPError:
        print("本地 API 连接失败；可在恢复后重试，重复内容不会累加余额。")
        return 3
    if response.status_code in {200, 201}:
        print(
            "期初快照已保存。"
            if response.status_code == 201
            else "同一期初已存在；未重复记账。"
        )
        return 0
    print(f"导入未完成（HTTP {response.status_code}）；未输出响应中的私有数据。")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
