"""macOS Reminders integration."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import ClassVar

from pydantic import BaseModel, Field

from app.planner.schemas import RiskLevel, ToolResult
from app.tools._common import applescript_quote, run_osascript
from app.tools.base import Tool

_MONTH_NAMES = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_MONTH_PATTERN = "|".join(sorted(_MONTH_NAMES, key=len, reverse=True))

_RELATIVE_DAY_TIME = re.compile(
    r"(?:on )?(?P<day>today|tomorrow)"
    r"(?: (?P<daypart>morning|afternoon|evening|night))?"
    r"(?:(?: at)? (?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<period>am|pm)?)?"
)
# Hour to assume when only a daypart is spoken ("tomorrow evening").
_DAYPART_DEFAULT_HOUR = {"morning": 9, "afternoon": 15, "evening": 18, "night": 21}
_ABSOLUTE_DATE = re.compile(
    r"(?:on )?(?:"
    r"(?P<day_a>\d{1,2})(?:st|nd|rd|th)?\s+(?P<month_a>" + _MONTH_PATTERN + r")"
    r"|(?P<month_b>" + _MONTH_PATTERN + r")\s+(?P<day_b>\d{1,2})(?:st|nd|rd|th)?"
    r")(?:,?\s+(?P<year>\d{4}))?"
    r"(?:\s*(?:at)?\s*(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<period>am|pm))?"
)


class CreateReminderArgs(BaseModel):
    title: str = Field(
        min_length=1,
        description="What to remind the user about, for example 'submit the report'.",
    )
    due_at: str = Field(
        description=(
            "When to show the reminder. Use a common phrase such as 'tomorrow at 10 AM', "
            "'17 July at 9 AM', or an ISO 8601 local date and time such as "
            "'2026-07-17T10:00:00'."
        )
    )


class CreateReminderTool(Tool):
    name: ClassVar[str] = "create_reminder"
    description: ClassVar[str] = (
        "Create a reminder in the user's default macOS Reminders list. Use only when the "
        "user has supplied both what to remind them about and a date/time."
    )
    args_model: ClassVar[type[BaseModel]] = CreateReminderArgs
    risk_level: ClassVar[RiskLevel] = RiskLevel.SAFE

    @staticmethod
    def _resolve_time(
        hour_str: str | None, minute_str: str | None, period: str | None
    ) -> tuple[int, int] | None:
        if hour_str is None:
            return 9, 0  # No time given — default to a reasonable morning reminder.
        hour = int(hour_str)
        minute = int(minute_str or 0)
        if minute > 59:
            return None
        if period is not None:
            if not 1 <= hour <= 12:
                return None
            if period == "pm" and hour != 12:
                hour += 12
            elif period == "am" and hour == 12:
                hour = 0
        elif not 0 <= hour <= 23:
            return None
        return hour, minute

    @staticmethod
    def _resolve_relative_time(
        daypart: str | None,
        hour_str: str | None,
        minute_str: str | None,
        period: str | None,
    ) -> tuple[int, int] | None:
        """Resolve "morning 10" / "at 10pm" / bare daypart into (hour, minute).

        A daypart plays the role of am/pm ("tomorrow morning 10" -> 10:00,
        "tomorrow night 10" -> 22:00); a bare daypart gets its customary hour;
        a bare hour with neither is read as 24-hour clock.
        """
        minute = int(minute_str or 0)
        if minute > 59:
            return None
        if hour_str is None:
            if daypart is not None:
                return _DAYPART_DEFAULT_HOUR[daypart], 0
            return 9, 0  # Day only ("tomorrow") — default to a morning reminder.
        hour = int(hour_str)
        if period is not None:
            if not 1 <= hour <= 12:
                return None
            if period == "pm" and hour != 12:
                hour += 12
            elif period == "am" and hour == 12:
                hour = 0
        elif daypart is not None:
            if not 0 <= hour <= 23:
                return None
            if daypart in {"afternoon", "evening", "night"} and 1 <= hour < 12:
                hour += 12
        elif not 0 <= hour <= 23:
            return None
        return hour, minute

    @staticmethod
    def _parse_due_at(value: str, now: datetime | None = None) -> datetime | None:
        """Parse ISO datetimes plus the relative and absolute forms people naturally say."""
        cleaned = value.strip()
        try:
            return datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        except ValueError:
            pass

        normalized = re.sub(r"\s+", " ", cleaned.lower().replace(".", "")).strip()
        base = now or datetime.now()

        relative = _RELATIVE_DAY_TIME.fullmatch(normalized)
        if relative:
            time_parts = CreateReminderTool._resolve_relative_time(
                relative.group("daypart"),
                relative.group("hour"),
                relative.group("minute"),
                relative.group("period"),
            )
            if time_parts is None:
                return None
            hour, minute = time_parts
            target = base + (
                timedelta(days=1) if relative.group("day") == "tomorrow" else timedelta()
            )
            return target.replace(hour=hour, minute=minute, second=0, microsecond=0)

        absolute = _ABSOLUTE_DATE.fullmatch(normalized)
        if absolute:
            day = int(absolute.group("day_a") or absolute.group("day_b"))
            month = _MONTH_NAMES.get(absolute.group("month_a") or absolute.group("month_b"))
            if month is None or not 1 <= day <= 31:
                return None
            time_parts = CreateReminderTool._resolve_time(
                absolute.group("hour"), absolute.group("minute"), absolute.group("period")
            )
            if time_parts is None:
                return None
            hour, minute = time_parts
            year = int(absolute.group("year")) if absolute.group("year") else base.year
            try:
                candidate = base.replace(
                    year=year, month=month, day=day,
                    hour=hour, minute=minute, second=0, microsecond=0,
                )
            except ValueError:
                return None
            if absolute.group("year") is None and candidate < base:
                # No year given and the date already passed this year — assume
                # next year, matching how people mean birthdays/anniversaries.
                try:
                    candidate = candidate.replace(year=year + 1)
                except ValueError:
                    return None
            return candidate

        return None

    async def run(self, args: CreateReminderArgs) -> ToolResult:  # type: ignore[override]
        due_at = self._parse_due_at(args.due_at)
        if due_at is None:
            return ToolResult.failure(
                self.name,
                "I need a date and time such as 'tomorrow at 10 AM', '17 July at 9 AM', "
                "or '2026-07-17T10:00:00'.",
            )
        title = applescript_quote(args.title)
        # AppleScript's `date "<string>"` literal is parsed using the system's
        # Date & Time locale format and rejects a fixed English string on many
        # machines ("Invalid date and time"). Building the date from numeric
        # properties instead sidesteps locale parsing entirely. Day is reset
        # to 1 before setting year/month to avoid a rollover if the current
        # day-of-month doesn't exist in the target month (e.g. 31 -> February).
        script = f'''set theDate to current date
set day of theDate to 1
set year of theDate to {due_at.year}
set month of theDate to {due_at.month}
set day of theDate to {due_at.day}
set hours of theDate to {due_at.hour}
set minutes of theDate to {due_at.minute}
set seconds of theDate to 0
tell application "Reminders"
    tell default list
        make new reminder with properties {{name:{title}, remind me date:theDate}}
    end tell
end tell'''
        output = await run_osascript(script)
        if not output.ok:
            return ToolResult.failure(
                self.name,
                "could not create the reminder: " + output.combined(),
            )
        return ToolResult(
            tool=self.name,
            ok=True,
            summary=(
                f"Reminder set for {due_at.strftime('%-d %B at %-I:%M %p')}: "
                f"{args.title}."
            ),
            data={"title": args.title, "due_at": due_at.isoformat()},
        )
