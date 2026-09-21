from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from ..cli_config import CliProfile
from ..mcp_config import ToolMcpConfig
from ..models import ExecutionRequest
from ..process import ProcessError, run_process

SchemaT = TypeVar("SchemaT", bound=BaseModel)


def opencode_runtime_env(
    cli: CliProfile, mcps: ToolMcpConfig, shared_env: dict[str, str]
) -> dict[str, str]:
    config = cli.opencode_config()
    try:
        inherited = json.loads(shared_env.get("OPENCODE_CONFIG_CONTENT", "{}"))
        if not isinstance(inherited, dict):
            raise TypeError
    except (ValueError, TypeError) as exc:
        raise ProcessError("OPENCODE_CONFIG_CONTENT must contain a JSON object") from exc
    mcp_config = mcps.opencode_config(resolve_env=True)
    providers = dict(inherited.get("provider", {}))
    for provider, override in config.pop("provider", {}).items():
        existing = providers.get(provider, {})
        providers[provider] = {
            **existing, **override,
            "options": {**existing.get("options", {}), **override.get("options", {})},
            "models": {**existing.get("models", {}), **override.get("models", {})},
        }
    combined = {**inherited, **config}
    if providers:
        combined["provider"] = providers
    mcp_servers = {**inherited.get("mcp", {}), **mcp_config.get("mcp", {})}
    if mcp_servers:
        combined["mcp"] = mcp_servers
    if not combined:
        return shared_env
    return {**shared_env, "OPENCODE_CONFIG_CONTENT": json.dumps(combined)}


class NineRouterExecutor:
    """Repository-aware execution through OpenCode and a stable 9router combo name."""

    def __init__(
        self,
        *,
        timeout: int = 1800,
        mcps: ToolMcpConfig | None = None,
        cli: CliProfile | None = None,
        shared_env: dict[str, str] | None = None,
    ) -> None:
        self.timeout = timeout
        self.mcps = mcps or ToolMcpConfig()
        self.cli = cli or CliProfile(command=["opencode"])
        self.shared_env = shared_env or {}

    def runtime_env(self) -> dict[str, str]:
        return opencode_runtime_env(self.cli, self.mcps, self.shared_env)

    @staticmethod
    def model_id(combo: str) -> str:
        combo = combo.strip()
        if not combo or combo.startswith("/") or combo.endswith("/"):
            raise ValueError("9router combo names must be non-empty logical names")
        return f"9router/{combo}"

    def verify_combo(self, combo: str, *, cwd: Path) -> None:
        result = run_process(
            [*self.cli.command, "models", "9router"],
            cwd=cwd,
            timeout=60,
            env=self.runtime_env(),
        )
        if not result.passed:
            raise ProcessError("OpenCode cannot access the configured 9router provider")
        if self.model_id(combo) not in result.stdout.splitlines():
            raise ProcessError(f"9router combo is not available to OpenCode: {combo}")

    def execute(self, request: ExecutionRequest) -> str:
        prompt = self._prompt(request)
        result = run_process(
            [
                *self.cli.command,
                "run",
                "--auto",
                "--format",
                "json",
                "--model",
                self.model_id(request.combo),
                "--dir",
                str(request.workspace),
                prompt,
            ],
            cwd=request.workspace,
            timeout=self.timeout,
            env=self.runtime_env(),
        )
        if not result.passed:
            raise ProcessError(result.stderr.strip() or "9router execution failed")
        return result.stdout

    @staticmethod
    def _prompt(request: ExecutionRequest) -> str:
        criteria = "\n".join(f"- {item}" for item in request.ticket.acceptance_criteria)
        commands = (
            "\n".join(f"- {item}" for item in request.ticket.validation_commands)
            or "- Infer and run the relevant tests."
        )
        return f"""{request.system_prompt}

You are an implementation agent working in an isolated git worktree.

Ticket: {request.ticket.id} — {request.ticket.title}
Execution intent: {request.intent.value}

Task:
{request.ticket.description}

Acceptance criteria:
{criteria}

Validation commands:
{commands}

Implement the ticket completely in this worktree. Inspect existing project instructions first.
Run relevant checks, but do not create commits, merge branches, or modify files outside this worktree.
Keep the change scoped to the ticket and finish with a concise implementation summary.
"""


class NineRouterReasoner:
    """Structured read-only reasoning through OpenCode and a stable 9router combo."""

    def __init__(
        self,
        *,
        combo: str,
        timeout: int = 1800,
        mcps: ToolMcpConfig | None = None,
        cli: CliProfile | None = None,
        shared_env: dict[str, str] | None = None,
    ) -> None:
        self.combo = combo
        self.timeout = timeout
        self.mcps = mcps or ToolMcpConfig()
        self.cli = cli or CliProfile(command=["opencode"])
        self.shared_env = shared_env or {}

    def runtime_env(self) -> dict[str, str]:
        return opencode_runtime_env(self.cli, self.mcps, self.shared_env)

    def structured(self, prompt: str, schema: type[SchemaT], *, cwd: Path) -> SchemaT:
        schema_json = json.dumps(schema.model_json_schema(), separators=(",", ":"))
        instruction = f"""Work read-only. Return only one JSON object matching this JSON Schema.
Do not wrap the JSON in markdown.

JSON Schema:
{schema_json}

Task:
{prompt}
"""
        result = run_process(
            [
                *self.cli.command,
                "run",
                "--agent",
                "plan",
                "--format",
                "json",
                "--model",
                NineRouterExecutor.model_id(self.combo),
                "--dir",
                str(cwd),
                instruction,
            ],
            cwd=cwd,
            timeout=self.timeout,
            env=self.runtime_env(),
        )
        if not result.passed:
            raise ProcessError(result.stderr.strip() or "9router reasoning failed")
        text = self._text_from_events(result.stdout)
        return schema.model_validate_json(self._json_object(text))

    def respond(self, prompt: str, *, cwd: Path) -> str:
        # Plan alone can still request shell/MCP tools. Deny everything except reads.
        permissions = {"*": "deny", "read": "allow", "glob": "allow", "grep": "allow"}
        config = {
            **self.cli.opencode_config(),
            "permission": permissions,
            "agent": {"plan": {"permission": permissions}},
            "share": "disabled",
        }
        result = run_process(
            [*self.cli.command, "run", "--pure", "--agent", "plan", "--format", "json",
             "--model", NineRouterExecutor.model_id(self.combo), "--dir", str(cwd), prompt],
            cwd=cwd,
            timeout=self.timeout,
            env={**self.shared_env, "OPENCODE_CONFIG_CONTENT": json.dumps(config),
                 "OPENCODE_PERMISSION": json.dumps(permissions)},
        )
        if not result.passed:
            raise ProcessError("9router could not reply. Check OpenCode and the selected route.")
        parts: list[str] = []
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "error":
                raise ProcessError("9router reported an error. Check the route and try again.")
            part = event.get("part", event)
            if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                parts.append(str(part["text"]))
        return "\n".join(parts).strip()

    @staticmethod
    def _text_from_events(output: str) -> str:
        parts: list[str] = []
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            part = event.get("part", event)
            if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                parts.append(part["text"])
            elif event.get("type") == "text" and event.get("text"):
                parts.append(event["text"])
        return "\n".join(parts) or output

    @staticmethod
    def _json_object(text: str) -> str:
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
        if fenced:
            return fenced.group(1)
        decoder = json.JSONDecoder()
        for index, character in enumerate(text):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(text[index:])
                return json.dumps(value)
            except json.JSONDecodeError:
                continue
        raise ProcessError("9router response did not contain a valid JSON object")
