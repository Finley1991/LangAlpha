import json

import pytest

from src.data_client.sina_stock_news import (
    DETAIL_ENDPOINT,
    LIST_ENDPOINT,
    SinaStockNewsClient,
    SinaStockNewsError,
    parse_article_detail,
    parse_stock_news_list,
)


def test_parse_stock_news_list_requires_comos_id_and_sina_article_url():
    payload = {
        "result": {
            "status": {"code": 0},
            "data": [
                {
                    "docid": "comos:kqcfnca4034650",
                    "short_title": "个股新闻标题",
                    "create_date": "2026-10-16",
                    "create_time": "09:30:00",
                    "media_source": "财经媒体",
                    "url": "https://finance.sina.com.cn/news/example.shtml",
                },
                {"docid": "zb724", "title": "无法取详情"},
                {
                    "docid": "comos:anotherdoc",
                    "title": "外部链接",
                    "url": "https://example.com/article",
                },
                {
                    "docid": "comos:nonstandardport",
                    "title": "非标准端口链接",
                    "url": "https://finance.sina.com.cn:8443/article",
                },
            ],
        }
    }

    items = parse_stock_news_list(json.dumps(payload).encode(), "600519.SH")

    assert items == [
        {
            "doc_id": "kqcfnca4034650",
            "title": "个股新闻标题",
            "published_at": "2026-10-16T09:30:00+08:00",
            "publisher": "财经媒体",
            "url": "https://finance.sina.com.cn/news/example.shtml",
            "category": "news",
        }
    ]


def test_parse_article_detail_extracts_only_bounded_plain_text():
    payload = {
        "title": "文章标题",
        "content": [
            {"content": "<p>正文摘要<strong>加粗</strong></p><script>忽略代码</script>"}
        ],
    }

    result = parse_article_detail(json.dumps(payload).encode())

    assert result == "正文摘要 加粗"


def test_parse_article_detail_caps_summary_and_rejects_invalid_payload():
    payload = {"content": [{"content": "<p>" + "甲" * 600 + "</p>"}]}
    result = parse_article_detail(json.dumps(payload).encode())

    assert result == "甲" * 320
    with pytest.raises(SinaStockNewsError):
        parse_article_detail(b"not json")


def test_parse_stock_news_list_rejects_service_error_and_oversized_payload():
    with pytest.raises(SinaStockNewsError):
        parse_stock_news_list(
            json.dumps({"result": {"status": {"code": 1}, "data": []}}).encode(),
            "600519.SH",
        )
    with pytest.raises(SinaStockNewsError):
        parse_stock_news_list(b" " * 600_000, "600519.SH")


@pytest.mark.asyncio
async def test_client_returns_report_evidence_id_and_short_detail_summary(monkeypatch):
    client = SinaStockNewsClient()
    list_response = json.dumps(
        {
            "result": {
                "status": {"code": 0},
                "data": [
                    {
                        "docid": "comos:kqcfnca4034650",
                        "title": "个股新闻",
                        "create_date": "2026-10-16",
                        "create_time": "09:30:00",
                        "media_source": "新浪财经",
                        "url": "https://finance.sina.com.cn/news/example.shtml",
                    }
                ],
            }
        }
    ).encode()
    detail_response = json.dumps({"content": [{"content": "<p>详情摘要</p>"}]}).encode()

    async def fake_get_json(endpoint, params):
        if endpoint == LIST_ENDPOINT:
            assert params["symbol"] == "sh600519"
            return list_response
        assert endpoint == DETAIL_ENDPOINT
        assert params["docID"] == "kqcfnca4034650"
        return detail_response

    monkeypatch.setattr(client, "_get_json", fake_get_json)
    try:
        items = await client.get_stock_news("600519.SH", detail_limit=1)
    finally:
        await client.close()

    assert items[0]["id"] == "sina-kqcfnca4034650"
    assert items[0]["summary"] == "详情摘要"
    assert "doc_id" not in items[0]
