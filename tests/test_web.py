import subprocess

import pytest
from fastapi.testclient import TestClient

from ninerouter_orchestrator import web
from ninerouter_orchestrator.job_store import JobStore
from ninerouter_orchestrator.web import app
from ninerouter_orchestrator.workflow_config import WorkflowStore

client = TestClient(app, headers={"x-switchyard-client": "local-ui"})


def test_folder_picker_selects_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "_folder_picker_command", lambda: ["picker", "--directory"])
    monkeypatch.setattr(
        web.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, str(tmp_path) + "\n", ""),
    )
    assert client.post("/api/repository/pick").json() == {"path": str(tmp_path)}


def test_folder_picker_cancel_and_origin(monkeypatch):
    monkeypatch.setattr(web, "_folder_picker_command", lambda: ["picker", "--directory"])
    monkeypatch.setattr(
        web.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess([], 1, "", "")
    )
    assert client.post("/api/repository/pick").json() == {"path": None}
    assert (
        client.post("/api/repository/pick", headers={"origin": "https://evil.example"}).status_code
        == 403
    )


def test_folder_picker_unavailable(monkeypatch):
    monkeypatch.setattr(web, "_folder_picker_command", lambda: None)
    assert client.post("/api/repository/pick").status_code == 503


@pytest.mark.parametrize(
    ("platform", "available", "expected"),
    [
        ("linux", "/usr/bin/zenity", ["/usr/bin/zenity", "--file-selection", "--directory", "--title=Select repository folder"]),
        ("darwin", "/usr/bin/osascript", ["/usr/bin/osascript", "-e", 'POSIX path of (choose folder with prompt "Select repository folder")']),
    ],
)
def test_native_folder_picker_commands(monkeypatch, platform, available, expected):
    monkeypatch.setattr(web.sys, "platform", platform)
    monkeypatch.setattr(web, "find_tool", lambda name: available)
    assert web._folder_picker_command() == expected


def test_windows_folder_picker_command(monkeypatch):
    monkeypatch.setattr(web.sys, "platform", "win32")
    monkeypatch.setattr(web, "find_tool", lambda name: "C:/Windows/powershell.exe" if name == "powershell.exe" else None)
    command = web._folder_picker_command()
    assert command[:4] == [
        "C:/Windows/powershell.exe", "-NoProfile", "-NonInteractive", "-STA"
    ]
    assert "BrowseForFolder" in command[-1]


def test_folder_browser_lists_directories(tmp_path):
    (tmp_path / "Zulu").mkdir()
    (tmp_path / "alpha").mkdir()
    (tmp_path / "file.txt").write_text("not a folder")

    response = client.post("/api/repository/folders", json={"path": str(tmp_path)})

    assert response.status_code == 200
    assert response.json() == {
        "path": str(tmp_path),
        "parent": str(tmp_path.parent),
        "folders": [
            {"name": "alpha", "path": str(tmp_path / "alpha")},
            {"name": "Zulu", "path": str(tmp_path / "Zulu")},
        ],
    }


def test_folder_browser_rejects_missing_and_relative_paths(tmp_path):
    assert client.post("/api/repository/folders", json={"path": "relative"}).status_code == 422
    assert (
        client.post("/api/repository/folders", json={"path": str(tmp_path / "missing")}).status_code
        == 404
    )


def test_folder_browser_home_file_and_permissions(monkeypatch, tmp_path):
    monkeypatch.setattr(web.Path, "home", classmethod(lambda cls: tmp_path))
    assert client.post("/api/repository/folders", json={}).json()["path"] == str(tmp_path)
    file = tmp_path / "file.txt"
    file.write_text("not a folder")
    assert client.post("/api/repository/folders", json={"path": str(file)}).status_code == 422

    def denied(path):
        raise PermissionError("denied")

    monkeypatch.setattr(web.Path, "iterdir", denied)
    assert client.post("/api/repository/folders", json={"path": str(tmp_path)}).status_code == 403


def test_folder_browser_requires_local_ui():
    assert TestClient(app).post("/api/repository/folders", json={}).status_code == 403
    assert client.post(
        "/api/repository/folders", headers={"origin": "https://evil.example"}, json={}
    ).status_code == 403


def test_cross_origin_dispatch_is_rejected():
    response = client.post("/api/jobs", headers={"origin": "https://untrusted.example"}, json={})
    assert response.status_code == 403


def test_home_page_loads():
    response = client.get("/")
    assert response.status_code == 200
    assert "9router Switchyard" in response.text
    assert "Dispatch a change" in response.text
    assert "Scope: all repositories" in response.text
    assert "Shared MCP servers" in response.text
    assert "AI runtimes and tools" in response.text
    assert 'class="run-drawer"' in response.text
    assert '<dialog id="run-dialog">' not in response.text
    assert '<dialog id="folder-browser"' in response.text
    assert 'label for="folder-browser-search"' in response.text
    assert 'id="folder-browser-search" type="search"' in response.text
    assert 'id="folder-browser-clear-search"' in response.text


def test_settings_tabs_do_not_show_console_grid():
    css = client.get("/assets/styles.css").text
    assert '#workspace:not([data-view="overview"]) .console-grid' not in css
    for view in ("git", "steps", "workflows", "mcps", "clis", "delivery"):
        assert f'#workspace[data-view="{view}"] .console-grid {{ display: none; }}' in css
    assert "[hidden] { display: none !important; }" in css
    script = client.get("/assets/app.js").text
    assert '"runs-panel": ["overview", "runs"]' in script
    assert "hidden = !visibleIn.includes(key)" in script


def test_template_catalog_is_not_a_workflow_sequence():
    page = client.get("/").text
    script = client.get("/assets/app.js").text
    css = client.get("/assets/styles.css").text
    assert '<ul class="step-catalog" id="step-catalog"' in page
    assert "No execution order here" in page
    assert '<li class="template-card"' in script
    assert ">Step type</label>" in script
    assert "Determines compatible workflow phases, not a template order." in script
    assert 'class="workflow-step"' not in script
    assert "route-step" not in css
    assert 'class="phase-number"' in script  # Sequence belongs to workflow bindings.


def test_catalog_order_does_not_change_workflow_execution(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "workflow_store", WorkflowStore(tmp_path / "workflow.json"))
    monkeypatch.setattr(web, "health", lambda: {"combos": [], "ready": True})
    original = web.workflow_store.get().model_dump()
    templates = client.get("/api/steps").json()["steps"]
    independent = {**templates[0], "id": "independent-plan", "name": "Another planner"}
    catalog = [independent, *reversed(templates)]
    response = client.put("/api/steps", json={"steps": catalog})
    assert response.status_code == 200
    assert response.json()["steps"] == catalog
    assert web.workflow_store.get().model_dump() == original


def test_workflow_editor_starts_empty_and_new_workflows_are_unassigned():
    page = client.get("/").text
    script = client.get("/assets/app.js").text
    assert "Build a workflow" in page
    assert "How to build a workflow" in page
    assert "Workflow to edit" in page
    assert "New workflow" in page
    assert "Add steps and drag them into order" in page
    assert '<option value="">Choose a workflow</option>' in script
    assert "selectedWorkflowId = null" in script
    assert "Choose where to start" in script
    assert "Create workflow" in script
    assert 'name: "", steps' in script
    assert 'const steps = [];' in script
    assert "Unsaved empty workflow" in script
    assert 'draggable="true"' in script
    assert "addTemplateToWorkflow" in script
    assert "moveWorkflowStep" in script
    assert "data-edit-workflow" in script
    assert "Discard all changes" in script
    assert "drop-ready" in script


def test_dispatch_can_run_with_or_without_a_saved_workflow(monkeypatch, tmp_path):
    class ImmediateApprovalLock:
        def acquire(self, blocking=False):
            return True

    monkeypatch.setattr(web, "workflow_store", WorkflowStore(tmp_path / "workflow.json"))
    monkeypatch.setattr(web, "store", JobStore(tmp_path / "jobs.json"))
    monkeypatch.setattr(web, "dispatch_lock", ImmediateApprovalLock())
    monkeypatch.setattr(web, "_work", lambda *args, **kwargs: None)
    config = web.workflow_store.get_config()
    config.workflows[0].steps[0].system_prompt = "Custom saved planning instructions."
    web.workflow_store.save_config(config)

    without = client.post("/api/jobs", json={
        "repository": str(tmp_path), "request": "Create a safe implementation plan",
        "allow_host_execution": True, "workflow_id": None,
    })
    assert without.status_code == 202
    assert "mode" not in without.json()
    assert without.json()["workflow_id"] is None
    assert without.json()["workflow_source"] == "built_in"
    assert without.json()["workflow"]["name"] == "Built-in engineering route (no saved workflow)"
    assert without.json()["workflow"]["steps"][0]["system_prompt"] != "Custom saved planning instructions."
    assert web.store.get(without.json()["id"])["workflow_source"] == "built_in"

    named = client.post("/api/jobs", json={
        "repository": str(tmp_path), "request": "Create another safe implementation plan",
        "allow_host_execution": True, "workflow_id": "default",
    })
    assert named.status_code == 202
    assert named.json()["workflow_id"] == "default"
    assert named.json()["workflow_source"] == "saved"
    assert named.json()["workflow"]["name"] == "Default engineering route"
    assert named.json()["workflow"]["steps"][0]["system_prompt"] == "Custom saved planning instructions."

    inherited = client.post("/api/jobs", json={
        "repository": str(tmp_path), "request": "Create a backwards compatible plan",
        "allow_host_execution": True,
    })
    assert inherited.status_code == 202
    assert inherited.json()["workflow_id"] == "default"

    config.workflows[0].steps = []
    web.workflow_store.save_config(config)
    bypass = client.post("/api/jobs", json={
        "repository": str(tmp_path), "request": "Execute the built-in engineering route",
        "allow_host_execution": True, "workflow_id": "",
    })
    assert bypass.status_code == 202
    assert bypass.json()["workflow_id"] is None
    assert len(bypass.json()["workflow"]["steps"]) == 6
    missing = client.post("/api/jobs", json={
        "repository": str(tmp_path), "request": "Create a safe implementation plan",
        "allow_host_execution": True, "workflow_id": "missing",
    })
    assert missing.status_code == 422

    page = client.get("/").text
    script = client.get("/assets/app.js").text
    assert "Workflow (optional)" in page
    assert "Without a workflow - built-in steps" in page
    assert 'workflow_id: data.get("workflow_id") || null' in script
    assert "Built-in engineering steps" in script


def test_job_requires_existing_repository():
    response = client.post(
        "/api/jobs",
        json={
            "repository": "/definitely/missing/repository",
            "request": "Create a safe implementation plan",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Choose an existing repository directory"


def test_run_requires_host_confirmation(tmp_path):
    response = client.post(
        "/api/jobs",
        json={
            "repository": str(tmp_path),
            "request": "Execute a trusted implementation request",
            "allow_host_execution": False,
        },
    )
    assert response.status_code == 422
    assert "Confirm host execution" in response.json()["detail"]


@pytest.mark.parametrize("mode", ["plan", "run"])
def test_obsolete_job_mode_is_rejected(mode, tmp_path):
    response = client.post("/api/jobs", json={
        "repository": str(tmp_path), "request": "Implement a trusted change",
        "allow_host_execution": True, "mode": mode,
    })
    assert response.status_code == 422
    assert any(error["loc"] == ["body", "mode"] for error in response.json()["detail"])


def test_dispatch_controls_and_schema_have_no_mode():
    page = client.get("/").text
    assert "Switchyard records existing non-ignored files in a local baseline commit" in page
    script = client.get("/assets/app.js").text
    workspaces = client.get("/assets/workspaces.js").text
    css = client.get("/assets/styles.css").text
    assert "Dispatch mode" not in page
    assert "workspace-mode" not in page + workspaces
    assert 'name="mode"' not in page + workspaces
    assert 'get("mode")' not in script
    assert "payload.mode" not in script
    assert "default_mode" not in workspaces
    assert ".mode-card" not in css
    assert ".host-confirm { display: grid;" in css
    assert ".delivery-options { display: block;" in css
    schema = client.get("/openapi.json").json()["components"]["schemas"]
    assert "mode" not in schema["JobRequest"]["properties"]
    assert "default_mode" not in schema["Workspace"]["properties"]


def test_dispatch_rejects_plan_only_workflow(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "workflow_store", WorkflowStore(tmp_path / "workflow.json"))
    monkeypatch.setattr(web, "store", JobStore(tmp_path / "jobs.json"))
    config = web.workflow_store.get_config()
    config.workflows[0].steps = config.workflows[0].steps[:1]
    web.workflow_store.save_config(config)
    response = client.post("/api/jobs", json={
        "repository": str(tmp_path), "request": "Implement a trusted change",
        "allow_host_execution": True,
    })
    assert response.status_code == 422
    assert "runner currently requires" in response.json()["detail"]
    assert web.store.list() == []


def test_workflow_can_be_saved(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "workflow_store", WorkflowStore(tmp_path / "workflow.json"))
    monkeypatch.setattr(
        web,
        "health",
        lambda: {"combos": ["OpenCode-Go"], "ready": True},
    )
    recipe = client.get("/api/workflow").json()
    recipe["name"] = "Custom route"
    recipe["steps"][0]["engine"] = "9router/OpenCode-Go"
    response = client.put("/api/workflow", json=recipe)
    assert response.status_code == 200
    assert response.json()["name"] == "Custom route"
    assert response.json()["steps"][0]["engine"] == "9router/OpenCode-Go"


def test_steps_and_workflows_are_separate_global_resources(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "workflow_store", WorkflowStore(tmp_path / "workflow.json"))
    monkeypatch.setattr(web, "health", lambda: {"combos": ["Kimi"], "ready": True})
    config = client.get("/api/workflows").json()
    assert config["default_workflow_id"] == "default"
    assert len(config["steps"]) == 6
    config["workflows"][0]["steps"][5]["engine"] = "9router/Kimi"
    response = client.put(
        "/api/workflows",
        json={
            "workflows": config["workflows"],
            "default_workflow_id": "default",
        },
    )
    assert response.status_code == 200
    assert web.workflow_store.get().step("review").engine == "9router/Kimi"


def test_workflow_rejects_unavailable_override_combo(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "workflow_store", WorkflowStore(tmp_path / "workflow.json"))
    monkeypatch.setattr(web, "health", lambda: {"combos": [], "ready": True})
    config = client.get("/api/workflows").json()
    config["workflows"][0]["steps"][0]["engine"] = "9router/Missing"
    response = client.put(
        "/api/workflows",
        json={
            "workflows": config["workflows"],
            "default_workflow_id": "default",
        },
    )
    assert response.status_code == 422
    assert "Missing" in response.json()["detail"]


def test_edit_workflow_persists_order_overrides_and_drafts(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "workflow_store", WorkflowStore(tmp_path / "workflow.json"))
    monkeypatch.setattr(web, "health", lambda: {"combos": [], "ready": True})
    config = client.get("/api/workflows").json()
    workflow = config["workflows"][0]
    workflow["name"] = "Edited workflow"
    workflow["steps"].reverse()
    workflow["steps"][0]["system_prompt"] = "Review security boundaries."
    config["workflows"].append({"id": "draft", "name": "Empty draft", "steps": []})
    payload = {key: config[key] for key in ("workflows", "default_workflow_id")}
    response = client.put("/api/workflows", json=payload)
    assert response.status_code == 200
    reopened = client.get("/api/workflows").json()
    assert reopened["workflows"] == payload["workflows"]
    assert reopened["steps"] == config["steps"]
    blocked = client.post("/api/jobs", json={
        "repository": str(tmp_path), "request": "Run the reordered workflow",
        "allow_host_execution": True, "workflow_id": workflow["id"],
    })
    assert blocked.status_code == 422
    assert "runner currently requires" in blocked.json()["detail"]


def test_combo_refresh_reflects_registry(monkeypatch):
    monkeypatch.setattr(
        web.combo_registry,
        "refresh",
        lambda: {
            "combos": ["Kimi", "OpenCode-Go"],
            "added": ["Kimi"],
            "removed": [],
            "source": "test registry",
            "refreshed_at": "2026-09-21T00:00:00+00:00",
        },
    )
    response = client.post("/api/9router/refresh")
    assert response.status_code == 200
    assert response.json()["added"] == ["Kimi"]
    assert response.json()["combos"] == ["Kimi", "OpenCode-Go"]
