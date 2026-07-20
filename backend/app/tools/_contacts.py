"""Shared macOS Contacts lookup for tools that resolve people by name.

Underscore-prefixed so the registry's discovery walk skips this module.

Scripting Contacts fails with "Application isn't running" (-600) unless the
app is up, so lookups launch it hidden first; a real permission denial
(-1743) is distinguished from other failures so callers can point the user
at the right System Settings pane.
"""

from __future__ import annotations

import asyncio
import re
from typing import Literal

from app.tools._common import applescript_quote, run_command, run_osascript

ContactField = Literal["phone", "email"]

_FIELD_PROPERTY = {"phone": "phones", "email": "emails"}


class ContactsError(Exception):
    """Contacts could not be searched at all."""

    def __init__(self, message: str, permission_denied: bool = False) -> None:
        super().__init__(message)
        self.permission_denied = permission_denied


async def lookup(name: str, field: ContactField) -> list[tuple[str, str]]:
    """Return deduplicated (display name, value) pairs for contacts matching
    `name`. The value may be empty when the card lacks that field. Raises
    ContactsError when the search itself fails.
    """
    prop = _FIELD_PROPERTY[field]
    script = (
        'tell application "Contacts"\n'
        f"    set matched to (people whose name contains {applescript_quote(name)})\n"
        '    set output to ""\n'
        "    repeat with p in matched\n"
        '        set valueText to ""\n'
        f"        if (count of {prop} of p) > 0 then "
        f"set valueText to value of item 1 of {prop} of p\n"
        '        set output to output & (name of p) & "||" & valueText & linefeed\n'
        "    end repeat\n"
        "    return output\n"
        "end tell"
    )
    await run_command(["/usr/bin/open", "-g", "-j", "-a", "Contacts"])
    result = await run_osascript(script)
    if not result.ok and "-600" in result.combined():
        # Contacts was still starting up — give it a moment and retry once.
        await asyncio.sleep(2)
        result = await run_osascript(script)
    if not result.ok:
        error_text = result.combined()
        if "-1743" in error_text or "Not authorized" in error_text:
            raise ContactsError(
                "not allowed to read Contacts", permission_denied=True
            )
        raise ContactsError(error_text.strip() or "unknown Contacts error")

    entries: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in result.stdout.splitlines():
        contact_name, sep, value = line.partition("||")
        if not sep or not contact_name.strip():
            continue
        entry = (contact_name.strip(), value.strip())
        # Synced address books often hold duplicate cards of the same
        # person; identical (name, value) pairs are one contact.
        key = (entry[0].lower(), re.sub(r"\s+", "", entry[1].lower()))
        if key not in seen:
            seen.add(key)
            entries.append(entry)
    return entries


def pick(entries: list[tuple[str, str]], name: str) -> tuple[str, str] | None:
    """Choose the best match with a value: exact name match wins; otherwise a
    single candidate (or several cards of the same one person) wins; genuine
    ambiguity returns None so the caller can ask which person was meant.
    """
    with_value = [entry for entry in entries if entry[1]]
    exact = [entry for entry in with_value if entry[0].lower() == name.lower()]
    if exact:
        return exact[0]
    if len({entry[0].lower() for entry in with_value}) == 1 and with_value:
        return with_value[0]
    return None
