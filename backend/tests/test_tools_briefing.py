"""Morning briefing: composition and graceful per-section degradation.

Calendar, mail, and the curl-fetched weather/headlines are all mocked.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.planner.schemas import ToolResult
from app.tools import briefing as briefing_module
from app.tools._common import CommandOutput
from app.tools.briefing import MorningBriefingTool, _greeting


def _tool() -> MorningBriefingTool:
    return MorningBriefingTool(Settings(briefing_location="Vellore"))


def _mock_all(
    monkeypatch: pytest.MonkeyPatch,
    *,
    events: list[str] | None = None,
    unread: tuple[int, list[dict[str, str]]] | None = None,
    weather: str = "Patchy rain nearby +29°C",
    rss: str = "",
) -> None:
    async def fake_calendar_run(self, args):  # type: ignore[no-untyped-def]
        if events is None:
            return ToolResult.failure("calendar", "denied")
        return ToolResult(tool="calendar", ok=True, summary="", data={"events": events})

    async def fake_scan(sender, include_body, limit, unread_only=True, keyword=None):  # type: ignore[no-untyped-def]
        if unread is None:
            return CommandOutput(1, "", "denied")
        return unread

    async def fake_run_command(argv, cwd=None, timeout=30.0):  # type: ignore[no-untyped-def]
        url = argv[-1]
        if "wttr.in" in url:
            return CommandOutput(0, weather, "")
        return CommandOutput(0, rss, "")  # news RSS

    monkeypatch.setattr(briefing_module.CalendarTool, "run", fake_calendar_run)
    monkeypatch.setattr(briefing_module.mail_module, "_scan_inbox", fake_scan)
    monkeypatch.setattr(briefing_module, "run_command", fake_run_command)


def test_greeting_varies_by_hour() -> None:
    from datetime import datetime

    assert _greeting(datetime(2026, 7, 21, 8)) == "Good morning"
    assert _greeting(datetime(2026, 7, 21, 14)) == "Good afternoon"
    assert _greeting(datetime(2026, 7, 21, 20)) == "Good evening"


async def test_briefing_combines_all_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_all(
        monkeypatch,
        events=["Standup (9:00 AM - 9:15 AM)", "Lecture (11:00 AM - 12:00 PM)"],
        unread=(3, [{"from": "Alice <a@x.com>", "subject": "", "body": ""}]),
        weather="Sunny +31°C",
        rss="<channel><title>Google News</title>"
        "<item><title>Headline one - Paper</title>"
        "<pubDate>Tue, 21 Jul 2026 05:00:00 GMT</pubDate></item>"
        "<item><title>Headline two - TV</title>"
        "<pubDate>Tue, 21 Jul 2026 04:00:00 GMT</pubDate></item></channel>",
    )
    result = await _tool().execute({})
    assert result.ok, result.summary
    s = result.summary
    assert "2 calendar events today" in s
    assert "3 unread emails, including from Alice" in s
    assert "weather in Vellore is Sunny 31°C" in s  # + stripped
    assert "Headline one" in s and "Paper" not in s  # publisher trimmed
    assert "Google News" not in s  # feed name never leaks


async def test_briefing_drops_failed_sections_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Calendar denied, no unread, weather blocked (HTML), news empty.
    _mock_all(monkeypatch, events=None, unread=(0, []), weather="<html>blocked</html>", rss="")
    result = await _tool().execute({})
    assert result.ok, result.summary
    s = result.summary
    assert s.startswith("Good ")  # greeting + date always present
    assert "No unread email." in s
    assert "weather" not in s.lower()  # HTML page rejected, line dropped
    assert "calendar" not in s.lower()  # denied, line dropped


async def test_headlines_sorted_by_actual_publish_time_not_feed_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Google's 'top stories' order blends relevance with recency; the
    briefing must report the most RECENTLY published headlines, so an older
    item listed first in the feed must not outrank a newer one listed later."""
    _mock_all(
        monkeypatch,
        events=[],
        unread=(0, []),
        weather="<html>",
        rss="<channel><title>Google News</title>"
        "<item><title>Older, listed first - Paper</title>"
        "<pubDate>Mon, 20 Jul 2026 10:00:00 GMT</pubDate></item>"
        "<item><title>Newest, listed second - Paper</title>"
        "<pubDate>Tue, 21 Jul 2026 09:00:00 GMT</pubDate></item>"
        "<item><title>Middle, listed third - Paper</title>"
        "<pubDate>Tue, 21 Jul 2026 03:00:00 GMT</pubDate></item></channel>",
    )
    result = await _tool().execute({})
    part = result.summary.split("Top headlines: ")[1]
    # Newest first, regardless of feed order.
    assert part.index("Newest, listed second") < part.index("Middle, listed third")
    assert part.index("Middle, listed third") < part.index("Older, listed first")


async def test_headlines_skip_items_with_unparseable_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_all(
        monkeypatch,
        events=[],
        unread=(0, []),
        weather="<html>",
        rss="<channel>"
        "<item><title>No date - Paper</title><pubDate>not a date</pubDate></item>"
        "<item><title>Good headline - Paper</title>"
        "<pubDate>Tue, 21 Jul 2026 05:00:00 GMT</pubDate></item></channel>",
    )
    result = await _tool().execute({})
    assert "Good headline" in result.summary
    assert "No date" not in result.summary


async def test_headline_internal_semicolon_does_not_read_as_two_headlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real bug: Google News sometimes titles a single roundup story with its
    own '; ' inside it ("...adjourned; Kharge raises..."), which collided
    with the '; ' used to join separate headlines and made it sound like two
    stories instead of one."""
    _mock_all(
        monkeypatch,
        events=[],
        unread=(0, []),
        weather="<html>",
        rss="<channel><item><title>"
        "Parliament adjourned; Kharge raises CJP protest - The Hindu"
        "</title><pubDate>Tue, 21 Jul 2026 05:00:00 GMT</pubDate></item></channel>",
    )
    result = await _tool().execute({})
    assert "Parliament adjourned, Kharge raises CJP protest" in result.summary


async def test_weather_uses_configured_location_without_calling_corelocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit JARVIS_BRIEFING_LOCATION must win outright — Location
    Services shouldn't even be queried."""
    _mock_all(monkeypatch, events=[], unread=(0, []), weather="Sunny +31°C", rss="")

    async def explode() -> str | None:
        raise AssertionError("current_city() must not be called when a location is configured")

    monkeypatch.setattr(briefing_module._location, "current_city", explode)
    result = await _tool().execute({})
    assert "weather in Vellore" in result.summary


async def test_weather_auto_detects_location_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_all(monkeypatch, events=[], unread=(0, []), weather="Sunny +31°C", rss="")

    async def fake_current_city() -> str | None:
        return "Chennai"

    monkeypatch.setattr(briefing_module._location, "current_city", fake_current_city)
    tool = MorningBriefingTool(Settings(briefing_location=None))
    result = await tool.execute({})
    assert "weather in Chennai" in result.summary


async def test_weather_falls_back_when_location_detection_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Denied permission / no fix / unsupported platform must degrade to the
    pre-existing behaviour (IP geolocation via a blank wttr.in query), not
    fail the whole briefing."""
    calls: list[str] = []

    async def fake_run_command(argv, cwd=None, timeout=30.0):  # type: ignore[no-untyped-def]
        calls.append(argv[-1])
        if "wttr.in" in argv[-1]:
            return CommandOutput(0, "Sunny +31°C", "")
        return CommandOutput(0, "", "")

    async def fake_calendar_run(self, args):  # type: ignore[no-untyped-def]
        return ToolResult.failure("calendar", "denied")

    async def fake_scan(sender, include_body, limit, unread_only=True, keyword=None):  # type: ignore[no-untyped-def]
        return (0, [])

    async def fake_current_city() -> str | None:
        return None

    monkeypatch.setattr(briefing_module.CalendarTool, "run", fake_calendar_run)
    monkeypatch.setattr(briefing_module.mail_module, "_scan_inbox", fake_scan)
    monkeypatch.setattr(briefing_module, "run_command", fake_run_command)
    monkeypatch.setattr(briefing_module._location, "current_city", fake_current_city)

    tool = MorningBriefingTool(Settings(briefing_location=None))
    result = await tool.execute({})
    assert "weather is Sunny 31°C" in result.summary  # no "in <city>" clause
    assert calls[0] == "https://wttr.in/?format=%C+%t&m"  # blank location, as before


async def test_briefing_dedupes_repeated_email_senders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_all(
        monkeypatch,
        events=[],
        unread=(2, [
            {"from": "VIT CDC <cdc@vit.ac.in>", "subject": "", "body": ""},
            {"from": "VIT CDC <cdc@vit.ac.in>", "subject": "", "body": ""},
        ]),
        weather="<html>",
        rss="",
    )
    result = await _tool().execute({})
    assert "including from VIT CDC." in result.summary
    assert result.summary.count("VIT CDC") == 1  # not repeated
