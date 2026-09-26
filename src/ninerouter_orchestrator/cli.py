from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated

import typer

from .adapters.nine_router import NineRouterExecutor
from .config import Settings
from .graph import build_workflow
from .mcp_catalog import mcp_catalog
from .mcp_config import McpConfigStore
from .orchestrator import EngineeringOrchestrator
from .web import main as start_ui

app = typer.Typer(
    no_args_is_help=True, invoke_without_command=True,
    help="Codex-led orchestration through stable 9router combos."
)
mcp_app = typer.Typer(help="Inspect and export shared MCP connections.", no_args_is_help=True)
app.add_typer(mcp_app, name="mcp")


def installed_version() -> str:
    try:
        return version("switchyard")
    except PackageNotFoundError:
        return "development"


@app.callback()
def root(
    show_version: Annotated[
        bool, typer.Option("--version", help="Show the installed Switchyard version and exit.", is_eager=True)
    ] = False,
) -> None:
    if show_version:
        typer.echo(installed_version())
        raise typer.Exit()


@mcp_app.command("catalog")
def list_mcps(search: Annotated[str, typer.Option(help="Filter catalog entries by name.")] = ""):
    """List catalog identities; connection details must come from each official source."""
    typer.echo(json.dumps([
        item.model_dump() for item in mcp_catalog() if search.lower() in item.name.lower()
    ], indent=2))


@mcp_app.command("export")
def export_mcps(tool: Annotated[str, typer.Argument(help="codex, opencode, claude-code, or cursor")]):
    """Print a native configuration fragment without exposing environment values."""
    try:
        exported = McpConfigStore().get().export_for(tool)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="tool") from exc
    typer.echo(f"Merge into {exported['destination']}", err=True)
    for warning in exported["warnings"]:
        typer.echo(warning, err=True)
    typer.echo(exported["content"])


@app.command()
def doctor(
    repository: Annotated[Path, typer.Option("--repo", exists=True, file_okay=False)] = Path("."),
) -> None:
    """Verify Git, Codex subscription auth, OpenCode, and configured combos."""
    settings = Settings()
    orchestrator = EngineeringOrchestrator(
        repository.resolve(), settings, mcp_config=McpConfigStore().get()
    )
    orchestrator.preflight()
    typer.echo(f"Ready: Codex + OpenCode + {len(settings.combos)} 9router combo(s)")
    for combo in settings.combos:
        typer.echo(f"- {NineRouterExecutor.model_id(combo)}")


@app.command()
def plan(
    request: Annotated[str, typer.Argument(help="Engineering request to decompose.")],
    repository: Annotated[Path, typer.Option("--repo", exists=True, file_okay=False)] = Path("."),
) -> None:
    """Create and persist a structured plan without executing tickets."""
    orchestrator = EngineeringOrchestrator(
        repository.resolve(), Settings(), mcp_config=McpConfigStore().get()
    )
    run = orchestrator.create_run(request)
    typer.echo(json.dumps(run, indent=2))


@app.command("graph")
def graph_info() -> None:
    """Validate that the LangGraph state machine compiles."""
    graph = build_workflow()
    typer.echo(str(graph.get_graph().draw_mermaid()))


@app.command()
def ui() -> None:
    """Start the local Switchyard web interface."""
    start_ui()


@app.command()
def run(
    request: str,
    repository: Annotated[Path, typer.Option("--repo", exists=True, file_okay=False)],
    parallel_tickets: Annotated[
        int | None,
        typer.Option(
            "--parallel-tickets",
            min=1,
            max=8,
            help="Maximum independent ticket subagents to run at once (default: 1).",
        ),
    ] = None,
    allow_host_execution: Annotated[
        bool,
        typer.Option(
            help="Allow agents and tests to execute on the host; worktrees are NOT security sandboxes."
        ),
    ] = False,
) -> None:
    """Execute a trusted task on a separate integration branch; never push or merge to main."""
    overrides = {"max_parallel_tickets": parallel_tickets} if parallel_tickets is not None else {}
    orchestrator = EngineeringOrchestrator(
        repository, Settings(allow_host_execution=allow_host_execution, **overrides),
        mcp_config=McpConfigStore().get(),
    )
    result = orchestrator.run(request)
    typer.echo(json.dumps(result, indent=2))
    if result["status"] not in {"completed", "approved"}:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
