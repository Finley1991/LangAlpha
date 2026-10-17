import json
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest

from src.config import settings
from src.server.services import simulation_financials

NOW = datetime(2026, 10, 17, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
POSITIONS = [{"code": "600000", "exchange": "SH", "name": "虚构证券"}]


def test_tushare_token_is_not_exposed_to_financial_collector_without_opt_in(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "fictional-token")
    monkeypatch.delenv("SIMULATION_FINANCIALS_ENABLED", raising=False)
    assert settings.get_simulation_financials_token() is None

    monkeypatch.setenv("SIMULATION_FINANCIALS_ENABLED", "1")
    assert settings.get_simulation_financials_token() == "fictional-token"


@pytest.mark.asyncio
async def test_financial_collection_is_disabled_without_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("SIMULATION_FINANCIALS_ENABLED", raising=False)
    monkeypatch.setattr(settings, "get_simulation_financials_token", lambda: None)

    result = await simulation_financials.collect(POSITIONS, NOW)

    assert result == {"enabled": False, "financials": {}, "sources": [], "issues": {}}


@pytest.mark.asyncio
async def test_financial_collection_freezes_latest_disclosed_data_and_disclosure_plan(
    monkeypatch,
):
    monkeypatch.setenv("SIMULATION_FINANCIALS_ENABLED", "1")
    monkeypatch.setattr(
        settings, "get_simulation_financials_token", lambda: "fictional-token"
    )
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        api = body["api_name"]
        if api == "disclosure_date":
            rows = [{
                "ts_code": "600000.SH",
                "ann_date": "20260829",
                "end_date": body["params"]["end_date"],
                "pre_date": "20261028",
                "actual_date": None,
                "modify_date": None,
            }]
        else:
            period = "20260630" if api != "fina_indicator" else "20260630"
            rows = [{
                "ts_code": "600000.SH",
                "ann_date": "20260829",
                "f_ann_date": "20260829" if api != "fina_indicator" else None,
                "end_date": period,
                "report_type": "1" if api != "fina_indicator" else None,
                "basic_eps": Decimal("0.42"),
                "total_revenue": Decimal("1000000"),
                "revenue": Decimal("900000"),
                "operate_profit": Decimal("120000"),
                "total_profit": Decimal("110000"),
                "n_income": Decimal("80000"),
                "n_income_attr_p": Decimal("75000"),
                "total_cur_assets": Decimal("600000"),
                "total_assets": Decimal("1200000"),
                "total_cur_liab": Decimal("300000"),
                "total_liab": Decimal("500000"),
                "total_hldr_eqy_exc_min_int": Decimal("700000"),
                "money_cap": Decimal("200000"),
                "n_cashflow_act": Decimal("90000"),
                "n_cashflow_inv_act": Decimal("-30000"),
                "n_cash_flows_fnc_act": Decimal("10000"),
                "c_cash_equ_end_period": Decimal("220000"),
                "eps": Decimal("0.42"),
                "profit_dedt": Decimal("70000"),
                "gross_margin": Decimal("25.4"),
                "current_ratio": Decimal("2.0"),
                "quick_ratio": Decimal("1.5"),
                "debt_to_assets": Decimal("41.7"),
                "roe": Decimal("8.5"),
                "roa": Decimal("3.2"),
                "ocfps": Decimal("0.65"),
            }]
            future = dict(rows[0], ann_date="20261020", f_ann_date="20261020", end_date="20260930")
            rows.append(future)
        fields = body["fields"].split(",")
        return httpx.Response(
            200,
            content=json.dumps({
                "code": 0,
                "data": {
                    "fields": fields,
                    "items": [[row.get(field) for field in fields] for row in rows],
                },
            }, default=str),
            headers={"content-type": "application/json"},
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        simulation_financials.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )

    result = await simulation_financials.collect(POSITIONS, NOW)

    assert {call["api_name"] for call in calls} == {
        "income", "balancesheet", "cashflow", "fina_indicator", "disclosure_date"
    }
    disclosure_call = next(call for call in calls if call["api_name"] == "disclosure_date")
    assert disclosure_call["params"]["end_date"] == "20260630"
    assert all(call["token"] == "fictional-token" for call in calls)
    evidence = result["financials"]["600000.SH"]
    assert evidence["tables"]["income"]["end_date"] == "2026-06-30"
    assert evidence["tables"]["income"]["ann_date"] == "2026-08-29"
    assert evidence["tables"]["income"]["metrics"]["total_revenue"] == "1000000"
    assert evidence["tables"]["fina_indicator"]["actual_ann_date"] is None
    assert evidence["disclosure"]["end_date"] == "2026-06-30"
    assert evidence["disclosure"]["planned_date"] == "2026-10-28"
    assert evidence["disclosure"]["actual_date"] is None
    assert all(source["kind"] == "financial_statement" for source in result["sources"])
    assert len(evidence["source_ids"]) == 5
    assert result["issues"] == {}
    assert "fictional-token" not in json.dumps(result)


@pytest.mark.asyncio
async def test_financial_permission_failure_is_sanitized_and_kept_pending(monkeypatch):
    from src.data_client.tushare import TushareError

    monkeypatch.setenv("SIMULATION_FINANCIALS_ENABLED", "1")
    monkeypatch.setattr(
        settings, "get_simulation_financials_token", lambda: "fictional-token"
    )

    async def denied(*_args, **_kwargs):
        raise TushareError("permission_denied", 2002)

    monkeypatch.setattr(simulation_financials, "request_table", denied)

    result = await simulation_financials.collect(POSITIONS, NOW)

    assert result["financials"] == {}
    assert "600000.SH" in result["issues"]
    assert "权限" in result["issues"]["600000.SH"]
    assert "fictional-token" not in json.dumps(result)
    assert "2002" not in json.dumps(result)


def test_report_content_freezes_financial_tables_with_provenance():
    from src.server.services.simulation_reports import content

    snapshot = {
        "account_id": "00000000-0000-0000-0000-000000000001",
        "version": 1,
        "as_of": NOW.isoformat(),
        "cash": "0.00",
        "available_cash": "0.00",
        "reserved_cash": "0.00",
        "realized_pnl": "0.00",
        "positions": [{
            "code": "600000", "exchange": "SH", "name": "虚构证券",
            "quantity": "100", "total_cost": "1000.00",
            "available_quantity": "100", "lots": [],
        }],
        "sellable_basis": "虚构测试口径",
    }
    table = {
        "table": "income", "end_date": "2026-06-30", "ann_date": "2026-08-29",
        "actual_ann_date": "2026-08-29", "report_type": "1",
        "metrics": {"total_revenue": "1000000"}, "source_ids": ["financial-income"],
    }
    source = {
        "source_id": "financial-income", "kind": "financial_statement",
        "title": "Tushare income 结构化财务数据",
        "reference": "https://tushare.pro/document/2?doc_id=33",
        "as_of": NOW.isoformat(), "fetched_at": NOW.isoformat(),
        "evidence": {"api": "income", "row_count": 1},
    }
    financials = {
        "enabled": True, "sources": [source], "issues": {},
        "financials": {"600000.SH": {
            "tables": {"income": table},
            "disclosure": {"end_date": "2026-09-30", "planned_date": "2026-10-28",
                           "actual_date": None, "source_ids": ["financial-disclosure"]},
            "fetched_at": NOW.isoformat(),
            "source_ids": ["financial-income", "financial-disclosure"],
        }},
    }

    report = content(snapshot, NOW, "2020-01-01", financials=financials)

    check = report["checks"][0]
    assert check["financials"]["tables"]["income"]["metrics"]["total_revenue"] == "1000000"
    assert check["financials"]["disclosure"]["planned_date"] == "2026-10-28"
    assert "financial-income" in check["source_ids"]
    assert any(source["kind"] == "financial_statement" for source in report["sources"])
    assert "结构化财务数值" in report["summary"]
    assert "风险说明及审计意见" in " ".join(check["missing_data"])
    from src.server.models.simulation_reports import ReportContent

    ReportContent.model_validate(report)
