import hashlib
import json
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from ninerouter_orchestrator import web
from ninerouter_orchestrator.adapters.codex import CodexAdapter
from ninerouter_orchestrator.chat_history import ChatHistory
from ninerouter_orchestrator.chat_import import parse_export
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
    assert {entry.source for entry in entries} == {"gemini-cli", "qwen-code"}
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


def test_aider_history_is_workspace_local_and_splits_sessions(history, tmp_path):
    path = tmp_path / ".aider.chat.history.md"
    path.write_text(
        "# aider chat started at 2026-01-01 10:00:00\n\n"
        "#### First line  \n#### second line  \n\nFirst answer\n\n"
        "# aider chat started at 2026-01-02 10:00:00\n\n"
        "#### Second question  \n\nSecond answer\n",
        encoding="utf-8",
    )
    entries, _ = history.scan(tmp_path)
    aider = [entry for entry in entries if entry.source == "aider"]
    assert len(aider) == 2
    first = next(entry for entry in aider if entry.title == "First line")
    assert [message["content"] for message in history.messages(first)["messages"]] == [
        "First line\nsecond line", "First answer",
    ]
    assert not history.scan(tmp_path / "other")[0]


def test_cline_roo_and_kilo_are_scoped_and_exclude_internal_context(history, tmp_path):
    cline = history.home / ".cline/data"
    write_json(cline / "state/taskHistory.json", [{
        "id": "c1", "ts": 1767270000000, "task": "Cline task",
        "cwdOnTaskInitialization": str(tmp_path), "modelId": "model-one",
    }])
    write_json(cline / "tasks/c1/api_conversation_history.json", [
        {"role": "user", "content": [{"type": "text", "text": "<task>Cline task</task>"},
                                      {"type": "text", "text": "<environment_details>secret</environment_details>"}]},
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "private"},
                                           {"type": "text", "text": "Cline answer"}]},
        {"role": "user", "content": [{"type": "tool_result", "content": "private output"}]},
    ])

    storage = history.home / ".config/Code/User/globalStorage"
    roo = storage / "rooveterinaryinc.roo-cline"
    write_json(roo / "tasks/r1/history_item.json", {
        "id": "r1", "ts": 1767270000001, "task": "Roo task", "workspace": str(tmp_path),
    })
    write_json(roo / "tasks/r1/api_conversation_history.json", [
        {"role": "user", "content": "Roo question"},
        {"role": "assistant", "content": "Roo answer"},
    ])

    kilo = storage / "kilocode.kilo-code"
    write_json(kilo / "tasks/k1/api_conversation_history.json", [
        {"role": "user", "content": "Kilo question"},
        {"role": "assistant", "content": "Kilo answer"},
    ])
    database = storage / "state.vscdb"
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE ItemTable (key TEXT UNIQUE, value BLOB)")
        db.execute("INSERT INTO ItemTable VALUES (?, ?)", ("kilocode.kilo-code", json.dumps({
            "taskHistory": [{
                "id": "k1", "ts": 1767270000002, "task": "Kilo task",
                "workspace": str(tmp_path),
            }],
        })))

    entries, sources = history.scan(tmp_path)
    family = {entry.source: entry for entry in entries if entry.source in {"cline", "roo", "kilo-code"}}
    assert set(family) == {"cline", "roo", "kilo-code"}
    assert [message["content"] for message in history.messages(family["cline"])["messages"]] == [
        "Cline task", "Cline answer",
    ]
    assert [message["content"] for message in history.messages(family["roo"])["messages"]] == [
        "Roo question", "Roo answer",
    ]
    assert [message["content"] for message in history.messages(family["kilo-code"])["messages"]] == [
        "Kilo question", "Kilo answer",
    ]
    assert all(next(source for source in sources if source["id"] == item)["status"] == "ready"
               for item in family)


def test_amp_and_hermes_are_workspace_scoped(history, tmp_path, monkeypatch):
    amp = history.data / "amp/threads/thread.json"
    write_json(amp, {
        "id": "amp1", "created": 1767270000000, "title": "Amp task",
        "env": {"initial": {"trees": [{"uri": tmp_path.as_uri()}]}},
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Amp question"}]},
            {"role": "assistant", "content": [{"type": "thinking", "thinking": "secret"},
                                               {"type": "text", "text": "Amp answer"}],
             "usage": {"model": "amp-model", "timestamp": "2026-01-01T00:00:00Z"}},
        ],
    })
    hermes = history.home / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes))
    hermes.mkdir()
    with sqlite3.connect(hermes / "state.db") as db:
        db.execute("CREATE TABLE sessions (id, cwd, title, started_at, ended_at, model, "
                   "model_config, archived)")
        db.execute("INSERT INTO sessions VALUES ('h1', ?, 'Hermes task', 1767270000, NULL, "
                   "'hermes-model', NULL, 0)", (str(tmp_path),))
        db.execute("CREATE TABLE messages (id, session_id, role, content, timestamp)")
        db.execute("INSERT INTO messages VALUES (1, 'h1', 'user', 'Hermes question', 1767270000)")
        db.execute("INSERT INTO messages VALUES (2, 'h1', 'assistant', ?, 1767270001)",
                   (json.dumps([{"type": "text", "text": "Hermes answer"}]),))

    entries, _ = history.scan(tmp_path)
    found = {entry.source: entry for entry in entries if entry.source in {"amp-cli", "hermes-agent"}}
    assert set(found) == {"amp-cli", "hermes-agent"}
    assert [message["content"] for message in history.messages(found["amp-cli"])["messages"]] == [
        "Amp question", "Amp answer",
    ]
    assert [message["content"] for message in history.messages(found["hermes-agent"])["messages"]] == [
        "Hermes question", "Hermes answer",
    ]


def test_copilot_events_follow_working_directory_changes(history, tmp_path):
    other = tmp_path / "other"
    path = history.home / ".copilot/session-state/copilot1/events.jsonl"
    write_jsonl(path, [
        {"type": "session.start", "timestamp": "2026-01-01T00:00:00Z", "data": {
            "sessionId": "copilot1", "selectedModel": "copilot-model",
            "context": {"cwd": str(tmp_path)},
        }},
        {"type": "user.message", "timestamp": "2026-01-01T00:00:01Z",
         "data": {"content": "Copilot question"}},
        {"type": "assistant.message", "timestamp": "2026-01-01T00:00:02Z",
         "data": {"content": "Copilot answer", "reasoningText": "private"}},
        {"type": "session.context_changed", "timestamp": "2026-01-01T00:00:03Z",
         "data": {"cwd": str(other)}},
        {"type": "user.message", "timestamp": "2026-01-01T00:00:04Z",
         "data": {"content": "Other workspace"}},
    ])
    entry = next(item for item in history.scan(tmp_path)[0] if item.source == "github-copilot")
    assert entry.model == "copilot-model"
    assert [message["content"] for message in history.messages(entry)["messages"]] == [
        "Copilot question", "Copilot answer",
    ]
    other_entry = next(item for item in history.scan(other)[0] if item.source == "github-copilot")
    assert [message["content"] for message in history.messages(other_entry)["messages"]] == [
        "Other workspace",
    ]


def test_import_requires_preview_selection_and_stays_in_workspace(history_client, history, tmp_path):
    exported = {"conversations": [
        {"title": "Keep", "messages": [{"role": "user", "content": "Question"},
                                          {"role": "assistant", "content": "Answer"}]},
        {"title": "Skip", "messages": [{"role": "user", "content": "Other"}]},
    ]}
    payload = {"workspace_id": "one", "source_name": "ChatGPT", "export": exported,
               "confirmed": False, "selected": []}
    preview = history_client.post("/api/chat/history/import", json=payload)
    assert preview.status_code == 200
    assert [item["title"] for item in preview.json()["conversations"]] == ["Keep", "Skip"]
    payload.update(confirmed=True, selected=[0], repository=str(tmp_path))
    saved = history_client.post("/api/chat/history/import", json=payload)
    assert saved.status_code == 200
    assert saved.json()["imported"] == 1
    assert history_client.post("/api/chat/history/import", json=payload).json()["imported"] == 0
    entries, _ = history.scan(tmp_path)
    imported = next(entry for entry in entries if entry.source == "imports")
    assert imported.model == "ChatGPT"
    assert [message["content"] for message in history.messages(imported)["messages"]] == [
        "Question", "Answer",
    ]
    assert not [entry for entry in history.scan(tmp_path / "other")[0] if entry.source == "imports"]


def test_chatgpt_export_uses_only_visible_active_branch():
    exported = {"title": "Branch", "current_node": "answer", "mapping": {
        "root": {"parent": None, "message": None},
        "question": {"parent": "root", "message": {"author": {"role": "user"},
            "content": {"content_type": "text", "parts": ["Question"]}}},
        "hidden": {"parent": "question", "message": {"author": {"role": "assistant"},
            "content": {"content_type": "text", "parts": ["Hidden"]}, "channel": "analysis"}},
        "answer": {"parent": "question", "message": {"author": {"role": "assistant"},
            "content": {"content_type": "text", "parts": ["Answer"]}, "channel": "final"}},
    }}
    assert [message["content"] for message in parse_export(exported)[0]["messages"]] == [
        "Question", "Answer",
    ]


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
    for name in ("chat-import-form", "chat-import-source", "chat-import-file", "chat-import-selection"):
        assert f'id="{name}"' in page
    script = history_client.get("/assets/chat.js").text
    assert "if (request !== detailRequest) return" in script
    assert "if (selectedHistory) return" in script
    open_history = script[script.index("async function openHistory"):script.index("async function loadTools")]
    assert "list.scrollTop = 0" not in open_history
    assert "displayMessages(body.messages)" in open_history
    import_script = history_client.get("/assets/chat-import.js").text
    assert 'confirmed: true, selected' in import_script
    assert 'new TextEncoder().encode(serialized).length' in import_script
