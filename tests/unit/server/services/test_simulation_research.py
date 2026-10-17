from datetime import datetime

import pytest

from src.data_client.sina_research import SinaResearchError
from src.server.services import simulation_research as research


NOW = datetime.fromisoformat("2026-10-15T16:00:00+08:00")
POSITIONS = [{"code": "000001", "exchange": "SZ"}]


def test_runtime_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("SIMULATION_RESEARCH_ENABLED", raising=False)

    assert research.settings.is_simulation_research_enabled() is False


def test_source_dates_must_match_the_requested_range():
    research._validate_window(
        [{"ann_date": "2026-10-15"}], "ann_date", NOW.date(), NOW.date()
    )
    with pytest.raises(SinaResearchError, match="date_out_of_range"):
        research._validate_window(
            [{"ann_date": "2026-10-16"}], "ann_date", NOW.date(), NOW.date()
        )


@pytest.mark.asyncio
async def test_research_provider_is_disabled_without_explicit_runtime_flag(monkeypatch):
    monkeypatch.setattr(
        research.settings, "is_simulation_research_enabled", lambda: False
    )

    result = await research.collect(POSITIONS, NOW)

    assert result == {
        "enabled": False,
        "announcements": {},
        "research_reports": {},
        "sources": [],
        "issues": {},
    }


@pytest.mark.asyncio
async def test_collect_freezes_announcement_and_research_metadata_only(monkeypatch):
    monkeypatch.setattr(
        research.settings, "is_simulation_research_enabled", lambda: True
    )

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get_announcements(self, code, start, end):
            assert (code, start, end) == ("000001.SZ", "20251015", "20261015")
            return b"announcement", {
                "PaperCode": "000001",
                "StartDate": start,
                "EndDate": end,
            }

        async def get_research_reports(self, code, start, end):
            return b"research", {
                "symbol": "sz000001",
                "start_date": "2025-10-15",
                "end_date": "2026-10-15",
            }

    monkeypatch.setattr(research, "SinaResearchClient", Client)
    monkeypatch.setattr(
        research,
        "parse_announcements",
        lambda raw, code, fetched: [
            {"id": "a1", "title": "虚构年报", "ann_date": "2026-04-01"}
        ],
    )
    monkeypatch.setattr(
        research,
        "parse_research_reports",
        lambda raw, code, fetched: [
            {"id": "r1", "title": "虚构评级", "published_date": "2026-04-02"}
        ],
    )

    result = await research.collect(POSITIONS, NOW)

    assert result["enabled"] is True
    assert result["announcements"]["000001.SZ"][0]["title"] == "虚构年报"
    assert result["research_reports"]["000001.SZ"][0]["title"] == "虚构评级"
    assert {source["kind"] for source in result["sources"]} == {
        "announcement",
        "analyst_research",
    }
    assert all(source["fetched_at"] for source in result["sources"])
    assert not result["issues"]


@pytest.mark.asyncio
async def test_research_provider_caps_holding_count_before_opening_client(monkeypatch):
    monkeypatch.setattr(
        research.settings, "is_simulation_research_enabled", lambda: True
    )

    class ForbiddenClient:
        def __init__(self):
            raise AssertionError("must reject before network setup")

    monkeypatch.setattr(research, "SinaResearchClient", ForbiddenClient)
    positions = [{"code": f"{index:06d}", "exchange": "SH"} for index in range(21)]

    result = await research.collect(positions, NOW)

    assert result["sources"] == []
    assert result["issues"]["all"] == "持仓数量超出公告和研报检查上限"
