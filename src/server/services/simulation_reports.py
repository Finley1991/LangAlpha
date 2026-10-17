"""Deterministic missing-data reports never call models or create trades."""

import asyncio
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import UUID, uuid4

from src.server.database import simulation_orders as accounts
from src.server.database import simulation_reports as db
from src.server.models.simulation_reports import ReportContent, ReportRequest
from src.server.services import simulation_closing
from src.server.services import simulation_research
from src.server.services import simulation_quotes
from src.server.services import simulation_news
from src.server.services import simulation_financials
from src.server.services.simulation_orders import (
    CHINA,
    SimulationError,
    ledger_summary,
    require_account,
)

CENT = Decimal("0.01")

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


def realtime_pnl(position: dict, quote: dict | None, issue: str | None) -> dict:
    source_ids = quote.get("source_ids", []) if quote else []
    reason = issue
    if not quote:
        reason = reason or "本次未取得实时行情，盈亏待核实"
    elif quote["freshness"] != "fresh":
        reason = (
            "实时行情时间异常，盈亏待核实"
            if quote["freshness"] == "future"
            else "实时行情已过期，盈亏待核实"
        )

    if reason:
        return {
            "status": "pending_verification",
            "market_value": None,
            "unrealized_pnl": None,
            "currency": "CNY",
            "as_of": None,
            "reason": reason,
            "source_ids": source_ids,
        }

    try:
        price = Decimal(quote["price"])
        quantity = Decimal(position["quantity"])
        cost = Decimal(position["total_cost"])
        if (
            quote.get("currency") != "CNY"
            or not price.is_finite()
            or price <= 0
            or not quantity.is_finite()
            or quantity <= 0
            or not cost.is_finite()
            or cost < 0
        ):
            raise InvalidOperation
        market_value = price * quantity
        pnl = market_value - cost
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return {
            "status": "pending_verification",
            "market_value": None,
            "unrealized_pnl": None,
            "currency": "CNY",
            "as_of": None,
            "reason": "报价或账本成本格式异常，盈亏待核实",
            "source_ids": source_ids,
        }

    return {
        "status": "calculated",
        "market_value": format(market_value.quantize(CENT, rounding=ROUND_HALF_UP), ".2f"),
        "unrealized_pnl": format(pnl.quantize(CENT, rounding=ROUND_HALF_UP), ".2f"),
        "currency": "CNY",
        "as_of": quote["as_of"],
        "reason": None,
        "source_ids": source_ids,
    }


def content(
    snapshot: dict,
    now: datetime,
    opening_date: str,
    closing: dict | None = None,
    research: dict | None = None,
    quotes: dict | None = None,
    news: dict | None = None,
    financials: dict | None = None,
) -> dict:
    report_id = str(uuid4())
    source_id = "ledger-" + report_id
    opening_future = opening_date > now.astimezone(CHINA).date().isoformat()
    report_missing = list(MISSING)
    if research and research.get("enabled"):
        report_missing[1] = "公告与财报正文尚未获取和核实；机构研报只代表观点"
    if financials and financials.get("enabled"):
        report_missing[1] = (
            "公告与财报正文、风险说明及审计意见尚未核实；结构化财务数据仅为数值证据"
        )
    payload = {
        "schema_version": "simulation-report-v1",
        "report_id": report_id,
        "report_date": now.astimezone(CHINA).date().isoformat(),
        "generated_at": now.isoformat(),
        "cutoff_at": snapshot["as_of"],
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
                "as_of": snapshot["as_of"],
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
                "missing_data": report_missing,
                "realtime_pnl": realtime_pnl(
                    p,
                    quotes.get("quotes", {}).get(p["code"] + "." + p["exchange"])
                    if quotes
                    else None,
                    quotes.get("issues", {}).get(
                        p["code"] + "." + p["exchange"],
                        quotes.get("issues", {}).get("all"),
                    )
                    if quotes
                    else None,
                ),
            }
            for p in snapshot["positions"]
        ],
        "missing_data": report_missing
        + (["期初起算日在未来，当前记录仅为预设快照"] if opening_future else []),
        "scope": "手动账本检查；非已核验收盘日报。实时未实现盈亏仅在报价较新时按持仓数量 × 新浪报价 − 账本总成本计算，按分四舍五入；缺失、过期或异常报价标记待核实。未创建建议、订单或成交。",
    }
    if closing:
        payload["sources"].extend(closing["sources"])
        missing = MISSING[1:]
        for check in payload["checks"]:
            code = check["code"] + "." + check["exchange"]
            price = closing["prices"].get(code)
            check["missing_data"] = list(missing)
            if price:
                check["closing_price"] = price
                check["counterargument"] = (
                    "单一收盘价格不能证明投资价值；公告、论点及投资约束仍不足，无法判断应否买卖。"
                )
                check["trigger"] = (
                    "补齐公告、研究论点和投资约束后重新检查；本报告不会创建订单。"
                )
                check["source_ids"].extend(price["source_ids"])
                check["fact"] += (
                    f" Tushare {price['trade_date']} 未复权收盘价 {price['close']} CNY（盘后历史价格，不可用于当前成交）。"
                )
            else:
                reason = closing["issues"].get(
                    code, closing["issues"].get("all", MISSING[0])
                )
                check["missing_data"].insert(0, code + "：" + reason)
        payload["missing_data"] = list(
            dict.fromkeys(
                item for check in payload["checks"] for item in check["missing_data"]
            )
        ) or list(missing)
        if opening_future:
            payload["missing_data"].append("期初起算日在未来，当前记录仅为预设快照")
        if closing["prices"]:
            payload["title"] = "持仓检查日报 · 已接入收盘证据"
            payload["summary"] = (
                "已冻结账本及可用的交易日历、未复权收盘价。公告、论点和投资约束仍待核实，暂不形成买卖判断。"
            )
        payload["scope"] = (
            "手动收盘证据检查；收盘日期按交易所日历及检查截止时间确定，取数时间不代表发布时间。缺失不回退旧价，也不以收盘价推断当前市值或收益；仅在新浪报价较新时按持仓数量 × 新浪报价 − 账本总成本计算实时未实现盈亏。未创建建议、订单或成交。"
        )
    if financials and financials.get("enabled"):
        payload["sources"].extend(financials["sources"])
        for check in payload["checks"]:
            code = check["code"] + "." + check["exchange"]
            evidence = financials["financials"].get(code)
            issue = financials["issues"].get(code) or financials["issues"].get("all")
            if evidence:
                check["financials"] = evidence
                check["source_ids"].extend(evidence["source_ids"])
                check["missing_data"] = [
                    item for item in check["missing_data"] if item != MISSING[1]
                ]
                check["missing_data"].append(report_missing[1])
                if not evidence["tables"]:
                    check["missing_data"].append("未取得已披露财务报表记录，原因待核实")
            else:
                check["missing_data"] = [
                    item for item in check["missing_data"] if item != MISSING[1]
                ]
                check["missing_data"].append(report_missing[1])
            if issue:
                check["missing_data"].append(issue)
            check["missing_data"] = list(dict.fromkeys(check["missing_data"]))
            check["counterargument"] = (
                "结构化财务指标受报告期、合并口径和更正版本影响；不能替代公告/财报正文、风险说明、审计意见或投资论点。"
            )
            check["trigger"] = (
                "核对对应报告原文与审计意见，并建立持仓论点、补齐投资约束后再检查；本报告不会创建订单。"
            )
        payload["missing_data"] = list(
            dict.fromkeys(
                item for check in payload["checks"] for item in check["missing_data"]
            )
        )
        if opening_future:
            payload["missing_data"].append("期初起算日在未来，当前记录仅为预设快照")
        if financials["financials"]:
            payload["title"] += " · 已接入结构化财务数据"
            payload["summary"] = (
                "已冻结账本、市场证据与可取得的结构化财务数据；公告/财报正文、论点和投资约束仍待核实，暂不形成买卖判断。"
            )
        payload["scope"] += (
            " 已按报告截止时间查询 Tushare 最近已披露的三表及财务指标，并单独保存报告期、公告日期和披露计划；结构化数值不替代公告/年报正文，不用于成交。"
        )

    if research and research.get("enabled"):
        payload["sources"].extend(research["sources"])
        source_ids_by_code: dict[str, list[str]] = {}
        for source in research["sources"]:
            params = source["evidence"]["params"]
            code = params.get("PaperCode")
            if code:
                source_ids_by_code.setdefault(
                    code + "." + code_exchange(code, snapshot), []
                ).append(source["source_id"])
            symbol = params.get("symbol")
            if symbol:
                code = symbol[2:] + "." + symbol[:2].upper()
                source_ids_by_code.setdefault(code, []).append(source["source_id"])
        for check in payload["checks"]:
            code = check["code"] + "." + check["exchange"]
            check["announcements"] = research["announcements"].get(code, [])
            check["analyst_reports"] = research["research_reports"].get(code, [])
            check["source_ids"].extend(source_ids_by_code.get(code, []))
            issues = research["issues"].get(code, []) + research["issues"].get(
                "all", []
            )
            check["missing_data"] = [
                item for item in check["missing_data"] if item != MISSING[1]
            ]
            if report_missing[1] not in check["missing_data"]:
                check["missing_data"].append(report_missing[1])
            if issues:
                check["missing_data"].extend(issues)
            if not check["announcements"] and not issues:
                check["missing_data"].append(
                    "近一年公告接口本次未返回记录；单页查询不能证明没有公告"
                )
            check["missing_data"] = list(dict.fromkeys(check["missing_data"]))
            check["counterargument"] = (
                "公告标题和分类、机构研报评级不能代替公告/财报正文或已记录的持仓论点；研报评级仅是机构观点。"
            )
            check["trigger"] = (
                "核对公告与财报正文、建立持仓论点并补齐投资约束后再检查；本报告不会创建订单。"
            )
        payload["missing_data"] = (
            list(
                dict.fromkeys(
                    item
                    for check in payload["checks"]
                    for item in check["missing_data"]
                )
            )
            or report_missing
        )
        if (
            opening_future
            and "期初起算日在未来，当前记录仅为预设快照" not in payload["missing_data"]
        ):
            payload["missing_data"].append("期初起算日在未来，当前记录仅为预设快照")
        if research["sources"]:
            payload["title"] = (
                "持仓检查日报 · 部分新浪元数据已获取"
                if research["issues"]
                else "持仓检查日报 · 已获取新浪元数据"
            )
            payload["summary"] = (
                "已冻结账本、可用的收盘证据及本次成功取得的新浪元数据。公告正文、财报内容和持仓论点仍待核实；研报评级仅为机构观点。"
            )
        else:
            payload["title"] = "持仓检查日报 · 公告与研报待核实"
            payload["summary"] = (
                "已冻结账本；新浪公告/研报元数据未成功取得。公告正文、财报内容和持仓论点仍待核实；本次不形成买卖判断。"
            )
        payload["scope"] += (
            " 新浪查询限近一年单页公告与研报元数据；不下载公告 PDF 或保存研报全文，不把机构评级作为事实或交易依据。"
        )
    if quotes:
        payload["sources"].extend(quotes["sources"])
        for check in payload["checks"]:
            code = check["code"] + "." + check["exchange"]
            quote = quotes["quotes"].get(code)
            issue = quotes["issues"].get(code, quotes["issues"].get("all"))
            check["missing_data"].append("成交所需交易日历、停牌和涨跌停边界仍待核验")
            if quote:
                check["current_quote"] = quote
                check["source_ids"].extend(quote["source_ids"])
                check["fact"] += (
                    f" 新浪实时行情快照 {quote['price']} CNY，报价时间 "
                    f"{quote['as_of']}；新鲜度 {quote['freshness']}。"
                )
                check["counterargument"] += (
                    " 实时报价只是短时快照，不代表可信成交价格或完整行情边界。"
                )
                if quote["freshness"] != "fresh" and not issue:
                    issue = (
                        "实时行情时间异常，待核实"
                        if quote["freshness"] == "future"
                        else "实时行情已过期，待核实"
                    )
            if issue:
                check["missing_data"].append("实时行情：" + issue)
            check["missing_data"] = list(dict.fromkeys(check["missing_data"]))
        payload["missing_data"] = list(
            dict.fromkeys(
                item for check in payload["checks"] for item in check["missing_data"]
            )
        ) or list(payload["missing_data"])
        if quotes["sources"]:
            payload["title"] += " · 含新浪行情快照"
            payload["summary"] += "本次另含独立的新浪报价快照及报价时间。"
        payload["scope"] += (
            " 新浪实时行情仅为生成时取得的报价快照；报价时间和取数时间分别记录，"
            "陈旧或异常数据标记待核实。该快照不验证停牌、交易日历或涨跌停边界，绝不用于成交。"
        )
        payload["scope"] = payload["scope"].replace(
            "未调用外部模型或行情；", "未调用外部模型；"
        )
    if news and news.get("enabled"):
        payload["sources"].extend(news["sources"])
        for check in payload["checks"]:
            code = check["code"] + "." + check["exchange"]
            items = news["items"].get(code, [])
            check["news"] = items
            check["source_ids"].extend(
                source_id for item in items for source_id in item.get("source_ids", [])
            )
            issues = news["issues"].get(code, []) + news["issues"].get("all", [])
            check["missing_data"].extend(issues)
            if not items and not issues:
                check["missing_data"].append(
                    "本次个股新闻接口未返回可核对记录；不代表没有相关新闻"
                )
            check["missing_data"] = list(dict.fromkeys(check["missing_data"]))
            check["counterargument"] += (
                " 新闻标题和摘要属于来源材料，须回查原文及正式披露，不能单独证明观点。"
            )
        payload["missing_data"] = list(
            dict.fromkeys(item for check in payload["checks"] for item in check["missing_data"])
        ) or list(payload["missing_data"])
        if opening_future and "期初起算日在未来，当前记录仅为预设快照" not in payload["missing_data"]:
            payload["missing_data"].append("期初起算日在未来，当前记录仅为预设快照")
        if news["sources"]:
            partial = bool(news["issues"])
            payload["title"] += " · 含个股新闻" if not partial else " · 部分个股新闻已获取"
            payload["summary"] += "本次冻结了个股新闻来源；内容仅供回查，观点仍待核实。"
        else:
            payload["title"] += " · 个股新闻待核实"
            payload["summary"] += "本次没有取得可核对的个股新闻，不能据此认定没有相关新闻。"
        payload["scope"] += (
            " 新闻标题/短摘要来自新浪财经和东方财富个股新闻接口，按来源和抓取时间冻结。"
            "不抓取全文、不把接口缺失解释为没有相关新闻，也不据此形成买卖判断。"
        )
    if financials and financials.get("financials"):
        if "结构化财务数据" not in payload["title"]:
            payload["title"] += " · 含结构化财务数据"
        payload["summary"] += (
            "本次另冻结 Tushare 结构化财务数值和披露日期；公告/年报正文、风险说明及审计意见仍待核实。"
        )
    return ReportContent.model_validate(payload).model_dump(mode="json")


def code_exchange(code: str, snapshot: dict) -> str:
    for position in snapshot["positions"]:
        if position["code"] == code:
            return position["exchange"]
    return ""


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


def previous_or_validate(
    tx, previous: dict | None, request: ReportRequest
) -> dict | None:
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
    return None


async def generate(user_id: str, request: ReportRequest) -> dict:
    async with accounts.transaction(user_id) as tx:
        require_account(tx)
        await tx.expire()
        old = previous_or_validate(
            tx, await db.by_request(tx, request.client_request_id), request
        )
        if old:
            return old
        snapshot = await ledger_summary(tx)
        opening_date = tx.account["opening_payload"]["opening_date"]
        cutoff = tx.now
    # No provider/model work while holding the financial account lock.
    closing, research, quotes, news, financials = await asyncio.gather(
        simulation_closing.collect(snapshot["positions"], cutoff),
        simulation_research.collect(snapshot["positions"], cutoff),
        simulation_quotes.collect(snapshot["positions"], cutoff),
        simulation_news.collect(snapshot["positions"], cutoff),
        simulation_financials.collect(snapshot["positions"], cutoff),
    )
    async with accounts.transaction(user_id) as tx:
        require_account(tx)
        await tx.expire()
        old = previous_or_validate(
            tx, await db.by_request(tx, request.client_request_id), request
        )
        if old:
            return old
        if (
            str(tx.account_id) != str(snapshot["account_id"])
            or tx.now.astimezone(CHINA).date() != cutoff.astimezone(CHINA).date()
        ):
            raise SimulationError(
                "STALE_LEDGER", "账户或北京时间日期已变化，请重新生成报告。", 409
            )
        payload = content(
            snapshot,
            tx.now,
            opening_date,
            closing if closing["sources"] or closing["issues"] else None,
            research if research["enabled"] or research["issues"] else None,
            quotes if quotes["sources"] or quotes["issues"] else None,
            news if news["enabled"] or news["issues"] else None,
            financials if financials["enabled"] or financials["issues"] else None,
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
