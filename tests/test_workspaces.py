import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_git_view import git, repository

from ninerouter_orchestrator import web
from ninerouter_orchestrator.job_store import JobStore
from ninerouter_orchestrator.workflow_config import WorkflowStore
from ninerouter_orchestrator.workspace_config import (
    Workspace,
    WorkspaceConfiguration,
    WorkspaceStore,
)

client = TestClient(web.app, headers={"x-switchyard-client": "local-ui"})


@pytest.fixture
def configured(monkeypatch, tmp_path):
    root = repository(tmp_path)
    monkeypatch.setattr(web, "workspace_store", WorkspaceStore(tmp_path / "workspaces.json"))
    monkeypatch.setattr(web, "workflow_store", WorkflowStore(tmp_path / "workflows.json"))
    monkeypatch.setattr(web, "store", JobStore(tmp_path / "jobs.json"))
    monkeypatch.setattr(web, "dispatch_lock", threading.Lock())
    return Workspace(
        id="alpha",
        name="Alpha",
        repository=str(root),
        command_timeout_seconds=300,
        forks_per_ticket=2,
    )


def save(workspace):
    return client.put(
        "/api/workspaces",
        json={"workspaces": [workspace.model_dump()], "default_workspace_id": workspace.id},
    )


def test_workspace_persistence_and_validation(configured):
    assert save(configured).status_code == 200
    loaded = WorkspaceStore(web.workspace_store.path).get()
    assert loaded.resolve("alpha").run_settings() == {
        "forks_per_ticket": 2,
        "command_timeout_seconds": 300,
    }
    assert client.get("/api/workspaces").json() == loaded.model_dump()
    for change in (
        {"repository": "relative/path"},
        {"repository": " "},
        {"name": " "},
        {"forks_per_ticket": 6},
        {"command_timeout_seconds": 2},
        {"git_base_branch": "-unsafe"},
    ):
        with pytest.raises(ValidationError):
            Workspace.model_validate({**configured.model_dump(), **change})
    with pytest.raises(ValidationError):
        WorkspaceConfiguration(workspaces=[configured, configured])
    with pytest.raises(ValidationError):
        WorkspaceConfiguration(workspaces=[configured], default_workspace_id="unknown")


def test_rejected_save_preserves_configuration(configured):
    assert save(configured).status_code == 200
    original = web.workspace_store.path.read_bytes()
    candidate = configured.model_copy(update={"repository": "/no/such/repository"})
    assert save(candidate).status_code == 422
    candidate = configured.model_copy(update={"workflow_id": "not-saved"})
    assert save(candidate).status_code == 422
    assert web.workspace_store.path.read_bytes() == original
    assert (
        client.put(
            "/api/workspaces", json={}, headers={"origin": "https://evil.example"}
        ).status_code
        == 403
    )


def test_workspace_save_initializes_git_repository(configured, tmp_path):
    root = tmp_path / "new-workspace"
    root.mkdir()
    (root / "keep.txt").write_text("Existing content\n")
    configured.repository = str(root)
    configured.git_base_branch = "develop"

    response = save(configured)

    assert response.status_code == 200
    assert (root / ".git").is_dir()
    assert git(root, "symbolic-ref", "--short", "HEAD") == "develop"
    assert git(root, "ls-files") == ""
    assert git(root, "status", "--porcelain") == "?? keep.txt"
    assert (root / "keep.txt").read_text() == "Existing content\n"
    assert subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=root,
        capture_output=True,
        check=False,
    ).returncode != 0
    assert web.workspace_store.get().resolve(configured.id).repository == str(root)
    assert client.get("/api/git/status", params={"workspace_id": configured.id}).status_code == 200
    assert save(configured).status_code == 200


def test_workspace_save_initializes_a_subfolder_as_an_independent_repository(configured):
    root = Path(configured.repository)
    nested = root / "nested"
    nested.mkdir()
    configured.repository = str(nested)

    response = save(configured)

    assert response.status_code == 200
    assert (nested / ".git").is_dir()
    assert Path(git(nested, "rev-parse", "--show-toplevel")) == nested


@pytest.mark.parametrize("kind", ["bare", "broken", "broken-parent"])
def test_workspace_save_does_not_reinitialize_invalid_repositories(configured, tmp_path, kind):
    root = tmp_path / "invalid-repository"
    root.mkdir()
    if kind == "bare":
        git(root, "init", "--bare")
    else:
        (root / ".git").mkdir()
        if kind == "broken-parent":
            root = root / "child"
            root.mkdir()
    configured.repository = str(root)

    assert save(configured).status_code == 422
    assert not (root / ".git" / "HEAD").exists()
    assert not web.workspace_store.path.exists()


def test_workspace_save_rejects_unknown_workflow_before_initializing(configured, tmp_path):
    root = tmp_path / "new-workspace"
    root.mkdir()
    configured.repository = str(root)
    invalid = configured.model_copy(update={"id": "invalid", "workflow_id": "missing"})

    response = client.put("/api/workspaces", json={
        "workspaces": [configured.model_dump(), invalid.model_dump()],
    })

    assert response.status_code == 422
    assert not (root / ".git").exists()


@pytest.mark.parametrize("old_mode", ["plan", "run"])
def test_old_workspace_mode_is_dropped_on_save(configured, old_mode):
    web.workspace_store.path.write_text(json.dumps({
        "workspaces": [{**configured.model_dump(), "default_mode": old_mode}],
        "default_workspace_id": configured.id,
    }))
    loaded = client.get("/api/workspaces")
    assert loaded.status_code == 200
    assert loaded.json()["workspaces"] == [configured.model_dump()]
    assert client.put("/api/workspaces", json=loaded.json()).status_code == 200
    assert "default_mode" not in web.workspace_store.path.read_text()


def test_corrupt_configuration_is_not_silently_replaced(configured):
    web.workspace_store.path.write_text("not JSON")
    assert client.get("/api/workspaces").status_code == 422
    assert save(configured).status_code == 422
    assert web.workspace_store.path.read_text() == "not JSON"


def test_workspace_controls_git_path_and_default_base(configured):
    assert save(configured).status_code == 200
    result = client.get("/api/git/status", params={"workspace_id": "alpha", "repository": "/wrong"})
    assert result.status_code == 200
    assert result.json()["repository"] == configured.repository
    result = client.get("/api/git/status", params={"workspace_id": "alpha", "comparison": "branch"})
    assert result.status_code == 200
    assert result.json()["comparison"]["base"] == "main"
    assert client.get("/api/git/status", params={"workspace_id": "unknown"}).status_code == 422


def test_dispatch_inherits_workspace_and_snapshots_defaults(configured, monkeypatch):
    assert save(configured).status_code == 200
    captured = {}

    class Worker:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def start(self):
            pass

    monkeypatch.setattr(web, "threading", SimpleNamespace(Thread=Worker))
    response = client.post(
        "/api/jobs",
        json={
            "workspace_id": "alpha",
            "repository": "/ignored",
            "request": "Implement a safe change",
            "allow_host_execution": True,
        },
    )
    assert response.status_code == 202
    job = response.json()
    assert job["repository"] == configured.repository
    assert job["workspace"] == configured.model_dump()
    assert job["workflow_id"] == configured.workflow_id
    assert job["run_settings"] == captured["args"][3] == configured.run_settings()
    configured.command_timeout_seconds = 900
    assert save(configured).status_code == 200
    assert web.store.get(job["id"])["run_settings"]["command_timeout_seconds"] == 300


def test_shared_workflow_can_dispatch_in_multiple_workspaces(configured, monkeypatch, tmp_path):
    monkeypatch.setattr(web, "_validate_step_combos", lambda steps: None)
    # Author the library before any workspace exists.
    library = client.get("/api/workflows").json()
    shared = {**library["workflows"][0], "id": "shared", "name": "Shared route"}
    collection = {"workflows": [*library["workflows"], shared], "default_workflow_id": "default"}
    assert client.put("/api/workflows", json=collection).status_code == 200
    assert web.workspace_store.get().workspaces == []
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    second_root = repository(second_dir)
    second = configured.model_copy(update={
        "id": "beta", "name": "Beta", "repository": str(second_root),
    })
    response = client.put("/api/workspaces", json={
        "workspaces": [configured.model_dump(), second.model_dump()],
        "default_workspace_id": configured.id,
    })
    assert response.status_code == 200
    monkeypatch.setattr(
        web,
        "threading",
        SimpleNamespace(Thread=lambda **kwargs: SimpleNamespace(start=lambda: None)),
    )

    runs = [client.post("/api/jobs", json={
        "workspace_id": workspace_id,
        "request": "Run the same shared workflow here",
        "allow_host_execution": True,
        "workflow_id": "shared",
    }) for workspace_id in ("alpha", "beta")]

    assert [run.status_code for run in runs] == [202, 202]
    assert [run.json()["workspace_id"] for run in runs] == ["alpha", "beta"]
    assert {run.json()["workflow_id"] for run in runs} == {"shared"}
    assert [run.json()["repository"] for run in runs] == [configured.repository, str(second_root)]
    assert runs[0].json()["workflow"] == runs[1].json()["workflow"]

    shared["steps"][0]["system_prompt"] = "Updated shared planning instructions."
    assert client.put("/api/workflows", json=collection).status_code == 200
    for workspace_id in ("alpha", "beta"):
        future = client.post("/api/jobs", json={
            "workspace_id": workspace_id, "workflow_id": "shared",
            "request": "Use the updated shared workflow",
            "allow_host_execution": True,
        })
        assert future.status_code == 202
        assert future.json()["workflow"]["steps"][0]["system_prompt"] == shared["steps"][0]["system_prompt"]
    for run in runs:
        assert web.store.get(run.json()["id"])["workflow"] == run.json()["workflow"]

    # Removing execution contexts never deletes their reusable definitions.
    assert client.put("/api/workspaces", json={"workspaces": []}).status_code == 200
    assert client.get("/api/workflows").json()["workflows"] == collection["workflows"]


def test_dispatch_never_bypasses_confirmation(configured):
    assert save(configured).status_code == 200
    response = client.post(
        "/api/jobs", json={"workspace_id": "alpha", "request": "Implement a trusted change"}
    )
    assert response.status_code == 422
    assert "Confirm host execution" in response.json()["detail"]
    assert web.store.list() == []


def test_workspace_can_use_builtin_workflow(configured, monkeypatch):
    configured.workflow_id = None
    assert save(configured).status_code == 200
    monkeypatch.setattr(
        web,
        "threading",
        SimpleNamespace(Thread=lambda **kwargs: SimpleNamespace(start=lambda: None)),
    )
    response = client.post(
        "/api/jobs",
        json={
            "workspace_id": "alpha",
            "request": "Run using built-in steps",
            "allow_host_execution": True,
        },
    )
    assert response.status_code == 202
    assert response.json()["workflow_id"] is None
    assert response.json()["workflow"]["name"] == "Built-in engineering route (no saved workflow)"


def test_worker_uses_snapshotted_settings(configured, monkeypatch):
    save(configured)
    observed = {}

    class Orchestrator:
        def __init__(self, repo, settings, *args):
            observed["settings"] = settings

        def run(self, request):
            return {"request": request, "status": "needs_repair"}

        def save_run(self, result):
            pass

    monkeypatch.setattr(web, "EngineeringOrchestrator", Orchestrator)
    job = web.store.create(
        repository=configured.repository,
        request="Run using configured settings",
        workflow=web.workflow_store.get().model_dump(),
    )
    from pathlib import Path

    web._work(
        job["id"],
        web.JobRequest(request=job["request"], allow_host_execution=True),
        Path(configured.repository),
        configured.run_settings(),
    )
    assert observed["settings"].forks_per_ticket == 2
    assert observed["settings"].command_timeout_seconds == 300
    assert observed["settings"].allow_host_execution
    assert web.store.get(job["id"])["status"] == "needs_repair"


def test_workspace_history_includes_legacy_runs_before_limit(configured):
    save(configured)
    legacy = web.store.create(
        repository=configured.repository, request="Legacy", workflow={}
    )
    for index in range(32):
        web.store.create(
            repository="/another/repo", request=f"Other {index}", workflow={}
        )
    response = client.get("/api/jobs", params={"workspace_id": "alpha"})
    assert [item["id"] for item in response.json()] == [legacy["id"]]
    assert client.get("/api/jobs", params={"workspace_id": "missing"}).status_code == 422


def test_removing_configuration_preserves_repository_and_runs(configured):
    save(configured)
    web.store.create(
        repository=configured.repository, request="Keep this run", workflow={}
    )
    response = client.put("/api/workspaces", json={"workspaces": [], "default_workspace_id": None})
    assert response.status_code == 200
    from pathlib import Path

    assert Path(configured.repository).is_dir()
    assert len(web.store.list()) == 1


def test_workspace_with_running_commands_cannot_be_removed_or_moved(configured, monkeypatch):
    save(configured)
    monkeypatch.setattr(web.command_console, "has_active_workspace", lambda workspace_id: True)

    removed = client.put("/api/workspaces", json={"workspaces": [], "default_workspace_id": None})
    moved = configured.model_copy(update={"repository": str(Path(configured.repository) / "other")})

    assert removed.status_code == 409
    assert "Stop commands" in removed.json()["detail"]
    assert save(moved).status_code == 409


def test_workspace_tab_and_shared_selectors_are_served():
    page = client.get("/").text
    script = client.get("/assets/workspaces.js").text
    styles = client.get("/assets/styles.css").text
    assert 'data-panel="workspaces"' in page
    assert page.count("data-workspace-selector") == 1
    assert 'class="sidebar-workspace" id="workspace-context"' in page
    assert 'id="active-workspace" data-workspace-selector' in page
    assert 'id="active-workspace-path"' not in page
    assert '<p class="sidebar-label">Navigation</p>' not in page
    assert page.count('<a href="#workspaces">Manage workspaces</a>') == 2
    assert 'id="chat-workspace"' not in page
    assert 'id="dispatch-workspace"' not in page
    assert 'id="git-workspace"' not in page
    assert ".sidebar { width: 100%; padding: 11px 14px; overflow: visible;" in styles
    assert 'id="workspace-timeout"' in page
    assert 'id="browse-repository"' not in page
    assert 'id="browse-git-repository"' not in page
    assert 'id="load-workspace-git-defaults"' in page
    assert 'fetch("/api/repository/git-defaults"' in script
    assert "The new remote branch template and draft preference remain editable." in page
    assert "/assets/workspaces.js" in page


def test_repository_git_defaults_read_remote_and_cached_default_branch(tmp_path):
    root = repository(tmp_path)
    git(root, "remote", "add", "origin", "git@example.test:team/repo.git")
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")

    response = client.post("/api/repository/git-defaults", json={"path": str(root)})

    assert response.status_code == 200
    assert response.json() == {
        "repository": str(root), "remote": "origin", "base_branch": "main",
        "remotes": ["origin"],
    }


def test_repository_git_defaults_prefer_push_remote_and_local_main(tmp_path):
    root = repository(tmp_path)
    git(root, "remote", "add", "origin", "git@example.test:team/repo.git")
    git(root, "remote", "add", "publish", "git@example.test:team/fork.git")
    git(root, "config", "remote.pushDefault", "publish")
    git(root, "checkout", "-b", "feature/live-settings")

    response = client.post("/api/repository/git-defaults", json={"path": str(root)})

    assert response.status_code == 200
    assert response.json()["remote"] == "publish"
    assert response.json()["base_branch"] == "main"


def test_repository_git_defaults_handle_missing_metadata_and_invalid_folders(tmp_path):
    root = repository(tmp_path)
    nested = root / "nested"
    nested.mkdir()
    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()

    defaults = client.post("/api/repository/git-defaults", json={"path": str(root)})
    assert defaults.status_code == 200
    assert defaults.json()["remote"] is None
    assert defaults.json()["base_branch"] == "main"
    nested_response = client.post("/api/repository/git-defaults", json={"path": str(nested)})
    assert nested_response.status_code == 422
    assert "until it is saved as a workspace" in nested_response.json()["detail"]
    ordinary_response = client.post(
        "/api/repository/git-defaults", json={"path": str(ordinary)}
    )
    assert ordinary_response.status_code == 422
    assert "Git settings are unavailable" in ordinary_response.json()["detail"]
    assert client.post(
        "/api/repository/git-defaults", json={"path": str(tmp_path / "missing")}
    ).status_code == 404


def test_local_only_workspace_preserves_detected_delivery_fields():
    source = Path("src/ninerouter_orchestrator/web_assets/app.js").read_text(encoding="utf-8")
    function = source[
        source.index("function readDelivery(scope)"):
        source.index('document.querySelectorAll("[data-delivery-mode]")')
    ]
    script = """
const values = {
  '#workspace-delivery-remote': {value: 'publish'},
  '#workspace-delivery-branch': {value: 'orchestrator/{run_id}'},
  '#workspace-delivery-base': {value: 'develop'},
  '#workspace-delivery-draft': {checked: false},
};
const document = {querySelector: selector => selector.includes(':checked')
  ? {value: 'none'} : values[selector]};
""" + function + "\nconsole.log(JSON.stringify(readDelivery('workspace')));"

    output = subprocess.check_output(["node", "-e", script], text=True)

    assert json.loads(output) == {
        "mode": "none", "remote": "publish", "branch": "orchestrator/{run_id}",
        "base_branch": "develop", "draft": False,
    }
