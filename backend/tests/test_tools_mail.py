"""Apple Mail tools: unread check, sending, and contact-to-address resolution.

Mail scripting and Contacts lookups are mocked; nothing here touches the
real mailbox or address book.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.planner.schemas import RiskLevel
from app.tools import _contacts as contacts_module
from app.tools import mail as mail_module
from app.tools._common import CommandOutput
from app.tools.mail import CheckEmailTool, SendEmailTool


def _send_tool(mail_from: str | None = None) -> SendEmailTool:
    return SendEmailTool(Settings(mail_from=mail_from))


def _mock_mail(
    monkeypatch: pytest.MonkeyPatch, stdout: str, ok: bool = True, stderr: str = ""
) -> list[str]:
    scripts: list[str] = []

    async def fake_osascript(script: str, timeout: float = 30.0) -> CommandOutput:
        scripts.append(script)
        return CommandOutput(0 if ok else 1, stdout, stderr)

    async def fake_open(argv: list[str], cwd=None, timeout=30.0) -> CommandOutput:
        assert argv[:2] == ["/usr/bin/open", "-g"]  # Mail launched hidden first
        return CommandOutput(0, "", "")

    monkeypatch.setattr(mail_module, "run_osascript", fake_osascript)
    monkeypatch.setattr(mail_module, "run_command", fake_open)
    return scripts


def _mock_contact_emails(monkeypatch: pytest.MonkeyPatch, stdout: str) -> None:
    async def fake_osascript(script: str, timeout: float = 30.0) -> CommandOutput:
        assert "emails" in script  # email lookup, not phones
        return CommandOutput(0, stdout, "")

    async def fake_open(argv: list[str], cwd=None, timeout=30.0) -> CommandOutput:
        return CommandOutput(0, "", "")

    monkeypatch.setattr(contacts_module, "run_osascript", fake_osascript)
    monkeypatch.setattr(contacts_module, "run_command", fake_open)


def _scan_output(unread: int, messages: list[tuple[str, str, str]]) -> str:
    """Build the stdout format _scan_unread parses: count@@ then
    sender||subject||body@@@REC@@@ per message."""
    body = "".join(f"{s}||{subj}||{b}@@@REC@@@" for s, subj, b in messages)
    return f"{unread}@@{body}"


def test_send_email_is_sensitive_so_the_gate_confirms_it() -> None:
    assert SendEmailTool.risk_level is RiskLevel.SENSITIVE


async def test_check_email_reports_unread_with_senders(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_mail(
        monkeypatch,
        _scan_output(3, [
            ("Alice <alice@example.com>", "Project update", ""),
            ("Bob", "Lunch?", ""),
        ]),
    )
    result = await CheckEmailTool().execute({})
    assert result.ok, result.summary
    assert result.data["unread"] == 3
    assert len(result.data["messages"]) == 2
    assert "Alice" in result.summary and "Project update" in result.summary


async def test_check_email_zero_unread(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_mail(monkeypatch, _scan_output(0, []))
    result = await CheckEmailTool().execute({})
    assert result.ok, result.summary
    assert result.summary == "No unread email."


async def test_check_email_from_specific_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_mail(monkeypatch, _scan_output(9, [("Alice <a@x.com>", "Re: project", "")]))
    result = await CheckEmailTool().execute({"sender": "Alice"})
    assert result.ok, result.summary
    assert "1 unread from Alice" in result.summary
    assert "Re: project" in result.summary


async def test_check_email_from_sender_none_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_mail(monkeypatch, _scan_output(9, []))
    result = await CheckEmailTool().execute({"sender": "Zoe"})
    assert result.ok, result.summary
    assert result.summary == "No unread email from Zoe."


async def test_summarize_inbox_returns_bodies_for_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.tools.mail import SummarizeInboxTool

    _mock_mail(
        monkeypatch,
        _scan_output(2, [
            ("Alice", "Deadline", "The report is due Friday."),
            ("Bob", "Lunch", "Are you free at noon?"),
        ]),
    )
    result = await SummarizeInboxTool().execute({})
    assert result.ok, result.summary
    assert "The report is due Friday." in result.summary  # body included
    assert "Are you free at noon?" in result.summary


async def test_reply_email_sends_re_subject_to_original_sender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.tools.mail import ReplyEmailTool

    scripts: list[str] = []

    async def fake_osascript(script: str, timeout: float = 30.0) -> CommandOutput:
        scripts.append(script)
        if "extract address" in script:  # _latest_unread lookup
            return CommandOutput(0, "alice@example.com||Alice <alice@example.com>||Project", "")
        return CommandOutput(0, "sent", "")  # _send_message

    async def fake_open(argv: list[str], cwd=None, timeout=30.0) -> CommandOutput:
        return CommandOutput(0, "", "")

    monkeypatch.setattr(mail_module, "run_osascript", fake_osascript)
    monkeypatch.setattr(mail_module, "run_command", fake_open)

    result = await ReplyEmailTool(Settings()).execute({"body": "Got it, thanks."})
    assert result.ok, result.summary
    assert "Replied to Alice" in result.summary
    send_script = scripts[-1]
    assert 'address:"alice@example.com"' in send_script
    assert 'subject:"Re: Project"' in send_script


async def test_reply_email_no_unread_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.tools.mail import ReplyEmailTool

    _mock_mail(monkeypatch, "")  # _latest_unread returns nothing
    result = await ReplyEmailTool(Settings()).execute({"body": "hi"})
    assert not result.ok
    assert "no unread email" in result.summary


def test_reply_email_is_sensitive_and_previews_the_reply() -> None:
    from app.tools.mail import ReplyEmailTool

    tool = ReplyEmailTool(Settings())
    assert tool.risk_level is RiskLevel.SENSITIVE
    args = tool.args_model.model_validate({"body": "See you then."})
    preview = tool.confirmation_action(args)
    assert preview is not None and "See you then." in preview


async def test_check_email_permission_error_names_the_automation_pane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_mail(
        monkeypatch, "", ok=False,
        stderr="Not authorized to send Apple events to Mail. (-1743)",
    )
    result = await CheckEmailTool().execute({})
    assert not result.ok
    assert "Automation" in result.summary


async def test_send_email_to_a_direct_address_skips_contacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def explode(script: str, timeout: float = 30.0) -> CommandOutput:
        raise AssertionError("Contacts must not be queried for a literal address")

    monkeypatch.setattr(contacts_module, "run_osascript", explode)
    scripts = _mock_mail(monkeypatch, "sent")

    result = await _send_tool().execute(
        {"recipient": "someone@example.com", "body": "hello there"}
    )
    assert result.ok, result.summary
    assert "someone@example.com" in result.summary
    assert 'address:"someone@example.com"' in scripts[0]


async def test_send_email_resolves_contact_name_to_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_contact_emails(monkeypatch, "Mohan Kirushna R||semosh.work@example.com\n")
    scripts = _mock_mail(monkeypatch, "sent")

    result = await _send_tool().execute(
        {"recipient": "mohan kirushna", "body": "meeting at 5", "subject": "Meeting"}
    )
    assert result.ok, result.summary
    assert "Mohan Kirushna R" in result.summary
    assert 'address:"semosh.work@example.com"' in scripts[0]
    assert 'subject:"Meeting"' in scripts[0]


async def test_send_email_derives_a_subject_from_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts = _mock_mail(monkeypatch, "sent")
    result = await _send_tool().execute(
        {"recipient": "a@b.co", "body": "can we move tomorrows standup to eleven am please"}
    )
    assert result.ok, result.summary
    assert 'subject:"can we move tomorrows standup to eleven am…"' in scripts[0]


async def test_send_email_stuck_in_outbox_is_reported_not_claimed_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mail's `send` verb only queues; a message lingering in the outbox
    (account needs re-login, no network) must be reported as NOT sent."""
    _mock_mail(monkeypatch, "queued")
    result = await _send_tool().execute({"recipient": "a@b.co", "body": "hi"})
    assert not result.ok
    assert "stuck in Mail's outbox" in result.summary


def test_send_email_confirmation_shows_the_full_letter() -> None:
    """The confirmation the user approves must show the actual email content,
    not raw JSON, so they can review a drafted letter before it sends."""
    tool = _send_tool()
    args = tool.args_model.model_validate(
        {"recipient": "mohan", "subject": "Invitation", "body": "Dear Mohan,\n\nPlease join."}
    )
    preview = tool.confirmation_action(args)
    assert preview is not None
    assert "mohan" in preview
    assert "Invitation" in preview
    assert "Please join." in preview


async def test_send_email_never_claims_success_without_mails_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Mail's `send` did not come back with our sentinel, the email may
    not have gone out — the tool must say NOT sent, never pretend."""
    _mock_mail(monkeypatch, "")  # osascript "succeeded" but returned nothing
    result = await _send_tool().execute({"recipient": "a@b.co", "body": "hi"})
    assert not result.ok
    assert "NOT sent" in result.summary


async def test_send_email_pins_the_configured_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    scripts = _mock_mail(monkeypatch, "sent")
    result = await _send_tool("official@example.com").execute(
        {"recipient": "a@b.co", "body": "hi"}
    )
    assert result.ok, result.summary
    assert 'set sender of newMessage to "official@example.com"' in scripts[0]
    assert "senderOK" in scripts[0]  # account existence is verified first


async def test_send_email_refuses_when_configured_sender_has_no_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never silently send from a different identity than configured."""
    _mock_mail(monkeypatch, "no-account")
    result = await _send_tool("official@example.com").execute(
        {"recipient": "a@b.co", "body": "hi"}
    )
    assert not result.ok
    assert "NOT sent" in result.summary
    assert "official@example.com" in result.summary


async def test_send_email_without_configured_sender_uses_mail_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts = _mock_mail(monkeypatch, "sent")
    result = await _send_tool().execute({"recipient": "a@b.co", "body": "hi"})
    assert result.ok, result.summary
    assert "set sender" not in scripts[0]


async def test_send_email_contact_without_address_fails_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_contact_emails(monkeypatch, "Mohan Kirushna R||\n")
    result = await _send_tool().execute({"recipient": "mohan kirushna", "body": "hi"})
    assert not result.ok
    assert "no email address" in result.summary


async def test_send_email_ambiguous_contact_asks_which_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_contact_emails(
        monkeypatch,
        "Mohan Raj||raj@example.com\nMohan Kirushna||mk@example.com\n",
    )
    result = await _send_tool().execute({"recipient": "Mohan", "body": "hi"})
    assert not result.ok
    assert "Mohan Raj" in result.summary and "Mohan Kirushna" in result.summary
