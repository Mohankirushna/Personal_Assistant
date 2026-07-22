"""ProjectRegistry: scans real local git repos, reads their real remotes."""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.core.project_registry import ProjectRegistry, normalize_remote_url


def _make_repo(root: Path, name: str, remote: str | None) -> None:
    """A real `git init` repo (git rejects a hand-crafted .git/config with no
    HEAD/objects as 'not a git repository'), optionally with an origin set."""
    repo = root / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    if remote is not None:
        subprocess.run(["git", "remote", "add", "origin", remote], cwd=repo, check=True)


def test_normalize_ssh_remote() -> None:
    assert normalize_remote_url("git@github.com:mohan/skin-analyser.git") == (
        "https://github.com/mohan/skin-analyser"
    )


def test_normalize_https_remote() -> None:
    assert normalize_remote_url("https://github.com/mohan/jarvis.git\n") == (
        "https://github.com/mohan/jarvis"
    )


async def test_refresh_indexes_all_folders_flagging_git(tmp_path: Path) -> None:
    """Non-git folders are indexed too (so keyword matching finds them), but
    flagged is_git=False and carry no remote."""
    _make_repo(tmp_path, "skin_analyser", "git@github.com:mohan/skin-analyser.git")
    (tmp_path / "automated_mail_classification-main").mkdir()  # no .git
    (tmp_path / ".hidden").mkdir()  # dotfolders skipped

    registry = ProjectRegistry(tmp_path)
    count = await registry.refresh()

    projects = {p.name: p for p in await registry.list_projects()}
    assert set(projects) == {"skin_analyser", "automated_mail_classification-main"}
    assert count == 2
    assert projects["skin_analyser"].is_git is True
    assert projects["automated_mail_classification-main"].is_git is False
    assert projects["automated_mail_classification-main"].remote_url is None


async def test_find_matches_non_git_folder_by_keyword(tmp_path: Path) -> None:
    """The original bug: 'automated mail' must resolve the non-git folder."""
    _make_repo(tmp_path, "jarvis_v2", "https://github.com/mohan/Personal_Assistant.git")
    (tmp_path / "automated_mail_classification-main").mkdir()

    registry = ProjectRegistry(tmp_path)
    await registry.refresh()

    match = await registry.find("where is my automated mail project")
    assert match is not None
    assert match.name == "automated_mail_classification-main"
    assert match.is_git is False


async def test_find_prefers_stronger_keyword_overlap(tmp_path: Path) -> None:
    (tmp_path / "automated_mail_classification-main").mkdir()
    (tmp_path / "mailer").mkdir()

    registry = ProjectRegistry(tmp_path)
    await registry.refresh()

    # "automated mail classification" overlaps the long compound name on
    # multiple long tokens; it should win over the folder sharing only "mail".
    match = await registry.find("automated mail classification")
    assert match is not None
    assert match.name == "automated_mail_classification-main"


async def test_repo_without_remote_reports_none(tmp_path: Path) -> None:
    _make_repo(tmp_path, "fresh_project", remote=None)

    registry = ProjectRegistry(tmp_path)
    await registry.refresh()
    projects = await registry.list_projects()

    assert len(projects) == 1
    assert projects[0].remote_url is None


async def test_find_matches_by_keyword(tmp_path: Path) -> None:
    _make_repo(tmp_path, "skin_analyser", "git@github.com:mohan/skin-analyser.git")
    _make_repo(tmp_path, "jarvis_v2", "https://github.com/mohan/Personal_Assistant.git")

    registry = ProjectRegistry(tmp_path)
    await registry.refresh()

    match = await registry.find("open skin in github")
    assert match is not None
    assert match.name == "skin_analyser"
    assert match.remote_url == "https://github.com/mohan/skin-analyser"


async def test_find_no_match_returns_none(tmp_path: Path) -> None:
    _make_repo(tmp_path, "jarvis_v2", "https://github.com/mohan/Personal_Assistant.git")

    registry = ProjectRegistry(tmp_path)
    await registry.refresh()

    assert await registry.find("something totally unrelated xyz") is None


async def test_missing_projects_dir_yields_empty_registry(tmp_path: Path) -> None:
    registry = ProjectRegistry(tmp_path / "does_not_exist")
    count = await registry.refresh()

    assert count == 0
    assert await registry.list_projects() == []
