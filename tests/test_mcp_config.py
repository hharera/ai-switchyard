import json
import tomllib

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from ninerouter_orchestrator import cli, web
from ninerouter_orchestrator.adapters.nine_router import opencode_runtime_env
from ninerouter_orchestrator.cli_config import CliProfile
from ninerouter_orchestrator.config import Settings
from ninerouter_orchestrator.mcp_catalog import mcp_catalog
from ninerouter_orchestrator.mcp_config import (
    McpConfigStore,
    McpConfiguration,
    McpServer,
    ToolMcpConfig,
)
from ninerouter_orchestrator.orchestrator import EngineeringOrchestrator
from ninerouter_orchestrator.web import app

client = TestClient(app, headers={"x-switchyard-client": "local-ui"})


def sample():
    return McpConfiguration(
        shared=ToolMcpConfig(
            servers=[
                McpServer(
                    name="docs",
                    catalog_id="context7",
                    transport="stdio",
                    command=["npx", "-y", "docs-mcp"],
                    env_vars=["DOCS_TOKEN"],
                )
            ]
        ),
        codex=ToolMcpConfig(
            servers=[
                McpServer(
                    name="jira",
                    transport="http",
                    url="https://example.com/mcp",
                    bearer_token_env_var="JIRA_TOKEN",
                )
            ]
        ),
        opencode=ToolMcpConfig(
            servers=[
                McpServer(
                    name="local",
                    transport="stdio",
                    command=["npx", "-y", "example-mcp"],
                    env_vars=["JIRA_TOKEN"],
                    enabled=False,
                )
            ]
        ),
    )


def test_mcp_store_round_trip(tmp_path):
    store = McpConfigStore(tmp_path / "mcps.json")
    assert store.get() == McpConfiguration()
    store.save(sample())
    assert store.get() == sample()


def test_mcp_api_validation_and_origin(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "mcp_store", McpConfigStore(tmp_path / "mcps.json"))
    assert client.put("/api/mcps", json=sample().model_dump()).status_code == 200
    assert client.get("/api/mcps").json() == sample().model_dump()
    assert (
        client.put("/api/mcps", json={}, headers={"origin": "https://evil.example"}).status_code
        == 403
    )
    bad = sample().model_dump()
    bad["codex"]["servers"][0]["url"] = "file:///etc/passwd"
    assert client.put("/api/mcps", json=bad).status_code == 422
    exported = client.get("/api/mcps/export/claude-code").json()
    assert exported["tool"] == "claude-code"
    assert "mcpServers" in exported["content"]
    assert client.get("/api/mcps/export/unsupported").status_code == 422
    catalog = client.get("/api/mcps/catalog").json()["servers"]
    assert len(catalog) == 100
    assert catalog[0] == {
        "id": "playwright-mcp", "name": "Playwright MCP", "rank": 1,
        "weekly_installs": 4_631_836,
    }


def test_legacy_tool_specific_file_loads_with_empty_shared_config(tmp_path):
    path = tmp_path / "mcps.json"
    legacy = sample().model_dump(exclude={"shared"})
    path.write_text(json.dumps(legacy), encoding="utf-8")
    loaded = McpConfigStore(path).get()
    assert loaded.shared.servers == []
    assert loaded.codex == sample().codex


def test_duplicate_names_rejected():
    server = sample().codex.servers[0]
    with pytest.raises(ValueError, match="unique"):
        ToolMcpConfig(servers=[server, server])


def test_supported_mcp_catalog_is_ranked_and_unique():
    catalog = mcp_catalog()
    assert [item.rank for item in catalog] == list(range(1, 101))
    assert len({item.id for item in catalog}) == 100
    assert len({item.name for item in catalog}) == 100
    assert catalog[-1].name == "Appium MCP"
    with pytest.raises(ValueError, match="unknown MCP catalog"):
        McpServer(
            name="unknown", catalog_id="not-in-the-catalog", transport="http",
            url="https://example.com/mcp",
        )


def test_native_tool_configuration(monkeypatch):
    config = sample()
    parsed = tomllib.loads("\n".join(config.for_tool("codex").codex_args()[1::2]))
    assert parsed["mcp_servers"]["jira"]["bearer_token_env_var"] == "JIRA_TOKEN"
    assert parsed["mcp_servers"]["docs"]["command"] == "npx"
    monkeypatch.setenv("JIRA_TOKEN", "test-secret")
    monkeypatch.setenv("DOCS_TOKEN", "docs-secret")
    native = json.loads(config.for_tool("opencode").opencode_env()["OPENCODE_CONFIG_CONTENT"])
    assert native["mcp"]["local"]["command"] == ["npx", "-y", "example-mcp"]
    assert native["mcp"]["local"]["enabled"] is False
    assert native["mcp"]["local"]["environment"]["JIRA_TOKEN"] == "test-secret"
    assert native["mcp"]["docs"]["environment"]["DOCS_TOKEN"] == "docs-secret"
    assert "test-secret" not in config.model_dump_json()
    assert "docs-secret" not in config.model_dump_json()


def test_tool_override_replaces_shared_server():
    shared = McpServer(name="docs", transport="http", url="https://shared.example/mcp")
    override = McpServer(name="docs", transport="http", url="https://codex.example/mcp")
    config = McpConfiguration(
        shared=ToolMcpConfig(servers=[shared]),
        codex=ToolMcpConfig(servers=[override]),
    )
    assert config.for_tool("codex").servers == [override]
    assert config.for_tool("opencode").servers == [shared]


def test_secret_free_cli_exports(monkeypatch):
    monkeypatch.setenv("DOCS_TOKEN", "docs-secret")
    config = sample()
    codex = config.export_for("codex")
    opencode = config.export_for("opencode")
    claude = config.export_for("claude-code")
    cursor = config.export_for("cursor")
    assert "mcp_servers.docs" in codex["content"]
    assert "${DOCS_TOKEN}" in claude["content"]
    assert "${env:DOCS_TOKEN}" in cursor["content"]
    assert "{env:DOCS_TOKEN}" in opencode["content"]
    assert "docs-secret" not in json.dumps([codex, opencode, claude, cursor])
    assert "catalog_id" not in json.dumps([codex, opencode, claude, cursor])


@pytest.mark.parametrize("item", mcp_catalog(), ids=lambda item: item.id)
@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_every_catalog_identity_can_be_saved_shared_and_exported(item, transport, tmp_path):
    server = McpServer(
        name=item.id, catalog_id=item.id, transport=transport,
        command=["test-mcp", "serve"] if transport == "stdio" else [],
        url="https://example.com/mcp" if transport == "http" else "",
        env_vars=["TEST_MCP_CREDENTIAL"],
    )
    config = McpConfiguration(shared=ToolMcpConfig(servers=[server]))
    store = McpConfigStore(tmp_path / "mcps.json")
    store.save(config)
    saved = store.get()
    assert saved.shared.servers[0].catalog_id == item.id
    for tool in ("codex", "opencode", "claude-code", "cursor"):
        assert saved.for_tool(tool).servers == [server]
        exported = saved.export_for(tool)["content"]
        native = tomllib.loads(exported) if tool == "codex" else json.loads(exported)
        key = "mcp_servers" if tool == "codex" else "mcp" if tool == "opencode" else "mcpServers"
        assert item.id in native[key]
        assert "catalog_id" not in exported


def test_cli_catalog_and_exports_use_shared_store(monkeypatch, tmp_path):
    store = McpConfigStore(tmp_path / "mcps.json")
    store.save(sample())
    monkeypatch.setattr(cli, "McpConfigStore", lambda: store)
    runner = CliRunner()
    catalog = runner.invoke(cli.app, ["mcp", "catalog"])
    assert catalog.exit_code == 0
    assert len(json.loads(catalog.stdout)) == 100
    filtered = runner.invoke(cli.app, ["mcp", "catalog", "--search", "APPium"])
    assert json.loads(filtered.stdout)[0]["id"] == "appium-mcp"
    assert json.loads(runner.invoke(cli.app, ["mcp", "catalog", "--search", "no-match"]).stdout) == []
    exported = runner.invoke(cli.app, ["mcp", "export", "claude-code"])
    assert exported.exit_code == 0
    assert json.loads(exported.stdout)["mcpServers"]["docs"]["command"] == "npx"
    assert runner.invoke(cli.app, ["mcp", "export", "unknown"]).exit_code != 0


def test_opencode_merges_shared_native_and_switchyard_servers(monkeypatch):
    monkeypatch.setenv("DOCS_TOKEN", "docs-secret")
    native = {"mcp": {"native": {"type": "remote", "url": "https://native.example"}}}
    result = opencode_runtime_env(
        CliProfile(command=["opencode"]),
        sample().for_tool("opencode"),
        {"OPENCODE_CONFIG_CONTENT": json.dumps(native)},
    )
    merged = json.loads(result["OPENCODE_CONFIG_CONTENT"])
    assert set(merged["mcp"]) == {"native", "docs", "local"}


def test_orchestrator_receives_tool_specific_settings(tmp_path):
    config = sample()
    runner = EngineeringOrchestrator(tmp_path, Settings(), mcp_config=config)
    assert runner.planner.mcps == config.for_tool("codex")
    assert runner.reviewer.mcps == config.for_tool("codex")
    assert runner.executor.mcps == config.for_tool("opencode")


def test_mcp_tab_is_served():
    page = client.get("/").text
    assert 'href="#mcps"' in page
    assert 'id="mcp-form"' in page
    assert 'id="mcp-catalog-search"' in page
    assert 'id="export-mcps"' in page
