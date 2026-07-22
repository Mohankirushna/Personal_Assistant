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


async def test_refresh_finds_real_repos_only(tmp_path: Path) -> None:
    _make_repo(tmp_path, "skin_analyser", "git@github.com:mohan/skin-analyser.git")
    (tmp_path / "plain_folder").mkdir()  # no .git — must be skipped

    registry = ProjectRegistry(tmp_path)
    count = await registry.refresh()

    projects = await registry.list_projects()
    names = {p.name for p in projects}
    assert names == {"skin_analyser"}
    assert count == 1


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
