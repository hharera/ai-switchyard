import threading
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from ninerouter_orchestrator import web
from ninerouter_orchestrator.adapters import codex
from ninerouter_orchestrator.process import ProcessError
from ninerouter_orchestrator.workspace_config import (
    Workspace,
    WorkspaceConfiguration,
    WorkspaceStore,
)

client = TestClient(web.app, headers={"x-switchyard-client": "local-ui"})


def configure(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    nested = root / "backend"
    nested.mkdir()
    store = WorkspaceStore(tmp_path / "workspaces.json")
    store.save(WorkspaceConfiguration(workspaces=[
        Workspace(id="project", name="Project", repository=str(root)),
    ]))
    monkeypatch.setattr(web, "workspace_store", store)
    for name in ("inline_agent_lock", "dispatch_lock", "git_lock", "file_editor_lock", "chat_lock"):
        monkeypatch.setattr(web, name, threading.Lock())
    return root, nested


def payload(root, *, mode="ask", tool="codex", allow=False, context=None):
    return {
        "workspace_id": "project",
        "tool": tool,
        "mode": mode,
        "messages": [{"role": "user", "content": "Explain this selection"}],
        "context": context or {
            "surface": "files", "repository": str(root), "file": "app.py",
            "selected_paths": [], "unsaved_paths": [],
        },
        "allow_host_execution": allow,
    }


def test_inline_agent_ask_is_read_only_and_receives_context(monkeypatch, tmp_path):
    root, _ = configure(monkeypatch, tmp_path)
    captured = {}

    def respond(self, prompt, *, cwd, allow_changes=False):
        captured.update(prompt=prompt, cwd=cwd, allow_changes=allow_changes)
        return "The file handles startup."

    monkeypatch.setattr(web.CodexAdapter, "respond", respond)
    response = client.post("/api/inline-agent", json=payload(root))
    assert response.status_code == 200
    assert response.json()["message"] == "The file handles startup."
    assert captured["cwd"] == root
    assert captured["allow_changes"] is False
    assert "app.py" in captured["prompt"]
    assert "read-only workspace assistant" in captured["prompt"]


def test_inline_agent_edit_uses_selected_checkout_and_workspace_write(monkeypatch, tmp_path):
    root, nested = configure(monkeypatch, tmp_path)
    captured = {}

    def respond(self, prompt, *, cwd, allow_changes=False):
        captured.update(prompt=prompt, cwd=cwd, allow_changes=allow_changes)
        (cwd / "changed.txt").write_text("done\n")
        return "Changed changed.txt and ran tests."

    monkeypatch.setattr(web.CodexAdapter, "respond", respond)
    body = payload(root, mode="edit", allow=True, context={
        "surface": "git", "repository": str(nested), "commit": "a" * 40,
        "selected_paths": ["changed.txt"], "unsaved_paths": [],
    })
    response = client.post("/api/inline-agent", json=body)
    assert response.status_code == 200
    assert response.json()["mode"] == "edit"
    assert captured["cwd"] == nested
    assert captured["allow_changes"] is True
    assert "Never commit, stage, push, reset, publish" in captured["prompt"]
    assert (nested / "changed.txt").read_text() == "done\n"
    assert all(not lock.locked() for lock in (
        web.inline_agent_lock, web.dispatch_lock, web.git_lock, web.file_editor_lock,
    ))


def test_inline_agent_edit_requires_confirmation_saved_files_and_codex(monkeypatch, tmp_path):
    root, _ = configure(monkeypatch, tmp_path)
    assert client.post("/api/inline-agent", json=payload(root, mode="edit")).status_code == 422
    unsaved = payload(root, mode="edit", allow=True)
    unsaved["context"]["unsaved_paths"] = ["app.py"]
    assert client.post("/api/inline-agent", json=unsaved).status_code == 409
    assert client.post("/api/inline-agent", json=payload(
        root, mode="edit", tool="9router/Kimi", allow=True,
    )).status_code == 422


def test_inline_agent_rejects_checkout_outside_workspace_and_releases_locks(monkeypatch, tmp_path):
    root, _ = configure(monkeypatch, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    invalid = payload(root)
    invalid["context"]["repository"] = str(outside)
    assert client.post("/api/inline-agent", json=invalid).status_code == 422

    monkeypatch.setattr(web.CodexAdapter, "respond", lambda *args, **kwargs: (_ for _ in ()).throw(ProcessError("failed")))
    response = client.post("/api/inline-agent", json=payload(root, mode="edit", allow=True))
    assert response.status_code == 503
    assert "inspect Git" in response.json()["detail"]
    assert all(not lock.locked() for lock in (
        web.inline_agent_lock, web.dispatch_lock, web.git_lock, web.file_editor_lock,
    ))


def test_edit_adapter_uses_workspace_write_without_remote_tools(monkeypatch, tmp_path):
    def run(command, **kwargs):
        assert command[command.index("--sandbox") + 1] == "workspace-write"
        assert "--ignore-user-config" in command
        assert "--ephemeral" in command
        assert "--skip-git-repo-check" in command
        assert "mcp_servers" not in " ".join(command)
        Path(command[command.index("--output-last-message") + 1]).write_text("Edited")
        return SimpleNamespace(passed=True)

    monkeypatch.setattr(codex, "run_process", run)
    assert codex.CodexAdapter().respond("Edit", cwd=tmp_path, allow_changes=True) == "Edited"


def test_inline_agent_busy_and_local_ui_guards(monkeypatch, tmp_path):
    root, _ = configure(monkeypatch, tmp_path)
    body = payload(root, mode="edit", allow=True)
    web.git_lock.acquire()
    assert client.post("/api/inline-agent", json=body).status_code == 409
    assert not web.inline_agent_lock.locked()
    assert not web.dispatch_lock.locked()
    assert web.git_lock.locked()
    web.git_lock.release()
    assert TestClient(web.app).post("/api/inline-agent", json=body).status_code == 403
    assert client.post("/api/inline-agent", json=body, headers={"origin": "https://evil.example"}).status_code == 403
