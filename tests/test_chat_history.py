import hashlib
import json
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from ninerouter_orchestrator import web
from ninerouter_orchestrator.adapters.codex import CodexAdapter
from ninerouter_orchestrator.chat_history import ChatHistory
from ninerouter_orchestrator.workspace_config import WorkspaceConfiguration, WorkspaceStore


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def write_jsonl(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(value) for value in values), encoding="utf-8")
    return path


@pytest.fixture
def history(tmp_path, monkeypatch):
    for key in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "XDG_DATA_HOME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "home/AppData/Local"))
    return ChatHistory(tmp_path / "home")


def codex_rollout(history, repository, session="c1", archived=False):
    folder = "archived_sessions" if archived else "sessions/2026/01/01"
    return write_jsonl(history.codex / folder / f"{session}.jsonl", [
        {"type": "session_meta", "payload": {"id": session, "cwd": str(repository)}},
        {"type": "response_item", "payload": {"role": "developer", "content": "secret instructions"}},
        {"type": "event_msg", "timestamp": "2026-01-01T12:00:00Z",
         "payload": {"type": "user_message", "message": "Explain architecture"}},
        {"type": "response_item", "payload": {"role": "user", "content": "duplicate"}},
        {"type": "event_msg", "payload": {"type": "agent_message", "message": "The entry point is main.py"}},
    ])


def test_rollout_is_path_scoped_and_excludes_internal_context(history, tmp_path):
    repository = tmp_path / "project"
    repository.mkdir()
    codex_rollout(history, repository)
    codex_rollout(history, tmp_path / "project-other", "other")
    codex_rollout(history, repository / "nested", "nested")
    entries, _ = history.scan(repository)
    assert len(entries) == 1
    assert entries[0].title == "Explain architecture"
    detail = history.messages(entries[0])
    assert [m["content"] for m in detail["messages"]] == ["Explain architecture", "The entry point is main.py"]
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(repository, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are not enabled on this Windows runner")
    assert history.scan(alias)[0][0].id == entries[0].id


def test_codex_database_deduplicates_rollouts_and_reads_desktop_messages(history, tmp_path):
    rollout = codex_rollout(history, tmp_path, archived=True)
    dbpath = history.codex / "state_5.sqlite"
    with sqlite3.connect(dbpath) as db:
        db.execute("CREATE TABLE threads (id, cwd, title, updated_at, rollout_path, archived, model)")
        db.execute("INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?)",
                   ("c1", str(tmp_path), "Saved desktop title", 1767270000, str(rollout), 1, "Model"))
    with sqlite3.connect(history.codex / "thread_history_1.sqlite") as db:
        db.execute("CREATE TABLE thread_items (thread_id, rollout_ordinal, item_type, item_json, created_at_ms)")
        for i, item in enumerate([
            {"type": "userMessage", "content": [{"type": "text", "text": "Question"}]},
            {"type": "reasoning", "text": "Private reasoning"},
            {"type": "agentMessage", "text": "Answer"},
        ]):
            db.execute("INSERT INTO thread_items VALUES (?, ?, ?, ?, ?)",
                       ("c1", i, item["type"], json.dumps(item), 1767270000000))
    entries, _ = history.scan(tmp_path)
    assert len(entries) == 1
    assert entries[0].archived
    assert entries[0].title == "Saved desktop title"
    assert [m["content"] for m in history.messages(entries[0])["messages"]] == ["Question", "Answer"]


def test_claude_reads_text_not_tool_results_or_thinking(history, tmp_path):
    rows = [
        {"type": "user", "cwd": str(tmp_path), "sessionId": "claude1", "message": {"role": "user", "content": "Question"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "secret"}, {"type": "text", "text": "Answer"}]}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "token"}]}},
    ]
    path = write_jsonl(history.claude / "projects/test/claude1.jsonl", rows)
    with path.open("a") as stream:
        stream.write('\n{"incomplete":')
    entries, _ = history.scan(tmp_path)
    assert [m["content"] for m in history.messages(entries[0])["messages"]] == ["Question", "Answer"]


def test_gemini_and_qwen_project_hashes_and_sources(history, tmp_path):
    key = hashlib.sha256(str(tmp_path).encode()).hexdigest()
    for root in (".gemini", ".qwen"):
        write_json(history.home / root / "tmp" / key / "chats/session-one.json", {
            "sessionId": "same-id", "projectHash": key, "lastUpdated": "2026-01-01T00:00:00Z",
            "messages": [{"type": "user", "content": "Question"},
                         {"type": "gemini", "content": "Answer", "thoughts": [{"description": "secret"}]}],
        })
    entries, _ = history.scan(tmp_path)
    assert {entry.source for entry in entries} == {"gemini", "qwen-code"}
    assert len({entry.id for entry in entries}) == 2
    assert len(history.messages(entries[0])["messages"]) == 2
    assert not history.scan(tmp_path / "other")[0]


def test_opencode_legacy_and_database(history, tmp_path):
    root = history.data / "opencode/storage"
    write_json(root / "session/project/s1.json", {"id": "s1", "title": "Legacy", "directory": str(tmp_path)})
    write_json(root / "message/s1/m1.json", {"id": "m1", "role": "user", "time": {"created": 1767270000000}})
    write_json(root / "part/m1/p1.json", {"type": "text", "text": "Legacy question"})
    entries, _ = history.scan(tmp_path)
    assert history.messages(entries[0])["messages"][0]["content"] == "Legacy question"
    with sqlite3.connect(history.data / "opencode/opencode.db") as db:
        db.execute("CREATE TABLE session (id, title, directory, time_updated)")
        db.execute("INSERT INTO session VALUES ('s1', 'Database', ?, 1767270000000)", (str(tmp_path),))
        db.execute("CREATE TABLE message (id, session_id, time_created, data)")
        db.execute("INSERT INTO message VALUES ('m1', 's1', 1, ?)", (json.dumps({"role": "user"}),))
        db.execute("CREATE TABLE part (id, message_id, data)")
        db.execute("INSERT INTO part VALUES ('p1', 'm1', ?)", (json.dumps({"type": "text", "text": "Database question"}),))
    entries, _ = history.scan(tmp_path)
    assert len(entries) == 1
    assert history.messages(entries[0])["messages"][0]["content"] == "Database question"


def test_switchyard_history_persists_across_instances(history, tmp_path):
    sid = "12345678-1234-1234-1234-123456789abc"
    history.save_exchange(tmp_path, sid, "First", "Reply", "codex")
    history.save_exchange(tmp_path, sid, "Second", "Second reply", "9router/Kimi")
    restored = ChatHistory(history.home)
    entries, _ = restored.scan(tmp_path)
    assert len(entries) == 1
    assert entries[0].title == "First"
    assert len(restored.messages(entries[0])["messages"]) == 4
    if os.name == "posix":
        assert entries[0].path.stat().st_mode & 0o777 == 0o600


def test_unreadable_source_does_not_hide_other_sources(history, tmp_path):
    codex_rollout(history, tmp_path)
    path = history.home / ".continue/sessions/broken.json"
    path.parent.mkdir(parents=True)
    path.write_text("{invalid")
    entries, sources = history.scan(tmp_path)
    assert len(entries) == 1
    assert next(s for s in sources if s["id"] == "continue")["status"] == "partial"


@pytest.fixture
def history_client(history, tmp_path, monkeypatch):
    workspaces = WorkspaceStore(tmp_path / "workspaces.json")
    workspaces.save(WorkspaceConfiguration.model_validate({"workspaces": [
        {"id": "one", "name": "One", "repository": str(tmp_path)},
        {"id": "two", "name": "Two", "repository": str(tmp_path / "other")},
    ]}))
    monkeypatch.setattr(web, "workspace_store", workspaces)
    monkeypatch.setattr(web, "chat_history", history)
    return TestClient(web.app, headers={"x-switchyard-client": "local-ui"})


def test_history_api_requires_local_header_and_valid_workspace(history_client, history, tmp_path):
    codex_rollout(history, tmp_path)
    response = history_client.get("/api/chat/history?workspace_id=one")
    assert response.status_code == 200
    body = response.json()
    assert body["repository"] == str(tmp_path)
    assert len(body["conversations"]) == 1
    assert any(source["status"] == "unsupported" for source in body["sources"])
    conversation_id = body["conversations"][0]["id"]
    assert "path" not in body["conversations"][0]
    assert history_client.get(f"/api/chat/history/{conversation_id}?workspace_id=one").status_code == 200
    assert history_client.get(f"/api/chat/history/{conversation_id}?workspace_id=two").status_code == 404
    assert history_client.get("/api/chat/history?workspace_id=missing").status_code == 422
    assert TestClient(web.app).get("/api/chat/history?workspace_id=one").status_code == 403
    assert history_client.get("/api/chat/history?workspace_id=one", headers={"origin": "https://evil.example"}).status_code == 403


def test_chat_api_saves_only_successful_replies(history_client, history, tmp_path, monkeypatch):
    monkeypatch.setattr(CodexAdapter, "respond", lambda *a, **kw: "Saved reply")
    payload = {"workspace_id": "one", "tool": "codex", "session_id": "12345678-1234-1234-1234-123456789abc",
               "messages": [{"role": "user", "content": "Saved question"}]}
    assert history_client.post("/api/chat", json=payload).status_code == 200
    entries, _ = history.scan(tmp_path)
    assert len(entries) == 1
    assert len(history.messages(entries[0])["messages"]) == 2
    payload["session_id"] = "../../somewhere"
    assert history_client.post("/api/chat", json=payload).status_code == 422


def test_history_ui_has_source_and_privacy_controls(history_client):
    page = history_client.get("/").text
    for name in ("chat-history-list", "chat-history-source", "chat-history-search", "chat-history-sources", "chat-history-detail"):
        assert f'id="{name}"' in page
    assert "Imported history is read-only and is never included in new chats." in page
    script = history_client.get("/assets/chat.js").text
    assert "if (request !== detailRequest) return" in script
    assert "if (selectedHistory) return" in script
