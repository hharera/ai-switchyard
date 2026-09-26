from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Literal

from .process import ProcessError

Comparison = Literal["working", "staged", "unstaged", "branch"]
GitAction = Literal[
    "stage",
    "unstage",
    "discard",
    "delete_untracked",
    "commit",
    "checkout",
    "create_branch",
    "rename_branch",
    "delete_branch",
    "reset",
    "resolve_ours",
    "resolve_theirs",
    "fetch",
    "pull",
    "push",
    "stash",
    "stash_apply",
    "stash_pop",
    "stash_drop",
    "merge",
    "rebase",
    "cherry_pick",
    "revert",
    "abort_operation",
    "continue_operation",
    "skip_operation",
]
MAX_PREVIEW_BYTES = 256_000
MAX_PATCH_LINES = 4_000
MAX_FILES = 500
MAX_LOG_COMMITS = 100
MAX_REPOSITORIES = 50
MAX_REPOSITORY_DEPTH = 6
DIFF_OPTIONS = ("--no-color", "--no-ext-diff", "--no-textconv", "--find-renames")
REPOSITORY_SCAN_SKIPS = {
    ".cache",
    ".idea",
    ".next",
    ".pytest_cache",
    ".tox",
    ".venv",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "vendor",
}


def discover_repositories(workspace: Path) -> list[dict]:
    """Find independent repositories without walking generated dependency trees."""
    root = workspace.expanduser().resolve()
    if not root.is_dir():
        raise ProcessError("Choose an existing workspace folder.")
    repositories: list[dict] = []
    pending = [(root, 0, None)]
    visited = 0
    while pending and len(repositories) < MAX_REPOSITORIES and visited < 20_000:
        folder, depth, repository_root = pending.pop()
        visited += 1
        marker = folder / ".git"
        if marker.is_dir() or marker.is_file():
            repository_root = folder
            relative = folder.relative_to(root).as_posix() or "."
            repositories.append(
                {
                    "path": str(folder),
                    "relative_path": relative,
                    "name": root.name if relative == "." else folder.name,
                }
            )
        if depth >= MAX_REPOSITORY_DEPTH:
            continue
        try:
            children = [entry for entry in folder.iterdir() if entry.is_dir() and not entry.is_symlink()]
        except OSError:
            continue
        ignored = set()
        if repository_root and children:
            # Git handles nested ignore files, negations, and tracked submodules.
            candidates = [child.relative_to(repository_root).as_posix() for child in children]
            try:
                result = subprocess.run(
                    ["git", "check-ignore", "--stdin", "-z"], cwd=repository_root,
                    input="\0".join(candidates) + "\0", text=True,
                    capture_output=True, timeout=5, check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ProcessError("Unable to check repository discovery ignore rules.") from exc
            if result.returncode not in (0, 1):
                continue
            ignored = set(result.stdout.split("\0"))
        for child in sorted(children, key=lambda item: item.name.casefold(), reverse=True):
            if child.name == ".git" or child.name in REPOSITORY_SCAN_SKIPS:
                continue
            if repository_root and child.relative_to(repository_root).as_posix() in ignored:
                continue
            pending.append((child, depth + 1, repository_root))
    repositories.sort(key=lambda item: (item["relative_path"] != ".", item["relative_path"]))
    return repositories


class GitReview:
    """Git inspection and explicit, validated repository operations."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        if not self.root.is_dir():
            raise ProcessError("Choose an existing repository directory.")
        result = self.git("rev-parse", "--show-toplevel", check=False)[0].strip()
        if not result or Path(result).resolve() != self.root:
            raise ProcessError(
                "This workspace is not an independent repository. Save it in Workspaces first."
            )

    def git(
        self, *args: str, check: bool = True, limit: int = 4_000_000, timeout: int = 30
    ) -> tuple[str, bool]:
        # Avoid index refreshes, configured diff helpers, and unbounded in-memory patches.
        command = [
            "git",
            "--no-optional-locks",
            "--literal-pathspecs",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.quotePath=false",
            *args,
        ]
        try:
            with tempfile.TemporaryFile() as output:
                result = subprocess.run(
                    command,
                    cwd=self.root,
                    input=b"",
                    stdout=output,
                    stderr=subprocess.PIPE,
                    timeout=timeout,
                    check=False,
                    env={
                        **os.environ,
                        "GIT_TERMINAL_PROMPT": "0",
                        "GCM_INTERACTIVE": "Never",
                    },
                )
                if result.returncode and check:
                    detail = result.stderr.decode("utf-8", errors="replace").strip()
                    raise ProcessError(
                        detail or "Git could not read this comparison. Refresh and retry."
                    )
                if result.returncode:
                    return "", False
                output.seek(0)
                data = output.read(limit + 1)
        except subprocess.TimeoutExpired as exc:
            raise ProcessError(
                "Git inspection timed out. Try a smaller repository or retry."
            ) from exc
        except OSError as exc:
            raise ProcessError(
                "Unable to read Git. Check that Git is installed and the folder is accessible."
            ) from exc
        if len(data) > limit and limit != MAX_PREVIEW_BYTES:
            raise ProcessError("This repository has too many changes to display. Use Git locally.")
        return data[:limit].decode("utf-8", errors="replace"), len(data) > limit

    def run(self, *args: str, timeout: int = 120) -> str:
        """Run a validated Git mutation without allowing interactive prompts."""
        command = [
            "git",
            "--no-optional-locks",
            "--literal-pathspecs",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.quotePath=false",
            *args,
        ]
        try:
            result = subprocess.run(
                command,
                cwd=self.root,
                input=b"",
                capture_output=True,
                timeout=timeout,
                check=False,
                env={
                    **os.environ,
                    "GIT_TERMINAL_PROMPT": "0",
                    "GCM_INTERACTIVE": "Never",
                    "GIT_EDITOR": "true",
                },
            )
        except subprocess.TimeoutExpired as exc:
            raise ProcessError("Git operation timed out. Check the remote and try again.") from exc
        except OSError as exc:
            raise ProcessError("Unable to run Git. Check that Git is installed.") from exc
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        if result.returncode:
            raise ProcessError(stderr or stdout or "Git could not complete this operation.")
        return stdout or stderr

    def output(self, *args: str, check: bool = True) -> str:
        return self.git(*args, check=check)[0].strip()

    def revision(self, ref: str) -> str:
        if not ref or "\0" in ref:
            raise ProcessError("Enter a valid local base branch or commit.")
        result = self.output(
            "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}", check=False
        )
        if not result:
            raise ProcessError(
                "Base branch or commit was not found locally. Choose an available branch."
            )
        return result

    def branch_refs(self) -> list[dict]:
        output = self.output(
            "for-each-ref",
            "--sort=refname",
            "--format=%(refname)%00%(refname:short)%00%(objectname)%00%(upstream:short)%00%(upstream:track)%00%(HEAD)%00%(symref)",
            "refs/heads",
            "refs/remotes",
        )
        refs = []
        for line in output.splitlines():
            parts = line.split("\0", 6)
            if len(parts) != 7 or parts[6]:
                continue
            full_name, short_name, commit, upstream, tracking, head, _ = parts
            kind = "local" if full_name.startswith("refs/heads/") else "remote"
            short_name = full_name.removeprefix(
                "refs/heads/" if kind == "local" else "refs/remotes/"
            )
            refs.append(
                {
                    "full_name": full_name,
                    "name": short_name,
                    "commit": commit,
                    "upstream": upstream,
                    "tracking": tracking.strip("[]"),
                    "current": head == "*",
                    "kind": kind,
                }
            )
        return refs[:500]

    def commit_log(
        self, ref: str | None = None, *, query: str = "", author: str = "",
        since: str = "", until: str = "", path: str = "",
    ) -> list[dict]:
        for value in (since, until):
            if value:
                date.fromisoformat(value)
        if since and until and since > until:
            raise ProcessError("The start date must be on or before the end date.")
        if path and ("\0" in path or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts):
            raise ProcessError("Enter a repository-relative file or folder path.")
        if not self.output("rev-parse", "--verify", "HEAD", check=False):
            return []
        revisions = [self.revision(ref)] if ref else ["--all", "HEAD"]
        filters = []
        if query:
            if re.fullmatch(r"[a-fA-F0-9]{4,64}", query):
                # Hash search targets a single commit, rather than its ancestors.
                target = self.revision(query)
                filters.append("--no-walk")
                if ref and self.output("merge-base", target, revisions[0], check=False) != target:
                    return []
                revisions = [target]
            else:
                filters.extend(["--fixed-strings", "--regexp-ignore-case", f"--grep={query}"])
        if author:
            filters.extend(["--fixed-strings", "--regexp-ignore-case", f"--author={author}"])
        if since:
            filters.append(f"--since-as-filter={since}T00:00:00")
        if until:
            filters.append(f"--until={until}T23:59:59")
        output = self.git(
            "log",
            *revisions,
            "--date-order",
            "--topo-order",
            f"--max-count={MAX_LOG_COMMITS}",
            *filters,
            "--format=%x1e%H%x1f%h%x1f%s%x1f%an%x1f%aI%x1f%D%x1f%P",
            "--", *([path] if path else []),
        )[0]
        commits = []
        for record in output.split("\x1e"):
            if not record:
                continue
            parts = record.strip("\r\n").split("\x1f", 6)
            if len(parts) != 7:
                continue
            commits.append(
                {
                    "hash": parts[0],
                    "short": parts[1],
                    "subject": parts[2],
                    "author": parts[3],
                    "authored_at": parts[4],
                    "decorations": parts[5],
                    "parents": parts[6].split() if parts[6] else [],
                }
            )
        return commits

    def commit_detail(self, ref: str) -> dict:
        commit = self.revision(ref)
        fields = self.git(
            "show", "-s", "--format=%H%x00%an%x00%ae%x00%aI%x00%cn%x00%cI%x00%P%x00%B", commit,
            limit=MAX_PREVIEW_BYTES,
        )[0].split("\0", 7)
        parents = fields[6].split()
        base = parents[0] if parents else self.output("hash-object", "-t", "tree", "--stdin")
        files = self.files([base, commit], "branch", [])
        return {
            "hash": commit, "author": fields[1], "author_email": fields[2],
            "authored_at": fields[3], "committer": fields[4], "committed_at": fields[5],
            "parents": parents, "message": fields[7].rstrip("\n"), "base": base,
            "comparison_label": "First parent" if parents else "Empty tree (initial commit)",
            "files": files[:MAX_FILES], "files_truncated": len(files) > MAX_FILES,
            "file_count": len(files), "repository": str(self.root),
        }

    def commit_patch(self, ref: str, path: str) -> dict:
        detail = self.commit_detail(ref)
        file = next((item for item in detail["files"] if item["path"] == path), None)
        if file is None:
            raise ProcessError("Choose a changed file from this commit.")
        paths = [file["old_path"], path] if file["old_path"] else [path]
        patch, truncated = self.git(
            "diff", *DIFF_OPTIONS, "--unified=3", detail["base"], detail["hash"],
            "--", *paths, limit=MAX_PREVIEW_BYTES,
        )
        lines = patch.split("\n")
        return {**file, "patch": "\n".join(lines[:MAX_PATCH_LINES]),
                "truncated": truncated or len(lines) > MAX_PATCH_LINES}

    def working_files(self) -> list[dict]:
        # Let Git apply scoped ignore rules and negations before collecting output.
        # Tracked files remain visible even when they match an ignore pattern.
        tokens = iter(
            self.git(
                "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=no"
            )[0].split("\0")
        )
        files = []
        for entry in tokens:
            if not entry:
                continue
            xy, path = entry[:2], entry[3:]
            old_path = next(tokens) if "R" in xy or "C" in xy else None
            conflict = "U" in xy or xy in {"AA", "DD"}
            status = (
                "untracked"
                if xy == "??"
                else "conflicted"
                if conflict
                else status_name(xy.strip()[0])
            )
            files.append(
                {
                    "path": path,
                    "old_path": old_path,
                    "status": status,
                    "code": xy,
                    "staged": xy[0] not in {" ", "?"} and not conflict,
                    "unstaged": xy[1] not in {" ", "?"} and not conflict,
                }
            )
        return files

    def comparison(self, kind: Comparison, base: str | None) -> tuple[list[str], dict]:
        head = self.output("rev-parse", "--verify", "HEAD", check=False)
        if kind in {"working", "staged", "unstaged"}:
            # Git recognizes its empty-tree hash even in an unborn repository.
            start = head or self.output("hash-object", "-t", "tree", "--stdin")
            revisions = [] if kind == "unstaged" else [start]
            if kind == "staged":
                revisions.insert(0, "--cached")
            return revisions, {
                "kind": kind,
                "label": {
                    "working": "Working tree vs HEAD" if head else "Initial changes",
                    "staged": "Staged changes vs HEAD",
                    "unstaged": "Unstaged changes vs index",
                }[kind],
                "base": "HEAD",
            }
        if not head:
            raise ProcessError("Create a first commit before comparing branches.")
        if not base:
            raise ProcessError("Choose a base branch or commit to compare with HEAD.")
        base_hash = self.revision(base)
        merge_base = self.output("merge-base", base_hash, head, check=False)
        if not merge_base:
            raise ProcessError("These branches have no shared history. Choose a different base.")
        return [merge_base, head], {
            "kind": kind,
            "label": f"{base}...HEAD",
            "base": base,
            "merge_base": merge_base[:12],
        }

    def files(self, revisions: list[str], kind: Comparison, working: list[dict]) -> list[dict]:
        files = [dict(file) for file in working] if kind != "branch" else []
        if kind == "staged":
            files = [file for file in files if file["staged"] or file["status"] == "conflicted"]
        elif kind == "unstaged":
            files = [
                file for file in files
                if file["unstaged"] or file["status"] in {"untracked", "conflicted"}
            ]
        if kind == "branch":
            tokens = iter(
                self.git("diff", *DIFF_OPTIONS, "--name-status", "-z", *revisions, "--")[0].split(
                    "\0"
                )
            )
            for code in tokens:
                if not code:
                    continue
                path, old_path = next(tokens), None
                if code[0] in {"R", "C"}:
                    old_path, path = path, next(tokens)
                files.append(
                    {
                        "path": path,
                        "old_path": old_path,
                        "status": status_name(code[0]),
                        "code": code,
                        "staged": False,
                        "unstaged": False,
                    }
                )
        stats = {}
        tokens = iter(
            self.git("diff", *DIFF_OPTIONS, "--numstat", "-z", *revisions, "--")[0].split("\0")
        )
        for entry in tokens:
            if not entry:
                continue
            added, deleted, path = entry.split("\t", 2)
            if not path:
                next(tokens)  # The source path precedes the destination for a rename.
                path = next(tokens)
            stats[path] = {
                "additions": int(added) if added != "-" else 0,
                "deletions": int(deleted) if deleted != "-" else 0,
                "binary": added == "-",
            }
        for file in files:
            file.update(stats.get(file["path"], {"additions": 0, "deletions": 0, "binary": False}))
        return files

    def snapshot(self, *, comparison: Comparison = "working", base: str | None = None) -> dict:
        working = self.working_files()
        branch = self.output("symbolic-ref", "--quiet", "--short", "HEAD", check=False) or None
        head = self.output("rev-parse", "--verify", "HEAD", check=False)
        local_branches = self.output(
            "for-each-ref", "--format=%(refname:short)", "refs/heads"
        ).splitlines()
        branches = self.output(
            "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes"
        ).splitlines()
        branches = [name for name in branches if not name.endswith("/HEAD")]
        default_base = next(
            (
                name
                for name in ("main", "master", "origin/main", "origin/master", *branches)
                if name in branches and name != branch
            ),
            None,
        )
        revisions, info = self.comparison(comparison, base or default_base)
        files = self.files(revisions, comparison, working)
        upstream = self.output("rev-parse", "--abbrev-ref", "@{upstream}", check=False) or None
        counts = (
            self.output(
                "rev-list", "--left-right", "--count", "@{upstream}...HEAD", check=False
            ).split()
            if upstream
            else []
        )
        last_commit = None
        if head:
            short, subject, author, date = self.output(
                "log", "-1", "--format=%h%x00%s%x00%an%x00%aI"
            ).split("\0", 3)
            last_commit = {
                "short": short,
                "subject": subject,
                "author": author,
                "authored_at": date,
            }
        remotes = self.output("remote").splitlines()
        stashes = []
        for line in self.output(
            "stash", "list", "--format=%gd%x00%gs%x00%cr", check=False
        ).splitlines():
            parts = line.split("\0", 2)
            if len(parts) == 3:
                stashes.append({"ref": parts[0], "message": parts[1], "age": parts[2]})
        commit_log = self.commit_log() if head else []
        return {
            "repository": str(self.root),
            "name": self.root.name,
            "branch": branch,
            "head": head[:12],
            "detached": branch is None,
            "upstream": upstream,
            "behind": int(counts[0]) if counts else 0,
            "ahead": int(counts[1]) if counts else 0,
            "clean": not working,
            "branches": branches[:500],
            "local_branches": local_branches[:500],
            "remotes": remotes,
            "stashes": stashes[:50],
            "recent_commits": commit_log[:15],
            "commit_log": commit_log,
            "operation": self.operation_state(),
            "default_base": default_base,
            "comparison": info,
            "last_commit": last_commit,
            "status_summary": {
                "staged": sum(file["staged"] for file in working),
                "unstaged": sum(file["unstaged"] for file in working),
                "untracked": sum(file["status"] == "untracked" for file in working),
                "conflicted": sum(file["status"] == "conflicted" for file in working),
            },
            "summary": {
                "files": len(files),
                "additions": sum(file["additions"] for file in files),
                "deletions": sum(file["deletions"] for file in files),
            },
            "files": files[:MAX_FILES],
            "files_truncated": len(files) > MAX_FILES,
        }

    def action(
        self,
        action: GitAction,
        *,
        paths: list[str] | None = None,
        recursive_paths: list[str] | None = None,
        message: str = "",
        branch: str = "",
        start_point: str = "",
        remote: str = "",
        strategy: Literal["ff_only", "rebase", "merge"] = "ff_only",
        stash_ref: str = "",
        ref: str = "",
        include_untracked: bool = False,
        amend: bool = False,
        reset_mode: Literal["soft", "mixed", "hard"] = "mixed",
    ) -> dict:
        requested = list(dict.fromkeys(paths or []))
        recursive = list(dict.fromkeys(recursive_paths or []))
        output = ""
        if action == "stage":
            files = self.known_paths(requested, recursive)
            selected = [
                item["path"] for item in files
                if item["unstaged"] or item["status"] in {"untracked", "conflicted"}
            ]
            if not selected:
                raise ProcessError("Select at least one file with unstaged changes.")
            output = self.run("add", "--", *selected)
        elif action == "unstage":
            files = self.known_paths(requested, recursive)
            targets = []
            for item in files:
                if item["staged"]:
                    targets.append(item["path"])
                    if item["old_path"]:
                        targets.append(item["old_path"])
            if not targets:
                raise ProcessError("Select at least one staged file to unstage.")
            if self.output("rev-parse", "--verify", "HEAD", check=False):
                output = self.run("reset", "-q", "HEAD", "--", *targets)
            else:
                output = self.run("rm", "--cached", "-r", "--ignore-unmatch", "--", *targets)
        elif action == "discard":
            files = self.known_paths(requested, recursive)
            if not recursive and any(item["status"] == "untracked" for item in files):
                raise ProcessError("Use Delete untracked for files that are not tracked by Git.")
            if not recursive and any(item["status"] == "conflicted" for item in files):
                raise ProcessError("Resolve conflicted files before discarding their changes.")
            targets = [
                item["path"] for item in files
                if item["unstaged"] and item["status"] not in {"untracked", "conflicted"}
            ]
            if not targets:
                raise ProcessError("Select at least one file with unstaged changes.")
            output = self.run("restore", "--worktree", "--", *targets)
        elif action == "delete_untracked":
            files = self.known_paths(requested, recursive)
            if not recursive and (not files or any(item["status"] != "untracked" for item in files)):
                raise ProcessError("Select only untracked files to delete.")
            targets = [item["path"] for item in files if item["status"] == "untracked"]
            if not targets:
                raise ProcessError("Select at least one untracked file to delete.")
            output = self.run("clean", "-f", "-d", "--", *targets)
        elif action == "commit":
            commit_message = message.strip()
            if not commit_message:
                raise ProcessError("Enter a commit message.")
            args = ["commit"]
            if amend:
                args.append("--amend")
            args.extend(["-m", commit_message])
            output = self.run(*args)
        elif action == "checkout":
            if branch not in self.local_branches():
                raise ProcessError("Choose an existing local branch.")
            output = self.run("switch", branch)
        elif action == "create_branch":
            name = branch.strip()
            self.require_branch_name(name)
            args = ["switch", "-c", name]
            if start_point.strip():
                args.append(self.revision(start_point.strip()))
            output = self.run(*args)
        elif action == "rename_branch":
            self.require_branch_name(branch)
            output = self.run("branch", "-m", branch)
        elif action == "delete_branch":
            if branch not in self.local_branches():
                raise ProcessError("Choose an existing local branch.")
            output = self.run("branch", "-d", "--", branch)
        elif action == "reset":
            target = self.revision(ref.strip())
            output = self.run("reset", f"--{reset_mode}", target)
        elif action in {"resolve_ours", "resolve_theirs"}:
            files = self.known_paths(requested, recursive)
            if any(item["status"] != "conflicted" for item in files):
                raise ProcessError("Select only conflicted files.")
            side = "--ours" if action == "resolve_ours" else "--theirs"
            output = self.run("checkout", side, "--", *(item["path"] for item in files))
        elif action == "fetch":
            args = ["fetch", "--prune"]
            if remote:
                self.require_remote(remote)
                args.append(remote)
            else:
                args.append("--all")
            output = self.run(*args)
        elif action == "pull":
            args = ["pull", {"ff_only": "--ff-only", "rebase": "--rebase", "merge": "--no-rebase"}[strategy]]
            if remote:
                self.require_remote(remote)
                args.append(remote)
            elif not self.output("rev-parse", "--abbrev-ref", "@{upstream}", check=False):
                raise ProcessError("Choose a remote or set an upstream before pulling.")
            output = self.run(*args)
        elif action == "push":
            current = self.output("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
            if not current:
                raise ProcessError("Check out a branch before pushing.")
            if remote:
                self.require_remote(remote)
                output = self.run("push", "--set-upstream", remote, current)
            elif self.output("rev-parse", "--abbrev-ref", "@{upstream}", check=False):
                output = self.run("push")
            else:
                raise ProcessError("Choose a remote for the first push of this branch.")
        elif action == "stash":
            args = ["stash", "push"]
            if include_untracked:
                args.append("--include-untracked")
            if message.strip():
                args.extend(["-m", message.strip()])
            output = self.run(*args)
        elif action in {"stash_apply", "stash_pop", "stash_drop"}:
            stash = self.require_stash(stash_ref)
            verb = {"stash_apply": "apply", "stash_pop": "pop", "stash_drop": "drop"}[action]
            output = self.run("stash", verb, stash)
        elif action in {"merge", "rebase", "cherry_pick", "revert"}:
            target = self.revision(ref.strip())
            if action == "merge":
                output = self.run("merge", "--no-edit", target)
            elif action == "rebase":
                output = self.run("rebase", target)
            elif action == "cherry_pick":
                output = self.run("cherry-pick", target)
            else:
                output = self.run("revert", "--no-edit", target)
        elif action in {"abort_operation", "continue_operation", "skip_operation"}:
            operation = self.operation_state()
            if operation is None:
                raise ProcessError("There is no Git operation in progress. Refresh the page.")
            verb = action.split("_", 1)[0]
            if verb == "skip" and operation == "merge":
                raise ProcessError("A merge cannot be skipped. Continue or abort it.")
            output = self.run(operation, f"--{verb}")
        else:
            raise ProcessError("Unsupported Git action.")
        return {"action": action, "output": output[:32_000]}

    def require_branch_name(self, name: str) -> None:
        if not name or name.startswith("-") or "\0" in name:
            raise ProcessError("Enter a valid branch name that does not start with a dash.")
        self.run("check-ref-format", f"refs/heads/{name}")

    def known_paths(
        self, paths: list[str], recursive_paths: list[str] | None = None
    ) -> list[dict]:
        scopes = recursive_paths or []
        if not paths and not scopes:
            raise ProcessError("Select at least one changed file.")
        working_files = self.working_files()
        working = {item["path"]: item for item in working_files}
        if any("\0" in path or path not in working for path in paths):
            raise ProcessError("One or more selected files are no longer changed. Refresh and try again.")
        selected = [working[path] for path in paths]
        for scope in scopes:
            candidate = PurePosixPath(scope)
            if (
                not scope
                or "\0" in scope
                or candidate.is_absolute()
                or ".." in candidate.parts
                or ".git" in candidate.parts
                or candidate.as_posix() != scope.rstrip("/")
            ):
                raise ProcessError("Choose a folder inside the repository.")
            prefix = f"{candidate.as_posix()}/"
            matches = [
                item for item in working_files
                if scope == "." or item["path"].startswith(prefix)
                or bool(item["old_path"] and item["old_path"].startswith(prefix))
            ]
            if not matches:
                raise ProcessError(
                    "This folder no longer contains changed files. Refresh and try again."
                )
            selected.extend(matches)
        return list({item["path"]: item for item in selected}.values())

    def local_branches(self) -> list[str]:
        return self.output("for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines()

    def require_remote(self, remote: str) -> str:
        if remote.startswith("-") or remote not in self.output("remote").splitlines():
            raise ProcessError("Choose an existing Git remote.")
        return remote

    def require_stash(self, stash_ref: str) -> str:
        refs = {
            line.split("\0", 1)[0]
            for line in self.output("stash", "list", "--format=%gd%x00%gs", check=False).splitlines()
        }
        if stash_ref not in refs:
            raise ProcessError("Choose an existing stash.")
        return stash_ref

    def operation_state(self) -> str | None:
        checks = (
            ("rebase", "rebase-merge"),
            ("rebase", "rebase-apply"),
            ("merge", "MERGE_HEAD"),
            ("cherry-pick", "CHERRY_PICK_HEAD"),
            ("revert", "REVERT_HEAD"),
        )
        for operation, marker in checks:
            path = self.output("rev-parse", "--git-path", marker, check=False)
            if path and (self.root / path).resolve().exists():
                return operation
        return None

    def patch(
        self, path: str, *, comparison: Comparison = "working", base: str | None = None
    ) -> dict:
        revisions, _ = self.comparison(comparison, base)
        files = self.files(revisions, comparison, self.working_files())
        file = next((file for file in files if file["path"] == path), None)
        if file is None:
            raise ProcessError("This file is no longer in the comparison. Refresh changes.")
        if file["status"] == "untracked":
            return self.untracked_patch(file)
        paths = [file["old_path"], path] if file["old_path"] else [path]
        patch, truncated = self.git(
            "diff", *DIFF_OPTIONS, "--unified=3", *revisions, "--", *paths, limit=MAX_PREVIEW_BYTES
        )
        lines = patch.split("\n")
        return {
            **file,
            "patch": "\n".join(lines[:MAX_PATCH_LINES]),
            "truncated": truncated or len(lines) > MAX_PATCH_LINES,
        }

    def untracked_patch(self, file: dict) -> dict:
        path = self.root / file["path"]
        result = {**file, "patch": "", "truncated": False, "message": ""}
        try:
            parent = path.parent.resolve()
            if self.root not in parent.parents and parent != self.root:
                raise OSError("Outside repository")
            # Never follow a link or block on a FIFO while previewing untracked files.
            if path.is_symlink():
                data = os.readlink(path).encode()
            else:
                before = path.lstat()
                if not stat.S_ISREG(before.st_mode):
                    raise OSError("Not a regular file")
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                descriptor = os.open(path, flags | getattr(os, "O_BINARY", 0))
                with os.fdopen(descriptor, "rb") as stream:
                    opened = os.fstat(stream.fileno())
                    # Windows lacks O_NOFOLLOW: reject a swapped file before reading it.
                    if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(before, opened):
                        raise OSError("File changed while opening")
                    if not path.resolve().is_relative_to(self.root):
                        raise OSError("Outside repository")
                    data = stream.read(MAX_PREVIEW_BYTES + 1)
        except OSError:
            return {
                **result,
                "message": "File preview unavailable. Refresh changes or inspect it locally.",
            }
        if b"\0" in data:
            return {**result, "binary": True}
        content = data[:MAX_PREVIEW_BYTES].decode("utf-8", errors="replace").split("\n")
        if content[-1] == "":
            content.pop()
        truncated = len(data) > MAX_PREVIEW_BYTES or len(content) > MAX_PATCH_LINES
        lines = content[:MAX_PATCH_LINES]
        patch = (
            "\n".join([f"@@ -0,0 +1,{len(content)} @@", *(f"+{line}" for line in lines)])
            if lines
            else ""
        )
        if data and not data.endswith(b"\n") and not truncated:
            patch += "\n\\ No newline at end of file"
        return {**result, "patch": patch, "truncated": truncated, "additions": len(lines)}


def status_name(code: str) -> str:
    return {
        "A": "added",
        "C": "copied",
        "D": "deleted",
        "M": "modified",
        "R": "renamed",
        "T": "type changed",
        "U": "conflicted",
    }.get(code, "modified")
