from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..cli_config import CliProfile
from ..process import ProcessError, run_process


@dataclass(frozen=True)
class WorkflowToolSpec:
    tool_type: str
    arguments: tuple[str, ...]
    prompt_flag: str | None = None
    model_flag: str | None = None
    effort_flag: str | None = None
    efforts: tuple[str, ...] = ()


WORKFLOW_TOOL_SPECS: dict[str, WorkflowToolSpec] = {
    "claude-code": WorkflowToolSpec(
        tool_type="AI tool",
        arguments=("--permission-mode", "bypassPermissions", "--output-format", "text"),
        prompt_flag="-p",
        model_flag="--model",
        effort_flag="--effort",
        efforts=("low", "medium", "high"),
    ),
    "gemini-cli": WorkflowToolSpec(
        tool_type="CLI", arguments=("--yolo",), prompt_flag="--prompt", model_flag="--model"
    ),
    "qwen-code": WorkflowToolSpec(
        tool_type="CLI", arguments=("--yolo",), prompt_flag="--prompt", model_flag="--model"
    ),
    "aider": WorkflowToolSpec(
        tool_type="CLI", arguments=("--yes-always",), prompt_flag="--message", model_flag="--model"
    ),
    "github-copilot": WorkflowToolSpec(
        tool_type="AI tool", arguments=("--allow-all-tools",), prompt_flag="-p", model_flag="--model"
    ),
    "amp-cli": WorkflowToolSpec(tool_type="CLI", arguments=("-x",), model_flag="--model"),
}


class WorkflowToolAdapter:
    """Runs a supported installed agent CLI in its non-interactive mode."""

    def __init__(
        self,
        tool_id: str,
        profile: CliProfile,
        *,
        timeout: int,
        shared_env: dict[str, str] | None = None,
    ) -> None:
        if tool_id not in WORKFLOW_TOOL_SPECS:
            raise ValueError(f"No workflow adapter is registered for {tool_id}")
        self.tool_id = tool_id
        self.profile = profile
        self.timeout = timeout
        self.shared_env = shared_env or {}

    def execute(
        self,
        prompt: str,
        *,
        cwd: Path,
        model: str | None = None,
        effort: str | None = None,
    ) -> str:
        spec = WORKFLOW_TOOL_SPECS[self.tool_id]
        command = [*self.profile.command, *spec.arguments]
        selected_model = model or self.profile.model or None
        if selected_model and spec.model_flag:
            command.extend([spec.model_flag, selected_model])
        if effort and spec.effort_flag:
            command.extend([spec.effort_flag, effort])
        if spec.prompt_flag:
            command.extend([spec.prompt_flag, prompt])
        else:
            command.append(prompt)
        result = run_process(
            command, cwd=cwd, timeout=self.timeout, env=self.shared_env
        )
        if not result.passed:
            detail = result.stderr.strip() or result.stdout.strip()
            raise ProcessError(detail or f"{self.tool_id} workflow step failed")
        output = result.stdout.strip()
        if not output:
            raise ProcessError(f"{self.tool_id} returned no workflow output")
        return output
