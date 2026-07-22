"""Discovers local git repos and their real GitHub remotes.

Scans immediate subdirectories of a projects folder for `.git` repos and
reads each one's actual `origin` remote via `git remote get-url origin` —
never guessed, never hand-maintained. This is what lets "open skin in
github" resolve to a real, working URL instead of a fabricated one.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

from app.tools._common import run_command

# Keywords shorter than this (e.g. "v2", "an") are too generic to match on
# their own and cause false positives against unrelated speech.
_MIN_KEYWORD_LEN = 3


@dataclass(frozen=True)
class ProjectInfo:
    name: str  # folder name, e.g. "skin_analyser"
    path: Path
    remote_url: str | None  # normalized https URL, or None if no 'origin' set
    keywords: tuple[str, ...]


def normalize_remote_url(url: str) -> str:
    """git@github.com:user/repo.git -> https://github.com/user/repo."""
    url = url.strip()
    if url.startswith("git@"):
        host_and_path = url.split("@", 1)[1]
        host, _, path = host_and_path.partition(":")
        url = f"https://{host}/{path}"
    return url.removesuffix(".git")


def _derive_keywords(folder_name: str) -> tuple[str, ...]:
    """'skin_analyser-main' -> tokens like 'skin', 'analyser', plus the whole name."""
    tokens = {t for t in re.split(r"[_\-\s]+", folder_name.lower()) if len(t) >= _MIN_KEYWORD_LEN}
    tokens.add(folder_name.lower())
    return tuple(sorted(tokens))


class ProjectRegistry:
    """Scans a projects directory for local git repos, caching results until refreshed."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser()
        self._projects: dict[str, ProjectInfo] = {}
        self._scanned = False

    async def refresh(self) -> int:
        """Re-scan the projects directory. Returns how many repos were found."""
        if not self.root.is_dir():
            self._projects = {}
            self._scanned = True
            return 0
        candidates = [
            entry for entry in self.root.iterdir()
            if entry.is_dir() and (entry / ".git").exists()
        ]
        results = await asyncio.gather(*(self._inspect(entry) for entry in candidates))
        self._projects = {info.name: info for info in results}
        self._scanned = True
        return len(self._projects)

    @staticmethod
    async def _inspect(path: Path) -> ProjectInfo:
        result = await run_command(["git", "config", "--get", "remote.origin.url"], cwd=path)
        remote = normalize_remote_url(result.stdout) if result.ok and result.stdout.strip() else None
        return ProjectInfo(
            name=path.name, path=path, remote_url=remote, keywords=_derive_keywords(path.name)
        )

    async def list_projects(self) -> list[ProjectInfo]:
        if not self._scanned:
            await self.refresh()
        return list(self._projects.values())

    async def find(self, query: str) -> ProjectInfo | None:
        """Keyword match: a project's keyword appearing in the query wins."""
        if not self._scanned:
            await self.refresh()
        query_lower = query.lower()
        for info in self._projects.values():
            if any(kw in query_lower for kw in info.keywords if len(kw) >= _MIN_KEYWORD_LEN):
                return info
        return None
