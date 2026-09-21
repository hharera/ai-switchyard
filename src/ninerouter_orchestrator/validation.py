from __future__ import annotations

from pathlib import Path

from .git import GitRepository
from .models import ValidationReport
from .process import run_shell


def validate_candidate(
    repository: GitRepository,
    workspace: Path,
    commands: list[str],
    *,
    timeout: int,
) -> ValidationReport:
    effective_commands = commands or ["git diff --check"]
    results = [run_shell(command, cwd=workspace, timeout=timeout) for command in effective_commands]
    return ValidationReport(commands=results, changed_files=repository.changed_files(workspace))
