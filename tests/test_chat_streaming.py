import asyncio
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ninerouter_orchestrator import web
from ninerouter_orchestrator.adapters import codex, nine_router
from ninerouter_orchestrator.chat import ChatRequest
from ninerouter_orchestrator.chat_history import ChatHistory
from ninerouter_orchestrator.cli_config import CliConfigStore
from ninerouter_orchestrator.process import ProcessError, stream_process


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "chat_lock", threading.Lock())
    monkeypatch.setattr(web, "cli_store", CliConfigStore(tmp_path / "clis.json"))
    monkeypatch.setattr(web, "chat_history", ChatHistory(tmp_path / "home"))
    monkeypatch.setattr(web.combo_registry, "refresh", lambda: {"combos": ["Kimi"]})
    return {"repository": str(tmp_path), "tool": "codex",
            "messages": [{"role": "user", "content": "Explain"}],
            "session_id": "12345678-1234-1234-1234-123456789abc"}


def test_stream_progress_final_and_persistence(setup, monkeypatch, tmp_path):
    def respond(self, prompt, *, cwd, emit):
        emit({"type": "thinking", "content": "Checking entry points"})
        emit({"type": "step", "label": "Read", "detail": "README.md"})
        emit({"type": "delta", "content": "Partial"})
        return "Final answer"

    monkeypatch.setattr(codex.CodexAdapter, "respond_stream", respond)
    client = TestClient(web.app, headers={"x-switchyard-client": "local-ui"})
    response = client.post("/api/chat/stream", json=setup)
    events = [json.loads(line) for line in response.text.splitlines() if line]
    assert [event["type"] for event in events] == ["thinking", "step", "delta", "done"]
    assert events[-1]["message"] == "Final answer"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["cache-control"] == "no-store"
    entries, _ = web.chat_history.scan(tmp_path)
    assert web.chat_history.messages(entries[0])["messages"][-1]["content"] == "Final answer"
    assert not web.chat_lock.locked()


def test_progress_is_available_before_completion(setup, monkeypatch):
    release = threading.Event()

    def respond(self, prompt, *, cwd, emit):
        emit({"type": "step", "label": "Read", "detail": "README.md"})
        assert release.wait(3)
        return "Done"

    monkeypatch.setattr(codex.CodexAdapter, "respond_stream", respond)

    async def consume():
        response = web.chat_stream(ChatRequest(**setup))
        first = await anext(response.body_iterator)
        assert json.loads(first)["type"] == "step"
        assert web.chat_lock.locked()
        release.set()
        assert json.loads(await anext(response.body_iterator))["type"] == "done"
        await response.body_iterator.aclose()

    asyncio.run(consume())


def test_stream_failure_is_safe_and_releases_lock(setup, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("PRIVATE_TRANSCRIPT")

    monkeypatch.setattr(codex.CodexAdapter, "respond_stream", fail)
    client = TestClient(web.app, headers={"x-switchyard-client": "local-ui"})
    response = client.post("/api/chat/stream", json=setup)
    assert json.loads(response.text)["type"] == "error"
    assert "PRIVATE_TRANSCRIPT" not in response.text
    assert not web.chat_lock.locked()
    web.chat_lock.acquire()
    assert client.post("/api/chat/stream", json=setup).status_code == 409
    web.chat_lock.release()
    assert client.post("/api/chat/stream", json={**setup, "tool": "unknown"}).status_code == 422
    assert TestClient(web.app).post("/api/chat/stream", json=setup).status_code == 403


def test_codex_stream_keeps_read_only_boundary(monkeypatch, tmp_path):
    def run(command, **kwargs):
        assert "--json" in command
        assert "--ignore-user-config" in command
        assert command[command.index("--sandbox") + 1] == "read-only"
        for event in [
            {"type": "item.completed", "item": {"type": "reasoning", "text": "Summary"}},
            {"type": "item.started", "item": {"type": "command_execution", "command": "rg main"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Answer"}},
        ]:
            kwargs["on_line"](json.dumps(event))
        Path(command[command.index("--output-last-message") + 1]).write_text("Answer")

    monkeypatch.setattr(codex, "stream_process", run)
    events = []
    assert codex.CodexAdapter().respond_stream("Question", cwd=tmp_path, emit=events.append) == "Answer"
    assert [event["type"] for event in events] == ["thinking", "step", "delta"]


def test_router_stream_parses_events_and_denies_writes(monkeypatch, tmp_path):
    def run(command, **kwargs):
        config = json.loads(kwargs["env"]["OPENCODE_CONFIG_CONTENT"])
        assert config["permission"] == {"*": "deny", "read": "allow", "glob": "allow", "grep": "allow"}
        for event in [
            {"part": {"type": "reasoning", "text": "Summary"}},
            {"part": {"type": "tool", "tool": "read", "state": {"title": "README.md"}}},
            {"part": {"type": "text", "text": "Answer"}},
        ]:
            kwargs["on_line"](json.dumps(event))

    monkeypatch.setattr(nine_router, "stream_process", run)
    events = []
    adapter = nine_router.NineRouterReasoner(combo="Kimi")
    assert adapter.respond_stream("Question", cwd=tmp_path, emit=events.append) == "Answer"
    assert [event["type"] for event in events] == ["thinking", "step", "delta"]


def test_process_stream_delivers_lines_and_times_out(tmp_path):
    lines = []
    stream_process([sys.executable, "-u", "-c", "print('first'); print('second')"],
                   cwd=tmp_path, timeout=3, env=None, on_line=lines.append)
    assert lines == ["first\n", "second\n"]
    with pytest.raises(ProcessError, match="timed out"):
        stream_process([sys.executable, "-c", "import time; time.sleep(10)"],
                       cwd=tmp_path, timeout=0.1, env=None, on_line=lines.append)


def test_browser_consumes_split_utf8_and_restores_failed_draft():
    source = Path("src/ninerouter_orchestrator/web_assets/chat.js").read_text()
    sending = source[source.index("  async function sendMessage("):source.index('  form.addEventListener("submit"')]
    harness = r'''
const assert = require('node:assert/strict');
const state = {messages: [], sessionId: 'session'};
const workspace = {id: 'workspace', repository: '/project'};
const conversation = () => state, activeWorkspace = () => workspace;
const input = {removeAttribute() {}, value: ''}, select = {value: 'codex'};
const send = {querySelector: () => ({})}, newChat = {}, refresh = {}, error = {};
const document = {}, location = {hash: '#other'};
const tools = [{id: 'codex', available: true}];
const toolName = () => 'Codex', updateWorkspace = () => {}, loadHistory = () => {};
let pending = false, selectedHistory = null, failed = false;
const rendered = [];
const renderMessages = () => rendered.push(JSON.parse(JSON.stringify(state.messages)));
const fetch = async (url, options) => {
  assert.equal(url, '/api/chat/stream');
  const transcript = JSON.parse(options.body).messages;
  assert.equal(transcript.at(-1).role, 'user');
  assert.ok(transcript.every(message => message.content));
  const events = failed ? [{type: 'error', message: 'Try again'}] : [
    {type: 'thinking', content: 'Inspecting'},
    {type: 'delta', content: 'caf\u00e9'},
    {type: 'done', message: 'caf\u00e9 complete'}
  ];
  const bytes = new TextEncoder().encode(events.map(event => JSON.stringify(event)).join('\n') + '\n');
  let offset = 0;
  return {ok: true, body: {getReader: () => ({read: async () => offset < bytes.length
    ? {value: bytes.slice(offset, ++offset), done: false} : {done: true}})}};
};
'''
    checks = r'''
(async () => {
  await sendMessage('Explain');
  assert.equal(state.messages.length, 2);
  assert.equal(state.messages[1].content, 'caf\u00e9 complete');
  assert.equal(state.messages[1].activity[0].detail, 'Inspecting');
  assert.equal(state.messages[1].streaming, false);
  assert.ok(rendered.some(messages => messages.at(-1)?.content === 'caf\u00e9'));
  failed = true;
  await sendMessage('Follow up');
  assert.equal(state.messages.length, 2);
  assert.equal(state.draft, 'Follow up');
  assert.equal(state.error, 'Try again');
  assert.equal(pending, false);
})().catch(error => { console.error(error); process.exit(1); });
'''
    subprocess.run(["node"], input=harness + sending + checks, text=True, check=True)
