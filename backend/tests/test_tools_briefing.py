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
        "<item><title>Headline one - Paper</title></item>"
        "<item><title>Headline two - TV</title></item></channel>",
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
