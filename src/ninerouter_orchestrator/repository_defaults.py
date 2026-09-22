from pathlib import Path

from .process import ProcessError, run_process


def repository_defaults(path: str) -> dict:
    root = Path(path).expanduser()
    if not root.is_absolute() or "\0" in path:
        raise ValueError("Enter an absolute repository path.")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Choose a repository folder, not a file.")

    def git(*args: str) -> str:
        result = run_process(["git", *args], cwd=root, timeout=10)
        if result.return_code not in (0, 1):
            raise ProcessError(result.stderr.strip() or "Cannot read this Git repository.")
        return result.stdout.strip() if result.passed else ""

    top = run_process(["git", "rev-parse", "--show-toplevel"], cwd=root, timeout=10)
    if not top.passed:
        raise ValueError("Git settings are unavailable. Choose a Git repository or enter settings manually.")
    if Path(top.stdout.strip()).resolve() != root:
        raise ValueError(
            "Git settings are unavailable for this folder until it is saved as a workspace."
        )

    remotes = git("remote").splitlines()
    branch = git("symbolic-ref", "--quiet", "--short", "HEAD")
    tracking_remote = git("config", "--get", f"branch.{branch}.remote") if branch else ""
    push_remote = git("config", "--get", f"branch.{branch}.pushRemote") if branch else ""
    push_default = git("config", "--get", "remote.pushDefault")
    remote = next((name for name in [push_remote, push_default, tracking_remote, "origin", *remotes]
                   if name in remotes), None)

    # Read cached remote HEAD only; this operation never fetches or contacts a remote.
    base_remote = tracking_remote if tracking_remote in remotes else remote
    remote_head = git("symbolic-ref", "--quiet", f"refs/remotes/{base_remote}/HEAD") if base_remote else ""
    prefix = f"refs/remotes/{base_remote}/"
    base = remote_head.removeprefix(prefix) if remote_head.startswith(prefix) else None
    refs = git("for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes").splitlines()
    if not base:
        base = next((name for name in ("main", "master") if
                     f"refs/remotes/{base_remote}/{name}" in refs or f"refs/heads/{name}" in refs), None)
    return {"repository": str(root), "remote": remote, "base_branch": base, "remotes": remotes}
