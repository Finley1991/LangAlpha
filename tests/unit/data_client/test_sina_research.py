import json
from datetime import datetime

import pytest

from src.data_client.sina_research import (
    SinaResearchError,
    parse_announcements,
    parse_research_reports,
    SinaResearchClient,
)


FETCHED = datetime.fromisoformat("2026-10-15T16:00:00+08:00")


def envelope(data):
    return {
        "result": {
            "status": {"code": 0},
            "data": data,
        }
    }


def test_announcement_parser_maps_provider_type_fields_to_dictionary_labels():
    raw = {
        "otherinfo": {"PaperCode": "000001"},
        "data": [
            {
                "PaperCode": "000001",
                "ID": "10379539",
                "date": "2024-08-16 00:00:00",
                "EndDate": "20240630",
                "type": "中期报告",
                "type2": "财务指标",
                "title": "平安银行：2024年半年度报告",
                "url": "https://example.invalid/announcement",
                "pdf_path": "https://example.invalid/report.pdf",
            }
        ],
    }

    result = parse_announcements(
        json.dumps(envelope(raw)).encode(), "000001.SZ", FETCHED
    )

    assert result == [
        {
            "id": "10379539",
            "paper_code": "000001",
            "ann_date": "2024-08-16",
            "end_date": "20240630",
            "ann_type": "中期报告",
            "event_category": "财务指标",
            "title": "平安银行：2024年半年度报告",
            "url": "https://example.invalid/announcement",
            "pdf_path": "https://example.invalid/report.pdf",
        }
    ]


def test_announcement_parser_rejects_records_for_another_security():
    raw = envelope(
        {"data": [{"PaperCode": "600000", "ID": "1", "title": "other company"}]}
    )

    with pytest.raises(SinaResearchError, match="instrument_mismatch"):
        parse_announcements(json.dumps(raw).encode(), "000001.SZ", FETCHED)


def test_research_parser_keeps_metadata_but_never_stores_full_report_body():
    raw = envelope(
        {
            "total": "1",
            "data": [
                {
                    "id": "r-1",
                    "title": "虚构公司的研究报告",
                    "orgname": "测试机构",
                    "author": "测试分析师",
                    "adddate": "2026-10-10",
                    "rpt_name": "公司",
                    "isvalid": 1,
                    "rating": "买入",
                    "goal_price": "12.50",
                    "symbol": "sh600900",
                    "reportinfo": "必须丢弃的全文",
                    "detail_url": "https://example.invalid/research",
                }
            ],
            "more": {"SYMBOL": "sh600900", "SNAME": "长江电力"},
        }
    )

    result = parse_research_reports(json.dumps(raw).encode(), "600900.SH", FETCHED)

    assert result == [
        {
            "id": "r-1",
            "title": "虚构公司的研究报告",
            "institution": "测试机构",
            "authors": "测试分析师",
            "published_date": "2026-10-10",
            "report_type": "公司",
            "rating": "买入",
            "target_price": "12.50",
            "symbol": "sh600900",
            "url": "https://example.invalid/research",
        }
    ]
    assert "reportinfo" not in result[0]


def test_research_parser_rejects_malformed_provider_status():
    raw = {"result": {"status": {"code": 1}, "data": {"data": []}}}

    with pytest.raises(SinaResearchError, match="provider_error"):
        parse_research_reports(json.dumps(raw).encode(), "600900.SH", FETCHED)


@pytest.mark.asyncio
async def test_client_uses_public_hosts_and_both_required_date_parameters(monkeypatch):
    calls = []

    async with SinaResearchClient() as client:

        async def capture(url, params):
            calls.append((url, params))
            return b"{}"

        monkeypatch.setattr(client, "_read", capture)
        await client.get_announcements("000001.SZ", "20251015", "20261015")
        await client.get_research_reports("600900.SH", "20251015", "20261015")

    announcement_url, announcement = calls[0]
    report_url, report = calls[1]
    assert announcement_url.startswith("https://money.finance.sina.com.cn/")
    assert (
        announcement["StartDate"] == "20251015"
        and announcement["EndDate"] == "20261015"
    )
    assert (
        announcement["Type"] == "recommend_ai" and announcement["PaperCode"] == "000001"
    )
    assert "SubType" not in announcement and "SubType2" not in announcement
    assert report_url.startswith("https://stock.finance.sina.com.cn/")
    assert report["kind"] == "lastest" and report["symbol"] == "sh600900"
    assert report["start_date"] == "2025-10-15" and report["end_date"] == "2026-10-15"
    assert report["get_content_flag"] == "0"
