from __future__ import annotations

import re
from pathlib import Path

from .process import ProcessError, run_process

SWITCHYARD_GIT_CONFIG = (
    "-c",
    "user.name=Switchyard",
    "-c",
    "user.email=switchyard@localhost",
)


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
            raise ProcessError(
                "This workspace is not an independent repository. Save it in Workspaces first."
            )

    def initialize(self, branch: str = "main") -> bool:
        """Initialize the selected folder independently; reject damaged repositories."""
        if not self.root.is_dir():
            raise ProcessError("Choose an existing workspace folder.")
        result = run_process(
            ["git", "rev-parse", "--show-toplevel"], cwd=self.root,
            timeout=self.timeout, env={"LC_ALL": "C"}, check=False,
        )
        if result.passed:
            if Path(result.stdout.strip()).resolve() != self.root:
                self.git("init", "--initial-branch", branch)
                return True
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
            *SWITCHYARD_GIT_CONFIG, "-c", "commit.gpgsign=false",
            "commit", "--allow-empty", "-m",
            "chore: record baseline before first Switchyard dispatch",
        )
        return {"initialized": initialized, "baseline_commit": self.head()}

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").stdout.strip()

    def current_branch(self) -> str:
        result = self.git("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        if not result.passed or not result.stdout.strip():
            raise ProcessError(
                "The selected checkout has a detached HEAD. Choose a branch before dispatching "
                "without an isolated worktree."
            )
        return result.stdout.strip()

    def require_clean_checkout(self) -> None:
        if self.git("status", "--porcelain").stdout.strip():
            raise ProcessError(
                "The selected checkout has local changes. Commit or stash them before dispatching "
                "a workflow without an isolated worktree."
            )

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
            *SWITCHYARD_GIT_CONFIG,
            "commit",
            "-m",
            message,
            cwd=workspace,
        )
        return self.git("rev-parse", "HEAD", cwd=workspace).stdout.strip()

    def registered_worktrees(self) -> set[Path]:
        output = self.git("worktree", "list", "--porcelain", "-z").stdout
        return {
            Path(line.removeprefix("worktree ")).resolve()
            for line in output.split("\0")
            if line.startswith("worktree ")
        }

    def managed_worktrees(self, state_dir: Path) -> list[dict]:
        """Return Switchyard-owned worktrees without exposing unrelated Git checkouts."""
        managed_root = (state_dir / "worktrees").resolve()
        entries: list[dict] = []
        current: dict[str, str | bool] = {}
        output = self.git("worktree", "list", "--porcelain", "-z").stdout
        for line in output.split("\0"):
            if line:
                key, _, value = line.partition(" ")
                current[key] = value if value else True
                continue
            if not current.get("worktree"):
                current = {}
                continue
            path = Path(str(current["worktree"])).resolve()
            try:
                relative = path.relative_to(managed_root)
            except ValueError:
                current = {}
                continue
            parts = relative.parts
            if path == self.root or len(parts) != 3 or not re.fullmatch(r"fork-\d+", parts[2]):
                current = {}
                continue
            exists = path.is_dir()
            changed_files = None
            status_error = None
            if exists:
                result = self.git(
                    "status", "--porcelain", "--untracked-files=normal", "--ignored=traditional",
                    cwd=path, check=False,
                )
                if result.passed:
                    changed_files = len(result.stdout.splitlines())
                else:
                    status_error = result.stderr.strip() or "Git status is unavailable"
            branch = str(current.get("branch", ""))
            entries.append(
                {
                    "path": str(path),
                    "run_id": parts[0] if parts else None,
                    "ticket_id": parts[1] if len(parts) > 1 else None,
                    "attempt": parts[2] if len(parts) > 2 else None,
                    "branch": branch.removeprefix("refs/heads/") or None,
                    "head": str(current.get("HEAD", ""))[:12] or None,
                    "detached": bool(current.get("detached")),
                    "locked": bool(current.get("locked")),
                    "lock_reason": (
                        str(current["locked"]) if isinstance(current.get("locked"), str) else None
                    ),
                    "prunable": bool(current.get("prunable")),
                    "prune_reason": (
                        str(current["prunable"])
                        if isinstance(current.get("prunable"), str)
                        else None
                    ),
                    "exists": exists,
                    "changed_files": changed_files,
                    "status_error": status_error,
                    "removable": bool(
                        exists and changed_files == 0 and not current.get("locked") and branch
                    ),
                }
            )
            current = {}
        return entries

    def set_worktree_lock(self, workspace: Path, *, locked: bool) -> None:
        target = workspace.resolve()
        if target == self.root or target not in self.registered_worktrees():
            raise ProcessError("Worktree is no longer registered. Refresh the list.")
        if locked:
            result = self.git(
                "worktree", "lock", "--reason", "Protected in Switchyard", str(target),
                check=False,
            )
        else:
            result = self.git("worktree", "unlock", str(target), check=False)
        if not result.passed:
            action = "protect" if locked else "unprotect"
            raise ProcessError(result.stderr.strip() or f"Git could not {action} the worktree")

    def remove_worktree(
        self, workspace: Path, *, preserve_ignored: bool = False
    ) -> tuple[bool, str | None]:
        target = workspace.resolve()
        if target == self.root:
            return False, "The primary repository checkout is never removed"
        if target not in self.registered_worktrees():
            return False, "Worktree is no longer registered"
        flags = ["--ignored=traditional"] if preserve_ignored else []
        changes = self.git("status", "--porcelain", *flags, cwd=target).stdout.strip()
        if changes:
            return False, "Uncommitted files are preserved"
        result = self.git("worktree", "remove", str(target), check=False)
        if not result.passed:
            return False, result.stderr.strip() or "Git could not remove the worktree"
        return True, None

    def push_branch(self, local_branch: str, target_branch: str, remote: str) -> None:
        self.git("check-ref-format", "--branch", target_branch)
        self.git("show-ref", "--verify", f"refs/heads/{local_branch}")
        self.git("push", remote, f"{local_branch}:refs/heads/{target_branch}")
