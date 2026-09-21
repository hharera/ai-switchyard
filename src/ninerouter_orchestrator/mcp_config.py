from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .mcp_catalog import catalog_item
from .models import StrictModel
from .paths import data_dir

McpTool = Literal["codex", "opencode", "claude-code", "cursor"]


class McpServer(StrictModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    catalog_id: str | None = Field(default=None, pattern=r"^[a-z0-9-]+$")
    transport: Literal["http", "stdio"]
    enabled: bool = True
    url: str = ""
    command: list[str] = Field(default_factory=list)
    env_vars: list[str] = Field(default_factory=list)
    bearer_token_env_var: str | None = None

    @model_validator(mode="after")
    def validate_transport(self):
        self.env_vars = list(dict.fromkeys(item.strip() for item in self.env_vars if item.strip()))
        if self.transport == "http" and not self.url.startswith(("http://", "https://")):
            raise ValueError(f"{self.name} needs an http:// or https:// URL")
        if self.transport == "stdio" and not self.command:
            raise ValueError(f"{self.name} needs a command")
        if any(not item.strip() for item in self.command):
            raise ValueError(f"{self.name} needs nonempty command arguments")
        if self.catalog_id and not catalog_item(self.catalog_id):
            raise ValueError(f"{self.name} references an unknown MCP catalog entry")
        return self


class ToolMcpConfig(StrictModel):
    servers: list[McpServer] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_names(self):
        names = [server.name for server in self.servers]
        if len(names) != len(set(names)):
            raise ValueError("MCP server names must be unique within each tool")
        return self

    def codex_args(self) -> list[str]:
        args: list[str] = []
        for server in self.servers:
            prefix = f"mcp_servers.{server.name}"
            values: list[tuple[str, object]] = [("enabled", server.enabled)]
            if server.transport == "http":
                values.append(("url", server.url))
                if server.bearer_token_env_var:
                    values.append(("bearer_token_env_var", server.bearer_token_env_var))
            else:
                values.append(("command", server.command[0]))
                values.append(("args", server.command[1:]))
            if server.env_vars:
                values.append(("env_vars", server.env_vars))
            for key, value in values:
                args.extend(["-c", f"{prefix}.{key}={json.dumps(value)}"])
        return args

    def opencode_config(self, *, resolve_env: bool = False) -> dict:
        servers: dict[str, dict] = {}
        for server in self.servers:
            if server.transport == "http":
                entry: dict = {"type": "remote", "url": server.url, "enabled": server.enabled}
                if server.bearer_token_env_var:
                    entry["headers"] = {
                        "Authorization": f"Bearer {{env:{server.bearer_token_env_var}}}"
                    }
            else:
                entry = {
                    "type": "local",
                    "command": server.command,
                    "enabled": server.enabled,
                }
                environment = {
                    name: os.environ[name] if resolve_env else f"{{env:{name}}}"
                    for name in server.env_vars if not resolve_env or name in os.environ
                }
                if environment:
                    entry["environment"] = environment
            servers[server.name] = entry
        return {"mcp": servers} if servers else {}

    def opencode_env(self) -> dict[str, str]:
        config = self.opencode_config(resolve_env=True)
        return {"OPENCODE_CONFIG_CONTENT": json.dumps(config)} if config else {}


class McpConfiguration(StrictModel):
    shared: ToolMcpConfig = Field(default_factory=ToolMcpConfig)
    codex: ToolMcpConfig = Field(default_factory=ToolMcpConfig)
    opencode: ToolMcpConfig = Field(default_factory=ToolMcpConfig)

    def for_tool(self, tool: McpTool) -> ToolMcpConfig:
        if tool not in ("codex", "opencode", "claude-code", "cursor"):
            raise ValueError("Unsupported MCP tool")
        # Replace whole definitions, so changing transports never retains stale fields.
        servers = {server.name: server for server in self.shared.servers}
        if tool in ("codex", "opencode"):
            servers.update({server.name: server for server in getattr(self, tool).servers})
        return ToolMcpConfig(servers=[server.model_copy(deep=True) for server in servers.values()])

    def export_for(self, tool: McpTool) -> dict:
        config = self.for_tool(tool)
        warnings = [
            (
                "Export only: native settings are not changed or kept in sync. Merge by server "
                "name; preserve unrelated settings. Set credential variables in the tool's "
                "environment. OAuth sessions are not shared; authorize each client separately."
            )
        ]
        if tool == "codex":
            content = "\n".join(config.codex_args()[1::2])
            destination = "~/.codex/config.toml (TOML fragment)"
        elif tool == "opencode":
            content = json.dumps(config.opencode_config(), indent=2)
            destination = "~/.config/opencode/opencode.json (JSON fragment)"
        else:
            servers = {}
            for server in config.servers:
                if not server.enabled:
                    warnings.append(
                        f"{server.name} is disabled and omitted. Remove any existing native entry "
                        "to disable it there."
                    )
                    continue
                def reference(name: str) -> str:
                    return f"${{{name}}}" if tool == "claude-code" else f"${{env:{name}}}"

                if server.transport == "http":
                    entry = {"url": server.url}
                    if tool == "claude-code":
                        entry["type"] = "http"
                    if server.bearer_token_env_var:
                        entry["headers"] = {
                            "Authorization": f"Bearer {reference(server.bearer_token_env_var)}"
                        }
                else:
                    entry = {"command": server.command[0], "args": server.command[1:]}
                    if server.env_vars:
                        entry["env"] = {name: reference(name) for name in server.env_vars}
                servers[server.name] = entry
            content = json.dumps({"mcpServers": servers}, indent=2)
            destination = (
                ".mcp.json in the project (Claude Code)" if tool == "claude-code"
                else "~/.cursor/mcp.json or .cursor/mcp.json in the project"
            )
        return {
            "tool": tool, "destination": destination, "content": content,
            "warnings": warnings,
        }


class McpConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "mcps.json"
        self.lock = threading.Lock()

    def get(self) -> McpConfiguration:
        with self.lock:
            if not self.path.exists():
                return McpConfiguration()
            try:
                return McpConfiguration.model_validate_json(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return McpConfiguration()

    def save(self, config: McpConfiguration) -> McpConfiguration:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(config.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(self.path)
        return config
