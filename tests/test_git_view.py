import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ninerouter_orchestrator.git_view import GitReview
from ninerouter_orchestrator.process import ProcessError
from ninerouter_orchestrator.web import app

client = TestClient(app, headers={"x-switchyard-client": "local-ui"})


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Switchyard Test")
    git(root, "config", "user.email", "switchyard@example.test")
    (root / "tracked.txt").write_text("first\nsecond\n")
    git(root, "add", "tracked.txt")
    git(root, "commit", "-m", "base")
    return root


def test_working_tree_snapshot_and_lazy_patches(tmp_path):
    root = repository(tmp_path)
    (root / "tracked.txt").write_text("first\nchanged\n")
    (root / "new file.txt").write_text("one\ntwo\n")

    review = GitReview(root)
    snapshot = review.snapshot()

    assert snapshot["branch"] == "main"
    assert snapshot["clean"] is False
    assert snapshot["summary"]["files"] == 2
    assert snapshot["status_summary"]["unstaged"] == 1
    assert snapshot["status_summary"]["untracked"] == 1
    assert {file["path"] for file in snapshot["files"]} == {"tracked.txt", "new file.txt"}

    tracked = review.patch("tracked.txt")
    assert "-second" in tracked["patch"]
    assert "+changed" in tracked["patch"]
    untracked = review.patch("new file.txt")
    assert untracked["patch"].startswith("@@ -0,0 +1,2 @@")
    assert "+one" in untracked["patch"]


def test_branch_comparison_uses_merge_base(tmp_path):
    root = repository(tmp_path)
    git(root, "checkout", "-b", "feature/review")
    (root / "tracked.txt").write_text("first\nfeature\n")
    git(root, "add", "tracked.txt")
    git(root, "commit", "-m", "feature change")

    review = GitReview(root)
    snapshot = review.snapshot(comparison="branch", base="main")

    assert snapshot["comparison"]["label"] == "main...HEAD"
    assert snapshot["summary"] == {"files": 1, "additions": 1, "deletions": 1}
    patch = review.patch("tracked.txt", comparison="branch", base="main")
    assert "+feature" in patch["patch"]


def test_git_actions_manage_files_commits_branches_and_stashes(tmp_path):
    root = repository(tmp_path)
    tracked = root / "tracked.txt"
    untracked = root / "new file.txt"
    tracked.write_text("changed\n")
    untracked.write_text("new\n")
    review = GitReview(root)

    review.action("stage", paths=["tracked.txt", "new file.txt"])
    assert review.snapshot()["status_summary"]["staged"] == 2

    review.action("unstage", paths=["tracked.txt", "new file.txt"])
    snapshot = review.snapshot()
    assert snapshot["status_summary"]["unstaged"] == 1
    assert snapshot["status_summary"]["untracked"] == 1

    review.action("discard", paths=["tracked.txt"])
    review.action("delete_untracked", paths=["new file.txt"])
    assert tracked.read_text() == "first\nsecond\n"
    assert not untracked.exists()

    tracked.write_text("committed\n")
    review.action("stage", paths=["tracked.txt"])
    review.action("commit", message="managed commit")
    assert review.snapshot()["last_commit"]["subject"] == "managed commit"

    review.action("create_branch", branch="feature/control")
    assert review.snapshot()["branch"] == "feature/control"
    review.action("checkout", branch="main")
    assert review.snapshot()["branch"] == "main"
    review.action("delete_branch", branch="feature/control")

    tracked.write_text("stashed\n")
    review.action("stash", message="from control page")
    stash = review.snapshot()["stashes"][0]
    assert stash["ref"] == "stash@{0}"
    review.action("stash_apply", stash_ref=stash["ref"])
    assert tracked.read_text() == "stashed\n"
    review.action("discard", paths=["tracked.txt"])
    review.action("stash_drop", stash_ref=stash["ref"])
    assert review.snapshot()["stashes"] == []


def test_git_actions_reject_unknown_paths_and_refs(tmp_path):
    review = GitReview(repository(tmp_path))

    for action, values in (
        ("stage", {"paths": ["../outside.txt"]}),
        ("checkout", {"branch": "missing"}),
        ("stash_apply", {"stash_ref": "stash@{99}"}),
        ("merge", {"ref": "missing"}),
    ):
        with pytest.raises(ProcessError):
            review.action(action, **values)


def test_staged_and_unstaged_diffs_and_discard_preserve_index(tmp_path):
    root = repository(tmp_path)
    review = GitReview(root)
    path = root / "tracked.txt"
    path.write_text("staged\n")
    review.action("stage", paths=["tracked.txt"])
    path.write_text("unstaged\n")
    assert "+staged" in review.patch("tracked.txt", comparison="staged")["patch"]
    assert "+unstaged" in review.patch("tracked.txt", comparison="unstaged")["patch"]
    assert review.snapshot(comparison="staged")["summary"]["files"] == 1
    assert review.snapshot(comparison="unstaged")["summary"]["files"] == 1
    review.action("discard", paths=["tracked.txt"])
    assert path.read_text() == "staged\n"
    assert review.snapshot(comparison="unstaged")["summary"]["files"] == 0
    assert review.snapshot()["status_summary"]["staged"] == 1


def test_unstage_rename_restores_both_index_paths(tmp_path):
    root = repository(tmp_path)
    review = GitReview(root)
    git(root, "mv", "tracked.txt", "renamed.txt")
    review.action("unstage", paths=["renamed.txt"])
    assert git(root, "diff", "--cached", "--name-only") == ""
    assert (root / "renamed.txt").exists()


def test_unborn_repository_and_literal_filenames(tmp_path):
    root = tmp_path / "initial"
    root.mkdir()
    git(root, "init", "-b", "main")
    review = GitReview(root)
    for name in ["[abc].txt", "--flag", "space name.txt"]:
        (root / name).write_text("new\n")
    review.action("stage", paths=["[abc].txt", "--flag"])
    assert review.snapshot(comparison="staged")["summary"]["files"] == 2
    review.action("unstage", paths=["[abc].txt", "--flag"])
    assert review.snapshot()["status_summary"]["staged"] == 0
    review.action("delete_untracked", paths=["[abc].txt"])
    assert not (root / "[abc].txt").exists()
    assert (root / "--flag").exists()


def test_branch_reset_amend_revert_and_cherry_pick(tmp_path):
    root = repository(tmp_path)
    review = GitReview(root)
    base = git(root, "rev-parse", "HEAD")
    review.action("create_branch", branch="feature/test")
    (root / "feature.txt").write_text("feature\n")
    review.action("stage", paths=["feature.txt"])
    review.action("commit", message="feature")
    review.action("commit", message="amended feature", amend=True)
    commit = git(root, "rev-parse", "HEAD")
    review.action("rename_branch", branch="feature/renamed")
    assert review.snapshot()["branch"] == "feature/renamed"
    review.action("checkout", branch="main")
    review.action("cherry_pick", ref=commit)
    assert (root / "feature.txt").exists()
    review.action("revert", ref="HEAD")
    assert not (root / "feature.txt").exists()
    review.action("reset", ref=base, reset_mode="soft")
    assert git(root, "rev-parse", "HEAD") == base
    (root / "tracked.txt").write_text("discarded by hard reset\n")
    review.action("reset", ref=base, reset_mode="hard")
    assert (root / "tracked.txt").read_text() == "first\nsecond\n"


def test_merge_conflicts_can_be_resolved_continued_and_aborted(tmp_path):
    root = repository(tmp_path)
    review = GitReview(root)
    review.action("create_branch", branch="feature/conflict")
    (root / "tracked.txt").write_text("feature\n")
    review.action("stage", paths=["tracked.txt"])
    review.action("commit", message="feature")
    review.action("checkout", branch="main")
    (root / "tracked.txt").write_text("main\n")
    review.action("stage", paths=["tracked.txt"])
    review.action("commit", message="main")
    with pytest.raises(ProcessError):
        review.action("merge", ref="feature/conflict")
    assert review.snapshot()["operation"] == "merge"
    review.action("abort_operation")
    assert review.snapshot()["operation"] is None
    with pytest.raises(ProcessError):
        review.action("merge", ref="feature/conflict")
    review.action("resolve_theirs", paths=["tracked.txt"])
    assert (root / "tracked.txt").read_text() == "feature\n"
    review.action("stage", paths=["tracked.txt"])
    review.action("continue_operation")
    assert review.snapshot()["operation"] is None
    assert review.snapshot()["clean"] is True


def test_push_fetch_pull_against_local_remote(tmp_path):
    root = repository(tmp_path)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    git(root, "remote", "add", "origin", str(remote))
    review = GitReview(root)
    review.action("push", remote="origin")
    assert review.snapshot()["upstream"] == "origin/main"
    peer = tmp_path / "peer"
    git(tmp_path, "clone", str(remote), str(peer))
    git(peer, "config", "user.name", "Peer")
    git(peer, "config", "user.email", "peer@example.test")
    (peer / "peer.txt").write_text("remote change\n")
    git(peer, "add", "peer.txt")
    git(peer, "commit", "-m", "remote change")
    git(peer, "push")
    review.action("fetch", remote="origin")
    assert review.snapshot()["behind"] == 1
    review.action("pull", remote="origin")
    assert (root / "peer.txt").read_text() == "remote change\n"
    assert review.snapshot()["behind"] == 0


@pytest.mark.parametrize("action", ["stage", "unstage", "discard", "delete_untracked"])
def test_empty_file_selection_never_means_all_files(tmp_path, action):
    root = repository(tmp_path)
    (root / "tracked.txt").write_text("keep\n")
    review = GitReview(root)
    with pytest.raises(ProcessError):
        review.action(action, paths=[])
    assert (root / "tracked.txt").read_text() == "keep\n"
    assert review.snapshot()["status_summary"]["staged"] == 0


@pytest.mark.parametrize("name", ["--force", "@{-1}", "bad name", "main:other"])
def test_invalid_branch_names_are_rejected(tmp_path, name):
    review = GitReview(repository(tmp_path))
    with pytest.raises(ProcessError):
        review.action("create_branch", branch=name)


def test_git_api_and_frontend_assets(tmp_path):
    root = repository(tmp_path)
    (root / "tracked.txt").write_text("changed\n")

    response = client.get("/api/git/status", params={"repository": str(root)})
    assert response.status_code == 200
    assert response.json()["summary"]["files"] == 1
    assert (
        client.get(
            "/api/git/status",
            params={"repository": str(root)},
            headers={"origin": "https://untrusted.example"},
        ).status_code
        == 403
    )

    page = client.get("/").text
    script = client.get("/assets/git-view.js").text
    diff_script = client.get("/assets/git-diff.js").text
    css = client.get("/assets/styles.css").text
    assert 'data-panel="git"' in page
    assert 'id="git-panel"' in page
    assert "Branch changes" in page
    assert "GitDiff.render" in script
    assert "Line-by-line file changes" in diff_script
    assert "git-review-layout" in css
    assert ".routing-lattice .signal { position: absolute" in css
    assert "\n.signal { position: absolute" not in css
    assert 'data-git-form="commit"' in script
    assert 'data-git-action="push"' in script
    assert "Git workspace" in page


def test_git_action_api_requires_confirmation_and_local_ui(tmp_path):
    root = repository(tmp_path)
    (root / "tracked.txt").write_text("changed\n")
    payload = {
        "repository": str(root),
        "action": "discard",
        "paths": ["tracked.txt"],
    }

    assert client.post("/api/git/action", json=payload).status_code == 422
    response = client.post("/api/git/action", json={**payload, "confirmed": True})
    assert response.status_code == 200
    assert (root / "tracked.txt").read_text() == "first\nsecond\n"
    assert (
        client.post(
            "/api/git/action",
            json={"repository": str(root), "action": "fetch"},
            headers={"origin": "https://untrusted.example"},
        ).status_code
        == 403
    )
