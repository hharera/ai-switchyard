from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ninerouter_orchestrator import web
from ninerouter_orchestrator.workspace_config import (
    Workspace,
    WorkspaceConfiguration,
    WorkspaceStore,
)

client = TestClient(web.app, headers={"x-switchyard-client": "local-ui"})


def configure_workspace(monkeypatch, tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    store = WorkspaceStore(tmp_path / "workspaces.json")
    store.save(WorkspaceConfiguration(
        workspaces=[Workspace(id="project", name="Project", repository=str(root))],
        default_workspace_id="project",
    ))
    monkeypatch.setattr(web, "workspace_store", store)
    return root


def test_file_tree_lists_workspace_entries_and_hides_git_metadata(monkeypatch, tmp_path):
    root = configure_workspace(monkeypatch, tmp_path)
    (root / "src").mkdir()
    (root / ".git").mkdir()
    (root / "README.md").write_text("# Project\n", encoding="utf-8")

    response = client.get("/api/workspace/files", params={"workspace_id": "project"})

    assert response.status_code == 200
    assert response.json() == {
        "path": "",
        "entries": [
            {"name": "src", "path": "src", "type": "folder", "size": None},
            {"name": "README.md", "path": "README.md", "type": "file", "size": 10},
        ],
    }


def test_file_editor_reads_saves_and_detects_external_changes(monkeypatch, tmp_path):
    root = configure_workspace(monkeypatch, tmp_path)
    target = root / "app.py"
    target.write_text("print('old')\n", encoding="utf-8")

    opened = client.get(
        "/api/workspace/file", params={"workspace_id": "project", "path": "app.py"}
    )
    assert opened.status_code == 200
    assert opened.json()["language"] == "Python"
    assert opened.json()["content"] == "print('old')\n"

    saved = client.put("/api/workspace/file", json={
        "workspace_id": "project", "path": "app.py", "content": "print('new')\n",
        "revision": opened.json()["revision"],
    })
    assert saved.status_code == 200
    assert target.read_text(encoding="utf-8") == "print('new')\n"

    stale = client.put("/api/workspace/file", json={
        "workspace_id": "project", "path": "app.py", "content": "overwrite\n",
        "revision": opened.json()["revision"],
    })
    assert stale.status_code == 409
    assert target.read_text(encoding="utf-8") == "print('new')\n"


def test_file_editor_rejects_traversal_binary_and_symlinks(monkeypatch, tmp_path):
    root = configure_workspace(monkeypatch, tmp_path)
    (root / "binary.dat").write_bytes(b"text\0binary")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (root / "linked.txt").symlink_to(outside)

    traversal = client.get(
        "/api/workspace/file", params={"workspace_id": "project", "path": "../outside.txt"}
    )
    binary = client.get(
        "/api/workspace/file", params={"workspace_id": "project", "path": "binary.dat"}
    )
    symlink = client.get(
        "/api/workspace/file", params={"workspace_id": "project", "path": "linked.txt"}
    )

    assert traversal.status_code == 422
    assert binary.status_code == 415
    assert symlink.status_code == 422
    entries = client.get(
        "/api/workspace/files", params={"workspace_id": "project"}
    ).json()["entries"]
    assert "linked.txt" not in {entry["name"] for entry in entries}


def test_file_tree_creates_renames_and_recursively_deletes_entries(monkeypatch, tmp_path):
    root = configure_workspace(monkeypatch, tmp_path)

    folder = client.post("/api/workspace/entry-action", json={
        "workspace_id": "project", "action": "create_folder", "path": "", "name": "src",
    })
    created = client.post("/api/workspace/entry-action", json={
        "workspace_id": "project", "action": "create_file", "path": "src", "name": "app.py",
    })
    renamed = client.post("/api/workspace/entry-action", json={
        "workspace_id": "project", "action": "rename", "path": "src/app.py", "name": "main.py",
    })

    assert folder.status_code == 200
    assert created.json()["path"] == "src/app.py"
    assert renamed.json()["path"] == "src/main.py"
    assert (root / "src/main.py").is_file()
    info = client.get(
        "/api/workspace/entry-info", params={"workspace_id": "project", "path": "src"}
    ).json()
    assert info["files"] == 1
    assert client.post("/api/workspace/entry-action", json={
        "workspace_id": "project", "action": "delete", "path": "src",
        "revision": info["revision"],
    }).status_code == 422
    deleted = client.post("/api/workspace/entry-action", json={
        "workspace_id": "project", "action": "delete", "path": "src",
        "revision": info["revision"], "confirmed": True,
    })
    assert deleted.status_code == 200
    assert deleted.json()["files"] == 1
    assert not (root / "src").exists()


def test_file_tree_opens_selected_folder_in_desktop_file_manager(monkeypatch, tmp_path):
    root = configure_workspace(monkeypatch, tmp_path)
    folder = root / "src"
    folder.mkdir()
    (folder / "app.py").write_text("print('hello')\n")
    calls = []
    monkeypatch.setattr(web.sys, "platform", "linux")
    monkeypatch.setattr(web, "find_tool", lambda name: "/usr/bin/xdg-open" if name == "xdg-open" else None)
    monkeypatch.setattr(web.subprocess, "Popen", lambda command, **kwargs: calls.append((command, kwargs)))

    opened = client.post("/api/workspace/open-in-file-manager", json={
        "workspace_id": "project", "path": "src/app.py",
    })

    assert opened.status_code == 200
    assert opened.json() == {"path": "src/app.py", "folder": str(folder)}
    assert calls[0][0] == ["/usr/bin/xdg-open", str(folder)]
    assert calls[0][1]["cwd"] == folder
    assert calls[0][1]["start_new_session"] is True


def test_file_tree_open_in_file_manager_reports_missing_desktop_opener(monkeypatch, tmp_path):
    configure_workspace(monkeypatch, tmp_path)
    monkeypatch.setattr(web.sys, "platform", "linux")
    monkeypatch.setattr(web, "find_tool", lambda name: None)
    response = client.post("/api/workspace/open-in-file-manager", json={
        "workspace_id": "project", "path": "",
    })
    assert response.status_code == 422
    assert response.json()["detail"] == "No desktop file manager opener is available."


def test_file_manager_open_protects_paths_but_allows_repository_folders(monkeypatch, tmp_path):
    root = configure_workspace(monkeypatch, tmp_path)
    (root / ".worktrees/nested/.git").mkdir(parents=True)
    (root / "escape").symlink_to(tmp_path, target_is_directory=True)
    calls = []
    monkeypatch.setattr(web, "find_tool", lambda name: "/usr/bin/xdg-open")
    monkeypatch.setattr(web.subprocess, "Popen", lambda *args, **kwargs: calls.append(args))
    endpoint = "/api/workspace/open-in-file-manager"
    for path in ("../outside", str(tmp_path), "escape", ".worktrees/nested/.git", "bad\0path"):
        assert client.post(endpoint, json={"workspace_id": "project", "path": path}).status_code == 422
    assert client.post(endpoint, json={"workspace_id": "project", "path": "missing"}).status_code == 404
    assert client.post(endpoint, json={"workspace_id": "project", "path": ""}, headers={
        "origin": "https://untrusted.example",
    }).status_code == 403
    assert not calls
    for path in ("", ".worktrees"):
        assert client.post(endpoint, json={"workspace_id": "project", "path": path}).status_code == 200
    assert len(calls) == 2


def test_recursive_delete_rechecks_contents_and_warns_about_git_metadata(monkeypatch, tmp_path):
    root = configure_workspace(monkeypatch, tmp_path)
    folder = root / "src"
    folder.mkdir()
    (folder / "one.txt").write_text("one")
    info = client.get(
        "/api/workspace/entry-info", params={"workspace_id": "project", "path": "src"}
    ).json()
    (folder / "two.txt").write_text("two")

    stale = client.post("/api/workspace/entry-action", json={
        "workspace_id": "project", "action": "delete", "path": "src",
        "revision": info["revision"], "confirmed": True,
    })
    assert stale.status_code == 409
    assert folder.exists()

    (folder / ".git").mkdir()
    metadata = client.get(
        "/api/workspace/entry-info", params={"workspace_id": "project", "path": "src"}
    )
    assert metadata.status_code == 200
    assert metadata.json()["contains_git_metadata"] is True
    deleted = client.post("/api/workspace/entry-action", json={
        "workspace_id": "project", "action": "delete", "path": "src",
        "revision": metadata.json()["revision"], "confirmed": True,
    })
    assert deleted.status_code == 200
    assert deleted.json()["contains_git_metadata"] is True
    assert not folder.exists()


@pytest.mark.parametrize("name", ["../outside", ".git", "bad/name", "bad\\name", "trail."])
def test_file_tree_rejects_unsafe_entry_names(monkeypatch, tmp_path, name):
    configure_workspace(monkeypatch, tmp_path)
    response = client.post("/api/workspace/entry-action", json={
        "workspace_id": "project", "action": "create_file", "path": "", "name": name,
    })
    assert response.status_code == 422


def test_entry_actions_preserve_existing_destinations_and_workspace_root(monkeypatch, tmp_path):
    root = configure_workspace(monkeypatch, tmp_path)
    (root / "one.txt").write_text("one")
    (root / "two.txt").write_text("two")
    for action, path in [("create_file", ""), ("rename", "one.txt")]:
        response = client.post("/api/workspace/entry-action", json={
            "workspace_id": "project", "action": action, "path": path, "name": "two.txt",
        })
        assert response.status_code == 409
    for action in ["delete", "rename"]:
        response = client.post("/api/workspace/entry-action", json={
            "workspace_id": "project", "action": action, "path": ".", "name": "elsewhere",
            "confirmed": True,
        })
        assert response.status_code == 422
    assert (root / "one.txt").read_text() == "one"
    assert (root / "two.txt").read_text() == "two"


def test_entry_actions_reject_symlink_parents_and_cross_origin_calls(monkeypatch, tmp_path):
    root = configure_workspace(monkeypatch, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)
    payload = {
        "workspace_id": "project", "action": "create_file", "path": "linked", "name": "new.txt",
    }
    assert client.post("/api/workspace/entry-action", json=payload).status_code == 422
    assert not list(outside.iterdir())
    assert client.post("/api/workspace/entry-action", json={**payload, "path": ""}, headers={
        "origin": "https://untrusted.example",
    }).status_code == 403


def test_git_tree_status_is_not_truncated_to_review_limit(monkeypatch, tmp_path):
    import subprocess

    root = configure_workspace(monkeypatch, tmp_path)
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    (root / "nested").mkdir()
    for index in range(501):
        (root / "nested" / f"{index}.txt").write_text("new")
    response = client.get("/api/workspace/git-status", params={"workspace_id": "project"})
    assert response.status_code == 200
    assert len(response.json()["files"]) == 501
    staged = client.post("/api/git/action", json={
        "workspace_id": "project", "action": "stage", "recursive_paths": ["nested"],
    })
    assert staged.status_code == 200
    assert client.get("/api/workspace/git-status", params={"workspace_id": "project"}).json()[
        "summary"
    ]["staged"] == 501


def test_git_tree_aggregates_nested_repositories_and_line_counts(monkeypatch, tmp_path):
    from test_git_view import git, repository

    root = configure_workspace(monkeypatch, tmp_path)
    for name in ("backend", "frontend"):
        parent = root / name
        parent.mkdir()
        repo = repository(parent)
        (repo / "tracked.txt").write_text("first\nchanged\nextra\n")
        (repo / "new.txt").write_text("one\ntwo\n")
        (repo / ".gitignore").write_text("ignored/\n")
        (repo / "ignored").mkdir()
        (repo / "ignored/output.txt").write_text("hidden\n")
        (repo / "binary.dat").write_bytes(b"binary\0data")
        git(repo, "add", "tracked.txt", ".gitignore")
    (root / "broken/.git").mkdir(parents=True)
    body = client.get("/api/workspace/git-status", params={"workspace_id": "project"}).json()
    assert len(body["repositories"]) == 2
    assert len(body["repository_errors"]) == 1
    files = {file["path"]: file for file in body["files"]}
    modified = files["backend/repo/tracked.txt"]
    assert modified["repository_path"] == "tracked.txt"
    assert modified["additions"] == 2
    assert modified["deletions"] == 1
    assert modified["staged"]
    assert files["frontend/repo/new.txt"]["additions"] == 2
    assert files["frontend/repo/binary.dat"]["binary"]
    assert not any("ignored/" in path for path in files)
    assert body["summary"]["deletions"] == 2
    diff = client.get("/api/git/diff", params={
        "workspace_id": "project", "repository": modified["repository"],
        "path": modified["repository_path"],
    })
    assert diff.status_code == 200
    assert "+extra" in diff.json()["patch"]


def test_git_tree_non_repository_is_an_explicit_empty_state(monkeypatch, tmp_path):
    configure_workspace(monkeypatch, tmp_path)
    response = client.get("/api/workspace/git-status", params={"workspace_id": "project"})
    assert response.status_code == 200
    assert response.json()["repositories"] == []
    assert response.json()["repository_errors"] == []


def test_file_workspace_assets_and_local_ui_protection():
    page = client.get("/").text
    script = client.get("/assets/app.js").text
    files = client.get("/assets/files.js")

    assert 'data-panel="files"' in page
    assert 'id="files-panel"' in page
    assert 'src="/assets/files.js"' in page
    assert 'src="/assets/files-git.js"' in page
    assert 'href="/assets/files.css"' in page
    assert 'href="/assets/inline-agent.css"' in page
    assert 'src="/assets/agent-markdown.js"' in page
    assert 'src="/assets/inline-agent.js"' in page
    assert 'data-inline-agent="files"' in page
    assert 'data-inline-agent="git"' in page
    assert 'files: ["Files", "Explore the project and edit workspace files."]' in script
    assert '"files-panel": ["files"]' in script
    assert files.status_code == 200
    assert "saveActive" in files.text
    assert "Delete from disk" in files.text
    assert "openInFileManager" in files.text
    assert "Open in Files" in page
    assert TestClient(web.app).get(
        "/api/workspace/files", params={"workspace_id": "missing"}
    ).status_code == 403


@pytest.mark.parametrize("path", ["/etc/passwd", ".git/config", "src/../../outside", "bad\0path"])
def test_rejects_invalid_paths_on_read_and_write(monkeypatch, tmp_path, path):
    root = configure_workspace(monkeypatch, tmp_path)
    (root / ".git").mkdir()
    (root / ".git/config").write_text("metadata")
    (root / "src").mkdir()
    params = {"workspace_id": "project", "path": path}
    assert client.get("/api/workspace/file", params=params).status_code == 422
    assert client.put("/api/workspace/file", json={
        **params, "content": "changed", "revision": "0" * 64,
    }).status_code == 422


def test_editor_preserves_file_mode_utf8_bom_and_crlf(monkeypatch, tmp_path):
    root = configure_workspace(monkeypatch, tmp_path)
    target = root / "script.sh"
    target.write_bytes(b"\xef\xbb\xbfhello\r\nworld\r\n")
    target.chmod(0o755)
    mode = target.stat().st_mode
    params = {"workspace_id": "project", "path": "script.sh"}
    body = client.get("/api/workspace/file", params=params).json()
    assert body["line_ending"] == "CRLF"
    saved = client.put("/api/workspace/file", json={
        **params, "content": body["content"].replace("hello", "hi"), "revision": body["revision"],
    })
    assert saved.status_code == 200
    assert target.read_bytes() == b"\xef\xbb\xbfhi\r\nworld\r\n"
    assert target.stat().st_mode == mode
    assert not list(root.glob("*.switchyard.tmp"))


@pytest.mark.parametrize("contents,status_code", [(b"\xff\xfe", 415), (b"x" * (2 * 1024 * 1024 + 1), 413)])
def test_editor_refuses_unsupported_files(monkeypatch, tmp_path, contents, status_code):
    root = configure_workspace(monkeypatch, tmp_path)
    (root / "data").write_bytes(contents)
    assert client.get("/api/workspace/file", params={
        "workspace_id": "project", "path": "data",
    }).status_code == status_code


def test_missing_files_workspaces_and_non_files(monkeypatch, tmp_path):
    root = configure_workspace(monkeypatch, tmp_path)
    (root / "folder").mkdir()
    assert client.get("/api/workspace/files", params={"workspace_id": "missing"}).status_code == 422
    assert client.get("/api/workspace/file", params={
        "workspace_id": "project", "path": "missing",
    }).status_code == 404
    assert client.get("/api/workspace/file", params={
        "workspace_id": "project", "path": "folder",
    }).status_code == 422
    assert client.put("/api/workspace/file", json={
        "workspace_id": "project", "path": "missing", "content": "new", "revision": "0" * 64,
    }).status_code == 404


def test_file_api_rejects_cross_origin_reads_and_writes():
    params = {"workspace_id": "project", "path": "file"}
    headers = {"origin": "https://untrusted.example"}
    assert client.get("/api/workspace/file", params=params, headers=headers).status_code == 403
    assert client.put("/api/workspace/file", json={
        **params, "content": "unsafe", "revision": "0" * 64,
    }, headers=headers).status_code == 403
