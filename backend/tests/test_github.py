"""GitHub tools: project resolution against real local repos, smart push workflows.

No fabricated URLs anywhere here: every repo used in these tests is a real
`git init` fixture with a real `origin` remote, matching how ProjectRegistry
actually works in production.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.model_manager import ModelManager
from app.core.project_registry import ProjectRegistry
from app.tools import github as github_module
from app.tools._common import CommandOutput
from app.tools.github import (
    GitHubOpenRepoTool,
    GitHubPushTool,
    OpenRepoArgs,
    PushChangesArgs,
    RefreshProjectsTool,
    _resolve_project,
)
from tests.conftest import FakeOllamaClient


def _make_repo(root: Path, name: str, remote: str | None) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    if remote is not None:
        subprocess.run(["git", "remote", "add", "origin", remote], cwd=repo, check=True)
    return repo


async def test_resolve_project_keyword_match(tmp_path: Path, settings: Settings) -> None:
    _make_repo(tmp_path, "skin_analyser", "git@github.com:mohan/skin-analyser.git")
    registry = ProjectRegistry(tmp_path)
    fake = FakeOllamaClient()
    manager = ModelManager(fake, settings)

    result = await _resolve_project("open skin", registry, fake, manager, settings)

    assert result is not None
    assert result.name == "skin_analyser"
    # Keyword match found it — LLM was never consulted.
    assert ("chat", settings.llm_model) not in fake.calls


async def test_resolve_project_llm_fallback(tmp_path: Path, settings: Settings) -> None:
    _make_repo(tmp_path, "jarvis_v2", "https://github.com/mohan/Personal_Assistant.git")
    registry = ProjectRegistry(tmp_path)
    fake = FakeOllamaClient()
    fake.queued.append("jarvis_v2")
    manager = ModelManager(fake, settings)

    result = await _resolve_project(
        "the thing i use for talking to my computer", registry, fake, manager, settings
    )

    assert result is not None
    assert result.name == "jarvis_v2"
    assert ("chat", settings.llm_model) in fake.calls


async def test_resolve_project_not_found(tmp_path: Path, settings: Settings) -> None:
    _make_repo(tmp_path, "jarvis_v2", "https://github.com/mohan/Personal_Assistant.git")
    registry = ProjectRegistry(tmp_path)
    fake = FakeOllamaClient()
    fake.queued.append("unknown")
    manager = ModelManager(fake, settings)

    result = await _resolve_project("completely unrelated banana", registry, fake, manager, settings)

    assert result is None


async def test_open_repo_uses_real_remote(tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """The URL that gets opened is the repo's real 'origin', not a guess."""
    _make_repo(tmp_path, "skin_analyser", "git@github.com:mohan/skin-analyser.git")
    registry = ProjectRegistry(tmp_path)
    fake = FakeOllamaClient()
    manager = ModelManager(fake, settings)

    opened_urls: list[str] = []

    async def mock_run(cmd, cwd=None, timeout=30.0):
        if cmd[0] == "open":
            opened_urls.append(cmd[1])
            return CommandOutput(0, "", "")
        return CommandOutput(1, "", "unexpected command in this test")

    monkeypatch.setattr(github_module, "run_command", mock_run)

    tool = GitHubOpenRepoTool(registry, fake, manager, settings)
    result = await tool.run(OpenRepoArgs(project="skin"))

    assert result.ok, result.summary
    assert opened_urls == ["https://github.com/mohan/skin-analyser"]


async def test_open_repo_not_found_lists_known_projects(
    tmp_path: Path, settings: Settings
) -> None:
    _make_repo(tmp_path, "jarvis_v2", "https://github.com/mohan/Personal_Assistant.git")
    registry = ProjectRegistry(tmp_path)
    fake = FakeOllamaClient()
    fake.queued.append("unknown")
    manager = ModelManager(fake, settings)

    tool = GitHubOpenRepoTool(registry, fake, manager, settings)
    result = await tool.run(OpenRepoArgs(project="nonexistent thing"))

    assert not result.ok
    assert "jarvis_v2" in result.summary


async def test_open_repo_no_remote_configured(tmp_path: Path, settings: Settings) -> None:
    """A local repo with no 'origin' must fail honestly, never open a fake URL."""
    _make_repo(tmp_path, "fresh_project", remote=None)
    registry = ProjectRegistry(tmp_path)
    fake = FakeOllamaClient()
    manager = ModelManager(fake, settings)

    tool = GitHubOpenRepoTool(registry, fake, manager, settings)
    result = await tool.run(OpenRepoArgs(project="fresh_project"))

    assert not result.ok
    assert "no GitHub remote" in result.summary


async def test_push_no_changes(tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = ProjectRegistry(tmp_path)
    fake = FakeOllamaClient()
    manager = ModelManager(fake, settings)

    async def mock_run(cmd, cwd=None, timeout=30.0):
        if "status" in cmd:
            return CommandOutput(0, "", "")  # clean tree
        return CommandOutput(0, "", "")

    monkeypatch.setattr(github_module, "run_command", mock_run)

    tool = GitHubPushTool(registry, fake, manager, settings)
    result = await tool.run(PushChangesArgs())

    assert result.ok
    assert "No changes" in result.summary


async def test_push_executes_full_sequence(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = ProjectRegistry(tmp_path)
    fake = FakeOllamaClient()
    fake.queued.append("Fix bug in app.py")
    manager = ModelManager(fake, settings)

    calls: list[list[str]] = []

    async def mock_run(cmd, cwd=None, timeout=30.0):
        calls.append(cmd)
        if "status" in cmd:
            return CommandOutput(0, "M app.py\n", "")
        if "add" in cmd:
            return CommandOutput(0, "", "")
        if "diff" in cmd:
            return CommandOutput(0, "diff --git a/app.py b/app.py\n+fixed the bug\n", "")
        if "commit" in cmd:
            return CommandOutput(0, "", "")
        if "push" in cmd:
            return CommandOutput(0, "", "")
        if "rev-parse" in cmd:
            return CommandOutput(0, "main\n", "")
        if "config" in cmd:
            return CommandOutput(0, "https://github.com/mohan/repo.git\n", "")
        if cmd[0] == "open":
            return CommandOutput(0, "", "")
        return CommandOutput(1, "", "unexpected: " + " ".join(cmd))

    monkeypatch.setattr(github_module, "run_command", mock_run)

    tool = GitHubPushTool(registry, fake, manager, settings)
    result = await tool.run(PushChangesArgs())

    assert result.ok, result.summary
    assert "Fix bug in app.py" in result.summary
    cmd_kinds = [c[1] if len(c) > 1 else c[0] for c in calls]
    add_idx = cmd_kinds.index("add")
    commit_idx = cmd_kinds.index("commit")
    push_idx = cmd_kinds.index("push")
    assert add_idx < commit_idx < push_idx


async def test_push_unknown_project_fails_honestly(
    tmp_path: Path, settings: Settings
) -> None:
    registry = ProjectRegistry(tmp_path)
    fake = FakeOllamaClient()
    fake.queued.append("unknown")
    manager = ModelManager(fake, settings)

    tool = GitHubPushTool(registry, fake, manager, settings)
    result = await tool.run(PushChangesArgs(project="nonexistent"))

    assert not result.ok
    assert "Could not find" in result.summary


async def test_refresh_projects_reports_count(tmp_path: Path) -> None:
    _make_repo(tmp_path, "skin_analyser", "git@github.com:mohan/skin-analyser.git")
    _make_repo(tmp_path, "jarvis_v2", "https://github.com/mohan/Personal_Assistant.git")
    registry = ProjectRegistry(tmp_path)

    tool = RefreshProjectsTool(registry)
    result = await tool.run(github_module.RefreshProjectsArgs())

    assert result.ok
    assert "2" in result.summary
    assert "skin_analyser" in result.summary and "jarvis_v2" in result.summary
