"""Apple Mail integration: check unread mail, send email.

Both tools script the Mail app. Lessons applied from the calendar tool:
unbounded `whose` queries over a real mailbox are too slow for voice, so the
unread scan walks only the newest messages. Sending is SENSITIVE (the safety
gate confirms it) and success is only ever claimed when Mail's own `send`
verb completed without error.
"""

from __future__ import annotations

import asyncio
import re
from typing import ClassVar

from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.planner.schemas import RiskLevel, ToolResult
from app.tools import _contacts
from app.tools._common import applescript_quote, run_command, run_osascript
from app.tools.base import Tool

# How many of the newest inbox messages to scan for unread ones. Bounded so
# large mailboxes stay fast enough for a voice reply.
_SCAN_LIMIT = 50
_LIST_LIMIT = 5

_EMAIL_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_AUTOMATION_HELP = (
    "Approve the 'Jarvis wants to control Mail' popup, or enable it in "
    "System Settings > Privacy & Security > Automation > Jarvis > Mail."
)


async def _run_mail_script(script: str):
    """Run a Mail AppleScript, launching Mail hidden first (scripting an app
    that isn't running fails with -600)."""
    await run_command(["/usr/bin/open", "-g", "-j", "-a", "Mail"])
    result = await run_osascript(script, timeout=60.0)
    if not result.ok and "-600" in result.combined():
        await asyncio.sleep(2)
        result = await run_osascript(script, timeout=60.0)
    return result


class CheckEmailArgs(BaseModel):
    pass


class CheckEmailTool(Tool):
    name: ClassVar[str] = "check_email"
    description: ClassVar[str] = (
        "Check the user's Apple Mail inbox: unread count plus sender and "
        "subject of the newest unread messages."
    )
    args_model: ClassVar[type[BaseModel]] = CheckEmailArgs
    risk_level: ClassVar[RiskLevel] = RiskLevel.SAFE

    async def run(self, args: CheckEmailArgs) -> ToolResult:  # type: ignore[override]
        script = f'''tell application "Mail"
    set unreadTotal to unread count of inbox
    set msgCount to count of messages of inbox
    set output to ""
    set found to 0
    set i to 1
    repeat while i <= msgCount and i <= {_SCAN_LIMIT} and found < {_LIST_LIMIT}
        set m to message i of inbox
        if read status of m is false then
            set output to output & (sender of m) & "||" & (subject of m) & linefeed
            set found to found + 1
        end if
        set i to i + 1
    end repeat
    return (unreadTotal as string) & "@@" & linefeed & output
end tell'''
        result = await _run_mail_script(script)
        if not result.ok:
            error_text = result.combined()
            if "-1743" in error_text or "Not authorized" in error_text:
                return ToolResult.failure(
                    self.name, "I'm not allowed to read Mail yet. " + _AUTOMATION_HELP
                )
            return ToolResult.failure(
                self.name, f"could not check Mail: {error_text.strip() or 'unknown error'}"
            )

        count_text, _, listing = result.stdout.partition("@@")
        try:
            unread = int(count_text.strip())
        except ValueError:
            return ToolResult.failure(self.name, "could not read the unread count from Mail.")
        messages = []
        for line in listing.splitlines():
            sender, sep, subject = line.partition("||")
            if sep and sender.strip():
                messages.append({"from": sender.strip(), "subject": subject.strip()})
        if unread == 0:
            return ToolResult(
                tool=self.name, ok=True,
                summary="No unread email.", data={"unread": 0, "messages": []},
            )
        lines = [f"{m['from']}: {m['subject']}" for m in messages]
        shown = "\n".join(lines)
        summary = f"{unread} unread email(s)." + (f" Latest:\n{shown}" if shown else "")
        return ToolResult(
            tool=self.name, ok=True, summary=summary,
            data={"unread": unread, "messages": messages},
        )


class SendEmailArgs(BaseModel):
    recipient: str = Field(
        min_length=2,
        description=(
            "Who to email: a contact name from the user's address book "
            "(for example 'Mohan Kirushna') or an email address."
        ),
    )
    body: str = Field(min_length=1, max_length=10_000, description="The message text.")
    subject: str | None = Field(
        default=None,
        description="Subject line; omitted, a short one is derived from the body.",
    )


class SendEmailTool(Tool):
    name: ClassVar[str] = "send_email"
    description: ClassVar[str] = (
        "Send an email through the user's Apple Mail account. The recipient can "
        "be a saved contact's name or an email address. Use only when the user "
        "clearly states both a recipient and the message."
    )
    args_model: ClassVar[type[BaseModel]] = SendEmailArgs
    risk_level: ClassVar[RiskLevel] = RiskLevel.SENSITIVE

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def confirmation_action(self, args: BaseModel) -> str | None:
        assert isinstance(args, SendEmailArgs)
        subject = (args.subject or "").strip()
        if not subject:
            words = args.body.split()
            subject = " ".join(words[:8]) + ("…" if len(words) > 8 else "")
        return (
            f"Send this email to {args.recipient}?\n"
            f"Subject: {subject}\n\n{args.body}"
        )

    async def _resolve_address(self, recipient: str) -> tuple[str, str] | ToolResult:
        """Return (display name, email address), or a failed ToolResult."""
        cleaned = recipient.strip()
        if _EMAIL_ADDRESS.fullmatch(cleaned):
            return cleaned, cleaned
        try:
            entries = await _contacts.lookup(cleaned, "email")
        except _contacts.ContactsError as exc:
            if exc.permission_denied:
                return ToolResult.failure(
                    self.name,
                    "I'm not allowed to read your contacts yet. Approve the "
                    "'Jarvis wants to control Contacts' popup, or enable it in "
                    "System Settings > Privacy & Security > Automation > Jarvis > Contacts. "
                    "Or say the email address.",
                )
            return ToolResult.failure(
                self.name,
                f"I couldn't search your contacts ({exc}). "
                "You can say the email address instead.",
            )
        if not entries:
            return ToolResult.failure(
                self.name,
                f"I couldn't find {cleaned} in your contacts. "
                "Try the full name as saved, or say the email address.",
            )
        chosen = _contacts.pick(entries, cleaned)
        if chosen is not None:
            return chosen
        with_email = [entry for entry in entries if entry[1]]
        if not with_email:
            return ToolResult.failure(
                self.name,
                f"{entries[0][0]} is in your contacts but has no email address saved.",
            )
        candidates = ", ".join(entry[0] for entry in with_email[:4])
        return ToolResult.failure(
            self.name,
            f"Several contacts match {cleaned}: {candidates}. Which one did you mean?",
        )

    async def run(self, args: SendEmailArgs) -> ToolResult:  # type: ignore[override]
        resolved = await self._resolve_address(args.recipient)
        if isinstance(resolved, ToolResult):
            return resolved
        display, address = resolved

        subject = (args.subject or "").strip()
        if not subject:
            words = args.body.split()
            subject = " ".join(words[:8]) + ("…" if len(words) > 8 else "")

        properties = (
            f"{{subject:{applescript_quote(subject)}, "
            f"content:{applescript_quote(args.body)}, visible:false}}"
        )
        mail_from = (self._settings.mail_from or "").strip()
        if mail_from:
            # Refuse rather than silently send from another identity when the
            # configured address has no signed-in Mail account.
            sender_block = f'''    set senderOK to false
    repeat with acc in accounts
        if (email addresses of acc) contains {applescript_quote(mail_from)} then ¬
            set senderOK to true
    end repeat
    if not senderOK then return "no-account"
'''
            sender_line = f"    set sender of newMessage to {applescript_quote(mail_from)}\n"
        else:
            sender_block = ""
            sender_line = ""
        # Mail's `send` verb only QUEUES the message; transmission happens in
        # the background afterwards. Poll the outbox until the message leaves
        # it (sent) or a timeout elapses (genuinely stuck: account needs
        # re-login, no network) so we never claim "sent" while it lingers —
        # nor cry "stuck" just because filing the Sent copy to a slow/over-
        # quota account delays the outbox clearing by a few seconds.
        script = f'''tell application "Mail"
{sender_block}    set newMessage to make new outgoing message with properties {properties}
{sender_line}    tell newMessage
        make new to recipient at end of to recipients ¬
            with properties {{address:{applescript_quote(address)}}}
    end tell
    send newMessage
    set stillQueued to true
    repeat 10 times
        delay 1.5
        set stillQueued to false
        repeat with m in (messages of outbox)
            if subject of m is {applescript_quote(subject)} then set stillQueued to true
        end repeat
        if not stillQueued then exit repeat
    end repeat
    if stillQueued then return "queued"
    return "sent"
end tell'''
        result = await _run_mail_script(script)
        if result.ok and result.stdout.strip() == "no-account":
            return ToolResult.failure(
                self.name,
                f"the email was NOT sent: {mail_from} isn't signed in to the Mail app. "
                "Add that account in Mail > Settings > Accounts, or change JARVIS_MAIL_FROM.",
            )
        if result.ok and result.stdout.strip() == "queued":
            return ToolResult.failure(
                self.name,
                f"the email to {display} is stuck in Mail's outbox — it has NOT gone out. "
                "Mail usually needs you to sign in to the account again; open the Mail app "
                "and check for a sign-in prompt.",
            )
        if not result.ok or result.stdout.strip() != "sent":
            error_text = result.combined()
            if "-1743" in error_text or "Not authorized" in error_text:
                return ToolResult.failure(
                    self.name, "I'm not allowed to control Mail yet. " + _AUTOMATION_HELP
                )
            return ToolResult.failure(
                self.name,
                "the email was NOT sent: " + (error_text.strip() or "Mail reported no result. "
                "Is an email account set up in the Mail app?"),
            )
        return ToolResult(
            tool=self.name,
            ok=True,
            summary=f"Sent the email to {display} ({address}).",
            data={"recipient": display, "address": address, "subject": subject},
        )
