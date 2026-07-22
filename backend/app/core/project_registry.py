"""Discovers local projects and (for git repos) their real GitHub remotes.

Scans every immediate subdirectory of a projects folder — not only git repos
— so keyword matching works for any project the user names, whether or not
it's under version control yet. For folders that ARE git repos, the real
`origin` remote is read via `git config` (never guessed), which is what lets
"open skin in github" resolve to a working URL.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

from app.tools._common import run_command

# Keywords shorter than this (e.g. "v2", "rl") are too generic to match on
# their own and cause false positives against unrelated speech.
_MIN_KEYWORD_LEN = 3

# Noise tokens that come from how downloaded repos are named (e.g. a GitHub
# zip extracts as "<repo>-main"). They shouldn't be standalone match keywords —
# "where is the main project" must not match every downloaded folder.
_KEYWORD_STOPWORDS = frozenset({"main", "master", "new", "old", "final", "copy"})


@dataclass(frozen=True)
class ProjectInfo:
    name: str  # folder name, e.g. "automated_mail_classification-main"
    path: Path
    remote_url: str | None  # normalized https URL, or None if not a git repo / no origin
    keywords: tuple[str, ...]
    is_git: bool = False


def normalize_remote_url(url: str) -> str:
    """git@github.com:user/repo.git -> https://github.com/user/repo."""
    url = url.strip()
    if url.startswith("git@"):
        host_and_path = url.split("@", 1)[1]
        host, _, path = host_and_path.partition(":")
        url = f"https://{host}/{path}"
    return url.removesuffix(".git")


def _derive_keywords(folder_name: str) -> tuple[str, ...]:
    """'automated_mail_classification-main' -> 'automated','mail','classification'
    (plus the whole name). Noise suffixes like '-main' are dropped so they don't
    become standalone match terms."""
    parts = re.split(r"[_\-\s]+", folder_name.lower())
    tokens = {
        t for t in parts if len(t) >= _MIN_KEYWORD_LEN and t not in _KEYWORD_STOPWORDS
    }
    tokens.add(folder_name.lower())  # the exact name always matches
    return tuple(sorted(tokens))


class ProjectRegistry:
    """Scans a projects directory for local projects, caching until refreshed."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser()
        self._projects: dict[str, ProjectInfo] = {}
        self._scanned = False

    async def refresh(self) -> int:
        """Re-scan the projects directory. Returns how many projects were found."""
        if not self.root.is_dir():
            self._projects = {}
            self._scanned = True
            return 0
        candidates = [
            entry for entry in self.root.iterdir()
            if entry.is_dir() and not entry.name.startswith(".")
        ]
        results = await asyncio.gather(*(self._inspect(entry) for entry in candidates))
        self._projects = {info.name: info for info in results}
        self._scanned = True
        return len(self._projects)

    @staticmethod
    async def _inspect(path: Path) -> ProjectInfo:
        is_git = (path / ".git").exists()
        remote: str | None = None
        if is_git:
            result = await run_command(
                ["git", "config", "--get", "remote.origin.url"], cwd=path
            )
            if result.ok and result.stdout.strip():
                remote = normalize_remote_url(result.stdout)
        return ProjectInfo(
            name=path.name,
            path=path,
            remote_url=remote,
            keywords=_derive_keywords(path.name),
            is_git=is_git,
        )

    async def list_projects(self) -> list[ProjectInfo]:
        if not self._scanned:
            await self.refresh()
        return list(self._projects.values())

    def cached_projects(self) -> list[ProjectInfo]:
        """Synchronous view of whatever is already cached (no scan). Empty
        before the first scan. For callers that can't await, e.g. a tool's
        confirmation_action building its preview."""
        return list(self._projects.values())

    async def find(self, query: str) -> ProjectInfo | None:
        """Best keyword match for the query, or None.

        Every project keyword appearing in the query scores by its length, so
        "automated mail" prefers `automated_mail_classification` (two long
        hits) over a folder that merely shares a short token. Git repos win
        ties (the user more often asks about ones they've pushed)."""
        if not self._scanned:
            await self.refresh()
        return self.find_cached(query)

    def find_cached(self, query: str) -> ProjectInfo | None:
        """Same scoring as `find`, but synchronous — only searches whatever is
        already cached (no scan triggered). Returns None before the first
        scan. Exists for callers that can't await, e.g. a Tool's
        `confirmation_action`, which needs to build its preview text (and any
        side effect like opening a browser tab) before the async gate runs."""
        query_lower = query.lower()
        best: ProjectInfo | None = None
        best_score = 0.0
        for info in self._projects.values():
            score = sum(
                len(kw)
                for kw in info.keywords
                if len(kw) >= _MIN_KEYWORD_LEN and kw in query_lower
            )
            if score == 0:
                continue
            # Break ties in favour of git repos, then shorter folder names
            # (a more specific match than a long compound name).
            ranked = (score, info.is_git, -len(info.name))
            best_ranked = (
                (best_score, best.is_git, -len(best.name)) if best else (0.0, False, 0)
            )
            if ranked > best_ranked:
                best, best_score = info, score
        return best
