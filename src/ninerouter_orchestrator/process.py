from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from .models import CommandResult


class ProcessError(RuntimeError):
    pass


def format_command(command: Sequence[str]) -> str:
    return subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)


def shell_argv(command: str, *, windows: bool | None = None) -> list[str]:
    is_windows = windows if windows is not None else os.name == "nt"
    if is_windows:
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command]
    shell = os.environ.get("SWITCHYARD_SHELL") or find_tool("bash") or "/bin/sh"
    return [shell, "-lc", command]


def windows_command(command: Sequence[str], env: Mapping[str, str]) -> list[str]:
    """Bypass npm's batch shim so prompts stay literal arguments, never cmd.exe input."""
    executable = find_tool(command[0], env.get("PATH"))
    if not executable:
        return list(command)
    if Path(executable).suffix.lower() not in {".cmd", ".bat"}:
        return [executable, *command[1:]]
    shim = Path(executable)
    content = shim.read_text(encoding="utf-8") if shim.stat().st_size <= 65536 else ""
    match = re.search(
        r'"%(?:dp0%[\\/]+|~dp0[\\/]*)([^"\r\n]+?\.(?:c?js|mjs))"', content, re.IGNORECASE
    )
    if match and "node" in content.lower():
        script = shim.parent / match[1].replace("\\", os.sep)
        local_node = shim.parent / "node.exe"
        node = str(local_node) if local_node.is_file() else find_tool("node", env.get("PATH"))
        if script.is_file() and node:
            return [node, str(script), *command[1:]]
    raise ProcessError(
        f"Cannot safely pass arguments to batch wrapper {shim.name}. "
        "Configure the tool using its native executable or [node.exe, path-to-cli.js]."
    )


def tool_path() -> str:
    current = os.environ.get("PATH", "")
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        candidates = [Path(appdata) / "npm"] if appdata else []
    else:
        candidates = sorted((Path.home() / ".nvm/versions/node").glob("*/bin"), reverse=True)
    return os.pathsep.join([current, *(str(path) for path in candidates)])


def find_tool(name: str, path: str | None = None) -> str | None:
    return shutil.which(name, path=path or tool_path())


def run_process(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: int,
    env: Mapping[str, str] | None = None,
    check: bool = False,
) -> CommandResult:
    started = time.monotonic()
    process_env = os.environ.copy()
    process_env["PATH"] = tool_path()
    if env:
        process_env.update(env)
    try:
        completed = subprocess.run(
            windows_command(command, process_env) if os.name == "nt" else list(command),
            cwd=cwd,
            env=process_env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProcessError(f"Command timed out after {timeout}s: {format_command(command)}") from exc
    except OSError as exc:
        raise ProcessError(f"Cannot start {command[0]}. Check its installation and PATH: {exc}") from exc
    result = CommandResult(
        command=format_command(command),
        return_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_seconds=time.monotonic() - started,
    )
    if check and not result.passed:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ProcessError(f"Command failed ({result.return_code}): {result.command}\n{detail}")
    return result


def run_shell(command: str, *, cwd: Path, timeout: int) -> CommandResult:
    return run_process(shell_argv(command), cwd=cwd, timeout=timeout)
