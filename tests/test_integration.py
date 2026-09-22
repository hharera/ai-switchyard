import copy

import pytest

from ninerouter_orchestrator.adapters.codex import strict_schema
from ninerouter_orchestrator.config import Settings
from ninerouter_orchestrator.git import GitRepository
from ninerouter_orchestrator.graph import build_workflow
from ninerouter_orchestrator.models import CandidateDecision, Plan, ReviewReport, Ticket
from ninerouter_orchestrator.orchestrator import EngineeringOrchestrator
from ninerouter_orchestrator.process import ProcessError, run_process


def test_strict_schema():
    schema = strict_schema(Plan.model_json_schema())
    for node in [schema, schema["$defs"]["Ticket"]]:
        assert node["additionalProperties"] is False
        assert set(node["required"]) == set(node["properties"])


def test_execution_needs_opt_in(tmp_path):
    with pytest.raises(RuntimeError, match="allow-host-execution"):
        EngineeringOrchestrator(tmp_path, Settings()).run("test")
    assert not (tmp_path / ".git").exists()


@pytest.mark.parametrize("plan_only", [True, False])
def test_preflight_checks_dependencies_without_cleanliness(monkeypatch, tmp_path, plan_only):
    orchestrator = EngineeringOrchestrator(tmp_path, Settings())
    calls = []
    combos = []
    monkeypatch.setattr(orchestrator.repository, "validate", lambda: calls.append("validate"))
    monkeypatch.setattr(orchestrator.repository, "git", lambda *args: calls.append(args))
    monkeypatch.setattr(
        orchestrator.executor, "verify_combo", lambda combo, **kwargs: combos.append(combo)
    )
    monkeypatch.setattr(
        "ninerouter_orchestrator.orchestrator.run_process", lambda *args, **kwargs: None
    )
    orchestrator.preflight(plan_only=plan_only)
    assert calls == ["validate", ("rev-parse", "HEAD")]
    assert combos == ([] if plan_only else sorted(orchestrator.settings.combos))


def test_run_preserves_dirty_checkout(monkeypatch, tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    run_process(["git", "init"], cwd=repo_path, timeout=30, check=True)
    progress = []
    orchestrator = EngineeringOrchestrator(
        repo_path, Settings(allow_host_execution=True, forks_per_ticket=1),
        progress=lambda stage, result: progress.append((stage, copy.deepcopy(result))),
    )
    repo = orchestrator.repository
    sample = repo_path / "sample.txt"
    sample.write_text("base")
    repo.commit_all(repo_path, "base")
    base = repo.head()
    sample.write_text("staged")
    repo.git("add", "sample.txt")
    sample.write_text("unstaged")
    (repo_path / "untracked.txt").write_text("local only")
    status = repo.git("status", "--porcelain").stdout
    index = repo.git("diff", "--cached").stdout
    branch = repo.git("symbolic-ref", "HEAD").stdout

    monkeypatch.setattr(orchestrator.executor, "verify_combo", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "ninerouter_orchestrator.orchestrator.run_process", lambda *args, **kwargs: None
    )
    plan = Plan(objective="Update sample", tickets=[Ticket(
        id="T1", title="Update sample", description="Update the committed sample",
        acceptance_criteria=["Sample updated"], validation_commands=["git diff --check"],
    )])
    monkeypatch.setattr(orchestrator, "plan", lambda request: plan)

    def execute(request):
        assert any(stage == "Implementation plan ready" for stage, _ in progress)
        assert request.workspace != repo_path
        assert (request.workspace / "sample.txt").read_text() == "base"
        assert not (request.workspace / "untracked.txt").exists()
        (request.workspace / "sample.txt").write_text("candidate")
        return "Implemented"

    monkeypatch.setattr(orchestrator.executor, "execute", execute)
    monkeypatch.setattr(
        orchestrator, "select_candidate",
        lambda *args: CandidateDecision(selected_fork=1, rationale="Checks pass"),
    )
    monkeypatch.setattr(
        orchestrator, "review",
        lambda *args: ReviewReport(approved=True, summary="Approved"),
    )

    result = orchestrator.run("Update sample")

    assert result["status"] == "approved"
    stages = [stage for stage, _ in progress]
    assert "T1: candidate 1 started" in stages
    assert "T1: validating candidate 1" in stages
    assert "Selecting the best candidate for T1" in stages
    assert "Integrating T1 and running checks" in stages
    assert "Integrated T1" in stages
    assert "Reviewing the integrated changes" in stages
    assert progress[-1][1]["review"]["approved"] is True
    assert result["base"] == base
    assert repo.git("show", f"{result['integration_branch']}:sample.txt").stdout == "candidate"
    assert repo.head() == base
    assert repo.git("symbolic-ref", "HEAD").stdout == branch
    assert repo.git("status", "--porcelain").stdout == status
    assert repo.git("diff", "--cached").stdout == index
    assert sample.read_text() == "unstaged"
    assert (repo_path / "untracked.txt").read_text() == "local only"


def test_unbound_graph_fails_explicitly():
    with pytest.raises(RuntimeError, match="bind runtime"):
        build_workflow().invoke({"request": "test"})


def test_real_worktree_isolation(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    run_process(["git", "init"], cwd=repo_path, timeout=30, check=True)
    repo = GitRepository(repo_path)
    (repo_path / "sample.txt").write_text("base")
    repo.commit_all(repo_path, "base")
    base = repo.head()
    fork, _ = repo.create_worktree(
        state_dir=tmp_path / "state", run_id="test", ticket_id="T1", fork_number=1, start_point=base
    )
    (fork / "sample.txt").write_text("candidate")
    candidate = repo.commit_all(fork, "candidate")
    assert candidate != base
    assert repo.head() == base
    assert (repo_path / "sample.txt").read_text() == "base"


def test_dispatch_initializes_and_commits_existing_files_as_baseline(tmp_path):
    repo_path = tmp_path / "new-repo"
    repo_path.mkdir()
    (repo_path / ".gitignore").write_text("secret.env\n")
    (repo_path / "app.py").write_text("print('ready')\n")
    (repo_path / "secret.env").write_text("TOKEN=hidden\n")
    repo = GitRepository(repo_path)

    setup = repo.prepare_dispatch()

    assert setup["initialized"] is True
    assert setup["baseline_commit"] == repo.head()
    assert repo.git("symbolic-ref", "--short", "HEAD").stdout.strip() == "main"
    assert repo.git("show", "--format=", "--name-only", "HEAD").stdout.split() == [
        ".gitignore", "app.py"
    ]
    assert repo.git("status", "--porcelain").stdout == ""
    assert repo.git("log", "-1", "--pretty=%s").stdout.strip() == (
        "chore: record baseline before first Switchyard dispatch"
    )


def test_dispatch_creates_empty_baseline_and_is_idempotent(tmp_path):
    repo_path = tmp_path / "empty-repo"
    repo_path.mkdir()
    repo = GitRepository(repo_path)

    first = repo.prepare_dispatch()
    second = repo.prepare_dispatch()

    assert first["initialized"] is True
    assert first["baseline_commit"] == repo.head()
    assert second == {"initialized": False, "baseline_commit": None}
    assert repo.git("rev-list", "--count", "HEAD").stdout.strip() == "1"


def test_dispatch_does_not_touch_existing_history_or_local_changes(tmp_path):
    repo_path = tmp_path / "existing-repo"
    repo_path.mkdir()
    run_process(["git", "init"], cwd=repo_path, timeout=30, check=True)
    repo = GitRepository(repo_path)
    tracked = repo_path / "tracked.txt"
    tracked.write_text("base\n")
    repo.commit_all(repo_path, "base")
    head = repo.head()
    tracked.write_text("local edit\n")
    (repo_path / "untracked.txt").write_text("keep me\n")
    status = repo.git("status", "--porcelain").stdout

    setup = repo.prepare_dispatch()

    assert setup == {"initialized": False, "baseline_commit": None}
    assert repo.head() == head
    assert repo.git("status", "--porcelain").stdout == status


def test_dispatch_initializes_nested_repository_folder(tmp_path):
    repo_path = tmp_path / "repo"
    nested = repo_path / "nested"
    nested.mkdir(parents=True)
    run_process(["git", "init"], cwd=repo_path, timeout=30, check=True)

    setup = GitRepository(nested).prepare_dispatch()

    assert setup["initialized"] is True
    assert setup["baseline_commit"]
    assert (nested / ".git").is_dir()


@pytest.mark.parametrize("existing_git", [False, True])
def test_run_prepares_baseline_before_planning(monkeypatch, tmp_path, existing_git):
    root = tmp_path / "project"
    root.mkdir()
    (root / "original.txt").write_text("starting content")
    repo = GitRepository(root)
    if existing_git:
        repo.git("init", "--initial-branch", "develop")
    orchestrator = EngineeringOrchestrator(root, Settings(allow_host_execution=True))
    monkeypatch.setattr(orchestrator, "preflight", lambda **kwargs: None)

    def plan(request):
        assert repo.git("show", "HEAD:original.txt").stdout == "starting content"
        assert repo.git("symbolic-ref", "--short", "HEAD").stdout.strip() == (
            "develop" if existing_git else "main"
        )
        raise RuntimeError("Stop before calling any agents")

    monkeypatch.setattr(orchestrator, "plan", plan)
    with pytest.raises(RuntimeError, match="Stop before calling any agents"):
        orchestrator.run("Implement a change")
    assert repo.git("rev-list", "--count", "HEAD").stdout.strip() == "1"


@pytest.mark.parametrize("kind", ["bare", "broken", "broken-parent"])
def test_dispatch_does_not_reinitialize_invalid_repositories(tmp_path, kind):
    root = tmp_path / "project"
    root.mkdir()
    if kind == "bare":
        GitRepository(root).git("init", "--bare")
    else:
        (root / ".git").mkdir()
        if kind == "broken-parent":
            root = root / "child"
            root.mkdir()
    with pytest.raises(ProcessError):
        GitRepository(root).prepare_dispatch()
    assert not (root / ".git" / "HEAD").exists()


def test_failed_baseline_stops_dispatch_before_agents(monkeypatch, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "source.txt").write_text("keep this content")
    orchestrator = EngineeringOrchestrator(root, Settings(allow_host_execution=True))
    real_git = orchestrator.repository.git

    def git(*args, **kwargs):
        if "commit" in args:
            raise ProcessError("Cannot create baseline commit")
        return real_git(*args, **kwargs)

    monkeypatch.setattr(orchestrator.repository, "git", git)
    monkeypatch.setattr(orchestrator, "create_run", lambda *args, **kwargs: pytest.fail(
        "Planning must not start without a baseline"
    ))
    with pytest.raises(ProcessError, match="Cannot create baseline commit"):
        orchestrator.run("Implement a change")
    assert (root / "source.txt").read_text() == "keep this content"
    assert not real_git("rev-parse", "--verify", "HEAD", check=False).passed


def test_clean_worktree_is_removed(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    run_process(["git", "init"], cwd=repo_path, timeout=30, check=True)
    repo = GitRepository(repo_path)
    (repo_path / "sample.txt").write_text("base")
    repo.commit_all(repo_path, "base")
    fork, _ = repo.create_worktree(
        state_dir=tmp_path / "state",
        run_id="clean",
        ticket_id="T1",
        fork_number=1,
        start_point=repo.head(),
    )
    removed, reason = repo.remove_worktree(fork)
    assert removed is True
    assert reason is None
    assert not fork.exists()


def test_dirty_worktree_is_preserved(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    run_process(["git", "init"], cwd=repo_path, timeout=30, check=True)
    repo = GitRepository(repo_path)
    (repo_path / "sample.txt").write_text("base")
    repo.commit_all(repo_path, "base")
    fork, _ = repo.create_worktree(
        state_dir=tmp_path / "state",
        run_id="dirty",
        ticket_id="T1",
        fork_number=1,
        start_point=repo.head(),
    )
    (fork / "sample.txt").write_text("uncommitted")
    removed, reason = repo.remove_worktree(fork)
    assert removed is False
    assert reason == "Uncommitted files are preserved"
    assert fork.exists()
