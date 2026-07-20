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
from app.tools._common import CommandOutput, applescript_quote, run_command, run_osascript
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


_BODY_PREVIEW_CHARS = 500
_REC = "@@@REC@@@"  # record terminator: message bodies contain newlines and "||"


async def _run_mail_script(script: str) -> CommandOutput:
    """Run a Mail AppleScript, launching Mail hidden first (scripting an app
    that isn't running fails with -600)."""
    await run_command(["/usr/bin/open", "-g", "-j", "-a", "Mail"])
    result = await run_osascript(script, timeout=60.0)
    if not result.ok and "-600" in result.combined():
        await asyncio.sleep(2)
        result = await run_osascript(script, timeout=60.0)
    return result


def _permission_or_error(tool_name: str, error_text: str, action: str) -> ToolResult:
    if "-1743" in error_text or "Not authorized" in error_text:
        return ToolResult.failure(
            tool_name, f"I'm not allowed to {action} Mail yet. {_AUTOMATION_HELP}"
        )
    return ToolResult.failure(
        tool_name, f"could not {action} Mail: {error_text.strip() or 'unknown error'}"
    )


async def _scan_inbox(
    sender: str | None, include_body: bool, limit: int, unread_only: bool = True,
    keyword: str | None = None,
) -> tuple[int, list[dict[str, str]]] | CommandOutput:
    """Return (unread total, newest matching messages) or the failed
    CommandOutput so the caller can classify the error. `unread_only` scans
    only unread mail (a fresh-inbox check); set it False to find the latest
    from a sender regardless of read status. `keyword` searches the whole
    inbox for mail whose sender OR subject contains it (a topic search).
    Bodies are optional because reading them is slower."""
    body_clause = (
        "try\n            set bodyText to content of m\n        end try" if include_body else ""
    )
    if keyword or sender:
        # Targeted search over the WHOLE inbox (not just the newest _SCAN_LIMIT).
        if keyword:
            q = applescript_quote(keyword)
            where = f"(subject contains {q} or sender contains {q})"
        else:
            assert sender is not None  # entered this branch, so sender is set
            read_filter = " and read status is false" if unread_only else ""
            where = f"sender contains {applescript_quote(sender)}{read_filter}"
        script = f'''tell application "Mail"
    with timeout of 90 seconds
        set unreadTotal to unread count of inbox
        set matchList to (messages of inbox whose {where})
        set output to ""
        set found to 0
        repeat with m in matchList
            if found >= {limit} then exit repeat
            set senderText to sender of m
            set bodyText to ""
            {body_clause}
            set output to output & senderText & "||" & (subject of m) & "||" & bodyText & "{_REC}"
            set found to found + 1
        end repeat
        return (unreadTotal as string) & "@@" & output
    end timeout
end tell'''
    else:
        # Untargeted: linear-scan just the newest messages (fast for a fresh-
        # inbox check); unread_only is always true in this branch.
        script = f'''tell application "Mail"
    set unreadTotal to unread count of inbox
    set msgCount to count of messages of inbox
    set output to ""
    set found to 0
    set i to 1
    repeat while i <= msgCount and i <= {_SCAN_LIMIT} and found < {limit}
        set m to message i of inbox
        if read status of m is false then
            set senderText to sender of m
            set bodyText to ""
            {body_clause}
            set output to output & senderText & "||" & (subject of m) & "||" & bodyText & "{_REC}"
            set found to found + 1
        end if
        set i to i + 1
    end repeat
    return (unreadTotal as string) & "@@" & output
end tell'''
    result = await _run_mail_script(script)
    if not result.ok:
        return result
    count_text, _, listing = result.stdout.partition("@@")
    try:
        unread = int(count_text.strip())
    except ValueError:
        unread = 0
    messages: list[dict[str, str]] = []
    for record in listing.split(_REC):
        record = record.strip()
        if not record:
            continue
        parts = record.split("||", 2)
        messages.append({
            "from": parts[0].strip(),
            "subject": (parts[1].strip() if len(parts) > 1 else ""),
            "body": (parts[2].strip()[:_BODY_PREVIEW_CHARS] if len(parts) > 2 else ""),
        })
    return unread, messages


async def _send_message(
    address: str, subject: str, body: str, mail_from: str
) -> tuple[str, str]:
    """Compose and send one message via Mail, polling the outbox so "sent" is
    only reported once the message truly leaves it. Returns (status, error);
    status is one of sent / queued / no-account / error."""
    properties = (
        f"{{subject:{applescript_quote(subject)}, "
        f"content:{applescript_quote(body)}, visible:false}}"
    )
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
    status = result.stdout.strip() if result.ok else ""
    if status in {"sent", "queued", "no-account"}:
        return status, ""
    return "error", result.combined()


class CheckEmailArgs(BaseModel):
    sender: str | None = Field(
        default=None,
        description="Only count/list unread mail whose sender contains this name or address.",
    )


class CheckEmailTool(Tool):
    name: ClassVar[str] = "check_email"
    description: ClassVar[str] = (
        "Check the user's Apple Mail inbox: unread count plus sender and subject of the "
        "newest unread messages. Pass 'sender' to check only mail from a specific person "
        "('any mail from Alice?')."
    )
    args_model: ClassVar[type[BaseModel]] = CheckEmailArgs
    risk_level: ClassVar[RiskLevel] = RiskLevel.SAFE

    async def run(self, args: CheckEmailArgs) -> ToolResult:  # type: ignore[override]
        scanned = await _scan_inbox(args.sender, include_body=False, limit=_LIST_LIMIT)
        if isinstance(scanned, CommandOutput):
            return _permission_or_error(self.name, scanned.combined(), "read")
        unread, messages = scanned

        if args.sender:
            if not messages:
                return ToolResult(
                    tool=self.name, ok=True,
                    summary=f"No unread email from {args.sender}.",
                    data={"sender": args.sender, "messages": []},
                )
            lines = "\n".join(f"{m['from']}: {m['subject']}" for m in messages)
            return ToolResult(
                tool=self.name, ok=True,
                summary=f"{len(messages)} unread from {args.sender}:\n{lines}",
                data={"sender": args.sender, "messages": messages},
            )
        if unread == 0:
            return ToolResult(
                tool=self.name, ok=True,
                summary="No unread email.", data={"unread": 0, "messages": []},
            )
        lines = "\n".join(f"{m['from']}: {m['subject']}" for m in messages)
        summary = f"{unread} unread email(s)." + (f" Latest:\n{lines}" if lines else "")
        return ToolResult(
            tool=self.name, ok=True, summary=summary,
            data={"unread": unread, "messages": messages},
        )


class SummarizeInboxArgs(BaseModel):
    sender: str | None = Field(
        default=None,
        description="Only summarize mail whose sender contains this name or address.",
    )
    query: str | None = Field(
        default=None,
        description="Topic keyword to search for; matches mail whose subject or sender "
        "contains it ('any mail about supabase' -> query='supabase').",
    )


class SummarizeInboxTool(Tool):
    name: ClassVar[str] = "summarize_inbox"
    description: ClassVar[str] = (
        "Read matching emails (sender, subject, and body) and return them so you can "
        "summarize the user's inbox in your own words. Use for 'summarize my emails', "
        "'mail from <person>' (sender=), or 'mail about <topic>' (query=). With neither, "
        "summarizes the newest unread mail."
    )
    args_model: ClassVar[type[BaseModel]] = SummarizeInboxArgs
    risk_level: ClassVar[RiskLevel] = RiskLevel.SAFE

    _SUMMARY_LIMIT = 5

    async def run(self, args: SummarizeInboxArgs) -> ToolResult:  # type: ignore[override]
        # query -> topic search (sender or subject). sender -> that person's
        # mail, read or not. Neither -> the newest fresh (unread) mail.
        scanned = await _scan_inbox(
            args.sender, include_body=True, limit=self._SUMMARY_LIMIT,
            unread_only=args.sender is None and args.query is None,
            keyword=args.query,
        )
        if isinstance(scanned, CommandOutput):
            return _permission_or_error(self.name, scanned.combined(), "read")
        unread, messages = scanned
        if not messages:
            if args.query:
                summary = f"No email found about {args.query}."
            elif args.sender:
                summary = f"No email found from {args.sender}."
            else:
                summary = "No unread email found."
            return ToolResult(tool=self.name, ok=True, summary=summary, data={"messages": []})
        blocks = [
            f"From {m['from']}\nSubject: {m['subject']}\n{m['body'] or '(no preview)'}"
            for m in messages
        ]
        if args.query:
            header = f"Emails about {args.query} to summarize:"
        elif args.sender:
            header = f"Recent emails from {args.sender} to summarize:"
        else:
            header = "Unread emails to summarize:"
        return ToolResult(
            tool=self.name, ok=True,
            summary=f"{header}\n\n" + "\n\n---\n\n".join(blocks),
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

    def _outcome(
        self, status: str, error: str, display: str, address: str, subject: str
    ) -> ToolResult:
        mail_from = (self._settings.mail_from or "").strip()
        if status == "no-account":
            return ToolResult.failure(
                self.name,
                f"the email was NOT sent: {mail_from} isn't signed in to the Mail app. "
                "Add that account in Mail > Settings > Accounts, or change JARVIS_MAIL_FROM.",
            )
        if status == "queued":
            return ToolResult.failure(
                self.name,
                f"the email to {display} is stuck in Mail's outbox — it has NOT gone out. "
                "Mail usually needs you to sign in to the account again; open the Mail app "
                "and check for a sign-in prompt.",
            )
        if status != "sent":
            if "-1743" in error or "Not authorized" in error:
                return ToolResult.failure(
                    self.name, "I'm not allowed to control Mail yet. " + _AUTOMATION_HELP
                )
            return ToolResult.failure(
                self.name,
                "the email was NOT sent: " + (error.strip() or "Mail reported no result. "
                "Is an email account set up in the Mail app?"),
            )
        return ToolResult(
            tool=self.name,
            ok=True,
            summary=f"Sent the email to {display} ({address}).",
            data={"recipient": display, "address": address, "subject": subject},
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

        status, error = await _send_message(
            address, subject, args.body, (self._settings.mail_from or "").strip()
        )
        return self._outcome(status, error, display, address, subject)


class ReplyEmailArgs(BaseModel):
    body: str = Field(min_length=1, max_length=10_000, description="The reply text.")
    sender: str | None = Field(
        default=None,
        description="Reply to the newest unread mail from this person; omit for the newest "
        "unread overall.",
    )


class ReplyEmailTool(Tool):
    name: ClassVar[str] = "reply_email"
    description: ClassVar[str] = (
        "Reply to the most recent unread email (optionally from a specific sender) through "
        "Apple Mail. Use for 'reply to the latest email saying ...'."
    )
    args_model: ClassVar[type[BaseModel]] = ReplyEmailArgs
    risk_level: ClassVar[RiskLevel] = RiskLevel.SENSITIVE

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def confirmation_action(self, args: BaseModel) -> str | None:
        assert isinstance(args, ReplyEmailArgs)
        who = f" from {args.sender}" if args.sender else ""
        return f"Reply to the latest unread email{who}?\n\n{args.body}"

    async def _latest_unread(self, sender: str | None) -> tuple[str, str, str] | None:
        """Return (reply-to address, display sender, subject) of the newest
        unread message, or None if there is none."""
        filter_clause = (
            f"if senderText does not contain {applescript_quote(sender)} then set skip to true"
            if sender
            else ""
        )
        script = f'''tell application "Mail"
    set msgCount to count of messages of inbox
    set i to 1
    repeat while i <= msgCount and i <= {_SCAN_LIMIT}
        set m to message i of inbox
        if read status of m is false then
            set senderText to sender of m
            set skip to false
            {filter_clause}
            if not skip then
                return (extract address from senderText) & "||" & senderText & "||" & (subject of m)
            end if
        end if
        set i to i + 1
    end repeat
    return ""
end tell'''
        result = await _run_mail_script(script)
        if not result.ok or not result.stdout.strip():
            return None
        parts = result.stdout.strip().split("||", 2)
        if len(parts) < 3 or not parts[0].strip():
            return None
        return parts[0].strip(), parts[1].strip(), parts[2].strip()

    async def run(self, args: ReplyEmailArgs) -> ToolResult:  # type: ignore[override]
        latest = await self._latest_unread(args.sender)
        if latest is None:
            where = f" from {args.sender}" if args.sender else ""
            return ToolResult.failure(
                self.name, f"there's no unread email{where} to reply to."
            )
        address, display, orig_subject = latest
        subject = orig_subject if orig_subject.lower().startswith("re:") else f"Re: {orig_subject}"

        status, error = await _send_message(
            address, subject, args.body, (self._settings.mail_from or "").strip()
        )
        if status == "sent":
            return ToolResult(
                tool=self.name, ok=True,
                summary=f"Replied to {display}.",
                data={"address": address, "subject": subject},
            )
        if status == "no-account":
            return ToolResult.failure(
                self.name,
                "the reply was NOT sent: the configured send-from account isn't signed in to Mail.",
            )
        if status == "queued":
            return ToolResult.failure(
                self.name,
                f"the reply to {display} is stuck in Mail's outbox — it has NOT gone out.",
            )
        if "-1743" in error or "Not authorized" in error:
            return ToolResult.failure(
                self.name, "I'm not allowed to control Mail yet. " + _AUTOMATION_HELP
            )
        return ToolResult.failure(
            self.name, "the reply was NOT sent: " + (error.strip() or "Mail reported no result."),
        )
