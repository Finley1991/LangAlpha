"""Adapt a confirmed private opening without fabricating unknown batch details."""

from decimal import Decimal

from src.server.models.simulation import OpeningHolding, OpeningRequest


class OpeningDataMismatch(ValueError):
    pass


def _amount(value) -> Decimal:
    if not isinstance(value, str):
        raise OpeningDataMismatch("原始金额须为十进制字符串")
    amount = Decimal(value)
    if not amount.is_finite() or amount < 0:
        raise OpeningDataMismatch("原始金额须为有限非负数")
    return amount


def adapt_private_opening(document: dict) -> OpeningRequest:
    if (
        document.get("schema_version") != 1
        or document.get("currency") != "CNY"
        or document.get("historical_fees_amount") is not None
        or document.get("account_type") != "simulation"
        or document.get("record_type") != "opening_snapshot"
    ):
        raise OpeningDataMismatch("期初文件版本、模拟盘类型或费用口径不支持")
    positions = document["positions"]
    cost = sum((_amount(row["cost_total"]) for row in positions), Decimal(0))
    if cost != _amount(document["position_cost_total"]):
        raise OpeningDataMismatch("持仓成本汇总不一致，请核对原文件")
    if cost + _amount(document["opening_cash"]) != _amount(
        document["cash_plus_position_cost"]
    ):
        raise OpeningDataMismatch("现金与成本汇总不一致，请核对原文件")
    holdings = []
    for row in positions:
        common = {
            "code": row["code"],
            "exchange": {"SSE": "SH", "SZSE": "SZ", "BSE": "BJ"}[row["exchange"]],
            "name": row["name"],
        }
        batches = row.get("user_reported_purchases")
        dates = row.get("user_reported_acquisition_dates") or [
            batch["date"] for batch in batches or []
        ]
        if not dates:
            raise OpeningDataMismatch("缺少已知买入日期，请核对原文件")
        aggregate = OpeningHolding.model_validate(
            {
                **common,
                "quantity": row["quantity"],
                "total_cost": row["cost_total"],
                "purchased_on": min(dates),
                "acquisition_dates": dates,
                "batch_details_known": False,
            }
        )
        if batches and document["historical_fees_included"] is False:
            amounts = [_amount(batch["price"]) * batch["quantity"] for batch in batches]
            if sum(batch["quantity"] for batch in batches) != row["quantity"] or sum(
                amounts
            ) != Decimal(row["cost_total"]):
                raise OpeningDataMismatch(
                    "已知批次数量或成本与持仓汇总不一致，请核对原文件"
                )
            holdings.extend(
                OpeningHolding.model_validate(
                    {
                        **common,
                        "quantity": batch["quantity"],
                        "total_cost": format(amount, "f"),
                        "purchased_on": batch["date"],
                    }
                )
                for batch, amount in zip(batches, amounts, strict=True)
            )
        else:
            holdings.append(aggregate)
    return OpeningRequest.model_validate(
        {
            "opening_date": document["effective_date"],
            "initial_cash": document["opening_cash"],
            "cost_includes_fees": document["historical_fees_included"],
            "holdings": [row.model_dump(mode="json") for row in holdings],
        }
    )
