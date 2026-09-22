import os
import sys
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from ninerouter_orchestrator.command_api import command_router
from ninerouter_orchestrator.command_console import CommandConsole, ConsoleError, command_argv
from ninerouter_orchestrator.process import format_command
from ninerouter_orchestrator.workspace_config import Workspace


def python_command(code):
    return format_command([sys.executable, "-c", code])


def wait_for(manager, workspace_id, run_id, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = manager.get_run(workspace_id, run_id)
        if run["status"] not in {"running", "stopping"}:
            return run
        time.sleep(0.02)
    raise AssertionError("command did not finish")


@pytest.fixture
def console(tmp_path):
    manager = CommandConsole(tmp_path / "commands.sqlite3")
    yield manager
    manager.close()


def test_command_tabs_run_capture_and_persist_history(console, tmp_path):
    tab = console.create_tab("alpha", "Tests")
    run = console.start(
        "alpha", tab["id"], tmp_path,
        python_command("import sys; print('stdout'); print('stderr', file=sys.stderr)"), 30,
    )

    finished = wait_for(console, "alpha", run["id"])

    assert finished["status"] == "completed"
    assert finished["exit_code"] == 0
    assert finished["output"].replace("\r\n", "\n") == "stdout\nstderr\n"
    assert console.tabs("alpha")[0]["latest"]["status"] == "completed"
    assert console.history("alpha", tab["id"])["runs"][0]["command"] == run["command"]


def test_command_can_be_stopped_and_active_tab_cannot_be_removed(console, tmp_path):
    tab = console.create_tab("alpha", "Server")
    run = console.start(
        "alpha", tab["id"], tmp_path, python_command("import time; time.sleep(30)"), 0
    )

    with pytest.raises(ConsoleError, match="Stop the running command"):
        console.remove_tab("alpha", tab["id"])
    assert console.stop("alpha", run["id"])["status"] == "stopping"
    finished = wait_for(console, "alpha", run["id"])

    assert finished["status"] == "stopped"
    assert finished["exit_code"] != 0
    console.remove_tab("alpha", tab["id"])
    assert console.tabs("alpha") == []


def test_command_timeout_and_restart_state(console, tmp_path):
    tab = console.create_tab("alpha", "Timeout")
    run = console.start(
        "alpha", tab["id"], tmp_path, python_command("import time; time.sleep(30)"), 1
    )
    assert wait_for(console, "alpha", run["id"])["status"] == "timed_out"

    console._db().execute(
        "UPDATE runs SET status='running', ended_at=NULL WHERE id=?", (run["id"],)
    )
    console._db().commit()
    console.db.close()
    console.db = None
    reopened = CommandConsole(console.path)
    try:
        assert reopened.get_run("alpha", run["id"])["status"] == "interrupted"
    finally:
        reopened.close()


def test_command_output_is_bounded(console, tmp_path):
    tab = console.create_tab("alpha", "Large output")
    run = console.start(
        "alpha", tab["id"], tmp_path, python_command("print('x' * 300000)"), 30
    )

    finished = wait_for(console, "alpha", run["id"])

    assert finished["status"] == "completed"
    assert finished["truncated"] == 1
    assert len(finished["output"]) == 262_144


def test_commands_run_in_parallel_across_workspaces_and_reject_overlap(console, tmp_path):
    alpha = console.create_tab("alpha", "Server")
    beta = console.create_tab("beta", "Tests")
    active = console.start(
        "alpha", alpha["id"], tmp_path,
        python_command("import time; print('ready', end='', flush=True); time.sleep(30)"), 0,
    )
    with pytest.raises(ConsoleError, match="already has a running"):
        console.start("alpha", alpha["id"], tmp_path, python_command("print('rejected')"), 0)
    other = console.start(
        "beta", beta["id"], tmp_path,
        python_command("import os, sys; print(os.getcwd()); sys.exit(7)"), 0,
    )
    finished = wait_for(console, "beta", other["id"])
    assert finished["status"] == "failed"
    assert finished["exit_code"] == 7
    assert finished["output"].strip() == str(tmp_path)
    assert console.get_run("alpha", active["id"])["status"] == "running"
    assert console.statuses() == [{"workspace_id": "alpha", "status": "running", "count": 1}]
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if "ready" in console.get_run("alpha", active["id"])["output"]:
            break
        time.sleep(0.02)
    assert console.get_run("alpha", active["id"])["output"] == "ready"
    console.close()
    assert console.get_run("alpha", active["id"])["status"] == "interrupted"


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal escalation test")
def test_stop_escalates_for_children_that_ignore_termination(console, tmp_path):
    tab = console.create_tab("alpha", "Child process")
    run = console.start(
        "alpha", tab["id"], tmp_path,
        "trap '' TERM; sleep 30 & echo ready; wait", 0,
    )
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if "ready" in console.get_run("alpha", run["id"])["output"]:
            break
        time.sleep(0.02)
    owned = console.running[run["id"]]
    console.stop("alpha", run["id"])
    assert wait_for(console, "alpha", run["id"])["status"] == "stopped"
    assert owned.process.poll() is not None


def test_console_read_routes_reject_cross_origin_and_missing_client_header():
    from ninerouter_orchestrator.web import app

    client = TestClient(app)
    for path in ("/api/commands/status", "/api/commands/tabs?workspace_id=alpha",
                 "/api/commands/runs/example?workspace_id=alpha"):
        assert client.get(path).status_code == 403
        assert client.get(path, headers={
            "x-switchyard-client": "local-ui", "origin": "https://evil.example",
        }).status_code == 403


def test_command_api_requires_confirmation_and_scopes_history(console, tmp_path):
    workspaces = {
        "alpha": Workspace(id="alpha", name="Alpha", repository=str(tmp_path)),
        "beta": Workspace(id="beta", name="Beta", repository=str(tmp_path)),
    }

    def workspace(identifier):
        if identifier not in workspaces:
            raise HTTPException(422, "Choose an existing workspace")
        return workspaces[identifier]

    app = FastAPI()
    app.include_router(command_router(
        console, workspace, lambda: SimpleNamespace(runtime_env=dict),
        lambda: SimpleNamespace(workspaces=list(workspaces.values())),
    ))
    client = TestClient(app)
    tab = client.post("/api/commands/tabs?workspace_id=alpha", json={"name": "Build"}).json()

    assert client.post(
        f"/api/commands/tabs/{tab['id']}/runs?workspace_id=alpha",
        json={"command": python_command("print('api', end='')"), "confirmed": False},
    ).status_code == 422
    response = client.post(
        f"/api/commands/tabs/{tab['id']}/runs?workspace_id=alpha",
        json={"command": python_command("print('api', end='')"), "confirmed": True},
    )
    assert response.status_code == 202
    run_id = response.json()["id"]
    wait_for(console, "alpha", run_id)
    assert client.get(
        f"/api/commands/runs/{run_id}?workspace_id=alpha"
    ).json()["output"] == "api"
    assert client.get(
        f"/api/commands/runs/{run_id}?workspace_id=beta"
    ).status_code == 409
    assert client.request(
        "DELETE", f"/api/commands/tabs/{tab['id']}?workspace_id=alpha",
        json={"confirmed": False},
    ).status_code == 422
    assert client.request(
        "DELETE", f"/api/commands/tabs/{tab['id']}?workspace_id=alpha",
        json={"confirmed": True},
    ).json() == {"removed": True}


def test_command_console_ui_is_served():
    from ninerouter_orchestrator.web import app

    client = TestClient(app)
    page = client.get("/").text
    script = client.get("/assets/command-console.js").text
    css = client.get("/assets/styles.css").text

    assert 'data-panel="commands"' in page
    assert 'role="tablist" aria-label="Command tabs"' in page
    assert 'id="command-output-text"' in page
    assert 'src="/assets/command-console.js"' in page
    assert "Bash on macOS and Linux or cmd.exe on Windows" in page
    commands = page.split('id="commands-panel"', 1)[1].split('<section class="hero"', 1)[0]
    assert "Workspace command center" not in commands
    assert '<summary>How commands run</summary>' in commands
    assert commands.index('id="command-refresh"') < commands.index('id="command-tab-content"')
    assert "Unable to reach Switchyard" in script
    assert 'window.addEventListener("workspacechange"' in script
    assert "command-workspace-chip" in css
    assert TestClient(app).get("/api/commands/status").status_code == 403


def test_command_shell_argv_is_native(monkeypatch):
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    assert command_argv("npm test", windows=True) == [
        r"C:\Windows\System32\cmd.exe", "/d", "/s", "/c", "npm test"
    ]
    monkeypatch.setattr(
        "ninerouter_orchestrator.command_console.find_tool", lambda name: "/bin/bash"
    )
    assert command_argv("npm test", windows=False) == [
        "/bin/bash", "--noprofile", "--norc", "-c", "npm test"
    ]


def test_missing_shell_records_failure(console, tmp_path, monkeypatch):
    def missing_shell(*args):
        raise OSError("Shell not found")

    monkeypatch.setattr("ninerouter_orchestrator.command_console.command_argv", missing_shell)
    tab = console.create_tab("alpha", "Tests")
    run = console.start("alpha", tab["id"], tmp_path, "echo example", 0)
    assert run["status"] == "failed"
    assert "Shell not found" in run["output"]
    assert not console.running


def test_completed_shell_cleans_up_background_children(console, tmp_path):
    script = tmp_path / "parent with spaces.py"
    script.write_text(
        "import subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-u', '-c', "
        "\"import time; print('child-ready', flush=True); time.sleep(30)\"], "
        "stdout=subprocess.PIPE)\n"
        "print(child.stdout.readline().decode().strip(), flush=True)\n",
        encoding="utf-8",
    )
    tab = console.create_tab("alpha", "Background")
    run = console.start("alpha", tab["id"], tmp_path, format_command([sys.executable, str(script)]), 0)
    finished = wait_for(console, "alpha", run["id"])
    assert finished["status"] == "completed"
    assert "child-ready" in finished["output"]
    assert not console.running
