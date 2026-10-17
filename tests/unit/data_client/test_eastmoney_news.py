import json

import pytest

from src.data_client.eastmoney_news import (
    EastmoneyNewsError,
    parse_stock_news,
)


def _response(rows: list[dict], *, callback: str = "jQuery_codex_1") -> bytes:
    return (
        callback
        + "("
        + json.dumps({"result": {"cmsArticleWebOld": rows}}, ensure_ascii=False)
        + ");"
    ).encode()


def test_parse_stock_news_keeps_bounded_attributed_metadata():
    rows = [
        {
            "code": "202610163431529001",
            "title": "<em>浦发银行</em>动态",
            "content": "公司公告摘要",
            "date": "2026-10-16 09:30:00",
            "mediaName": "财经媒体",
        }
    ]

    items = parse_stock_news(_response(rows), "600000.SH")

    assert items == [
        {
            "id": "202610163431529001",
            "title": "浦发银行动态",
            "summary": "公司公告摘要",
            "published_at": "2026-10-16T09:30:00+08:00",
            "publisher": "财经媒体",
            "url": "https://finance.eastmoney.com/a/202610163431529001.html",
            "category": "news",
        }
    ]


@pytest.mark.parametrize(
    "raw,code",
    [(b"not jsonp", "600000.SH"), (_response([{"code": "bad", "title": "x"}]), "600000.SH")],
)
def test_parse_stock_news_rejects_untrusted_or_mismatched_data(raw, code):
    with pytest.raises(EastmoneyNewsError):
        parse_stock_news(raw, code)
