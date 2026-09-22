import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ninerouter_orchestrator import web
from ninerouter_orchestrator.config import Settings
from ninerouter_orchestrator.git import GitRepository
from ninerouter_orchestrator.web import app
from ninerouter_orchestrator.workspace_config import (
    Workspace,
    WorkspaceConfiguration,
    WorkspaceStore,
)

client = TestClient(app, headers={"x-switchyard-client": "local-ui"})


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Switchyard Test")
    git(root, "config", "user.email", "switchyard@example.test")
    (root / "tracked.txt").write_text("base\n")
    git(root, "add", "tracked.txt")
    git(root, "commit", "-m", "base")
    return root


def configure_workspace(monkeypatch, tmp_path: Path, root: Path) -> Workspace:
    store = WorkspaceStore(tmp_path / "workspaces.json")
    workspace = Workspace(id="repo", name="Repo", repository=str(root))
    store.save(WorkspaceConfiguration(workspaces=[workspace], default_workspace_id="repo"))
    monkeypatch.setattr(web, "workspace_store", store)
    return workspace


def test_worktree_tab_assets_are_exposed():
    page = client.get("/").text
    script = client.get("/assets/app.js").text
    worktrees = client.get("/assets/worktrees.js").text

    assert 'href="#worktrees" data-panel="worktrees"' in page
    assert 'id="worktrees-panel"' in page
    assert 'worktrees: ["Worktrees"' in script
    assert 'fetch("/api/worktrees/action"' in worktrees
    assert "Remove checkout" in worktrees


def test_worktree_api_lists_protects_and_removes_managed_checkout(monkeypatch, tmp_path):
    root = repository(tmp_path)
    configure_workspace(monkeypatch, tmp_path, root)
    repository_git = GitRepository(root)
    state_dir = Settings().state_dir(root)
    checkout, branch = repository_git.create_worktree(
        state_dir=state_dir,
        run_id="run-one",
        ticket_id="ticket-a",
        fork_number=1,
        start_point="HEAD",
    )
    unrelated = tmp_path / "unrelated"
    git(root, "worktree", "add", "-b", "unrelated", str(unrelated), "HEAD")

    listing = client.get("/api/worktrees", params={"workspace_id": "repo"})
    assert listing.status_code == 200
    assert listing.json()["summary"] == {
        "total": 1, "changed": 0, "protected": 0, "missing": 0,
    }
    entry = listing.json()["worktrees"][0]
    assert entry["path"] == str(checkout)
    assert entry["branch"] == branch
    assert entry["run_id"] == "run-one"
    assert entry["ticket_id"] == "ticket-a"
    assert entry["removable"] is True

    protected = client.post("/api/worktrees/action", json={
        "workspace_id": "repo", "path": str(checkout), "action": "protect",
    })
    assert protected.status_code == 200
    assert client.get("/api/worktrees", params={"workspace_id": "repo"}).json()["worktrees"][0]["locked"] is True
    assert client.post("/api/worktrees/action", json={
        "workspace_id": "repo", "path": str(checkout), "action": "remove", "confirmed": True,
    }).status_code == 422

    assert client.post("/api/worktrees/action", json={
        "workspace_id": "repo", "path": str(checkout), "action": "unprotect",
    }).status_code == 200
    assert client.post("/api/worktrees/action", json={
        "workspace_id": "repo", "path": str(checkout), "action": "remove", "confirmed": True,
    }).status_code == 200
    assert not checkout.exists()
    assert branch in git(root, "branch", "--list")


def test_worktree_api_preserves_dirty_and_unmanaged_checkouts(monkeypatch, tmp_path):
    root = repository(tmp_path)
    configure_workspace(monkeypatch, tmp_path, root)
    checkout, _ = GitRepository(root).create_worktree(
        state_dir=Settings().state_dir(root),
        run_id="dirty-run",
        ticket_id="ticket-b",
        fork_number=2,
        start_point="HEAD",
    )
    (checkout / "notes.txt").write_text("keep this\n")

    entry = client.get("/api/worktrees", params={"workspace_id": "repo"}).json()["worktrees"][0]
    assert entry["changed_files"] == 1
    assert entry["removable"] is False
    response = client.post("/api/worktrees/action", json={
        "workspace_id": "repo", "path": str(checkout), "action": "remove", "confirmed": True,
    })
    assert response.status_code == 422
    assert checkout.exists()
    assert "Preserve local files" in response.json()["detail"]

    outside = tmp_path / "outside"
    outside.mkdir()
    response = client.post("/api/worktrees/action", json={
        "workspace_id": "repo", "path": str(outside), "action": "remove", "confirmed": True,
    })
    assert response.status_code == 422
    assert outside.exists()


def test_worktree_api_counts_ignored_files_before_removal(monkeypatch, tmp_path):
    root = repository(tmp_path)
    (root / ".gitignore").write_text("*.cache\n")
    git(root, "add", ".gitignore")
    git(root, "commit", "-m", "ignore cache")
    configure_workspace(monkeypatch, tmp_path, root)
    checkout, _ = GitRepository(root).create_worktree(
        state_dir=Settings().state_dir(root),
        run_id="ignored-run",
        ticket_id="ticket-c",
        fork_number=1,
        start_point="HEAD",
    )
    (checkout / "valuable.cache").write_text("keep this\n")

    entry = client.get("/api/worktrees", params={"workspace_id": "repo"}).json()["worktrees"][0]
    assert entry["changed_files"] == 1
    assert entry["removable"] is False
    response = client.post("/api/worktrees/action", json={
        "workspace_id": "repo", "path": str(checkout), "action": "remove", "confirmed": True,
    })
    assert response.status_code == 422
    assert (checkout / "valuable.cache").read_text() == "keep this\n"


def test_worktree_api_requires_local_origin_and_confirmation():
    assert TestClient(app).get("/api/worktrees?workspace_id=repo").status_code == 403
    assert client.get("/api/worktrees?workspace_id=repo", headers={
        "origin": "https://other.example",
    }).status_code == 403
    assert client.post("/api/worktrees/action", json={
        "workspace_id": "repo", "path": "/arbitrary", "action": "remove",
    }).status_code == 422


@pytest.mark.parametrize("lock_name", ["git_lock", "dispatch_lock"])
def test_worktree_actions_wait_for_git_and_dispatch_locks(lock_name):
    lock = getattr(web, lock_name)
    lock.acquire()
    try:
        response = client.post("/api/worktrees/action", json={
            "workspace_id": "repo", "path": "/arbitrary", "action": "protect",
        })
        assert response.status_code == 409
    finally:
        lock.release()
    assert not web.dispatch_lock.locked()
    assert not web.git_lock.locked()


def test_missing_detached_and_unicode_worktrees_are_safe(monkeypatch, tmp_path):
    root = repository(tmp_path)
    configure_workspace(monkeypatch, tmp_path, root)
    repo = GitRepository(root)
    state_dir = Settings().state_dir(root)
    missing, _ = repo.create_worktree(
        state_dir=state_dir, run_id="missing", ticket_id="integration",
        fork_number=0, start_point="HEAD",
    )
    missing.rename(tmp_path / "moved-checkout")
    detached, _ = repo.create_worktree(
        state_dir=state_dir, run_id="detached", ticket_id="integration",
        fork_number=0, start_point="HEAD",
    )
    git(detached, "checkout", "--detach")
    odd = state_dir / "worktrees" / "run space" / "ticket-\u00e9\nline" / "fork-1"
    git(root, "worktree", "add", "-b", "odd", str(odd))
    entries = {item["path"]: item for item in repo.managed_worktrees(state_dir)}
    assert entries[str(missing)]["prunable"] is True
    assert entries[str(missing)]["exists"] is False
    assert entries[str(detached)]["detached"] is True
    assert entries[str(detached)]["removable"] is False
    assert str(odd) in entries
    assert odd in repo.registered_worktrees()
    for path in (missing, detached, root):
        assert client.post("/api/worktrees/action", json={
            "workspace_id": "repo", "path": str(path), "action": "remove", "confirmed": True,
        }).status_code == 422
