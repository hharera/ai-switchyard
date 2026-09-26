import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import ninerouter_orchestrator.web as web
from ninerouter_orchestrator.git_view import GitReview, discover_repositories
from ninerouter_orchestrator.process import ProcessError
from ninerouter_orchestrator.web import app
from ninerouter_orchestrator.workflow_config import WorkflowStore
from ninerouter_orchestrator.workspace_config import Workspace, WorkspaceConfiguration, WorkspaceStore

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


@pytest.mark.parametrize("no_nofollow", [False, True])
def test_working_tree_snapshot_and_lazy_patches(tmp_path, monkeypatch, no_nofollow):
    if no_nofollow:
        monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
        monkeypatch.delattr(os, "O_NONBLOCK", raising=False)
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


@pytest.mark.parametrize("comparison", ["working", "unstaged", "staged"])
def test_git_review_respects_scoped_ignore_rules(tmp_path, comparison):
    root = repository(tmp_path)
    (root / ".gitignore").write_text("node_modules/\n*.log\ntracked.txt\n")
    (root / "app").mkdir()
    (root / "app/.gitignore").write_text("cache/\n*.tmp\n!keep.tmp\n")
    git(root, "add", ".gitignore", "app/.gitignore")
    git(root, "commit", "-m", "ignore generated files")
    for name in (
        "node_modules/pkg/index.js", "app/node_modules/pkg/index.js",
        "app/cache/output.txt", "app/debug.log", "app/scratch.tmp",
        "app/keep.tmp", "outside.tmp", "tracked.txt",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("changed\n")

    review = GitReview(root)
    expected = {"app/keep.tmp", "outside.tmp", "tracked.txt"}
    assert {item["path"] for item in review.working_files()} == expected
    if comparison == "staged":
        review.action("stage", paths=["tracked.txt"])
        expected = {"tracked.txt"}
    snapshot = review.snapshot(comparison=comparison)
    assert {item["path"] for item in snapshot["files"]} == expected
    assert snapshot["summary"]["files"] == len(expected)
    for ignored in ("node_modules/pkg/index.js", "app/cache/output.txt", "app/scratch.tmp"):
        with pytest.raises(ProcessError):
            review.patch(ignored)
        with pytest.raises(ProcessError):
            review.action("stage", paths=[ignored])


def test_nested_ignore_rules_do_not_apply_to_siblings(tmp_path):
    root = repository(tmp_path)
    (root / "app").mkdir()
    (root / "app/.gitignore").write_text("node_modules/\n")
    for folder in ("app/node_modules", "node_modules"):
        path = root / folder
        path.mkdir(parents=True)
        (path / "index.js").write_text("generated\n")

    paths = {item["path"] for item in GitReview(root).working_files()}
    assert "app/node_modules/index.js" not in paths
    assert "node_modules/index.js" in paths


def test_untracked_preview_rejects_file_swapped_during_open(tmp_path, monkeypatch):
    root = repository(tmp_path)
    preview = root / "preview.txt"
    preview.write_text("safe\n")
    outside = tmp_path / "private.txt"
    outside.write_text("do not expose\n")
    review = GitReview(root)
    original_open = os.open

    def swapped_open(path, flags, mode=0o777):
        return original_open(outside if Path(path) == preview else path, flags, mode)

    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    monkeypatch.setattr(os, "open", swapped_open)
    result = review.untracked_patch({"path": "preview.txt"})
    assert result["patch"] == ""
    assert "unavailable" in result["message"]


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


def test_commit_log_contains_merge_parents_side_branches_and_tags(tmp_path):
    root = repository(tmp_path)
    base = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-b", "feature")
    (root / "feature.txt").write_text("feature\n")
    git(root, "add", "feature.txt")
    git(root, "commit", "-m", "feature commit")
    feature = git(root, "rev-parse", "HEAD")
    git(root, "tag", "v1")
    git(root, "checkout", "main")
    (root / "main.txt").write_text("main\n")
    git(root, "add", "main.txt")
    git(root, "commit", "-m", "main commit")
    main = git(root, "rev-parse", "HEAD")
    git(root, "merge", "--no-ff", "feature", "-m", "merge feature")
    commits = GitReview(root).commit_log()
    assert commits[0]["parents"] == [main, feature]
    assert "HEAD -> main" in commits[0]["decorations"]
    assert {item["hash"] for item in commits} == {base, feature, main, git(root, "rev-parse", "HEAD")}
    assert next(item for item in commits if item["hash"] == base)["parents"] == []
    assert "tag: v1" in next(item for item in commits if item["hash"] == feature)["decorations"]


def test_commit_log_includes_detached_head_and_handles_unborn_repo(tmp_path):
    root = repository(tmp_path)
    git(root, "checkout", "--detach")
    git(root, "commit", "--allow-empty", "-m", "detached commit")
    assert GitReview(root).commit_log()[0]["subject"] == "detached commit"
    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init", "-b", "main")
    assert GitReview(empty).commit_log() == []


def test_branch_refs_include_local_remote_current_and_tracking(tmp_path):
    root = repository(tmp_path)
    git(root, "branch", "feature/nested/topic")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "-u", "origin", "main")
    git(root, "push", "origin", "feature/nested/topic")
    git(root, "commit", "--allow-empty", "-m", "local only")

    refs = GitReview(root).branch_refs()
    by_name = {item["name"]: item for item in refs}
    assert by_name["main"] == {
        "full_name": "refs/heads/main",
        "name": "main",
        "commit": git(root, "rev-parse", "main"),
        "upstream": "origin/main",
        "tracking": "ahead 1",
        "current": True,
        "kind": "local",
    }
    assert by_name["feature/nested/topic"]["kind"] == "local"
    assert by_name["origin/main"]["kind"] == "remote"
    assert by_name["origin/feature/nested/topic"]["kind"] == "remote"
    assert all(item["name"] != "origin/HEAD" for item in refs)


def test_commit_log_can_be_scoped_to_branch(tmp_path):
    root = repository(tmp_path)
    git(root, "checkout", "-b", "feature/topic")
    git(root, "commit", "--allow-empty", "-m", "feature only")
    git(root, "checkout", "main")
    git(root, "commit", "--allow-empty", "-m", "main only")
    review = GitReview(root)
    assert [item["subject"] for item in review.commit_log("refs/heads/feature/topic")] == [
        "feature only", "base"
    ]
    assert [item["subject"] for item in review.commit_log("refs/heads/main")] == [
        "main only", "base"
    ]
    with pytest.raises(ProcessError):
        review.commit_log("missing")


def test_repository_discovery_finds_nested_roots_and_worktree_files(tmp_path):
    root = repository(tmp_path)
    apps = root / "apps"
    apps.mkdir()
    nested = repository(apps)
    linked = root / "linked"
    git(root, "worktree", "add", "-b", "linked-branch", str(linked))
    dependencies = root / "node_modules"
    dependencies.mkdir()
    repository(dependencies)
    outside = tmp_path / "outside"
    outside.mkdir()
    repository(outside)
    (root / "escape").symlink_to(outside, target_is_directory=True)
    assert [item["path"] for item in discover_repositories(root)] == [str(root), str(nested), str(linked)]


def test_repository_discovery_respects_nested_ignore_rules_and_negations(tmp_path):
    root = repository(tmp_path)
    (root / ".gitignore").write_text("generated/\n")
    for folder in ("generated", "apps/hidden", "apps/keep", "other/hidden"):
        parent = root / folder
        parent.mkdir(parents=True)
        repository(parent)
    (root / "apps/.gitignore").write_text("/*/\n!/keep/\n")
    assert {item["relative_path"] for item in discover_repositories(root)} == {
        ".", "apps/keep/repo", "other/hidden/repo",
    }


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


def test_git_actions_expand_folder_scopes_recursively(tmp_path):
    root = repository(tmp_path)
    (root / "src/nested").mkdir(parents=True)
    (root / "src/one.txt").write_text("one\n")
    (root / "src/nested/two.txt").write_text("two\n")
    review = GitReview(root)

    review.action("stage", recursive_paths=["src"])
    assert review.snapshot()["status_summary"]["staged"] == 2
    review.action("unstage", recursive_paths=["src"])
    assert review.snapshot()["status_summary"]["staged"] == 0
    review.action("delete_untracked", recursive_paths=["src"])
    assert not (root / "src/one.txt").exists()
    assert not (root / "src/nested/two.txt").exists()


@pytest.mark.parametrize("scope", ["", "../outside", "/absolute", ".git", "src/../other"])
def test_git_actions_reject_unsafe_recursive_scopes(tmp_path, scope):
    root = repository(tmp_path)
    (root / "src").mkdir()
    (root / "src/new.txt").write_text("new\n")
    with pytest.raises(ProcessError):
        GitReview(root).action("stage", recursive_paths=[scope])


def test_recursive_actions_preserve_siblings_index_and_untracked_files(tmp_path):
    root = repository(tmp_path)
    (root / "src/deep").mkdir(parents=True)
    (root / "src/deep/file.txt").write_text("base\n")
    (root / "src/deleted.txt").write_text("base\n")
    (root / "src-other").mkdir()
    (root / "src-other/file.txt").write_text("base\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "nested base")
    review = GitReview(root)
    (root / "src/deep/file.txt").write_text("staged\n")
    git(root, "add", "src/deep/file.txt")
    (root / "src/deep/file.txt").write_text("unstaged\n")
    (root / "src/deleted.txt").unlink()
    (root / "src/untracked.txt").write_text("keep\n")
    (root / "src-other/file.txt").write_text("sibling\n")
    review.action("discard", recursive_paths=["src"])
    assert (root / "src/deep/file.txt").read_text() == "staged\n"
    assert (root / "src/deleted.txt").read_text() == "base\n"
    assert (root / "src/untracked.txt").read_text() == "keep\n"
    assert (root / "src-other/file.txt").read_text() == "sibling\n"
    (root / "src/deleted.txt").unlink()
    git(root, "add", "src/deleted.txt")
    review.action("stage", recursive_paths=["src"])
    assert review.snapshot()["status_summary"]["staged"] == 3


def test_recursive_rename_source_selects_destination_once(tmp_path):
    root = repository(tmp_path)
    (root / "before").mkdir()
    (root / "after").mkdir()
    git(root, "mv", "tracked.txt", "before/file.txt")
    git(root, "commit", "-m", "move to folder")
    git(root, "mv", "before/file.txt", "after/file.txt")
    review = GitReview(root)
    assert len(review.known_paths([], ["before", "after"])) == 1
    review.action("unstage", recursive_paths=["before"])
    assert git(root, "diff", "--cached", "--name-only") == ""


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
    assert 'id="git-repository-select"' in page
    assert 'id="git-file-actions-menu"' in script
    assert 'id="git-file-context-menu"' in script
    assert 'data-git-action="delete_untracked"' in script
    assert 'data-git-view="log"' in page
    assert "function graphRows(commits)" in script
    assert "git-log-workbench" in css


def test_commit_message_generation_uses_staged_diff_and_workspace_review_model(tmp_path, monkeypatch):
    root = repository(tmp_path)
    (root / "tracked.txt").write_text("first\nupdated\n")
    git(root, "add", "tracked.txt")
    workspace = Workspace(id="repo", name="Repository", repository=str(root))
    workspaces = WorkspaceStore(tmp_path / "workspaces.json")
    workspaces.save(WorkspaceConfiguration(workspaces=[workspace], default_workspace_id=workspace.id))
    monkeypatch.setattr(web, "workspace_store", workspaces)
    monkeypatch.setattr(web, "workflow_store", WorkflowStore(tmp_path / "workflow.json"))
    seen = {}

    def generate(step, prompt, *, cwd, timeout):
        seen.update(step=step, prompt=prompt, cwd=cwd, timeout=timeout)
        return "Update tracked-file content"

    monkeypatch.setattr(web, "_generate_commit_message", generate)
    response = client.post(
        "/api/git/commit-message", json={"workspace_id": workspace.id, "repository": str(root)}
    )

    assert response.status_code == 200
    assert response.json() == {"message": "Update tracked-file content", "model": "default"}
    assert seen["step"].kind == "review"
    assert "+updated" in seen["prompt"]
    assert seen["cwd"] != root


def test_commit_message_generation_requires_staged_changes(tmp_path, monkeypatch):
    root = repository(tmp_path)
    workspace = Workspace(id="repo", name="Repository", repository=str(root))
    workspaces = WorkspaceStore(tmp_path / "workspaces.json")
    workspaces.save(WorkspaceConfiguration(workspaces=[workspace], default_workspace_id=workspace.id))
    monkeypatch.setattr(web, "workspace_store", workspaces)

    response = client.post(
        "/api/git/commit-message", json={"workspace_id": workspace.id, "repository": str(root)}
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Stage one or more changes before generating a commit message."


def test_git_view_javascript_parses():
    result = subprocess.run(
        ["node", "--check", "src/ninerouter_orchestrator/web_assets/git-view.js"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_commit_graph_lanes_connect_merges_and_shared_parents():
    script = Path("src/ninerouter_orchestrator/web_assets/git-view.js").read_text()
    graph = script[script.index("  function graphRows("):script.index("  function graphColor(")]
    source = "import assert from 'node:assert/strict';\n" + graph + """
const commits = [
  {hash:'merge',parents:['main','feature']},
  {hash:'main',parents:['base']},
  {hash:'feature',parents:['base']},
  {hash:'base',parents:[]},
];
const rows = graphRows(commits);
assert.deepEqual(rows[0].next, ['main', 'feature']);
assert.deepEqual(rows[1].next, ['base', 'feature']);
assert.equal(rows[2].lane, 1);
assert.deepEqual(rows[2].next, ['base']);
assert.equal(rows[3].lane, 0);
assert.deepEqual(rows[3].next, []);
assert.equal(rows[0].incoming, false);
assert.equal(rows[3].incoming, true);
for (let index = 1; index < rows.length; index++) {
  assert.deepEqual(rows[index].before, rows[index - 1].next);
}
const disconnected = graphRows([{hash:'a',parents:['base']},{hash:'b',parents:[]},{hash:'base',parents:[]}]);
assert.equal(disconnected[1].lane, 1);
assert.deepEqual(disconnected[1].next, ['base']);
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_branch_tree_groups_slash_delimited_refs():
    script = Path("src/ninerouter_orchestrator/web_assets/git-view.js").read_text()
    tree = script[script.index("  function branchTree("):script.index("  function renderBranchNodes(")]
    source = "import assert from 'node:assert/strict';\n" + tree + """
const root = branchTree([
  {name:'main'},
  {name:'feature/auth/login'},
  {name:'feature/auth/logout'},
  {name:'origin/main'},
]);
assert.equal(root.children.get('main').ref.name, 'main');
assert.equal(root.children.get('feature').children.get('auth').children.get('login').ref.name, 'feature/auth/login');
assert.equal(root.children.get('feature').children.get('auth').children.get('logout').ref.name, 'feature/auth/logout');
assert.equal(root.children.get('origin').children.get('main').ref.name, 'origin/main');
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_git_diff_uses_bounded_scroll_regions_without_split_overlap():
    diff_script = client.get("/assets/git-diff.js").text
    css = client.get("/assets/styles.css").text

    assert 'class="git-diff-scroll" tabindex="0" role="region"' in diff_script
    assert ".git-file-list { min-height: 0; overflow: auto; overscroll-behavior: contain; scrollbar-gutter: stable;" in css
    assert ".git-file-nav > :not(.git-file-list) { flex-shrink: 0; }" in css
    assert ".git-diff-scroll { max-width: 100%; max-height: min(72vh, 760px); overflow: auto; overscroll-behavior: contain; scrollbar-gutter: stable both-edges; }" in css
    assert ".git-diff-scroll:focus-visible" in css
    assert ".git-diff-table { width: max-content; min-width: 100%; border-collapse: collapse; table-layout: auto;" in css
    assert ".git-diff-table .diff-code { min-width: 42rem;" in css
    assert ".git-diff-table.split .diff-code { min-width: 32rem; }" in css
    assert ".git-diff-table code { display: block; width: max-content; min-width: 100%;" in css


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
