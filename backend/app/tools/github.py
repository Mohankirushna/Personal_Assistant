"""GitHub integration: open real local repos, smart git workflows with confirmations.

Project names resolve against `ProjectRegistry`, which reads each local
repo's actual 'origin' remote — never a hardcoded or guessed URL. Push
workflows run against the resolved project's own directory (or Jarvis's own
directory if no project is named), with staged confirmations (branch, commit
message, final push) and open the repo/branch in the browser afterward.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.model_manager import ModelManager
from app.core.ollama_client import OllamaLike
from app.core.project_registry import ProjectInfo, ProjectRegistry, normalize_remote_url
from app.planner.schemas import RiskLevel, ToolResult
from app.tools._common import run_command
from app.tools.base import Tool


async def _resolve_project(
    user_input: str,
    registry: ProjectRegistry,
    client: OllamaLike,
    model_manager: ModelManager,
    settings: Settings,
) -> ProjectInfo | None:
    """Resolve a spoken project name to a local repo via keyword match, then LLM fallback."""
    match = await registry.find(user_input)
    if match is not None:
        return match

    projects = await registry.list_projects()
    if not projects:
        return None
    names = ", ".join(p.name for p in projects)
    model = await model_manager.ensure_llm()
    prompt = (
        f'User said: "{user_input}"\n'
        f"Available local projects: {names}\n"
        "Which project did they mean? Reply with ONLY the project name from the "
        "list, nothing else. If unsure, reply 'unknown'."
    )
    reply = await client.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        keep_alive=settings.llm_keep_alive,
    )
    matched_name = reply.strip().lower()
    for project in projects:
        if project.name.lower() == matched_name:
            return project
    return None


async def _current_branch(cwd: Path | None) -> str:
    result = await run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    return result.stdout.strip() if result.ok and result.stdout.strip() else "main"


async def _suggest_commit_message(
    client: OllamaLike, model_manager: ModelManager, settings: Settings, cwd: Path | None
) -> str:
    diff = await run_command(["git", "diff", "--cached"], cwd=cwd)
    if not diff.ok or not diff.stdout.strip():
        return "Update code"
    model = await model_manager.ensure_llm()
    prompt = (
        "Based on this git diff, suggest ONE short commit message (under 50 "
        f"chars, imperative mood, no period):\n\n{diff.stdout[:2000]}"
    )
    reply = await client.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        keep_alive=settings.llm_keep_alive,
    )
    return reply.strip().strip('"')[:72] or "Update code"


class OpenRepoArgs(BaseModel):
    project: str = Field(description="Project name or keyword (e.g., 'skin', 'jarvis', 'mail').")


class GitHubOpenRepoTool(Tool):
    name: ClassVar[str] = "github_open_repo"
    description: ClassVar[str] = (
        "Open a local project's GitHub repo in the browser. Say the project name "
        "or a keyword from it (e.g., 'open skin in github', 'show me jarvis'). "
        "Only works for projects that exist locally with a GitHub remote."
    )
    args_model: ClassVar[type[BaseModel]] = OpenRepoArgs
    risk_level: ClassVar[RiskLevel] = RiskLevel.SAFE

    def __init__(
        self,
        registry: ProjectRegistry,
        client: OllamaLike,
        model_manager: ModelManager,
        settings: Settings | None = None,
    ) -> None:
        self._registry = registry
        self._client = client
        self._model_manager = model_manager
        self._settings = settings or get_settings()

    async def run(self, args: OpenRepoArgs) -> ToolResult:  # type: ignore[override]
        project = await _resolve_project(
            args.project, self._registry, self._client, self._model_manager, self._settings
        )
        if project is None:
            known = ", ".join(p.name for p in await self._registry.list_projects())
            detail = (
                f" Known local projects: {known}." if known
                else f" No git repos found under {self._registry.root}."
            )
            return ToolResult.failure(
                self.name, f"Could not find a local project matching '{args.project}'.{detail}"
            )
        if project.remote_url is None:
            return ToolResult.failure(
                self.name,
                f"{project.name} exists locally but has no GitHub remote configured "
                "(no 'origin' set). Add one with `git remote add origin <url>`.",
            )
        result = await run_command(["open", project.remote_url])
        if not result.ok:
            return ToolResult.failure(self.name, f"Could not open browser: {result.combined()}")
        return ToolResult(
            tool=self.name, ok=True,
            summary=f"Opened {project.name} on GitHub.",
            data={"project": project.name, "url": project.remote_url},
        )


class PushChangesArgs(BaseModel):
    project: str | None = Field(
        default=None,
        description="Which local project to push (e.g. 'skin', 'jarvis'). "
        "Omit to push from Jarvis's own working directory.",
    )
    message: str | None = Field(
        default=None,
        description="Commit message. If omitted, one is suggested from the diff.",
    )
    branch: str | None = Field(
        default=None,
        description="Which branch to push to. If omitted, the current branch is used.",
    )


class GitHubPushTool(Tool):
    name: ClassVar[str] = "github_push"
    description: ClassVar[str] = (
        "Push staged changes to GitHub with confirmations: shows git status, "
        "suggests a commit message, confirms branch and final push. Use for "
        "'push changes', 'commit and push', 'ship it'."
    )
    args_model: ClassVar[type[BaseModel]] = PushChangesArgs
    risk_level: ClassVar[RiskLevel] = RiskLevel.SENSITIVE

    def __init__(
        self,
        registry: ProjectRegistry,
        client: OllamaLike,
        model_manager: ModelManager,
        settings: Settings | None = None,
    ) -> None:
        self._registry = registry
        self._client = client
        self._model_manager = model_manager
        self._settings = settings or get_settings()

    def confirmation_action(self, args: BaseModel) -> str | None:
        assert isinstance(args, PushChangesArgs)
        where = f" in {args.project}" if args.project else ""
        msg = args.message or "(will suggest based on diff)"
        branch = args.branch or "(current branch)"
        return f"About to push{where} with message '{msg}' to branch {branch}. Confirm?"

    async def run(self, args: PushChangesArgs) -> ToolResult:  # type: ignore[override]
        cwd: Path | None = None
        if args.project:
            project = await _resolve_project(
                args.project, self._registry, self._client, self._model_manager, self._settings
            )
            if project is None:
                known = ", ".join(p.name for p in await self._registry.list_projects())
                detail = f" Known local projects: {known}." if known else ""
                return ToolResult.failure(
                    self.name, f"Could not find a local project matching '{args.project}'.{detail}"
                )
            cwd = project.path

        status = await run_command(["git", "status", "--porcelain"], cwd=cwd)
        if not status.ok:
            return ToolResult.failure(self.name, f"git status failed: {status.combined()}")
        if not status.stdout.strip():
            return ToolResult(
                tool=self.name, ok=True, summary="No changes to commit.", data={"status": "clean"},
            )

        add_result = await run_command(["git", "add", "."], cwd=cwd)
        if not add_result.ok:
            return ToolResult.failure(self.name, f"git add failed: {add_result.combined()}")

        message = args.message or await _suggest_commit_message(
            self._client, self._model_manager, self._settings, cwd
        )
        branch = args.branch or await _current_branch(cwd)

        commit_result = await run_command(["git", "commit", "-m", message], cwd=cwd)
        if not commit_result.ok and "nothing to commit" not in commit_result.combined().lower():
            return ToolResult.failure(self.name, f"git commit failed: {commit_result.combined()}")

        push_result = await run_command(["git", "push", "origin", branch], cwd=cwd)
        if not push_result.ok:
            return ToolResult.failure(self.name, f"git push failed: {push_result.combined()}")

        remote = await run_command(["git", "config", "--get", "remote.origin.url"], cwd=cwd)
        if remote.ok and remote.stdout.strip():
            browser_url = f"{normalize_remote_url(remote.stdout)}/commits/{branch}"
            await run_command(["open", browser_url])

        return ToolResult(
            tool=self.name, ok=True,
            summary=f"Pushed '{message}' to {branch} and opened GitHub.",
            data={"branch": branch, "message": message, "status": "pushed"},
        )


class RefreshProjectsArgs(BaseModel):
    pass


class RefreshProjectsTool(Tool):
    name: ClassVar[str] = "refresh_projects"
    description: ClassVar[str] = (
        "Re-scan the local projects folder for git repos, e.g. after cloning a "
        "new one. Use for 'refresh my projects' or 'find my new repo'."
    )
    args_model: ClassVar[type[BaseModel]] = RefreshProjectsArgs
    risk_level: ClassVar[RiskLevel] = RiskLevel.SAFE

    def __init__(self, registry: ProjectRegistry) -> None:
        self._registry = registry

    async def run(self, args: RefreshProjectsArgs) -> ToolResult:  # type: ignore[override]
        count = await self._registry.refresh()
        projects = await self._registry.list_projects()
        names = ", ".join(p.name for p in projects) if projects else "none"
        return ToolResult(
            tool=self.name, ok=True,
            summary=f"Found {count} local project(s): {names}.",
            data={"count": count, "projects": names},
        )
