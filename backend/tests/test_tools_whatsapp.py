"""WhatsApp tool: number normalization, contact resolution, and sending.

Contacts lookups and the WAHA HTTP call are mocked; nothing here touches
the real address book or network.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import Settings
from app.tools import _contacts as contacts_module
from app.tools._common import CommandOutput
from app.tools.whatsapp import WhatsAppSendTool


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "waha_base_url": "http://127.0.0.1:3000",
        "waha_api_key": "test-key",
        "whatsapp_default_country_code": "91",
    }
    values.update(overrides)
    return Settings(**values)


def _mock_contacts(
    monkeypatch: pytest.MonkeyPatch, stdout: str, ok: bool = True, stderr: str = "denied"
) -> None:
    async def fake_osascript(script: str, timeout: float = 30.0) -> CommandOutput:
        assert "Contacts" in script
        return CommandOutput(0 if ok else 1, stdout, "" if ok else stderr)

    async def fake_open(argv: list[str], cwd=None, timeout=30.0) -> CommandOutput:
        assert argv[:2] == ["/usr/bin/open", "-g"]  # Contacts launched hidden first
        return CommandOutput(0, "", "")

    monkeypatch.setattr(contacts_module, "run_osascript", fake_osascript)
    monkeypatch.setattr(contacts_module, "run_command", fake_open)


def _capture_post(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    sent: dict[str, Any] = {}

    def fake_post(url: str, api_key: str, payload: dict[str, str]) -> tuple[int, str]:
        sent["url"] = url
        sent["payload"] = payload
        return 200, "{}"

    monkeypatch.setattr(WhatsAppSendTool, "_post", staticmethod(fake_post))
    return sent


def test_chat_id_prepends_default_country_code_to_bare_numbers() -> None:
    tool = WhatsAppSendTool(_settings())
    assert tool._chat_id("9080209303") == "919080209303@c.us"
    assert tool._chat_id("090802 09303") == "919080209303@c.us"  # trunk prefix dropped
    assert tool._chat_id("+91 90802 09303") == "919080209303@c.us"
    assert tool._chat_id("0014155550123") == "14155550123@c.us"  # 00 = international
    assert tool._chat_id("+1 415-555-0123") == "14155550123@c.us"  # 11 digits, untouched
    assert tool._chat_id("Mohan") is None


def test_extract_number_finds_a_number_after_a_name() -> None:
    assert WhatsAppSendTool._extract_number("Mohan kirushna 9080209303") == "9080209303"
    assert WhatsAppSendTool._extract_number("+1 415 555 0123") == "+1 415 555 0123"
    assert WhatsAppSendTool._extract_number("mohan kirushna") is None
    # Short digit runs stay part of the name, not a number.
    assert WhatsAppSendTool._extract_number("Area 51") is None


async def test_send_resolves_contact_name_via_contacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_contacts(monkeypatch, "Mohan Kirushna||+91 90802 09303\n")
    sent = _capture_post(monkeypatch)
    tool = WhatsAppSendTool(_settings())
    result = await tool.execute({"recipient": "mohan kirushna", "message": "hello"})
    assert result.ok, result.summary
    assert sent["url"] == "http://127.0.0.1:3000/api/sendText"
    assert sent["payload"] == {
        "chatId": "919080209303@c.us",
        "text": "hello",
        "session": "default",
    }
    assert "Mohan Kirushna" in result.summary


async def test_send_applies_country_code_to_contact_saved_without_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_contacts(monkeypatch, "Amma||090802 09303\n")
    sent = _capture_post(monkeypatch)
    result = await WhatsAppSendTool(_settings()).execute(
        {"recipient": "amma", "message": "good night"}
    )
    assert result.ok, result.summary
    assert sent["payload"]["chatId"] == "919080209303@c.us"


async def test_send_uses_spoken_number_without_contacts_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def explode(script: str, timeout: float = 30.0) -> CommandOutput:
        raise AssertionError("Contacts must not be queried when a number is given")

    monkeypatch.setattr(contacts_module, "run_osascript", explode)
    sent = _capture_post(monkeypatch)
    result = await WhatsAppSendTool(_settings()).execute(
        {"recipient": "Mohan kirushna 9080209303", "message": "hello"}
    )
    assert result.ok, result.summary
    assert sent["payload"]["chatId"] == "919080209303@c.us"


async def test_exact_name_match_beats_partial_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_contacts(
        monkeypatch,
        "Mohan Raj||+91 11111 11111\nMohan||+91 90802 09303\n",
    )
    sent = _capture_post(monkeypatch)
    result = await WhatsAppSendTool(_settings()).execute(
        {"recipient": "Mohan", "message": "hi"}
    )
    assert result.ok, result.summary
    assert sent["payload"]["chatId"] == "919080209303@c.us"


async def test_ambiguous_contact_asks_which_one(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_contacts(
        monkeypatch,
        "Mohan Raj||+91 11111 11111\nMohan Kirushna||+91 90802 09303\n",
    )
    result = await WhatsAppSendTool(_settings()).execute(
        {"recipient": "Mohan", "message": "hi"}
    )
    assert not result.ok
    assert "Mohan Raj" in result.summary and "Mohan Kirushna" in result.summary


async def test_unknown_contact_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_contacts(monkeypatch, "")
    result = await WhatsAppSendTool(_settings()).execute(
        {"recipient": "nobody i know", "message": "hi"}
    )
    assert not result.ok
    assert "couldn't find" in result.summary


async def test_contact_without_phone_number_fails_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_contacts(monkeypatch, "Mohan Kirushna||\n")
    result = await WhatsAppSendTool(_settings()).execute(
        {"recipient": "mohan kirushna", "message": "hi"}
    )
    assert not result.ok
    assert "no phone number" in result.summary


async def test_contacts_permission_error_names_the_automation_pane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_contacts(
        monkeypatch, "", ok=False,
        stderr="Not authorized to send Apple events to Contacts. (-1743)",
    )
    result = await WhatsAppSendTool(_settings()).execute(
        {"recipient": "mohan kirushna", "message": "hi"}
    )
    assert not result.ok
    assert "Automation" in result.summary


async def test_contacts_other_errors_are_reported_not_blamed_on_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real bug: EVERY lookup failure (e.g. 'Application isn't running'
    (-600)) was reported as a permissions problem, sending the user to grant
    access that was never the issue."""
    _mock_contacts(
        monkeypatch, "", ok=False,
        stderr="execution error: Contacts got an error: Application isn't running. (-600)",
    )
    result = await WhatsAppSendTool(_settings()).execute(
        {"recipient": "mohan kirushna", "message": "hi"}
    )
    assert not result.ok
    assert "Grant" not in result.summary and "Automation" not in result.summary
    assert "-600" in result.summary or "isn't running" in result.summary


async def test_duplicate_contact_cards_are_not_treated_as_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synced address books often hold two identical cards for one person —
    that must send to them, not ask 'which one did you mean?'"""
    _mock_contacts(
        monkeypatch,
        "Mohan Kirushna R||+91 90802 09303\nMohan Kirushna R||+91 90802 09303\n",
    )
    sent = _capture_post(monkeypatch)
    result = await WhatsAppSendTool(_settings()).execute(
        {"recipient": "mohan kirushna", "message": "hi"}
    )
    assert result.ok, result.summary
    assert sent["payload"]["chatId"] == "919080209303@c.us"
