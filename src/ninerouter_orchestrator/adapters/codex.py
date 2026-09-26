from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from ..cli_config import CliProfile
from ..mcp_config import ToolMcpConfig
from ..process import ProcessError, run_process, stream_process

SchemaT = TypeVar("SchemaT", bound=BaseModel)


def strict_schema(schema: dict) -> dict:
    for value in schema.values():
        if isinstance(value, dict):
            strict_schema(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    strict_schema(item)
    if schema.get("type") == "object":
        schema["additionalProperties"] = False
        schema["required"] = list(schema.get("properties", {}))
    schema.pop("default", None)
    return schema


class CodexAdapter:
    """Uses the locally authenticated ChatGPT/Codex subscription."""

    def __init__(
        self,
        *,
        timeout: int = 1800,
        model: str | None = None,
        effort: str | None = None,
        mcps: ToolMcpConfig | None = None,
        cli: CliProfile | None = None,
        shared_env: dict[str, str] | None = None,
    ) -> None:
        self.timeout = timeout
        self.mcps = mcps or ToolMcpConfig()
        self.cli = cli or CliProfile(command=["codex"])
        self.model = model or self.cli.model or None
        self.effort = effort
        self.shared_env = shared_env or {}

    def structured(self, prompt: str, schema: type[SchemaT], *, cwd: Path) -> SchemaT:
        with tempfile.TemporaryDirectory(prefix="orchestrator-codex-") as temp_dir:
            temp = Path(temp_dir)
            schema_path = temp / "schema.json"
            output_path = temp / "output.json"
            schema_path.write_text(
                json.dumps(strict_schema(schema.model_json_schema())), encoding="utf-8"
            )
            command = [
                *self.cli.command,
                *self.cli.codex_args(),
                *self.mcps.codex_args(),
                "--ask-for-approval",
                "never",
                "exec",
                "--ignore-user-config",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "-C",
                str(cwd),
            ]
            if self.model:
                command.extend(["--model", self.model])
            if self.effort:
                command.extend(["-c", f"model_reasoning_effort={json.dumps(self.effort)}"])
            command.append(prompt)
            result = run_process(command, cwd=cwd, timeout=self.timeout, env=self.shared_env)
            if not result.passed:
                raise ProcessError(result.stderr.strip() or "Codex structured call failed")
            return schema.model_validate_json(output_path.read_text(encoding="utf-8"))

    def execute(self, prompt: str, *, cwd: Path) -> str:
        with tempfile.TemporaryDirectory(prefix="orchestrator-codex-exec-") as temp_dir:
            output_path = Path(temp_dir) / "output.txt"
            command = [
                *self.cli.command,
                *self.cli.codex_args(),
                *self.mcps.codex_args(),
                "--ask-for-approval",
                "never",
                "exec",
                "--ignore-user-config",
                "--sandbox",
                "workspace-write",
                "--output-last-message",
                str(output_path),
                "-C",
                str(cwd),
            ]
            if self.model:
                command.extend(["--model", self.model])
            if self.effort:
                command.extend(["-c", f"model_reasoning_effort={json.dumps(self.effort)}"])
            command.append(prompt)
            result = run_process(command, cwd=cwd, timeout=self.timeout, env=self.shared_env)
            if not result.passed:
                raise ProcessError(result.stderr.strip() or "Codex execution failed")
            return output_path.read_text(encoding="utf-8")

    def respond(self, prompt: str, *, cwd: Path, allow_changes: bool = False) -> str:
        # Chat deliberately omits MCPs: their remote writes bypass the local sandbox.
        with tempfile.TemporaryDirectory(prefix="orchestrator-codex-chat-") as temp_dir:
            output_path = Path(temp_dir) / "reply.txt"
            command = [
                *self.cli.command, *self.cli.codex_args(), "--ask-for-approval", "never", "exec",
                "--ignore-user-config", "--ignore-rules", "--ephemeral",
                "--skip-git-repo-check", "--sandbox", "workspace-write" if allow_changes else "read-only",
                "--output-last-message", str(output_path), "-C", str(cwd),
            ]
            if self.model:
                command.extend(["--model", self.model])
            if self.effort:
                command.extend(["-c", f"model_reasoning_effort={json.dumps(self.effort)}"])
            command.append(prompt)
            result = run_process(command, cwd=cwd, timeout=self.timeout, env=self.shared_env)
            if not result.passed:
                raise ProcessError("Codex could not reply. Check its login and CLI configuration.")
            if not output_path.exists():
                raise ProcessError("Codex returned no reply. Try sending the message again.")
            return output_path.read_text(encoding="utf-8").strip()

    def respond_stream(self, prompt: str, *, cwd: Path, emit) -> str:
        """Stream Codex's public progress events and return its final answer."""
        with tempfile.TemporaryDirectory(prefix="orchestrator-codex-chat-") as temp_dir:
            output_path = Path(temp_dir) / "reply.txt"
            command = [
                *self.cli.command, *self.cli.codex_args(), "--ask-for-approval", "never", "exec",
                "--ignore-user-config", "--ignore-rules", "--ephemeral", "--json",
                "--skip-git-repo-check", "--sandbox", "read-only",
                "--output-last-message", str(output_path), "-C", str(cwd),
            ]
            if self.model:
                command.extend(["--model", self.model])
            if self.effort:
                command.extend(["-c", f"model_reasoning_effort={json.dumps(self.effort)}"])
            command.append(prompt)

            def handle(line: str) -> None:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    return
                item = event.get("item") if isinstance(event, dict) else None
                if not isinstance(item, dict):
                    return
                kind = item.get("type")
                text = str(item.get("text") or "").strip()
                if kind == "reasoning" and text:
                    emit({"type": "thinking", "content": text})
                elif event.get("type") == "item.started" and kind == "command_execution":
                    command_text = str(item.get("command") or "Inspecting the workspace")
                    emit({"type": "step", "label": "Inspecting", "detail": command_text[:500]})
                elif event.get("type") == "item.completed" and kind == "agent_message" and text:
                    emit({"type": "delta", "content": text})

            stream_process(
                command, cwd=cwd, timeout=self.timeout, env=self.shared_env, on_line=handle
            )
            if not output_path.exists():
                raise ProcessError("Codex returned no reply")
            return output_path.read_text(encoding="utf-8").strip()
