"""Deterministic missing-data reports never call models or create trades."""

import hashlib
import json
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from src.server.database import simulation_orders as accounts
from src.server.database import simulation_reports as db
from src.server.models.simulation_reports import ReportContent, ReportRequest
from src.server.services.simulation_orders import (
    CHINA,
    SimulationError,
    ledger_summary,
    require_account,
)

MISSING = [
    "行情与交易日历未核验",
    "公告与财报来源未接通",
    "持仓研究论点未建立",
    "投资约束档案尚未载入",
]


def digest(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def content(snapshot: dict, now: datetime, opening_date: str) -> dict:
    report_id = str(uuid4())
    source_id = "ledger-" + report_id
    opening_future = opening_date > now.astimezone(CHINA).date().isoformat()
    payload = {
        "schema_version": "simulation-report-v1",
        "report_id": report_id,
        "report_date": now.astimezone(CHINA).date().isoformat(),
        "generated_at": now.isoformat(),
        "cutoff_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=24)).isoformat(),
        "status": "awaiting_data",
        "title": "持仓检查日报 · 数据待核实",
        "summary": "已保存本次账本快照。市场事实和持仓论点不足，暂不形成买卖判断。",
        "validity": "仅对应本次账本快照；账本版本或北京时间检查日期变化、或超过失效时间后须重新检查。",
        "opening_in_future": opening_future,
        "snapshot": snapshot,
        "sources": [
            {
                "source_id": source_id,
                "kind": "ledger_snapshot",
                "title": "本报告内冻结的模拟账本快照（用户输入及程序记账）",
                "reference": f"/api/v1/users/me/simulation/reports/{report_id}#snapshot",
                "as_of": now.isoformat(),
                "snapshot_version": snapshot["version"],
            }
        ],
        "checks": [
            {
                "code": p["code"],
                "exchange": p["exchange"],
                "name": p["name"],
                "fact": f"账本记录 {p['quantity']} 股，当前精确成本 {p['total_cost']} CNY；保守可卖余额 {p['available_quantity']} 股。",
                "source_ids": [source_id],
                "judgment": "待核实，暂不形成买卖判断。",
                "counterargument": "成本和数量不能证明投资价值；缺失行情、公告及论点，无法核验最新变化。",
                "trigger": "补齐带来源和截止时间的市场证据、持仓论点及约束后重新检查；本报告不会创建订单。",
                "missing_data": MISSING,
            }
            for p in snapshot["positions"]
        ],
        "missing_data": MISSING
        + (["期初起算日在未来，当前记录仅为预设快照"] if opening_future else []),
        "scope": "手动账本检查；非已核验收盘日报。未调用外部模型或行情；没有市值、浮盈或总资产估算，未创建建议、订单或成交。",
    }
    return ReportContent.model_validate(payload).model_dump(mode="json")


def detail(tx, row: dict) -> dict:
    payload = row["payload"]
    if digest(payload) != row["content_hash"] or str(row["report_id"]) != payload.get(
        "report_id"
    ):
        raise SimulationError(
            "REPORT_INTEGRITY", "报告数据口径异常，已停止展示，请核对。", 409
        )
    expired = tx.now >= datetime.fromisoformat(payload["expires_at"])
    return {
        "report": payload,
        "sequence": row["sequence"],
        "content_hash": row["content_hash"],
        "current_version": tx.account["ledger_version"],
        "expired": expired,
        "is_stale": expired
        or tx.account["ledger_version"] != payload["snapshot"]["version"]
        or tx.now.astimezone(CHINA).date().isoformat() != payload["report_date"],
    }


async def generate(user_id: str, request: ReportRequest) -> dict:
    async with accounts.transaction(user_id) as tx:
        require_account(tx)
        await tx.expire()
        previous = await db.by_request(tx, request.client_request_id)
        if previous:
            if previous["requested_version"] != request.account_version:
                raise SimulationError(
                    "REPORT_REQUEST_CONFLICT", "同一报告请求不能对应不同账本版本。", 409
                )
            return detail(tx, previous)
        if tx.account["ledger_version"] != request.account_version:
            raise SimulationError(
                "STALE_LEDGER", "账本或预留已变化，请刷新后重新生成报告。", 409
            )
        snapshot = await ledger_summary(tx)
        payload = content(
            snapshot, tx.now, tx.account["opening_payload"]["opening_date"]
        )
        return detail(tx, await db.insert(tx, request, payload, digest(payload)))


async def get_report(user_id: str, report_id: UUID) -> dict:
    async with accounts.transaction(user_id) as tx:
        require_account(tx)
        await tx.expire()
        row = await db.by_id(tx, report_id)
        if not row:
            raise SimulationError("REPORT_NOT_FOUND", "报告不存在。", 404)
        return detail(tx, row)


async def list_reports(user_id: str, before: int | None, limit: int) -> dict:
    async with accounts.transaction(user_id) as tx:
        require_account(tx)
        await tx.expire()
        rows = await db.page(tx, before, limit)
        items = []
        for row in rows[:limit]:
            data = detail(tx, row)
            report = data["report"]
            items.append(
                {
                    **{
                        key: report[key]
                        for key in (
                            "report_id",
                            "report_date",
                            "generated_at",
                            "expires_at",
                            "title",
                            "status",
                        )
                    },
                    "sequence": row["sequence"],
                    "snapshot_version": report["snapshot"]["version"],
                    "is_stale": data["is_stale"],
                    "expired": data["expired"],
                }
            )
        return {
            "items": items,
            "next_cursor": rows[limit - 1]["sequence"] if len(rows) > limit else None,
        }
