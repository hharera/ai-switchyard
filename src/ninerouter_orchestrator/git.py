from __future__ import annotations

import re
from pathlib import Path

from .process import ProcessError, run_process


def slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-.").lower()
    return cleaned or "ticket"


class GitRepository:
    def __init__(self, root: Path, *, timeout: int = 120) -> None:
        self.root = root.resolve()
        self.timeout = timeout

    def git(self, *args: str, cwd: Path | None = None, check: bool = True):
        return run_process(["git", *args], cwd=cwd or self.root, timeout=self.timeout, check=check)

    def validate(self) -> None:
        result = self.git("rev-parse", "--show-toplevel")
        if Path(result.stdout.strip()).resolve() != self.root:
            raise ProcessError(f"Expected repository root {self.root}, got {result.stdout.strip()}")

    def initialize(self, branch: str = "main") -> bool:
        """Initialize only an ordinary folder, never a nested or damaged repository."""
        if not self.root.is_dir():
            raise ProcessError("Choose an existing workspace folder.")
        result = run_process(
            ["git", "rev-parse", "--show-toplevel"], cwd=self.root,
            timeout=self.timeout, env={"LC_ALL": "C"}, check=False,
        )
        if result.passed:
            if Path(result.stdout.strip()).resolve() != self.root:
                raise ProcessError("Choose the root folder of the existing Git repository.")
            return False
        if not result.stderr.startswith("fatal: not a git repository") or any(
            (folder / ".git").exists() or (folder / ".git").is_symlink()
            for folder in (self.root, *self.root.parents)
        ):
            raise ProcessError(result.stderr.strip() or "Git could not read this repository.")
        self.git("init", "--initial-branch", branch)
        return True

    def prepare_dispatch(self) -> dict:
        initialized = self.initialize()
        if self.git("rev-parse", "--verify", "HEAD^{commit}", check=False).passed:
            return {"initialized": initialized, "baseline_commit": None}

        # Only a genuinely unborn repository may receive an automatic first commit.
        branch = self.git("symbolic-ref", "-q", "HEAD", check=False)
        refs = self.git("show-ref", check=False)
        if not branch.passed or refs.return_code != 1 or refs.stdout.strip():
            raise ProcessError("Git HEAD cannot be read. Repair the repository before dispatching.")
        self.git("add", "--all")
        self.git(
            "-c", "user.name=Switchyard", "-c", "user.email=switchyard@localhost",
            "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m",
            "chore: record baseline before first Switchyard dispatch",
        )
        return {"initialized": initialized, "baseline_commit": self.head()}

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").stdout.strip()

    def branch_name(self, run_id: str, ticket_id: str, fork_number: int) -> str:
        return f"orchestrator/{slug(run_id)}/{slug(ticket_id)}/fork-{fork_number}"

    def worktree_path(self, state_dir: Path, run_id: str, ticket_id: str, fork_number: int) -> Path:
        return state_dir / "worktrees" / slug(run_id) / slug(ticket_id) / f"fork-{fork_number}"

    def create_worktree(
        self,
        *,
        state_dir: Path,
        run_id: str,
        ticket_id: str,
        fork_number: int,
        start_point: str,
    ) -> tuple[Path, str]:
        path = self.worktree_path(state_dir, run_id, ticket_id, fork_number)
        branch = self.branch_name(run_id, ticket_id, fork_number)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.git("worktree", "add", "-b", branch, str(path), start_point)
        return path, branch

    def changed_files(self, workspace: Path) -> list[str]:
        output = self.git("status", "--porcelain", cwd=workspace).stdout
        return [line[3:] for line in output.splitlines() if len(line) > 3]

    def commit_all(self, workspace: Path, message: str) -> str | None:
        if not self.changed_files(workspace):
            return None
        self.git("add", "--all", cwd=workspace)
        self.git(
            "-c",
            "user.name=9router Orchestrator",
            "-c",
            "user.email=hassan.shaban.harera@gmail.com",
            "commit",
            "-m",
            message,
            cwd=workspace,
        )
        return self.git("rev-parse", "HEAD", cwd=workspace).stdout.strip()

    def registered_worktrees(self) -> set[Path]:
        output = self.git("worktree", "list", "--porcelain").stdout
        return {
            Path(line.removeprefix("worktree ")).resolve()
            for line in output.splitlines()
            if line.startswith("worktree ")
        }

    def remove_worktree(self, workspace: Path) -> tuple[bool, str | None]:
        target = workspace.resolve()
        if target == self.root:
            return False, "The primary repository checkout is never removed"
        if target not in self.registered_worktrees():
            return False, "Worktree is no longer registered"
        changes = self.git("status", "--porcelain", cwd=target).stdout.strip()
        if changes:
            return False, "Uncommitted files are preserved"
        result = self.git("worktree", "remove", str(target), check=False)
        if not result.passed:
            return False, result.stderr.strip() or "Git could not remove the worktree"
        self.git("worktree", "prune")
        return True, None

    def push_branch(self, local_branch: str, target_branch: str, remote: str) -> None:
        self.git("check-ref-format", "--branch", target_branch)
        self.git("show-ref", "--verify", f"refs/heads/{local_branch}")
        self.git("push", remote, f"{local_branch}:refs/heads/{target_branch}")
