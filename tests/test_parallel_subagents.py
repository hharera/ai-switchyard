import json
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from ninerouter_orchestrator import cli
from ninerouter_orchestrator.config import Settings
from ninerouter_orchestrator.models import CandidateDecision, Plan, ReviewReport, Ticket
from ninerouter_orchestrator.orchestrator import EngineeringOrchestrator
from ninerouter_orchestrator.workflow_config import default_recipe


def ticket(identifier, dependencies=()):
    return Ticket(
        id=identifier, title=identifier, description=f"Implement {identifier}",
        acceptance_criteria=["File created"], dependencies=list(dependencies),
        validation_commands=["git diff --check"],
    )


def runner(monkeypatch, tmp_path, tickets, *, limit=2, isolated=True):
    root = tmp_path / "repo"
    root.mkdir()
    workflow = default_recipe()
    workflow.isolated_worktree = isolated
    orchestrator = EngineeringOrchestrator(
        root, Settings(allow_host_execution=True, forks_per_ticket=1,
                       max_parallel_tickets=limit), workflow,
    )
    orchestrator.repository.prepare_dispatch()
    monkeypatch.setattr(orchestrator, "preflight", lambda **kwargs: None)
    monkeypatch.setattr(orchestrator, "plan", lambda _: Plan(objective="Test", tickets=tickets))
    monkeypatch.setattr(orchestrator, "select_candidate", lambda *args: CandidateDecision(
        selected_fork=1, rationale="Validated",
    ))
    monkeypatch.setattr(orchestrator, "review", lambda *args: ReviewReport(
        approved=True, summary="Approved",
    ))
    return orchestrator


@pytest.mark.parametrize("limit", [1, 2])
@pytest.mark.parametrize("isolated", [True, False])
def test_bounded_batches_integrate_before_dependents(monkeypatch, tmp_path, limit, isolated):
    tickets = [ticket("E", ["D"]), ticket("D", ["A", "B"]),
               ticket("C"), ticket("B"), ticket("A")]
    orchestrator = runner(monkeypatch, tmp_path, tickets, limit=limit, isolated=isolated)
    repo = orchestrator.repository
    base = repo.head()
    barrier = threading.Barrier(limit)
    heads = {}
    stages = []
    orchestrator.progress = lambda stage, run: stages.append(stage)

    def execute(request):
        identifier = request.ticket.id
        heads[identifier] = repo.git("rev-parse", "HEAD", cwd=request.workspace).stdout.strip()
        for dependency in request.ticket.dependencies:
            assert (request.workspace / f"{dependency}.txt").read_text() == dependency
            assert f"Integrated {dependency}" in stages
        if identifier in {"A", "B"}:
            barrier.wait(timeout=5)
        (request.workspace / f"{identifier}.txt").write_text(identifier)
        return f"Implemented {identifier}"

    monkeypatch.setattr(orchestrator.executor, "execute", execute)
    result = orchestrator._run_engineering("Test")

    assert result["status"] == "approved", result.get("error")
    assert [entry["ticket"] for entry in result["tickets"]] == ["A", "B", "C", "D", "E"]
    assert all(entry["status"] == "integrated" for entry in result["tickets"])
    assert (heads["A"] == heads["B"]) is (limit == 2)
    assert heads["C"] != heads["B"]
    for identifier in "ABCDE":
        assert repo.git("show", f"{result['integration_branch']}:{identifier}.txt").stdout == identifier
    assert (repo.head() == base) is isolated
    manifest = orchestrator.state_dir / "runs" / f"{result['run_id']}.json"
    assert json.loads(manifest.read_text()) == result


@pytest.mark.parametrize("failure", ["execute", "select"])
def test_failed_subagent_preserves_peer_results_and_stops_dependents(
    monkeypatch, tmp_path, failure
):
    orchestrator = runner(monkeypatch, tmp_path, [ticket("A"), ticket("B"), ticket("C", ["A"])])
    barrier = threading.Barrier(2)
    started = []

    def execute(request):
        started.append(request.ticket.id)
        barrier.wait(timeout=5)
        if failure == "execute" and request.ticket.id == "A":
            raise RuntimeError("Execution unavailable")
        (request.workspace / f"{request.ticket.id}.txt").write_text("done")
        return "Implemented"

    select = orchestrator.select_candidate

    def select_candidate(ticket, results):
        if ticket.id == "A":
            if failure == "execute":
                assert results[0].status == "failed"
                assert results[0].error == "Execution unavailable"
            raise RuntimeError("No candidate selected")
        return select(ticket, results)

    monkeypatch.setattr(orchestrator.executor, "execute", execute)
    monkeypatch.setattr(orchestrator, "select_candidate", select_candidate)
    monkeypatch.setattr(orchestrator, "review", lambda *args: pytest.fail("Review must not run"))
    result = orchestrator._run_engineering("Test failure")

    assert result["status"] == "failed"
    assert "A: No candidate selected" in result["error"]
    assert sorted(started) == ["A", "B"]
    assert [entry["status"] for entry in result["tickets"]] == ["failed", "ready"]
    assert all(entry["candidates"] for entry in result["tickets"])
    assert all(Path(entry["candidates"][0]["request"]["workspace"]).exists()
               for entry in result["tickets"])


def test_parallel_merge_conflict_stops_before_dependents(monkeypatch, tmp_path):
    orchestrator = runner(monkeypatch, tmp_path, [ticket("A"), ticket("B"), ticket("C", ["A"])])
    started = []

    def execute(request):
        started.append(request.ticket.id)
        (request.workspace / "shared.txt").write_text(request.ticket.id)
        return "Implemented"

    monkeypatch.setattr(orchestrator.executor, "execute", execute)
    monkeypatch.setattr(orchestrator, "review", lambda *args: pytest.fail("Review must not run"))
    result = orchestrator._run_engineering("Conflicting changes")

    assert result["status"] == "failed"
    assert sorted(started) == ["A", "B"]
    assert result["tickets"][0]["status"] == "integrated"
    assert orchestrator.repository.git(
        "diff", "--name-only", "--diff-filter=U", cwd=Path(result["workspace"])
    ).stdout.strip() == "shared.txt"


@pytest.mark.parametrize("value", [0, 9])
def test_parallel_limit_is_validated(value):
    with pytest.raises(ValidationError):
        Settings(max_parallel_tickets=value)


@pytest.mark.parametrize("arguments, expected", [([], 2), (["--parallel-tickets", "4"], 4)])
def test_cli_parallel_limit_honors_environment_and_explicit_override(
    monkeypatch, tmp_path, arguments, expected
):
    observed = {}

    class Orchestrator:
        def __init__(self, repository, settings, **kwargs):
            observed["settings"] = settings

        def run(self, request):
            return {"status": "approved"}

    monkeypatch.setenv("ORCH_MAX_PARALLEL_TICKETS", "2")
    monkeypatch.setattr(cli, "EngineeringOrchestrator", Orchestrator)
    monkeypatch.setattr(cli.McpConfigStore, "get", lambda self: None)
    result = CliRunner().invoke(cli.app, [
        "run", "Implement test", "--repo", str(tmp_path), "--allow-host-execution", *arguments,
    ])

    assert result.exit_code == 0, result.output
    assert observed["settings"].max_parallel_tickets == expected


def test_parallel_setting_has_labeled_help_and_roundtrip_wiring():
    assets = Path("src/ninerouter_orchestrator/web_assets")
    page = (assets / "index.html").read_text()
    script = (assets / "workspaces.js").read_text()
    assert '<label for="workspace-parallel-tickets">Parallel tickets</label>' in page
    assert 'aria-describedby="workspace-parallel-tickets-help"' in page
    assert 'field("parallel-tickets").value = workspace?.max_parallel_tickets || 1' in script
    assert 'max_parallel_tickets: Number(field("parallel-tickets").value)' in script
