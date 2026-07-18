"""WhatsApp messaging through an optional self-hosted WAHA gateway.

WAHA (https://waha.devlike.pro) exposes WhatsApp Web as a local REST API:
POST /api/sendText with an X-Api-Key header and {chatId, text, session}.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import ClassVar
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.planner.schemas import RiskLevel, ToolResult
from app.tools._common import applescript_quote, run_command, run_osascript
from app.tools.base import Tool

# A phone-number-looking run inside the recipient text, e.g. the digits in
# "Mohan Kirushna 9080209303" or "+1 415-555-0123". Callers additionally
# require at least 7 digits so names like "Area 51" stay names.
_PHONE_IN_TEXT = re.compile(r"\+?\(?\d[\d\s\-().]*\d")


class WhatsAppSendArgs(BaseModel):
    recipient: str = Field(
        min_length=2,
        description=(
            "Who to message: a contact name from the user's address book "
            "(for example 'Mohan Kirushna'), a full international phone "
            "number such as +14155550123, or a WhatsApp chat ID ending in @c.us."
        ),
    )
    message: str = Field(min_length=1, max_length=4096, description="Text to send.")


class WhatsAppSendTool(Tool):
    """Send a WhatsApp message via the user's local WAHA server."""

    name: ClassVar[str] = "whatsapp_send"
    description: ClassVar[str] = (
        "Send a WhatsApp text message through the configured WhatsApp gateway. "
        "The recipient can be a saved contact's name or a phone number. "
        "Use only when the user clearly states both a recipient and a message."
    )
    args_model: ClassVar[type[BaseModel]] = WhatsAppSendArgs
    risk_level: ClassVar[RiskLevel] = RiskLevel.SENSITIVE

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @staticmethod
    def _extract_number(recipient: str) -> str | None:
        """Pull a phone number out of the recipient text, if one is present."""
        match = _PHONE_IN_TEXT.search(recipient)
        if match and sum(char.isdigit() for char in match.group()) >= 7:
            return match.group()
        return None

    def _chat_id(self, number: str) -> str | None:
        cleaned = number.strip()
        if cleaned.endswith("@c.us"):
            return cleaned
        international = cleaned.startswith("+")
        digits = "".join(char for char in cleaned if char.isdigit())
        if not international and digits.startswith("00"):
            digits, international = digits[2:], True
        if not international and len(digits) == 11 and digits.startswith("0"):
            # National trunk prefix (e.g. 09080209303) — drop it.
            digits = digits[1:]
        country_code = (self._settings.whatsapp_default_country_code or "").lstrip("+")
        if not international and country_code and len(digits) == 10:
            # Spoken numbers and locally saved contacts rarely include the
            # country code, but WhatsApp chat IDs require one.
            digits = country_code + digits
        if not 7 <= len(digits) <= 15:
            return None
        return f"{digits}@c.us"

    async def _resolve_contact(self, name: str) -> tuple[str, str] | ToolResult:
        """Look up a contact by name in macOS Contacts.

        Returns (display name, phone number) on success, or a failed
        ToolResult explaining what the user should do instead.
        """
        script = (
            'tell application "Contacts"\n'
            f"    set matched to (people whose name contains {applescript_quote(name)})\n"
            '    set output to ""\n'
            "    repeat with p in matched\n"
            '        set phoneText to ""\n'
            "        if (count of phones of p) > 0 then "
            "set phoneText to value of item 1 of phones of p\n"
            '        set output to output & (name of p) & "||" & phoneText & linefeed\n'
            "    end repeat\n"
            "    return output\n"
            "end tell"
        )
        # Scripting Contacts fails with "Application isn't running" (-600)
        # unless the app is up — launch it hidden in the background first
        # (idempotent and cheap when it's already running).
        await run_command(["/usr/bin/open", "-g", "-j", "-a", "Contacts"])
        lookup = await run_osascript(script)
        if not lookup.ok and "-600" in lookup.combined():
            # Contacts was still starting up — give it a moment and retry once.
            await asyncio.sleep(2)
            lookup = await run_osascript(script)
        if not lookup.ok:
            error_text = lookup.combined()
            if "-1743" in error_text or "Not authorized" in error_text:
                return ToolResult.failure(
                    self.name,
                    "I'm not allowed to read your contacts yet. Approve the "
                    "'Jarvis wants to control Contacts' popup, or enable it in "
                    "System Settings > Privacy & Security > Automation > Jarvis > Contacts. "
                    "Or just say the phone number.",
                )
            return ToolResult.failure(
                self.name,
                f"I couldn't search your contacts ({error_text.strip() or 'unknown error'}). "
                "You can say the phone number instead.",
            )
        entries: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for line in lookup.stdout.splitlines():
            contact_name, sep, phone = line.partition("||")
            if not sep or not contact_name.strip():
                continue
            entry = (contact_name.strip(), phone.strip())
            # Synced address books often hold duplicate cards of the same
            # person; identical (name, number) pairs are one contact.
            key = (entry[0].lower(), re.sub(r"\D", "", entry[1]))
            if key not in seen:
                seen.add(key)
                entries.append(entry)
        if not entries:
            return ToolResult.failure(
                self.name,
                f"I couldn't find {name} in your contacts. "
                "Try the full name as saved, or say the phone number.",
            )
        with_phone = [entry for entry in entries if entry[1]]
        if not with_phone:
            return ToolResult.failure(
                self.name,
                f"{entries[0][0]} is in your contacts but has no phone number saved.",
            )
        exact = [entry for entry in with_phone if entry[0].lower() == name.lower()]
        if exact:
            return exact[0]
        if len({entry[0].lower() for entry in with_phone}) == 1:
            # All matches are the same person (possibly several cards with
            # different numbers) — not genuinely ambiguous, take the first.
            return with_phone[0]
        candidates = ", ".join(entry[0] for entry in with_phone[:4])
        return ToolResult.failure(
            self.name,
            f"Several contacts match {name}: {candidates}. Which one did you mean?",
        )

    @staticmethod
    def _post(url: str, api_key: str, payload: dict[str, str]) -> tuple[int, str]:
        request = Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "X-Api-Key": api_key},
            method="POST",
        )
        with urlopen(request, timeout=20) as response:  # noqa: S310 - configured WAHA URL
            return response.status, response.read().decode("utf-8", errors="replace")

    async def run(self, args: WhatsAppSendArgs) -> ToolResult:  # type: ignore[override]
        base_url = self._settings.waha_base_url
        api_key = self._settings.waha_api_key
        if not (base_url and api_key):
            return ToolResult.failure(
                self.name,
                "WhatsApp is not configured. Start the WAHA gateway, then set "
                "JARVIS_WAHA_BASE_URL and JARVIS_WAHA_API_KEY and restart Jarvis.",
            )
        recipient = args.recipient.strip()
        display = recipient
        number = self._extract_number(recipient)
        if number is None and not recipient.endswith("@c.us"):
            resolved = await self._resolve_contact(recipient)
            if isinstance(resolved, ToolResult):
                return resolved
            display, number = resolved
        chat_id = self._chat_id(number if number is not None else recipient)
        if chat_id is None:
            return ToolResult.failure(
                self.name,
                "Use the recipient's full international phone number, including "
                "country code, or the name of a saved contact.",
            )
        url = base_url.rstrip("/") + "/api/sendText"
        payload = {
            "chatId": chat_id,
            "text": args.message,
            "session": self._settings.waha_session,
        }
        try:
            status, _body = await asyncio.to_thread(self._post, url, api_key, payload)
        except HTTPError as exc:
            return ToolResult.failure(
                self.name,
                f"the WhatsApp gateway rejected the message (HTTP {exc.code}). "
                "Check that the WAHA session is running and logged in.",
            )
        except URLError as exc:
            return ToolResult.failure(
                self.name, f"could not reach the WhatsApp gateway: {exc.reason}"
            )
        except OSError as exc:
            return ToolResult.failure(self.name, f"could not send the WhatsApp message: {exc}")
        if not 200 <= status < 300:
            return ToolResult.failure(
                self.name, f"the WhatsApp gateway returned HTTP {status}."
            )
        return ToolResult(
            tool=self.name,
            ok=True,
            summary=f"Sent your WhatsApp message to {display}.",
            data={"recipient": display, "chat_id": chat_id},
        )
