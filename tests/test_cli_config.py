import os
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ninerouter_orchestrator import web
from ninerouter_orchestrator.cli_config import (
    AdditionalCli,
    CliConfigStore,
    CliConfiguration,
    CliProfile,
)
from ninerouter_orchestrator.config import Settings
from ninerouter_orchestrator.orchestrator import EngineeringOrchestrator
from ninerouter_orchestrator.web import app

client = TestClient(app, headers={"x-switchyard-client": "local-ui"})


def sample():
    return CliConfiguration(
        codex=CliProfile(command=["npx", "-y", "codex-cli"]),
        opencode=CliProfile(command=["opencode-custom"]),
        path_entries=["/opt/shared/bin"],
        environment_vars=["JIRA_TOKEN", "GITHUB_TOKEN"],
        additional=[
            AdditionalCli(
                name="jira",
                command=["jira"],
                description="Read and update Jira issues",
                instructions="Ask before transitioning an issue.",
                enabled=True,
            )
        ],
    )


def test_cli_store_round_trip(tmp_path):
    store = CliConfigStore(tmp_path / "clis.json")
    assert store.get().codex.command == ["codex"]
    store.save(sample())
    assert store.get() == sample()


def test_cli_api(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "cli_store", CliConfigStore(tmp_path / "clis.json"))
    response = client.put("/api/clis", json=sample().model_dump())
    assert response.status_code == 200
    assert response.json()["codex"]["command"] == ["npx", "-y", "codex-cli"]
    assert "statuses" in response.json()
    assert response.json()["additional"][0]["name"] == "jira"
    assert len(response.json()["tools"]) == 40
    assert response.json()["statuses"]["tool:claude-code"]["command"] == ["claude"]
    assert client.get("/api/clis").status_code == 200
    assert client.post("/api/clis/check", json=sample().model_dump()).status_code == 200


def test_additional_cli_names_are_unique_and_reserved():
    profile = AdditionalCli(name="deepseek", command=["dsh"])
    with pytest.raises(ValueError, match="unique"):
        CliConfiguration(additional=[profile, profile])
    with pytest.raises(ValueError, match="cannot be"):
        CliConfiguration(additional=[AdditionalCli(name="Codex", command=["other"])])


def test_cli_runtime_resolves_shared_environment_without_storing_values(monkeypatch):
    monkeypatch.setenv("JIRA_TOKEN", "secret-value")
    config = sample()
    runtime = config.runtime_env()
    assert runtime["JIRA_TOKEN"] == "secret-value"
    assert runtime["PATH"].split(os.pathsep)[0] == str(Path("/opt/shared/bin"))
    assert "secret-value" not in config.model_dump_json()


def test_enabled_cli_tools_are_described_to_agents():
    prompt = sample().agent_tool_prompt()
    assert "jira" in prompt
    assert "Read and update Jira issues" in prompt
    assert "Ask before transitioning an issue" in prompt
    assert "credentials" in prompt
    disabled = CliConfiguration(
        additional=[AdditionalCli(name="wrangler", command=["wrangler"], enabled=False)]
    )
    assert disabled.agent_tool_prompt() == ""


def test_managed_catalog_includes_every_supported_tool():
    config = CliConfiguration()
    assert config.codex.command == ["codex"]
    assert {tool.name for tool in config.tools} == {
        "Claude Code", "Open Claw", "GitHub Copilot", "Claude Desktop", "Hermes Agent",
        "Factory Droid", "Cursor", "Cline", "Kilo Code", "Roo", "Continue", "Amp CLI",
        "Qwen Code", "DeepSeek TUI", "jcode", "Grok Build", "Devin Desktop / Windsurf",
        "OpenDesign", "Antigravity", "Kiro", "ChatGPT Desktop", "Gemini CLI",
        "JetBrains Junie", "Aider", "Ollama", "LM Studio", "Open WebUI", "AnythingLLM",
        "Jan", "GPT4All", "Msty Studio", "Chatbox AI", "llama.cpp", "KoboldCpp",
        "LocalAI", "Open Interpreter", "Fabric", "ComfyUI", "InvokeAI",
        "AUTOMATIC1111 WebUI",
    }
    reloaded = CliConfiguration.model_validate({"tools": []})
    assert len(reloaded.tools) == 40


def test_catalog_platform_compatibility_notes():
    tools = {tool.id: tool for tool in CliConfiguration().tools}
    assert tools["chatgpt-desktop"].platforms["linux"] == "preview"
    assert tools["claude-cowork"].platforms["linux"] == "beta"
    assert tools["localai"].platforms["windows"] == "wsl-docker"
    assert tools["invokeai"].platforms["macos"] == "limited"
    assert tools["automatic1111"].platforms["macos"] == "limited"


def test_detection_distinguishes_command_and_leftover_files(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    config = CliConfiguration()
    monkeypatch.setattr("ninerouter_orchestrator.cli_config.shutil.which", lambda *a, **kw: None)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.json").write_text("{}")
    status = config.statuses()["tool:claude-code"]
    assert status["available"] is True
    assert status["command_available"] is False
    assert status["detected_by"] == "path"
    assert Path(status["resolved"]) == tmp_path / ".claude/settings.json"


def test_detection_uses_configured_path_and_does_not_execute(monkeypatch, tmp_path):
    executable = tmp_path / ("my-claude.exe" if os.name == "nt" else "my-claude")
    executable.write_text("#!/bin/sh\nexit 99\n")
    executable.chmod(0o755)
    config = CliConfiguration(path_entries=[str(tmp_path)])
    config.tools[0].command = ["my-claude"]
    status = config.statuses()["tool:claude-code"]
    assert status["command_available"] is True
    assert Path(status["resolved"]) == executable


def test_legacy_custom_tool_names_do_not_overwrite_catalog_status(monkeypatch):
    monkeypatch.setattr("ninerouter_orchestrator.cli_config.shutil.which", lambda command, **kw: "/bin/claude" if command == "claude" else None)
    config = CliConfiguration(additional=[AdditionalCli(name="claude-code", command=["missing"])])
    assert config.statuses()["tool:claude-code"]["command_available"] is True
    assert config.statuses()["claude-code"]["command_available"] is False


def test_catalog_merge_preserves_settings_and_restores_trusted_metadata():
    catalog = {tool.id: tool for tool in CliConfiguration().tools}
    claude = catalog["claude-cowork"].model_dump()
    claude.update(
        provider="my-provider", model="my-model", enabled=True, name="Claude Cowork",
        platforms={"linux": "supported", "windows": "supported", "macos": "supported"},
    )
    devin = catalog["devin-cli"].model_dump()
    devin.update(provider="other-provider", model="other-model", enabled=True, name="Devin CLI")
    config = CliConfiguration.model_validate({"tools": [claude, devin]})
    migrated = {tool.id: tool for tool in config.tools}
    assert migrated["claude-cowork"].name == "Claude Desktop"
    assert migrated["claude-cowork"].provider == "my-provider"
    assert migrated["claude-cowork"].model == "my-model"
    assert migrated["claude-cowork"].enabled is True
    assert migrated["claude-cowork"].platforms["linux"] == "beta"
    assert migrated["devin-cli"].name == "Devin Desktop / Windsurf"
    assert migrated["devin-cli"].provider == "other-provider"
    assert migrated["devin-cli"].model == "other-model"
    assert migrated["devin-cli"].enabled is True
    assert len(config.tools) == 40


def test_all_tools_have_working_setup_guidance():
    for tool in CliConfiguration().tools:
        profile = {key: getattr(tool, key) for key in CliProfile.model_fields}
        response = client.post(f"/api/clis/setup/{tool.id}", json=profile)
        assert response.status_code == 200, tool.id
        assert response.json()["instructions"]
    assert client.post("/api/clis/setup/unknown", json={"command": ["tool"]}).status_code == 422


def test_cli_provider_settings_are_secret_free_and_runtime_ready(monkeypatch):
    monkeypatch.setenv("ROUTER_KEY", "secret-value")
    profile = CliProfile(
        command=["codex"], provider="switchyard", model="route/model",
        base_url="https://router.example/v1/", api_key_env="ROUTER_KEY",
    )
    parsed = tomllib.loads("\n".join(profile.codex_args()[1::2]))
    assert parsed["model_provider"] == "switchyard"
    assert parsed["model_providers"]["switchyard"]["base_url"] == "https://router.example/v1"
    assert parsed["model_providers"]["switchyard"]["env_key"] == "ROUTER_KEY"
    assert "secret-value" not in profile.model_dump_json()
    assert profile.opencode_config()["provider"]["switchyard"]["options"]["apiKey"] == "{env:ROUTER_KEY}"


def test_cli_setup_api_returns_instructions_without_applying_settings():
    profile = CliProfile(
        command=["claude"], provider="9router", model="Kimi",
        base_url="https://router.example/v1", api_key_env="ROUTER_KEY",
    )
    response = client.post("/api/clis/setup/claude-code", json=profile.model_dump())
    assert response.status_code == 200
    assert "ANTHROPIC_BASE_URL" in response.json()["content"]
    assert "ROUTER_KEY" in response.json()["content"]
    assert "secret" not in response.json()["content"].lower()


@pytest.mark.parametrize("base_url", ["file:///tmp/config", "https://user:pass@example.com/v1", "https://example.com/v1?token=x"])
def test_provider_endpoint_rejects_unsafe_urls(base_url):
    with pytest.raises(ValueError, match="Provider endpoint"):
        CliProfile(command=["tool"], base_url=base_url)


def test_orchestrator_uses_shared_cli_profiles(tmp_path):
    config = sample()
    runner = EngineeringOrchestrator(tmp_path, Settings(), cli_config=config)
    assert runner.planner.cli.command == config.codex.command
    assert runner.executor.cli.command == config.opencode.command
    assert runner.planner.shared_env["PATH"].split(os.pathsep)[0] == str(Path("/opt/shared/bin"))
    assert "Read and update Jira issues" in runner._step_prompt(runner.workflow.step("plan"))


def test_cli_tab_is_served():
    page = client.get("/").text
    assert 'href="#clis"' in page
    assert 'id="cli-form"' in page
    assert 'id="add-cli"' in page
    assert 'id="managed-cli-list"' in page
    assert 'id="cli-setup-dialog"' in page
    assert "AI tool catalog" in page
    assert "Detection does not confirm authentication" in page
    assert '<details class="cli-card integrated-cli-card" data-cli="codex">' in page
    assert '<details class="cli-card integrated-cli-card" data-cli="opencode">' in page
    assert '<details class="cli-card integrated-cli-card" data-cli="codex" open>' not in page
    assert '<details class="cli-card integrated-cli-card" data-cli="opencode" open>' not in page
