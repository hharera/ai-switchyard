import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ninerouter_orchestrator.adapters.nine_router import NineRouterExecutor
from ninerouter_orchestrator.config import Settings
from ninerouter_orchestrator.orchestrator import EngineeringOrchestrator
from ninerouter_orchestrator.process import ProcessError
from ninerouter_orchestrator.workflow_config import WorkflowRecipe, WorkflowStep


def step(identifier, kind="custom", engine="codex"):
    return WorkflowStep(id=identifier, name=identifier, kind=kind, engine=engine,
                        system_prompt=f"Instructions for {identifier}.")


def runner(monkeypatch, tmp_path, steps, *, isolated=True):
    root = tmp_path / "repo"
    root.mkdir()
    instance = EngineeringOrchestrator(
        root, Settings(allow_host_execution=True),
        WorkflowRecipe(name="Dynamic", isolated_worktree=isolated, steps=steps),
    )
    instance.repository.prepare_dispatch()
    monkeypatch.setattr(instance, "preflight", lambda **kwargs: None)
    for method in ("plan", "execute_ticket", "select_candidate", "review"):
        monkeypatch.setattr(instance, method, lambda *args: pytest.fail("No hidden phase"))
    return instance


@pytest.mark.parametrize("isolated", [True, False])
def test_saved_order_repeated_categories_and_tool_routing(monkeypatch, tmp_path, isolated):
    repeated = step("repeat", "validate", "9router/Kimi")
    steps = [step("first", "merge"), repeated, step("third", "anything"), repeated]
    instance = runner(monkeypatch, tmp_path, steps, isolated=isolated)
    calls = []
    progress = []
    instance.progress = lambda stage, run: progress.append((stage, copy.deepcopy(run)))

    def execute(prompt, *, cwd, combo=None):
        index = len(calls)
        assert f"Instructions for {steps[index].id}." in prompt
        assert "Do the requested work" in prompt
        if index:
            assert f"output-{index - 1}" in prompt
            assert (cwd / "artifact.txt").read_text() == str(index - 1)
        assert (cwd == instance.repository.root) is (not isolated)
        (cwd / "artifact.txt").write_text(str(index))
        calls.append((steps[index].id, combo))
        return f"output-{index}"

    monkeypatch.setattr(instance.planner, "execute", execute)
    monkeypatch.setattr(instance.executor, "run", execute)
    result = instance.run("Do the requested work")

    assert result["status"] == "completed", result.get("error")
    assert calls == [("first", None), ("repeat", "Kimi"), ("third", None), ("repeat", "Kimi")]
    assert [entry["index"] for entry in result["steps"]] == [0, 1, 2, 3]
    assert all(entry["status"] == "completed" for entry in result["steps"])
    assert instance.repository.git("show", f"{result['integration_branch']}:artifact.txt").stdout == "3"
    assert result["changes"]["file_count"] == 1
    assert result["changes"]["additions"] == 1
    assert result["changes"]["files"][0]["path"] == "artifact.txt"
    assert result["changes"]["files"][0]["status"] == "A"
    assert json.loads((instance.state_dir / "runs" / f"{result['run_id']}.json").read_text()) == result
    assert progress[-1][0] == "Workflow finished"
    assert "plan" not in result and "review" not in result


def test_empty_workflow_is_no_op_without_providers_or_git(monkeypatch, tmp_path):
    instance = EngineeringOrchestrator(tmp_path, Settings(allow_host_execution=True),
                                      WorkflowRecipe(steps=[]))
    monkeypatch.setattr(instance.repository, "prepare_dispatch", lambda: pytest.fail("No Git"))
    monkeypatch.setattr(instance, "preflight", lambda **kwargs: pytest.fail("No providers"))
    result = instance.run("No steps to run")
    assert result["status"] == "completed"
    assert result["no_op"] is True
    assert result["steps"] == []
    assert not (tmp_path / ".git").exists()


def test_tool_failure_preserves_output_and_worktree_and_stops_later_steps(monkeypatch, tmp_path):
    instance = runner(monkeypatch, tmp_path, [step("a"), step("b"), step("c")])
    calls = []

    def execute(prompt, *, cwd):
        calls.append(prompt)
        (cwd / "partial.txt").write_text("keep this work")
        if len(calls) == 2:
            raise RuntimeError("Tool unavailable")
        return "First step complete"

    monkeypatch.setattr(instance.planner, "execute", execute)
    result = instance.run("Test failure handling")
    assert result["status"] == "failed"
    assert result["error"] == "Tool unavailable"
    assert len(calls) == 2
    assert [entry["status"] for entry in result["steps"]] == ["completed", "failed"]
    assert result["steps"][0]["output"] == "First step complete"
    assert result["steps"][1]["error"] == "Tool unavailable"
    assert result["changes"]["files"][0]["path"] == "partial.txt"
    assert (Path(result["workspace"]) / "partial.txt").read_text() == "keep this work"


def test_dynamic_route_preserves_dirty_original_checkout(monkeypatch, tmp_path):
    instance = runner(monkeypatch, tmp_path, [step("only")])
    root = instance.repository.root
    (root / "tracked.txt").write_text("base")
    instance.repository.commit_all(root, "Base")
    (root / "tracked.txt").write_text("staged")
    instance.repository.git("add", "tracked.txt")
    (root / "tracked.txt").write_text("unstaged")
    (root / "untracked.txt").write_text("user file")
    before = instance.repository.git("status", "--porcelain").stdout

    def execute(prompt, *, cwd):
        assert (cwd / "tracked.txt").read_text() == "base"
        assert not (cwd / "untracked.txt").exists()
        (cwd / "tracked.txt").write_text("agent change")
        return "Updated"

    monkeypatch.setattr(instance.planner, "execute", execute)
    result = instance.run("Apply a change")
    assert result["status"] == "completed"
    assert instance.repository.git("status", "--porcelain").stdout == before
    assert (root / "tracked.txt").read_text() == "unstaged"
    assert (root / "untracked.txt").read_text() == "user file"


def test_followup_run_starts_from_previous_run_commit(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    instance = EngineeringOrchestrator(
        root, Settings(allow_host_execution=True), WorkflowRecipe(steps=[step("only")]),
    )
    instance.repository.git("init", "--initial-branch", "main")
    monkeypatch.setattr(instance, "preflight", lambda **kwargs: None)
    calls = []

    def execute(prompt, *, cwd):
        calls.append(prompt)
        artifact = cwd / "artifact.txt"
        if len(calls) == 1:
            assert not artifact.exists()
            artifact.write_text("first")
        else:
            assert artifact.read_text() == "first"
            artifact.write_text("follow-up")
        return f"response-{len(calls)}"

    monkeypatch.setattr(
        instance, "_execute_workflow_step",
        lambda step, prompt, *, cwd, index: execute(prompt, cwd=cwd),
    )
    first = instance.run("Create the artifact")
    followup = instance.run("Update the artifact", start_point=first["commit"])

    assert followup["status"] == "completed"
    assert followup["base"] == first["commit"]
    assert followup["continued_from"] == first["commit"]
    assert instance.repository.git(
        "show", f"{followup['integration_branch']}:artifact.txt"
    ).stdout == "follow-up"


def test_dynamic_route_rejects_dirty_direct_checkout_before_tools(monkeypatch, tmp_path):
    instance = runner(monkeypatch, tmp_path, [step("only")], isolated=False)
    (instance.repository.root / "local.txt").write_text("keep")
    monkeypatch.setattr(instance.planner, "execute", lambda *args, **kwargs: pytest.fail("No tool"))
    result = instance.run("Apply a change")
    assert result["status"] == "failed"
    assert "local changes" in result["error"]


def test_auto_tools_rotate_without_changing_step_order(monkeypatch, tmp_path):
    instance = runner(monkeypatch, tmp_path, [step(str(i), engine="9router/auto") for i in range(4)])
    instance.settings.execution_combos = "One,Two"
    calls = []

    def execute(prompt, *, cwd, combo):
        calls.append(combo)
        return combo

    monkeypatch.setattr(instance.executor, "run", execute)
    result = instance.run("Route through configured tools")
    assert result["status"] == "completed"
    assert calls == ["One", "Two", "One", "Two"]


def test_opencode_step_uses_configured_tool_and_returns_text(monkeypatch, tmp_path):
    captured = {}

    def run_process(command, **kwargs):
        captured.update(command=command, **kwargs)
        return SimpleNamespace(passed=True, stdout='{"type":"text","part":{"type":"text","text":"Done"}}')

    monkeypatch.setattr("ninerouter_orchestrator.adapters.nine_router.run_process", run_process)
    assert NineRouterExecutor().run("Custom instructions", cwd=tmp_path, combo="Kimi") == "Done"
    assert captured["command"] == [
        "opencode", "run", "--auto", "--format", "json", "--model", "9router/Kimi",
        "--dir", str(tmp_path), "Custom instructions",
    ]
    assert captured["cwd"] == tmp_path


def test_opencode_error_event_fails_even_with_zero_exit_code(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "ninerouter_orchestrator.adapters.nine_router.run_process",
        lambda *args, **kwargs: SimpleNamespace(passed=True, stdout='{"type":"error"}'),
    )
    with pytest.raises(ProcessError, match="reported an error"):
        NineRouterExecutor().run("Custom instructions", cwd=tmp_path, combo="Kimi")
