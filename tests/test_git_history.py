import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_git_view import git, repository

from ninerouter_orchestrator import web
from ninerouter_orchestrator.git_view import GitReview
from ninerouter_orchestrator.process import ProcessError
from ninerouter_orchestrator.workspace_config import (
    Workspace,
    WorkspaceConfiguration,
    WorkspaceStore,
)


def commit(root, message, date, author="History Tester"):
    subprocess.run(
        ["git", "-c", "user.name=" + author, "commit", "--allow-empty", "-m", message],
        cwd=root, check=True, capture_output=True,
        env={**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date},
    )
    return git(root, "rev-parse", "HEAD")


def test_history_filters_match_text_hash_author_dates_and_literal_paths(tmp_path):
    root = repository(tmp_path)
    (root / "special[1].txt").write_text("first\n")
    git(root, "add", "special[1].txt")
    target = commit(root, "Fix the needle", "2025-01-05T12:00:00+00:00", "Alice")
    (root / "other.txt").write_text("other\n")
    git(root, "add", "other.txt")
    commit(root, "Unrelated", "2025-03-05T12:00:00+00:00", "Bob")
    review = GitReview(root)
    assert [item["hash"] for item in review.commit_log(query="NEEDLE")] == [target]
    assert [item["hash"] for item in review.commit_log(query=target[:10])] == [target]
    assert [item["hash"] for item in review.commit_log(author="alice")] == [target]
    assert [item["hash"] for item in review.commit_log(since="2025-01-01", until="2025-01-31")] == [target]
    assert [item["hash"] for item in review.commit_log(path="special[1].txt")] == [target]
    assert review.commit_log(author="Bob", query="needle") == []
    with pytest.raises(ProcessError):
        review.commit_log(since="2025-03-01", until="2025-01-01")
    with pytest.raises(ValueError):
        review.commit_log(since="not-a-date")
    with pytest.raises(ProcessError):
        review.commit_log(path="../outside")


def test_history_filter_runs_before_result_limit(tmp_path):
    root = repository(tmp_path)
    for index in range(101):
        git(root, "commit", "--allow-empty", "-m", f"later {index}")
    assert len(GitReview(root).commit_log()) == 100
    assert [item["subject"] for item in GitReview(root).commit_log(query="base")] == ["base"]


def test_commit_inspector_initial_rename_delete_binary_and_dirty_worktree(tmp_path):
    root = repository(tmp_path)
    review = GitReview(root)
    initial = review.commit_detail("HEAD")
    assert initial["comparison_label"] == "Empty tree (initial commit)"
    assert initial["files"][0]["status"] == "added"
    assert "+first" in review.commit_patch(initial["hash"], "tracked.txt")["patch"]
    git(root, "mv", "tracked.txt", "renamed.txt")
    (root / "binary.dat").write_bytes(b"\0\xff\x00")
    git(root, "add", "binary.dat")
    git(root, "commit", "-m", "rename and binary", "-m", "Full commit body")
    detail = review.commit_detail("HEAD")
    assert "Full commit body" in detail["message"]
    files = {item["path"]: item for item in detail["files"]}
    assert files["renamed.txt"]["old_path"] == "tracked.txt"
    assert files["binary.dat"]["binary"]
    assert review.commit_patch(detail["hash"], "binary.dat")["binary"]
    (root / "renamed.txt").write_text("uncommitted, must not show\n")
    assert "uncommitted" not in review.commit_patch(detail["hash"], "renamed.txt")["patch"]
    git(root, "rm", "-f", "renamed.txt")
    git(root, "commit", "-m", "delete")
    assert review.commit_detail("HEAD")["files"][0]["status"] == "deleted"
    assert "-first" in review.commit_patch("HEAD", "renamed.txt")["patch"]
    with pytest.raises(ProcessError):
        review.commit_patch("HEAD", "../outside")


def test_merge_inspector_compares_first_parent(tmp_path):
    root = repository(tmp_path)
    git(root, "checkout", "-b", "feature")
    (root / "feature.txt").write_text("feature\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "feature")
    git(root, "checkout", "main")
    git(root, "commit", "--allow-empty", "-m", "main")
    parent = git(root, "rev-parse", "HEAD")
    git(root, "merge", "--no-ff", "feature", "-m", "merge")
    detail = GitReview(root).commit_detail("HEAD")
    assert len(detail["parents"]) == 2
    assert detail["base"] == parent
    assert detail["comparison_label"] == "First parent"
    assert [item["path"] for item in detail["files"]] == ["feature.txt"]


def test_multi_repository_history_keeps_duplicate_hashes_and_scopes_details(tmp_path, monkeypatch):
    root = repository(tmp_path)
    nested = root / "apps" / "backend"
    nested.parent.mkdir()
    git(root, "clone", str(root), str(nested))
    store = WorkspaceStore(tmp_path / "workspaces.json")
    store.save(WorkspaceConfiguration(workspaces=[Workspace(id="test", name="Test", repository=str(root))]))
    monkeypatch.setattr(web, "workspace_store", store)
    client = TestClient(web.app, headers={"x-switchyard-client": "local-ui"})
    params = {"workspace_id": "test", "all_repositories": True}
    response = client.get("/api/git/log", params=params)
    assert response.status_code == 200
    history = response.json()
    assert len(history["commits"]) == 2
    assert len({item["hash"] for item in history["commits"]}) == 1
    assert {item["repository_label"] for item in history["commits"]} == {".", "apps/backend"}
    assert {item["repository"] for item in history["refs"]} == {str(root), str(nested)}
    selected = client.get("/api/git/log", params={**params, "ref": "refs/heads/main", "ref_repository": str(nested)})
    assert {item["repository"] for item in selected.json()["commits"]} == {str(nested)}
    detail = client.get("/api/git/commit", params={"workspace_id": "test", "repository": str(nested), "commit": "HEAD"})
    assert detail.status_code == 200
    assert detail.json()["repository"] == str(nested)
    diff = client.get("/api/git/commit-diff", params={"workspace_id": "test", "repository": str(nested), "commit": "HEAD", "path": "tracked.txt"})
    assert diff.status_code == 200
    assert "+first" in diff.json()["patch"]
    outside = client.get("/api/git/commit", params={"workspace_id": "test", "repository": str(tmp_path), "commit": "HEAD"})
    assert outside.status_code == 422


def test_history_inspector_javascript_renders_escaped_metadata():
    script = Path("src/ninerouter_orchestrator/web_assets/git-view.js").read_text()
    source = script[script.index("  function renderCommitDetail("):script.index("  async function loadCommitDetail(")]
    program = """
import assert from 'node:assert/strict';
const panel = {innerHTML:'', querySelectorAll:()=>[], querySelector:()=>null};
const content = {querySelector:()=>panel};
const escapeHtml = value => String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const decorationTags = escapeHtml;
const statusLetter = ()=>'M';
""" + source + """
renderCommitDetail({hash:'abc', repository:'/repo/backend', message:'<script>bad</script>', author:'Alice', author_email:'a@example.test', authored_at:'2025-01-01', committed_at:'2025-01-01', committer:'Alice', parents:[], comparison_label:'Empty tree', file_count:1, files:[{path:'<img>.txt',status:'modified',additions:1,deletions:0}], files_truncated:false}, {subject:'<img>',decorations:'main'});
assert.ok(panel.innerHTML.includes('&lt;script&gt;'));
assert.ok(!panel.innerHTML.includes('<script>'));
assert.ok(panel.innerHTML.includes('data-repository="/repo/backend"'));
assert.ok(panel.innerHTML.includes('data-commit-file="&lt;img&gt;.txt"'));
"""
    result = subprocess.run(["node", "--input-type=module", "-e", program], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
