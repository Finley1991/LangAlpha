from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.config import settings
from src.server.services import simulation_news

NOW = datetime(2026, 10, 16, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
POSITIONS = [
    {"code": "600000", "exchange": "SH", "name": "虚构证券"},
]


@pytest.mark.asyncio
async def test_news_collection_is_disabled_without_explicit_runtime_opt_in(monkeypatch):
    monkeypatch.delenv("SIMULATION_NEWS_ENABLED", raising=False)

    result = await simulation_news.collect(POSITIONS, NOW)

    assert result == {"enabled": False, "items": {}, "sources": [], "issues": {}}


@pytest.mark.asyncio
async def test_news_collection_freezes_sina_and_eastmoney_only(monkeypatch):
    monkeypatch.setenv("SIMULATION_NEWS_ENABLED", "1")
    monkeypatch.setattr(settings, "is_simulation_news_enabled", lambda: True)

    class NewsClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get_stock_news(self, ts_code):
            assert ts_code == "600000.SH"
            return [
                {
                    "id": "eastmoney-1",
                    "title": "新闻标题",
                    "summary": "新闻摘要",
                    "published_at": "2026-10-16T09:00:00+08:00",
                    "publisher": "财经媒体",
                    "url": "https://finance.eastmoney.com/a/202610163431529001.html",
                    "category": "news",
                }
            ]

    class SinaNewsClient(NewsClient):
        async def get_stock_news(self, ts_code, detail_limit=3):
            assert ts_code == "600000.SH"
            assert detail_limit == 3
            return [
                {
                    "id": "sina-kqcfnca4034650",
                    "title": "新浪财经新闻",
                    "summary": "仅保留的短摘要",
                    "published_at": "2026-10-16T09:10:00+08:00",
                    "publisher": "新浪财经",
                    "url": "https://finance.sina.com.cn/article/example.shtml",
                    "category": "news",
                }
            ]

    monkeypatch.setattr(simulation_news, "EastmoneyStockNewsClient", NewsClient)
    monkeypatch.setattr(simulation_news, "SinaStockNewsClient", SinaNewsClient)
    result = await simulation_news.collect(POSITIONS, NOW)

    assert {item["category"] for item in result["items"]["600000.SH"]} == {"news"}
    assert all(
        item["url"].startswith("https://") for item in result["items"]["600000.SH"]
    )
    assert len(result["sources"]) == 2
    sina_sources = [
        source for source in result["sources"] if source["title"] == "新浪财经个股新闻"
    ]
    assert len(sina_sources) == 1
    assert sina_sources[0]["evidence"]["provider"] == "sina"
    assert any(item["publisher"] == "新浪财经" for item in result["items"]["600000.SH"])
    assert all(item["source_ids"] for item in result["items"]["600000.SH"])
    assert result["issues"] == {}


@pytest.mark.asyncio
async def test_news_query_errors_are_reported_without_provider_details(monkeypatch):
    monkeypatch.setenv("SIMULATION_NEWS_ENABLED", "1")
    monkeypatch.setattr(settings, "is_simulation_news_enabled", lambda: True)

    class NewsClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get_stock_news(self, ts_code):
            raise RuntimeError("private query and provider response")

    class SinaNewsClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get_stock_news(self, *_, **__):
            raise RuntimeError("private Sina response")

    monkeypatch.setattr(simulation_news, "EastmoneyStockNewsClient", NewsClient)
    monkeypatch.setattr(simulation_news, "SinaStockNewsClient", SinaNewsClient)
    result = await simulation_news.collect(POSITIONS, NOW)

    assert result["items"] == {}
    assert result["issues"]["600000.SH"]
    assert "private" not in str(result)
    assert "api key" not in str(result)
