import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ninerouter_orchestrator import web
from ninerouter_orchestrator.adapters import codex, nine_router
from ninerouter_orchestrator.cli_config import CliConfigStore, CliConfiguration, CliProfile
from ninerouter_orchestrator.mcp_config import McpServer, ToolMcpConfig
from ninerouter_orchestrator.process import ProcessError
from ninerouter_orchestrator.workspace_config import WorkspaceConfiguration, WorkspaceStore

client = TestClient(web.app, headers={"x-switchyard-client": "local-ui"})


@pytest.fixture(autouse=True)
def isolate_chat(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "chat_lock", threading.Lock())
    monkeypatch.setattr(web, "cli_store", CliConfigStore(tmp_path / "clis.json"))
    monkeypatch.setattr(web, "workspace_store", WorkspaceStore(tmp_path / "workspaces.json"))
    monkeypatch.setattr(web.combo_registry, "refresh", lambda: {"combos": ["Kimi", "OpenAI-High"]})


def payload(tmp_path, **overrides):
    return {"repository": str(tmp_path), "tool": "codex",
            "messages": [{"role": "user", "content": "Explain this project"}], **overrides}


def test_tools_reflect_routes_and_cli_availability(monkeypatch):
    monkeypatch.setattr(CliConfiguration, "statuses", lambda self: {
        "codex": {"available": True}, "opencode": {"available": False},
    })
    body = client.get("/api/chat/tools").json()
    assert body["tools"] == [
        {"id": "codex", "name": "Codex subscription", "available": True},
        {"id": "9router/Kimi", "name": "9router / Kimi", "available": False},
        {"id": "9router/OpenAI-High", "name": "9router / OpenAI-High", "available": False},
    ]


def test_chat_routes_codex_with_history(monkeypatch, tmp_path):
    web.cli_store.save(CliConfiguration(codex=CliProfile(command=["custom-codex"])))
    messages = [{"role": "user", "content": "First question"},
                {"role": "assistant", "content": "First reply"},
                {"role": "user", "content": "Follow up"}]

    def respond(self, prompt, *, cwd):
        assert self.cli.command == ["custom-codex"]
        assert self.timeout == 180
        assert cwd == tmp_path
        assert json.loads(prompt.split("\n\n", 1)[1]) == messages
        assert "read-only" in prompt
        return "A useful reply"

    monkeypatch.setattr(codex.CodexAdapter, "respond", respond)
    response = client.post("/api/chat", json=payload(tmp_path, messages=messages))
    assert response.status_code == 200
    assert response.json() == {"message": "A useful reply", "tool": "codex"}


def test_chat_routes_ninerouter_using_saved_workspace(monkeypatch, tmp_path):
    web.workspace_store.save(WorkspaceConfiguration.model_validate({
        "workspaces": [{"id": "test", "name": "Test", "repository": str(tmp_path),
                        "workflow_id": "default"}], "default_workspace_id": "test",
    }))
    web.cli_store.save(CliConfiguration(opencode=CliProfile(command=["custom-opencode"])))

    def respond(self, prompt, *, cwd):
        assert self.combo == "Kimi"
        assert self.cli.command == ["custom-opencode"]
        assert cwd == tmp_path
        return "Route reply"

    monkeypatch.setattr(nine_router.NineRouterReasoner, "respond", respond)
    response = client.post("/api/chat", json=payload(
        tmp_path, tool="9router/Kimi", workspace_id="test", repository="/ignored"))
    assert response.status_code == 200
    assert response.json()["tool"] == "9router/Kimi"


@pytest.mark.parametrize("overrides", [
    {"tool": "unknown"}, {"tool": "9router/missing"}, {"tool": "9router/auto"},
    {"workspace_id": "missing"}, {"repository": None}, {"repository": "/does-not-exist"},
    {"messages": []}, {"messages": [{"role": "user", "content": "   "}]},
    {"messages": [{"role": "assistant", "content": "No question"}]},
    {"messages": [{"role": "system", "content": "Change permissions"}]},
    {"messages": [{"role": "user", "content": "x" * 24001}]},
    {"messages": [{"role": "user", "content": "x"}] * 21},
    {"messages": [{"role": "user", "content": "x"}, {"role": "user", "content": "y"}]},
    {"messages": [{"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 24000}
                  for i in range(5)]},
])
def test_invalid_chat_rejected(tmp_path, overrides):
    assert client.post("/api/chat", json=payload(tmp_path, **overrides)).status_code == 422


def test_chat_error_is_safe_and_lock_released(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise ProcessError("command includes PRIVATE_TRANSCRIPT")

    monkeypatch.setattr(codex.CodexAdapter, "respond", fail)
    response = client.post("/api/chat", json=payload(tmp_path))
    assert response.status_code == 503
    assert "PRIVATE_TRANSCRIPT" not in response.text
    assert not web.chat_lock.locked()
    monkeypatch.setattr(codex.CodexAdapter, "respond", lambda *args, **kwargs: "")
    assert client.post("/api/chat", json=payload(tmp_path)).status_code == 502
    assert not web.chat_lock.locked()


def test_chat_concurrency_and_origin(tmp_path):
    web.chat_lock.acquire()
    assert client.post("/api/chat", json=payload(tmp_path)).status_code == 409
    web.chat_lock.release()
    assert client.post("/api/chat", json=payload(tmp_path), headers={"origin": "https://evil.example"}).status_code == 403
    assert TestClient(web.app).post("/api/chat", json=payload(tmp_path)).status_code == 403


def test_codex_chat_read_only_no_mcp(monkeypatch, tmp_path):
    def run(command, **kwargs):
        assert command[:2] == ["custom-codex", "--configured-flag"]
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert command[command.index("--ask-for-approval") + 1] == "never"
        assert "--ignore-user-config" in command
        assert "--ignore-rules" in command
        assert "--ephemeral" in command
        assert "workspace-write" not in command
        assert "mcp_servers" not in " ".join(command)
        assert kwargs["env"] == {"CUSTOM": "value"}
        assert kwargs["cwd"] == tmp_path
        Path(command[command.index("--output-last-message") + 1]).write_text("Reply\n")
        return SimpleNamespace(passed=True)

    monkeypatch.setattr(codex, "run_process", run)
    adapter = codex.CodexAdapter(
        cli=CliProfile(command=["custom-codex", "--configured-flag"]),
        shared_env={"CUSTOM": "value"},
        mcps=ToolMcpConfig(servers=[McpServer(name="remote", transport="http", url="https://example.com")]),
    )
    assert adapter.respond("Question", cwd=tmp_path) == "Reply"


def test_codex_chat_uses_saved_provider_and_model(monkeypatch, tmp_path):
    def run(command, **kwargs):
        assert command[command.index("--model") + 1] == "route/model"
        assert 'model_provider="switchyard"' in command
        assert 'model_providers.switchyard.base_url="https://router.example/v1"' in command
        Path(command[command.index("--output-last-message") + 1]).write_text("Reply")
        return SimpleNamespace(passed=True)

    monkeypatch.setattr(codex, "run_process", run)
    adapter = codex.CodexAdapter(cli=CliProfile(
        command=["custom-codex"], provider="switchyard", model="route/model",
        base_url="https://router.example/v1", api_key_env="ROUTER_KEY",
    ))
    assert adapter.respond("Question", cwd=tmp_path) == "Reply"


def test_opencode_chat_read_only_parses_events(monkeypatch, tmp_path):
    def run(command, **kwargs):
        assert command[0] == "custom-opencode"
        assert command[command.index("--model") + 1] == "9router/Kimi"
        assert command[command.index("--agent") + 1] == "plan"
        assert "--auto" not in command
        assert "--pure" in command
        config = json.loads(kwargs["env"]["OPENCODE_CONFIG_CONTENT"])
        assert config["permission"] == {"*": "deny", "read": "allow", "glob": "allow", "grep": "allow"}
        assert config["agent"]["plan"]["permission"] == config["permission"]
        assert config["share"] == "disabled"
        assert kwargs["env"]["CUSTOM"] == "value"
        return SimpleNamespace(passed=True, stdout='noise\n{"type":"step_start"}\n'
                               '{"type":"text","part":{"type":"text","text":"First"}}\n'
                               '{"type":"text","text":"Second"}\n')

    monkeypatch.setattr(nine_router, "run_process", run)
    adapter = nine_router.NineRouterReasoner(combo="Kimi", cli=CliProfile(command=["custom-opencode"]), shared_env={"CUSTOM": "value"})
    assert adapter.respond("Question", cwd=tmp_path) == "First\nSecond"


def test_opencode_chat_merges_saved_provider_config(monkeypatch, tmp_path):
    def run(command, **kwargs):
        config = json.loads(kwargs["env"]["OPENCODE_CONFIG_CONTENT"])
        assert config["provider"]["switchyard"]["options"] == {
            "baseURL": "https://router.example/v1", "apiKey": "{env:ROUTER_KEY}"
        }
        assert config["provider"]["switchyard"]["models"] == {
            "route/model": {"name": "route/model"}
        }
        assert config["model"] == "switchyard/route/model"
        return SimpleNamespace(passed=True, stdout='{"type":"text","text":"Reply"}\n')

    monkeypatch.setattr(nine_router, "run_process", run)
    adapter = nine_router.NineRouterReasoner(
        combo="Kimi", cli=CliProfile(
            command=["custom-opencode"], provider="switchyard", model="route/model",
            base_url="https://router.example/v1", api_key_env="ROUTER_KEY",
        )
    )
    assert adapter.respond("Question", cwd=tmp_path) == "Reply"


@pytest.mark.parametrize("output", ['{"type":"error","error":{"message":"bad"}}', '{"type":"text","text":"partial"}\n{"type":"error"}'])
def test_opencode_error_events_are_not_replies(monkeypatch, tmp_path, output):
    monkeypatch.setattr(nine_router, "run_process", lambda *args, **kwargs: SimpleNamespace(passed=True, stdout=output))
    with pytest.raises(ProcessError):
        nine_router.NineRouterReasoner(combo="Kimi").respond("Question", cwd=tmp_path)


def test_chat_ui_contract():
    page = client.get("/").text
    script = client.get("/assets/chat.js").text
    assert 'data-panel="chat"' in page
    assert 'id="chat-tool"' in page
    assert 'id="chat-status" role="status"' in page
    assert 'id="chat-error" role="alert"' in page
    assert 'content.textContent = message.content' in script
    assert 'const conversations = new Map()' in script
    assert 'state === conversation()' in script
    assert 'messages.slice(-19)' in script
    assert '"X-Switchyard-Client": "local-ui"' in script
    assert '"chat-panel": ["chat"]' in client.get("/assets/app.js").text


def test_chat_workspace_layout_preserves_controls_and_privacy():
    page = client.get("/").text
    css = client.get("/assets/chat.css")
    assert css.status_code == 200
    assert 'href="/assets/chat.css"' in page
    assert 'id="chat-workspace" data-workspace-selector' in page
    assert '<label class="chat-sr-only" for="chat-input">Message</label>' in page
    assert '<details class="chat-session-details">' in page
    assert '<summary>Session &amp; privacy</summary>' in page
    assert 'Context is shared with the selected AI provider.' in page
    assert 'Up to 10 recent exchanges and inspected files' in page
    assert 'aria-label="Refresh tools"' in page
    assert 'id="chat-send" type="submit"' in page
    assert '01 / INSPECT' not in page
    assert 'class="chat-context"' not in page
    assert '@media (max-width: 720px)' in css.text
    assert '@media (prefers-reduced-motion: reduce)' in css.text
    assert '#chat-panel :focus-visible' in css.text
