from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .adapters.codex import CodexAdapter
from .adapters.nine_router import NineRouterReasoner
from .chat import ChatRequest
from .chat_history import ChatHistory
from .chat_import import MAX_IMPORT_BYTES, parse_export
from .cli_config import CliConfigStore, CliConfiguration, CliProfile
from .cli_setup import setup_for
from .combo_registry import ComboRegistry
from .command_api import command_router
from .command_console import CommandConsole
from .config import Settings
from .delivery_config import DeliveryConfig, DeliveryConfigStore
from .git import GitRepository
from .git_view import Comparison, GitAction, GitReview
from .job_store import JobStore
from .mcp_catalog import mcp_catalog
from .mcp_config import McpConfigStore, McpConfiguration, McpTool
from .orchestrator import EngineeringOrchestrator
from .process import ProcessError, find_tool, run_process
from .repository_defaults import repository_defaults
from .workflow_config import (
    StepCatalog,
    WorkflowCollection,
    WorkflowRecipe,
    WorkflowStore,
    default_recipe,
)
from .workspace_config import Workspace, WorkspaceConfiguration, WorkspaceStore

ASSETS = Path(__file__).parent / "web_assets"
store = JobStore()
workflow_store = WorkflowStore()
combo_registry = ComboRegistry()
mcp_store = McpConfigStore()
cli_store = CliConfigStore()
delivery_store = DeliveryConfigStore()
workspace_store = WorkspaceStore()
command_console = CommandConsole()


@asynccontextmanager
async def lifespan(app):
    yield
    command_console.close()


app = FastAPI(title="9router Switchyard", docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
app.include_router(command_router(
    command_console, lambda identifier: _workspace(identifier),
    lambda: cli_store.get(), lambda: workspace_store.get(),
))
app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
dispatch_lock = threading.Lock()
folder_picker_lock = threading.Lock()
chat_lock = threading.Lock()
chat_history = ChatHistory()
git_lock = threading.Lock()


class FolderBrowseRequest(BaseModel):
    path: str = ""


class ChatImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    workspace_id: str = Field(min_length=1, max_length=120)
    source_name: str = Field(min_length=1, max_length=80)
    export: dict | list
    confirmed: bool = False
    selected: list[int] = Field(default_factory=list, max_length=1000)
    repository: str | None = None


class GitActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: GitAction
    repository: str | None = None
    workspace_id: str | None = None
    paths: list[str] = Field(default_factory=list, max_length=500)
    message: str = Field(default="", max_length=10_000)
    branch: str = Field(default="", max_length=255)
    start_point: str = Field(default="", max_length=255)
    remote: str = Field(default="", max_length=255)
    strategy: Literal["ff_only", "rebase", "merge"] = "ff_only"
    stash_ref: str = Field(default="", max_length=255)
    ref: str = Field(default="", max_length=255)
    include_untracked: bool = False
    amend: bool = False
    reset_mode: Literal["soft", "mixed", "hard"] = "mixed"
    confirmed: bool = False


class WorktreeActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    path: str = Field(min_length=1, max_length=4096)
    action: Literal["remove", "protect", "unprotect"]
    confirmed: bool = False


@app.post("/api/repository/folders")
def browse_repository_folders(request: FolderBrowseRequest) -> dict:
    try:
        path = Path(request.path).expanduser() if request.path else Path.home()
        if not path.is_absolute():
            raise HTTPException(422, "Enter an absolute folder path.")
        path = path.resolve(strict=True)
        if not path.is_dir():
            raise HTTPException(422, "Choose a folder, not a file.")
        folders = []
        for child in path.iterdir():
            try:
                if child.is_dir():
                    folders.append({"name": child.name, "path": str(child)})
            except OSError:
                continue
        folders.sort(key=lambda item: (item["name"].casefold(), item["name"]))
        return {
            "path": str(path),
            "parent": str(path.parent) if path.parent != path else None,
            "folders": folders,
        }
    except FileNotFoundError as exc:
        raise HTTPException(404, "Folder not found. Check the path or choose Home.") from exc
    except PermissionError as exc:
        raise HTTPException(403, "This folder cannot be read. Choose another folder.") from exc
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(422, "Cannot read this folder. Check the path and try again.") from exc


@app.post("/api/repository/git-defaults")
def get_repository_defaults(request: FolderBrowseRequest) -> dict:
    try:
        return repository_defaults(request.path)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Choose an existing repository folder.") from exc
    except PermissionError as exc:
        raise HTTPException(403, "This folder cannot be read. Choose another folder.") from exc
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/repository/pick")
def pick_repository() -> dict:
    picker = _folder_picker_command()
    if not picker:
        raise HTTPException(503, "Folder picker unavailable. Paste the repository path instead.")
    if not folder_picker_lock.acquire(blocking=False):
        raise HTTPException(409, "A folder selection window is already open.")
    try:
        result = subprocess.run(
            picker,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode == 1 or (
            sys.platform == "darwin" and "(-128)" in result.stderr
        ) or (result.returncode == 0 and not result.stdout.strip()):
            return {"path": None}
        if result.returncode != 0:
            raise HTTPException(503, "Cannot open the folder window. Paste a path instead.")
        path = Path(result.stdout.strip()).resolve()
        if not result.stdout.strip() or not path.is_dir():
            raise HTTPException(422, "Choose an existing directory.")
        return {"path": str(path)}
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(408, "Folder selection timed out. Try Browse again.") from exc
    except OSError as exc:
        raise HTTPException(503, "Cannot open the folder window. Paste a path instead.") from exc
    finally:
        folder_picker_lock.release()


def _folder_picker_command() -> list[str] | None:
    if sys.platform == "darwin":
        picker = find_tool("osascript")
        return [picker, "-e", 'POSIX path of (choose folder with prompt "Select repository folder")'] if picker else None
    if sys.platform == "win32":
        picker = find_tool("powershell.exe") or find_tool("powershell") or find_tool("pwsh")
        script = (
            "$shell=New-Object -ComObject Shell.Application;"
            "$folder=$shell.BrowseForFolder(0,'Select repository folder',0,0);"
            "if($folder){$folder.Self.Path}"
        )
        return [
            picker, "-NoProfile", "-NonInteractive", "-STA", "-Command", script
        ] if picker else None
    picker = find_tool("zenity")
    return [picker, "--file-selection", "--directory", "--title=Select repository folder"] if picker else None


@app.get("/api/git/status")
def git_status(
    repository: str | None = None,
    comparison: Comparison = "working",
    base: str | None = None,
    workspace_id: str | None = None,
) -> dict:
    try:
        if workspace_id and comparison == "branch" and not base:
            base = _workspace(workspace_id).git_base_branch
        return GitReview(_git_repository(repository, workspace_id)).snapshot(
            comparison=comparison, base=base
        )
    except (ProcessError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/git/diff")
def git_diff(
    path: str,
    repository: str | None = None,
    comparison: Comparison = "working",
    base: str | None = None,
    workspace_id: str | None = None,
) -> dict:
    try:
        if workspace_id and comparison == "branch" and not base:
            base = _workspace(workspace_id).git_base_branch
        return GitReview(_git_repository(repository, workspace_id)).patch(
            path, comparison=comparison, base=base
        )
    except (ProcessError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/git/action")
def git_action(request: GitActionRequest) -> dict:
    consequential = {
        "discard", "delete_untracked", "stash_drop", "abort_operation", "skip_operation",
        "reset", "rebase", "delete_branch", "resolve_ours", "resolve_theirs", "push",
        "pull", "merge", "cherry_pick", "revert",
    }
    if (request.action in consequential or request.amend) and not request.confirmed:
        raise HTTPException(422, "Confirm this Git action before continuing.")
    if not git_lock.acquire(blocking=False):
        raise HTTPException(409, "Another Git operation is running. Wait for it to finish.")
    try:
        review = GitReview(_git_repository(request.repository, request.workspace_id))
        return review.action(
            request.action,
            paths=request.paths,
            message=request.message,
            branch=request.branch,
            start_point=request.start_point,
            remote=request.remote,
            strategy=request.strategy,
            stash_ref=request.stash_ref,
            ref=request.ref,
            include_untracked=request.include_untracked,
            amend=request.amend,
            reset_mode=request.reset_mode,
        )
    except (ProcessError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        git_lock.release()


def _managed_worktrees(workspace: Workspace) -> tuple[GitRepository, Path, list[dict]]:
    repository = GitRepository(Path(workspace.repository))
    state_dir = Settings().state_dir(repository.root)
    return repository, state_dir, repository.managed_worktrees(state_dir)


@app.get("/api/worktrees")
def worktrees(workspace_id: str) -> dict:
    try:
        workspace = _workspace(workspace_id)
        _, state_dir, entries = _managed_worktrees(workspace)
        return {
            "workspace_id": workspace.id,
            "repository": workspace.repository,
            "state_dir": str(state_dir),
            "busy": dispatch_lock.locked(),
            "summary": {
                "total": len(entries),
                "changed": sum(bool(item["changed_files"]) for item in entries),
                "protected": sum(item["locked"] for item in entries),
                "missing": sum(not item["exists"] for item in entries),
            },
            "worktrees": entries,
        }
    except (ProcessError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/worktrees/action")
def worktree_action(request: WorktreeActionRequest) -> dict:
    if request.action == "remove" and not request.confirmed:
        raise HTTPException(422, "Confirm worktree removal before continuing.")
    if not dispatch_lock.acquire(blocking=False):
        raise HTTPException(409, "A workflow is running. Manage worktrees after it finishes.")
    if not git_lock.acquire(blocking=False):
        dispatch_lock.release()
        raise HTTPException(409, "Another Git operation is running. Wait for it to finish.")
    try:
        workspace = _workspace(request.workspace_id)
        repository, _, entries = _managed_worktrees(workspace)
        target = Path(request.path).resolve()
        entry = next((item for item in entries if Path(item["path"]) == target), None)
        if entry is None:
            raise ProcessError("Worktree is not managed by this workspace. Refresh the list.")
        if request.action == "remove":
            if not entry["removable"]:
                raise ProcessError(
                    "Only clean, unlocked worktrees on a branch can be removed. "
                    "Preserve local files (including ignored files) and unlock before retrying."
                )
            removed, reason = repository.remove_worktree(target, preserve_ignored=True)
            if not removed:
                raise ProcessError(reason or "Git could not remove the worktree")
        else:
            repository.set_worktree_lock(target, locked=request.action == "protect")
        return {"action": request.action, "path": str(target)}
    except (ProcessError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        git_lock.release()
        dispatch_lock.release()


@app.middleware("http")
async def local_only(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"} or request.url.path.startswith(("/api/git/", "/api/worktrees", "/api/chat/history", "/api/commands")):
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.headers.get('host')}"
        if (origin and origin != expected) or request.headers.get(
            "x-switchyard-client"
        ) != "local-ui":
            return JSONResponse(
                {"detail": "Use the local Switchyard interface to dispatch work"}, status_code=403
            )
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    response.headers["Cache-Control"] = "no-store"
    return response


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str | None = None
    workspace_id: str | None = None
    request: str = Field(min_length=10)
    allow_host_execution: bool = False
    delivery: DeliveryConfig | None = None
    workflow_id: str | None = None


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(ASSETS / "index.html")


@app.get("/api/health")
def health() -> dict:
    cli_config = cli_store.get()
    if cli_config.codex.base_url:
        # Custom API providers do not require a ChatGPT subscription login.
        credential = cli_config.codex.api_key_env
        codex = {"ok": bool(
            cli_config.statuses()["codex"]["command_available"]
            and (not credential or os.environ.get(credential))
        )}
    else:
        codex = _command_status(
            [*cli_config.codex.command, "login", "status"], env=cli_config.runtime_env()
        )
    registry = combo_registry.refresh()
    return {
        "ready": codex["ok"]
        and not registry.get("error")
        and bool(registry["combos"])
        and _port_open(20128),
        "codex": codex["ok"],
        "opencode": cli_config.statuses()["opencode"]["command_available"],
        "router": _port_open(20128),
        **registry,
        "workflow_issues": _workflow_issues(registry["combos"]),
    }


@app.post("/api/9router/refresh")
def refresh_combos() -> dict:
    registry = combo_registry.refresh()
    return {**registry, "workflow_issues": _workflow_issues(registry["combos"])}


@app.get("/api/chat/tools")
def chat_tools() -> dict:
    statuses = cli_store.get().statuses()
    registry = combo_registry.refresh()
    return {
        "tools": [
            {
                "id": "codex",
                "name": "Codex subscription",
                "available": statuses["codex"].get("command_available", statuses["codex"]["available"]),
            },
            *[
                {
                    "id": f"9router/{combo}",
                    "name": f"9router / {combo}",
                    "available": statuses["opencode"].get("command_available", statuses["opencode"]["available"]),
                }
                for combo in registry["combos"]
            ],
        ],
        "warning": registry.get("error"),
    }


@app.get("/api/chat/history")
def list_chat_history(workspace_id: str) -> dict:
    repository = Path(_workspace(workspace_id).repository).resolve()
    entries, sources = chat_history.scan(repository)
    supported = {source["id"] for source in sources}
    for tool in cli_store.get().tools:
        if tool.id not in supported:
            sources.append({"id": tool.id, "name": tool.name, "count": 0,
                            "status": "unsupported",
                            "detail": "Automatic history is not connected. Use Import chats with a JSON export."})
    return {"repository": str(repository), "conversations": [entry.summary() for entry in entries],
            "sources": sources}


@app.get("/api/chat/history/{conversation_id}")
def get_chat_history(conversation_id: str, workspace_id: str) -> dict:
    repository = Path(_workspace(workspace_id).repository).resolve()
    entries, _ = chat_history.scan(repository)
    entry = next((entry for entry in entries if entry.id == conversation_id), None)
    if not entry:
        raise HTTPException(404, "Conversation not found in this workspace. Refresh history.")
    try:
        return chat_history.messages(entry)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        raise HTTPException(503, "Cannot read this conversation. Refresh after the tool finishes writing.") from exc


@app.post("/api/chat/history/import")
async def import_chat_history(request: Request) -> dict:
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_IMPORT_BYTES:
            raise HTTPException(413, "Choose a JSON export smaller than 8 MB.")
        content.extend(chunk)
    try:
        payload = ChatImportRequest.model_validate_json(content)
        source_name = payload.source_name.strip()
        if not source_name or any(ord(char) < 32 for char in source_name):
            raise ValueError("Enter the name of the tool that created this export.")
        repository = Path(_workspace(payload.workspace_id).repository).resolve()
        conversations = await asyncio.to_thread(parse_export, payload.export)
        if not payload.confirmed:
            return {"repository": str(repository), "conversations": [
                {"index": item["index"], "title": item["title"], "messages": len(item["messages"])}
                for item in conversations
            ]}
        if payload.repository != str(repository):
            raise ValueError("The workspace path changed. Review the export again before importing.")
        selected = set(payload.selected)
        if not selected or not selected.issubset({item["index"] for item in conversations}):
            raise ValueError("Select the conversations that belong to this workspace.")
        count = await asyncio.to_thread(
            chat_history.save_imports, repository, source_name,
            [item for item in conversations if item["index"] in selected],
        )
        return {"imported": count, "repository": str(repository), "selected": len(selected)}
    except ValidationError as exc:
        raise HTTPException(422, "Check the workspace, tool name, and JSON export, then retry.") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, "Cannot import this export. " + str(exc)) from exc
    except (KeyError, AttributeError, RecursionError) as exc:
        raise HTTPException(422, "This JSON structure is not supported. Use the example export format.") from exc
    except OSError as exc:
        raise HTTPException(503, "Cannot save imported chats. Check local storage and retry.") from exc


@app.post("/api/chat")
def chat(payload: ChatRequest) -> dict:
    repository = _git_repository(payload.repository, payload.workspace_id).expanduser().resolve()
    if not repository.is_dir():
        raise HTTPException(422, "Choose an existing workspace directory")
    if payload.tool != "codex":
        registry = combo_registry.refresh()
        if payload.tool not in {f"9router/{combo}" for combo in registry["combos"]}:
            raise HTTPException(
                422, "Choose an available AI tool. Refresh the tool list and retry."
            )
    if not chat_lock.acquire(blocking=False):
        raise HTTPException(409, "A chat reply is already in progress. Wait for it to finish.")
    try:
        config = cli_store.get()
        options = {"timeout": 180, "shared_env": config.runtime_env()}
        adapter = (
            CodexAdapter(cli=config.codex, **options)
            if payload.tool == "codex"
            else NineRouterReasoner(
                combo=payload.tool.removeprefix("9router/"), cli=config.opencode, **options
            )
        )
        reply = adapter.respond(payload.prompt(), cwd=repository)
        if not reply:
            raise HTTPException(
                502, "The tool returned no reply. Try again or choose another tool."
            )
        result = {"message": reply, "tool": payload.tool}
        if payload.session_id:
            try:
                chat_history.save_exchange(repository, payload.session_id, payload.messages[-1].content, reply, payload.tool)
            except (OSError, ValueError):
                result["warning"] = "Reply received, but local history could not be saved. Keep this page open."
        return result
    except (ProcessError, OSError) as exc:
        # Process errors can contain the full command/transcript; never echo them to the UI.
        raise HTTPException(
            503,
            "Unable to get a reply. Check the selected tool's login, "
            "CLI settings, and connection, then retry. Replies time out after "
            "three minutes.",
        ) from exc
    finally:
        chat_lock.release()


@app.get("/api/jobs")
def jobs(workspace_id: str | None = None) -> list[dict]:
    entries = store.list()
    if workspace_id:
        workspace = _workspace(workspace_id)
        entries = [
            entry
            for entry in entries
            if entry.get("workspace_id") == workspace.id
            or (not entry.get("workspace_id") and entry.get("repository") == workspace.repository)
        ]
    return entries[:30]


def _workspace(workspace_id: str) -> Workspace:
    try:
        return workspace_store.get().resolve(workspace_id)
    except KeyError as exc:
        raise HTTPException(422, "Choose an existing workspace") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _git_repository(repository: str | None, workspace_id: str | None) -> Path:
    if workspace_id:
        return Path(_workspace(workspace_id).repository)
    if not repository:
        raise HTTPException(422, "Choose a saved workspace")
    return Path(repository)


@app.get("/api/workspaces")
def get_workspaces() -> dict:
    try:
        return workspace_store.get().model_dump()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _workspace_repository(path: Path, base_branch: str) -> None:
    root = path.expanduser().resolve()
    if not root.is_dir():
        raise ProcessError("Choose an existing workspace folder.")

    try:
        result = run_process(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            timeout=30,
            env={"LC_ALL": "C"},
            check=False,
        )
    except OSError as exc:
        raise ProcessError("Unable to use Git. Check that Git is installed.") from exc

    if result.passed:
        repository_root = Path(result.stdout.strip()).resolve()
        if repository_root != root:
            run_process(
                ["git", "init", "--initial-branch", base_branch],
                cwd=root,
                timeout=30,
                check=True,
            )
        return

    # Do not replace a broken or inaccessible repository with a new one.
    if not result.stderr.startswith("fatal: not a git repository") or any(
        (folder / ".git").exists() or (folder / ".git").is_symlink()
        for folder in (root, *root.parents)
    ):
        detail = result.stderr.strip() or "Git could not read this repository."
        raise ProcessError(detail)

    try:
        run_process(
            ["git", "init", "--initial-branch", base_branch],
            cwd=root,
            timeout=30,
            check=True,
        )
    except OSError as exc:
        raise ProcessError("Unable to initialize Git. Check that Git is installed.") from exc


@app.put("/api/workspaces")
def save_workspaces(config: WorkspaceConfiguration) -> dict:
    try:
        previous = workspace_store.get()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    workflow_ids = {workflow.id for workflow in workflow_store.get_config().workflows}
    for old in previous.workspaces:
        replacement = next((item for item in config.workspaces if item.id == old.id), None)
        if (not replacement or replacement.repository != old.repository) and command_console.has_active_workspace(old.id):
            raise HTTPException(409, f"Stop commands in {old.name} before removing or changing its folder.")
    for workspace in config.workspaces:
        old = next((item for item in previous.workspaces if item.id == workspace.id), None)
        if (
            workspace.workflow_id
            and workspace.workflow_id not in workflow_ids
            and (not old or old.workflow_id != workspace.workflow_id)
        ):
            raise HTTPException(
                status_code=422,
                detail=f"Workspace {workspace.name} uses a workflow that no longer exists",
            )
    for workspace in config.workspaces:
        old = next((item for item in previous.workspaces if item.id == workspace.id), None)
        try:
            if not old or old.repository != workspace.repository:
                _workspace_repository(Path(workspace.repository), workspace.git_base_branch)
        except ProcessError as exc:
            raise HTTPException(
                status_code=422, detail=f"Workspace {workspace.name}: {exc}"
            ) from exc
    return workspace_store.save(config).model_dump()


@app.get("/api/workflow")
def get_workflow() -> dict:
    return workflow_store.get().model_dump()


@app.put("/api/workflow")
def save_workflow(recipe: WorkflowRecipe) -> dict:
    available = set(health()["combos"])
    missing = {step.combo for step in recipe.steps if step.combo and step.combo not in available}
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"These 9router combos are unavailable: {', '.join(sorted(missing))}",
        )
    try:
        return workflow_store.save(recipe).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/workflow/reset")
def reset_workflow() -> dict:
    try:
        return workflow_store.reset().model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/steps")
def get_steps() -> dict:
    return {"steps": [step.model_dump() for step in workflow_store.get_config().steps]}


@app.put("/api/steps")
def save_steps(catalog: StepCatalog) -> dict:
    _validate_step_combos(catalog.steps)
    try:
        saved = workflow_store.save_steps(catalog)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"steps": [step.model_dump() for step in saved.steps]}


@app.get("/api/workflows")
def get_workflows() -> dict:
    config = workflow_store.get_config()
    return {
        "workflows": [workflow.model_dump() for workflow in config.workflows],
        "default_workflow_id": config.default_workflow_id,
        "steps": [step.model_dump() for step in config.steps],
    }


@app.put("/api/workflows")
def save_workflows(collection: WorkflowCollection) -> dict:
    from .workflow_config import WorkflowConfiguration

    try:
        candidate = WorkflowConfiguration(
            steps=workflow_store.get_config().steps,
            workflows=collection.workflows,
            default_workflow_id=collection.default_workflow_id,
        )
        _validate_step_combos(
            [
                step
                for workflow in candidate.workflows
                for step in candidate.resolve(workflow.id).steps
            ]
        )
        saved = workflow_store.save_workflows(collection)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "workflows": [workflow.model_dump() for workflow in saved.workflows],
        "default_workflow_id": saved.default_workflow_id,
        "steps": [step.model_dump() for step in saved.steps],
    }


@app.get("/api/mcps")
def get_mcps() -> dict:
    return mcp_store.get().model_dump()


@app.get("/api/mcps/catalog")
def get_mcp_catalog() -> dict:
    return {"servers": [item.model_dump() for item in mcp_catalog()]}


@app.put("/api/mcps")
def save_mcps(config: McpConfiguration) -> dict:
    return mcp_store.save(config).model_dump()


@app.get("/api/mcps/export/{tool}")
def export_mcps(tool: McpTool) -> dict:
    return mcp_store.get().export_for(tool)


@app.get("/api/clis")
def get_clis() -> dict:
    config = cli_store.get()
    return {**config.model_dump(), "statuses": config.statuses()}


@app.put("/api/clis")
def save_clis(config: CliConfiguration) -> dict:
    saved = cli_store.save(config)
    return {**saved.model_dump(), "statuses": saved.statuses()}


@app.post("/api/clis/check")
def check_clis(config: CliConfiguration) -> dict:
    return {"statuses": config.statuses()}


@app.post("/api/clis/setup/{tool_id}")
def cli_setup(tool_id: str, profile: CliProfile) -> dict:
    try:
        return setup_for(tool_id, profile)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/delivery")
def get_delivery() -> dict:
    return delivery_store.get().model_dump()


@app.put("/api/delivery")
def save_delivery(config: DeliveryConfig) -> dict:
    return delivery_store.save(config).model_dump()


@app.get("/api/jobs/{job_id}")
def job(job_id: str) -> dict:
    found = store.get(job_id)
    if not found:
        raise HTTPException(status_code=404, detail="Run not found")
    return found


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    job_store = store
    if not await asyncio.to_thread(job_store.get, job_id):
        raise HTTPException(status_code=404, detail="Run not found")

    async def stream():
        previous = None
        idle_ticks = 0
        while not await request.is_disconnected():
            found = await asyncio.to_thread(job_store.get, job_id)
            if not found:
                yield "event: unavailable\ndata: {}\n\n"
                return
            snapshot = json.dumps(found, separators=(",", ":"))
            if snapshot != previous:
                yield f"event: job\ndata: {snapshot}\n\n"
                previous = snapshot
                idle_ticks = 0
                if found.get("status") not in {"queued", "running"}:
                    return
            idle_ticks += 1
            if idle_ticks >= 30:
                yield ": keep-alive\n\n"
                idle_ticks = 0
            await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/jobs", status_code=202)
def create_job(payload: JobRequest) -> dict:
    workspace: Workspace | None = None
    if payload.workspace_id:
        workspace = _workspace(payload.workspace_id)
    repository_value = workspace.repository if workspace else payload.repository
    if not repository_value:
        raise HTTPException(status_code=422, detail="Choose a workspace or repository")
    repository = Path(repository_value).expanduser().resolve()
    if not repository.is_dir():
        raise HTTPException(status_code=422, detail="Choose an existing repository directory")
    if not payload.allow_host_execution:
        raise HTTPException(
            status_code=422,
            detail="Confirm host execution before running implementation agents",
        )
    try:
        # An explicit null/empty choice bypasses saved workflows; omitted fields
        # retain workspace/default inheritance for existing API clients.
        without_workflow = "workflow_id" in payload.model_fields_set and not payload.workflow_id
        if (
            "workflow_id" not in payload.model_fields_set
            and workspace
            and workspace.workflow_id is None
        ):
            without_workflow = True
        if without_workflow:
            workflow_id = None
            workflow = default_recipe()
            workflow.name = "Built-in engineering route (no saved workflow)"
        else:
            config = workflow_store.get_config()
            workflow_id = (
                payload.workflow_id
                or (workspace.workflow_id if workspace else None)
                or config.default_workflow_id
            )
            workflow = config.resolve(workflow_id)
        workflow.require_executable()
    except KeyError as exc:
        raise HTTPException(status_code=422, detail="Choose an existing workflow") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    delivery = payload.delivery or (workspace.delivery if workspace else delivery_store.get())
    created = store.create(
        repository=str(repository),
        request=payload.request.strip(),
        workflow=workflow.model_dump(),
    )
    created["mcp_config"] = mcp_store.get().model_dump()
    store.update(created["id"], mcp_config=created["mcp_config"])
    created["cli_config"] = cli_store.get().model_dump()
    store.update(created["id"], cli_config=created["cli_config"])
    created["delivery"] = delivery.model_dump()
    store.update(created["id"], delivery=created["delivery"])
    created["workflow_id"] = workflow_id
    created["workflow_source"] = "saved" if workflow_id else "built_in"
    store.update(
        created["id"],
        workflow_id=created["workflow_id"],
        workflow_source=created["workflow_source"],
    )
    created["workspace_id"] = workspace.id if workspace else None
    created["workspace"] = workspace.model_dump() if workspace else None
    created["run_settings"] = workspace.run_settings() if workspace else Settings().model_dump()
    store.update(
        created["id"],
        workspace_id=created["workspace_id"],
        workspace=created["workspace"],
        run_settings=created["run_settings"],
    )
    thread = threading.Thread(
        target=_work,
        args=(created["id"], payload, repository, created["run_settings"]),
        daemon=True,
        name=f"orchestrator-{created['id']}",
    )
    thread.start()
    return created


def _work(
    job_id: str, payload: JobRequest, repository: Path, run_settings: dict | None = None
) -> None:
    # Keep repository mutations serialized, but let callers queue work instead of rejecting it.
    dispatch_lock.acquire()
    try:
        store.update(job_id, status="running", stage="Preparing Git and checking providers")
        run_settings = run_settings or {}
        settings = Settings(
            allow_host_execution=payload.allow_host_execution,
            forks_per_ticket=run_settings.get("forks_per_ticket", 3),
            max_repair_rounds=run_settings.get("max_repair_rounds", 2),
            command_timeout_seconds=run_settings.get("command_timeout_seconds", 1800),
        )
        workflow = WorkflowRecipe.model_validate(store.get(job_id)["workflow"])
        mcp_config = McpConfiguration.model_validate(store.get(job_id).get("mcp_config", {}))
        cli_config = CliConfiguration.model_validate(store.get(job_id).get("cli_config", {}))
        def progress(stage: str, result: dict | None) -> None:
            changes = {"stage": stage}
            if result is not None:
                changes["result"] = result
            store.update(job_id, **changes)

        orchestrator = EngineeringOrchestrator(
            repository, settings, workflow, mcp_config, cli_config, progress
        )
        store.update(job_id, stage="Running isolated candidates through 9router")
        result = orchestrator.run(payload.request)
        status = result.get("status", "completed")
        if status == "approved":
            store.update(job_id, stage="Applying delivery settings")
            delivery = DeliveryConfig.model_validate(store.get(job_id).get("delivery", {}))
            try:
                result["delivery"] = _deliver(
                    orchestrator, result, delivery, payload.request, cli_config
                )
            except Exception as exc:  # noqa: BLE001 - keep the local branch recoverable.
                result["delivery"] = {
                    **result.get("delivery", {}),
                    "status": "failed",
                    "error": str(exc),
                    "local_branch": result.get("integration_branch"),
                }
                status = "delivery_failed"
            orchestrator.save_run(result)
        else:
            result["delivery"] = {"status": "skipped", "reason": "Final review not approved"}
            orchestrator.save_run(result)
        store.update(
            job_id, status=status, stage="Stopped" if status == "failed" else "Finished",
            result=result, error=result.get("error"),
        )
    except Exception as exc:  # noqa: BLE001 - background failures must be persisted.
        store.update(job_id, status="failed", stage="Stopped", error=str(exc))
    finally:
        dispatch_lock.release()


def _deliver(
    orchestrator: EngineeringOrchestrator,
    result: dict,
    config: DeliveryConfig,
    request: str,
    cli_config: CliConfiguration,
) -> dict:
    local_branch = result.get("integration_branch")
    if result.get("status") != "approved":
        return {"status": "skipped", "reason": "Final review not approved"}
    if config.mode == "none":
        return {"status": "local_only", "local_branch": local_branch}
    if not local_branch:
        raise RuntimeError("The completed run has no integration branch to deliver")
    target = config.target_branch(
        run_id=result["run_id"], repository=orchestrator.repository.root.name
    )
    pr_repository = None
    if config.mode == "pr":
        if target == config.base_branch:
            raise RuntimeError("PR head and base branches must be different")
        url = orchestrator.repository.git(
            "remote", "get-url", "--push", config.remote
        ).stdout.strip()
        match = re.fullmatch(
            r"(?:https://|ssh://git@|git@)([^/:]+)[:/]([^/]+/[^/]+?)(?:\.git)?", url
        )
        if not match:
            raise RuntimeError("PR delivery requires a GitHub HTTPS or SSH remote")
        pr_repository = f"{match[1]}/{match[2]}"
    orchestrator.repository.push_branch(local_branch, target, config.remote)
    delivered = {
        "status": "pushed",
        "remote": config.remote,
        "branch": target,
        "local_branch": local_branch,
        "pushed": True,
    }
    result["delivery"] = delivered
    orchestrator.save_run(result)
    if config.mode != "pr":
        return delivered
    title = request.strip().splitlines()[0][:120]
    command = [
        *cli_config.command_for("gh", ["gh"]),
        "pr",
        "create",
        "--repo",
        pr_repository,
        "--head",
        target,
        "--base",
        config.base_branch,
        "--title",
        title,
        "--body",
        f"Automated by 9router Switchyard.\n\nRun: {result['run_id']}\nReview status: {result['status']}",
    ]
    if config.draft:
        command.append("--draft")
    pr = run_process(
        command,
        cwd=orchestrator.repository.root,
        timeout=orchestrator.settings.command_timeout_seconds,
        env=cli_config.runtime_env(),
        check=True,
    )
    delivered.update(status="pr_opened", url=pr.stdout.strip().splitlines()[-1])
    return delivered


def _command_status(
    command: list[str], timeout: int = 20, env: dict[str, str] | None = None
) -> dict:
    if find_tool(command[0], (env or {}).get("PATH")) is None:
        return {"ok": False, "stdout": ""}
    try:
        result = run_process(command, cwd=Path.cwd(), timeout=timeout, env=env)
        return {"ok": result.passed, "stdout": result.stdout}
    except Exception:  # noqa: BLE001 - health checks return false instead of raising.
        return {"ok": False, "stdout": ""}


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _workflow_issues(combos: list[str]) -> list[dict]:
    available = set(combos)
    config = workflow_store.get_config()
    return [
        {"step": f"{workflow.name}: {step.name}", "combo": step.combo}
        for workflow in config.workflows
        for step in config.resolve(workflow.id).steps
        if step.combo and step.combo not in available
    ]


def _validate_step_combos(steps: list) -> None:
    available = set(health()["combos"])
    missing = {step.combo for step in steps if step.combo and step.combo not in available}
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"These 9router combos are unavailable: {', '.join(sorted(missing))}",
        )


def main() -> None:
    uvicorn.run("ninerouter_orchestrator.web:app", host="127.0.0.1", port=8765)
