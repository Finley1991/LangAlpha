"""Bounded HTTPS tables shared by the read-only probe and closing reports."""

import asyncio
import json
from decimal import Decimal

import httpx

ENDPOINT = "https://api.tushare.pro"
MAX_BYTES = 262_144
FIELDS = {
    "stock_basic": "ts_code,symbol,name,market,exchange,curr_type,list_status,list_date",
    "trade_cal": "exchange,cal_date,is_open,pretrade_date",
    "daily": "ts_code,trade_date,close",
    "stk_limit": "ts_code,trade_date,up_limit,down_limit",
    "suspend_d": "ts_code,trade_date,suspend_type,suspend_timing",
    "rt_k": "ts_code,close,trade_time",
    "income": "ts_code,ann_date,f_ann_date,end_date,report_type,basic_eps,total_revenue,revenue,operate_profit,total_profit,n_income,n_income_attr_p",
    "balancesheet": "ts_code,ann_date,f_ann_date,end_date,report_type,total_cur_assets,total_assets,total_cur_liab,total_liab,total_hldr_eqy_exc_min_int,money_cap",
    "cashflow": "ts_code,ann_date,f_ann_date,end_date,report_type,n_cashflow_act,n_cashflow_inv_act,n_cash_flows_fnc_act,c_cash_equ_end_period",
    "fina_indicator": "ts_code,ann_date,end_date,eps,profit_dedt,gross_margin,current_ratio,quick_ratio,debt_to_assets,roe,roa,ocfps",
    "disclosure_date": "ts_code,ann_date,end_date,pre_date,actual_date,modify_date",
}


class TushareError(Exception):
    def __init__(self, status: str, code: int | None = None):
        super().__init__(status)
        self.status = status
        self.code = code


def decode_table(raw: bytes, fields: str) -> list[dict]:
    try:
        payload = json.loads(raw, parse_float=Decimal)
        if not isinstance(payload, dict) or type(payload.get("code")) is not int:
            raise ValueError
        code = payload["code"]
        if code != 0:
            message = payload.get("msg")
            permission_denied = code == 2002 or (
                code == 40203 and isinstance(message, str) and "权限" in message
            )
            raise TushareError(
                "permission_denied" if permission_denied else "provider_error", code
            )
        data = payload["data"]
        names, rows = data["fields"], data["items"]
        if (
            not isinstance(names, list)
            or not all(isinstance(name, str) for name in names)
            or len(set(names)) != len(names)
            or set(names) != set(fields.split(","))
            or not isinstance(rows, list)
            or len(rows) > 100
            or any(not isinstance(row, list) or len(row) != len(names) for row in rows)
        ):
            raise ValueError
        return [dict(zip(names, row, strict=True)) for row in rows]
    except (ValueError, KeyError, TypeError):
        raise TushareError("invalid_response") from None


async def request_table(client: httpx.AsyncClient, token: str, api: str, params: dict):
    try:
        async with asyncio.timeout(15):
            async with client.stream(
                "POST",
                ENDPOINT,
                follow_redirects=False,
                json={
                    "api_name": api,
                    "token": token,
                    "params": params,
                    "fields": FIELDS[api],
                },
            ) as response:
                if response.status_code != 200:
                    raise TushareError("http_error", response.status_code)
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_BYTES:
                        raise TushareError("response_too_large")
        return decode_table(bytes(raw), FIELDS[api])
    except (TimeoutError, httpx.TimeoutException):
        raise TushareError("timeout") from None
    except httpx.HTTPError:
        raise TushareError("transport_error") from None
