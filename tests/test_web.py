import subprocess
import threading

import pytest
from fastapi.testclient import TestClient

from ninerouter_orchestrator import web
from ninerouter_orchestrator.job_store import JobStore
from ninerouter_orchestrator.web import app
from ninerouter_orchestrator.workflow_config import WorkflowStore

client = TestClient(app, headers={"x-switchyard-client": "local-ui"})


def test_main_opens_the_default_browser(monkeypatch):
    opened = []
    started = []
    monkeypatch.setattr(web.webbrowser, "open_new_tab", opened.append)
    monkeypatch.setattr(web.uvicorn, "run", lambda *args, **kwargs: started.append((args, kwargs)))

    web.main()

    assert opened == ["http://127.0.0.1:8765"]
    assert started == [
        (("ninerouter_orchestrator.web:app",), {"host": "127.0.0.1", "port": 8765})
    ]


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


def test_completed_job_stream_sends_latest_snapshot_and_closes(monkeypatch, tmp_path):
    job_store = JobStore(tmp_path / "jobs.json")
    monkeypatch.setattr(web, "store", job_store)
    created = job_store.create(repository=str(tmp_path), request="Stream progress", workflow={})
    job_store.update(created["id"], status="completed", stage="Finished", result={"ok": True})

    response = client.get(f"/api/jobs/{created['id']}/events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: job" in response.text
    assert '"status":"completed"' in response.text
    assert '"stage":"Finished"' in response.text


def test_job_store_records_ordered_activity(tmp_path):
    job_store = JobStore(tmp_path / "jobs.json")
    created = job_store.create(repository=str(tmp_path), request="Track stages", workflow={})

    job_store.update(created["id"], stage="Planning the implementation")
    updated = job_store.update(created["id"], stage="Running candidates for T1")

    assert updated["revision"] == 3
    assert [item["message"] for item in updated["activity"]] == [
        "Planning the implementation", "Running candidates for T1",
    ]


def test_home_page_loads():
    response = client.get("/")
    script = client.get("/assets/app.js").text
    assert response.status_code == 200
    assert "9router Switchyard" in response.text
    assert '<a class="sidebar-logo" href="#overview"><span class="brand-mark" aria-hidden="true"><img src="/assets/favicon.svg" alt="" width="34" height="34"></span><span>SwitchYard</span></a>' in response.text
    assert "Dispatch a change" in response.text
    assert "Scope and safety" in response.text
    assert "MCP servers" in response.text
    mcps = response.text.split('id="mcps-panel"', 1)[1].split('id="clis-panel"', 1)[0]
    assert "Global tool connections" not in mcps
    assert "Scope: all repositories" not in mcps
    assert "Only add trusted servers" in mcps
    assert '<details class="configuration-note">' in mcps
    assert 'shared: "Shared servers"' in script
    assert 'class="mcp-tool mcp-overrides"' in script
    assert "Use this list for connections both runtimes should receive" not in script
    assert "No overrides for this tool" not in script
    assert 'mcpForm.addEventListener("invalid"' in script
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
    assert 'class="template-heading"' in script
    assert 'class="template-fields"' in script
    assert 'class="template-footer"' in script
    assert 'id="template-count"' in page
    assert ">Step category</label>" in script
    assert "Categories do not control execution or order." in script
    assert "Code safety gate" not in script
    assert "Deterministic code" not in script
    assert 'title: "Remove template?"' in script
    assert "Replace those references and save the workflows" in script
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
    assert "Shared across all workspaces" in page
    assert "No workspace is needed to create or edit a workflow" in page
    assert "How to build a workflow" in page
    assert "Workflow to edit" in page
    assert "New workflow" in page
    assert "Add steps and drag them into order" in page
    assert '<option value="">Choose a workflow</option>' in script
    assert "selectedWorkflowId = null" in script
    assert "Choose where to start" in script
    assert "Create workflow" in script
    assert 'name: "", isolated_worktree: true, steps' in script
    assert 'const steps = [];' in script
    assert "Unsaved empty workflow" in script
    assert 'draggable="true"' in script
    assert "addTemplateToWorkflow" in script
    assert "moveWorkflowStep" in script
    assert "data-edit-workflow" in script
    assert "Discard all changes" in script
    assert "drop-ready" in script


def test_active_workspace_is_global_sidebar_context():
    page = client.get("/").text
    script = client.get("/assets/app.js").text
    assert 'class="sidebar-workspace" id="workspace-context"' in page
    assert page.index('id="workspace-context"') < page.index('<nav class="side-nav">')
    assert '"workspace-context":' not in script
    assert 'workflows: ["Workflows", "Build shared pipelines that any workspace can use."]' in script


def test_dispatch_can_run_with_or_without_a_saved_workflow(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "workflow_store", WorkflowStore(tmp_path / "workflow.json"))
    monkeypatch.setattr(web, "store", JobStore(tmp_path / "jobs.json"))
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


def test_dispatch_is_queued_while_another_dispatch_is_active(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "workflow_store", WorkflowStore(tmp_path / "workflow.json"))
    monkeypatch.setattr(web, "store", JobStore(tmp_path / "jobs.json"))
    monkeypatch.setattr(web, "dispatch_lock", threading.Lock())
    monkeypatch.setattr(web, "_work", lambda *args, **kwargs: None)
    web.dispatch_lock.acquire()
    try:
        response = client.post("/api/jobs", json={
            "repository": str(tmp_path), "request": "Queue this implementation",
            "allow_host_execution": True,
        })
    finally:
        web.dispatch_lock.release()

    assert response.status_code == 202
    assert response.json()["status"] == "queued"


def test_completed_dispatch_can_create_linked_followup(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "store", JobStore(tmp_path / "jobs.json"))
    monkeypatch.setattr(web, "_work", lambda *args, **kwargs: None)
    repository = tmp_path / "repo"
    repository.mkdir()
    result = subprocess.run(
        ["git", "init", "--initial-branch", "main"], cwd=repository,
        check=True, capture_output=True, text=True,
    )
    assert result.returncode == 0
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit",
         "--allow-empty", "-m", "baseline"],
        cwd=repository, check=True, capture_output=True, text=True,
    )
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    workflow = web.default_recipe().model_dump()
    parent = web.store.create(
        repository=str(repository), request="Build the account settings screen", workflow=workflow,
    )
    web.store.update(
        parent["id"], status="completed", result={
            "status": "completed", "base": commit, "commit": commit,
            "steps": [{"name": "Implement", "output": "Added the settings screen."}],
        }, mcp_config={}, cli_config={}, delivery={"mode": "pr"}, run_settings={},
    )

    response = client.post(f"/api/jobs/{parent['id']}/followups", json={
        "message": "Also cover the empty state with tests",
        "allow_host_execution": True,
    })

    assert response.status_code == 202
    followup = response.json()
    assert followup["followup_of"] == parent["id"]
    assert followup["followup_root"] == parent["id"]
    assert followup["followup_base"] == commit
    assert followup["delivery"]["mode"] == "none"
    assert "Build the account settings screen" in followup["execution_request"]
    assert "Also cover the empty state with tests" in followup["execution_request"]
    assert followup["original_request"] == parent["request"]

    for invalid in (
        {"message": "          ", "allow_host_execution": True},
        {"message": "Make another small change", "allow_host_execution": False},
    ):
        rejected = client.post(f"/api/jobs/{parent['id']}/followups", json=invalid)
        assert rejected.status_code == 422
    assert len(web.store.list()) == 2


def test_followup_rejects_active_or_failed_dispatch(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "store", JobStore(tmp_path / "jobs.json"))
    workflow = web.default_recipe().model_dump()
    active = web.store.create(repository=str(tmp_path), request="Active dispatch", workflow=workflow)
    response = client.post(f"/api/jobs/{active['id']}/followups", json={
        "message": "Continue this active dispatch", "allow_host_execution": True,
    })
    assert response.status_code == 409

    web.store.update(active["id"], status="failed", result={"status": "failed"})
    response = client.post(f"/api/jobs/{active['id']}/followups", json={
        "message": "Continue this failed dispatch", "allow_host_execution": True,
    })
    assert response.status_code == 422
    assert "preserved worktree" in response.json()["detail"]


@pytest.mark.parametrize("fail_first", [False, True])
def test_queued_dispatch_runs_after_active_dispatch_finishes(monkeypatch, tmp_path, fail_first):
    monkeypatch.setattr(web, "workflow_store", WorkflowStore(tmp_path / "workflow.json"))
    monkeypatch.setattr(web, "store", JobStore(tmp_path / "jobs.json"))
    monkeypatch.setattr(web, "dispatch_lock", threading.Lock())
    first_started = threading.Event()
    finish_first = threading.Event()
    second_started = threading.Event()
    workers = []
    work = web._work

    def tracked_work(*args):
        workers.append(threading.current_thread())
        work(*args)

    class Orchestrator:
        def __init__(self, *args):
            pass

        def run(self, request):
            if request == "First implementation":
                first_started.set()
                assert finish_first.wait(5)
                if fail_first:
                    raise RuntimeError("First run failed")
            else:
                second_started.set()
            return {"status": "needs_repair"}

        def save_run(self, result):
            pass

    monkeypatch.setattr(web, "_work", tracked_work)
    monkeypatch.setattr(web, "EngineeringOrchestrator", Orchestrator)
    payload = {"repository": str(tmp_path), "allow_host_execution": True}
    try:
        first = client.post("/api/jobs", json={**payload, "request": "First implementation"})
        assert first.status_code == 202
        assert first_started.wait(5)
        second = client.post("/api/jobs", json={**payload, "request": "Second implementation"})
        assert second.status_code == 202
        assert web.store.get(second.json()["id"])["status"] == "queued"
        assert not second_started.is_set()
    finally:
        finish_first.set()
        for worker in workers:
            worker.join(5)

    assert all(not worker.is_alive() for worker in workers)
    assert second_started.is_set()
    assert web.store.get(first.json()["id"])["status"] == (
        "failed" if fail_first else "needs_repair"
    )
    assert web.store.get(second.json()["id"])["status"] == "needs_repair"
    assert not web.dispatch_lock.locked()


def test_dispatch_setup_failure_does_not_hold_execution_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "workflow_store", WorkflowStore(tmp_path / "workflow.json"))
    monkeypatch.setattr(web, "store", JobStore(tmp_path / "jobs.json"))
    monkeypatch.setattr(web, "dispatch_lock", threading.Lock())

    def fail_create(**kwargs):
        raise OSError("Cannot save job")

    monkeypatch.setattr(web.store, "create", fail_create)
    with pytest.raises(OSError, match="Cannot save job"):
        client.post("/api/jobs", json={
            "repository": str(tmp_path), "request": "Create implementation",
            "allow_host_execution": True,
        })
    assert not web.dispatch_lock.locked()


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


def test_dispatch_accepts_single_step_workflow(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "workflow_store", WorkflowStore(tmp_path / "workflow.json"))
    monkeypatch.setattr(web, "store", JobStore(tmp_path / "jobs.json"))
    config = web.workflow_store.get_config()
    config.workflows[0].steps = config.workflows[0].steps[:1]
    web.workflow_store.save_config(config)
    monkeypatch.setattr(web, "_work", lambda *args: None)
    response = client.post("/api/jobs", json={
        "repository": str(tmp_path), "request": "Implement a trusted change",
        "allow_host_execution": True,
    })
    assert response.status_code == 202
    assert len(response.json()["workflow"]["steps"]) == 1


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
    monkeypatch.setattr(web, "_work", lambda *args, **kwargs: None)
    monkeypatch.setattr(web, "health", lambda: {"combos": [], "ready": True})
    config = client.get("/api/workflows").json()
    workflow = config["workflows"][0]
    workflow["name"] = "Edited workflow"
    workflow["steps"].reverse()
    workflow["steps"][0]["system_prompt"] = "Review security boundaries."
    config["workflows"].append({
        "id": "draft", "name": "Empty draft", "isolated_worktree": False, "steps": [],
    })
    payload = {key: config[key] for key in ("workflows", "default_workflow_id")}
    response = client.put("/api/workflows", json=payload)
    assert response.status_code == 200
    reopened = client.get("/api/workflows").json()
    assert reopened["workflows"] == payload["workflows"]
    assert reopened["workflows"][1]["isolated_worktree"] is False
    assert reopened["steps"] == config["steps"]
    dispatched = client.post("/api/jobs", json={
        "repository": str(tmp_path), "request": "Run the reordered workflow",
        "allow_host_execution": True, "workflow_id": workflow["id"],
    })
    assert dispatched.status_code == 202


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
