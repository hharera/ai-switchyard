"""Read local chat stores without invoking tools or changing their history."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from urllib.parse import unquote, urlsplit

from .paths import data_dir

MAX_FILE = 32 * 1024 * 1024
MAX_MESSAGES = 2000
MAX_TEXT = 100_000
CLINE_INTERNAL_BLOCKS = ("environment_details", "system-reminder", "workspace_diagnostics")


def normalized(value: object) -> str:
    if not isinstance(value, str) or not value or "\0" in value:
        return ""
    try:
        path = Path(value).expanduser()
        return str(path.resolve()) if path.is_absolute() else ""
    except (OSError, ValueError, RuntimeError):
        return ""


def timestamp(value: object) -> str:
    try:
        if isinstance(value, (int, float)):
            value = datetime.fromtimestamp(value / 1000 if value > 10**11 else value, UTC)
        elif isinstance(value, str):
            value = datetime.fromisoformat(value)
        else:
            return ""
        return value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC).isoformat()
    except (ValueError, OverflowError, OSError):
        return ""


def text_content(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        # Deliberately exclude reasoning, tool output, images and instructions.
        return "\n".join(
            part["text"] for part in value if isinstance(part, dict)
            and part.get("type") in {"text", "input_text", "output_text"}
            and isinstance(part.get("text"), str)
        )
    return ""


def cline_content(value: object) -> str:
    content = text_content(value)
    for tag in CLINE_INTERNAL_BLOCKS:
        content = re.sub(
            rf"<{re.escape(tag)}(?:\s[^>]*)?>.*?</{re.escape(tag)}>",
            "",
            content,
            flags=re.DOTALL | re.IGNORECASE,
        )
    return re.sub(r"</?(?:task|user_message|feedback)(?:\s[^>]*)?>", "", content).strip()


def read_json(path: Path):
    if path.stat().st_size > MAX_FILE:
        raise ValueError("History file exceeds the read limit")
    return json.loads(path.read_text(encoding="utf-8"))


def json_lines(path: Path, *, head: bool = False):
    with path.open(encoding="utf-8", errors="replace") as stream:
        budget = 512 * 1024 if head else MAX_FILE
        consumed = 0
        while consumed < budget:
            line = stream.readline(min(2 * 1024 * 1024, budget - consumed))
            if not line:
                break
            consumed += len(line.encode("utf-8"))
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    yield value
            except ValueError:
                continue


def readonly_db(path: Path):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only = ON")
    return db


@dataclass
class HistoryEntry:
    source: str
    native_id: str
    repository: str
    title: str
    updated_at: str
    kind: str
    path: Path
    model: str = ""
    archived: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        key = f"{self.source}:{self.repository}:{self.native_id}"
        return hashlib.sha256(key.encode()).hexdigest()[:32]

    def summary(self) -> dict:
        return {
            "id": self.id, "source": self.source, "title": self.title[:240],
            "repository": self.repository, "updated_at": self.updated_at,
            "model": self.model, "archived": self.archived,
        }


class ChatHistory:
    SOURCES: ClassVar[dict[str, str]] = {
        "switchyard": "Switchyard", "codex": "Codex", "claude-code": "Claude Code",
        "opencode": "OpenCode / 9router", "gemini-cli": "Gemini CLI", "qwen-code": "Qwen Code",
        "cursor": "Cursor", "continue": "Continue", "aider": "Aider", "cline": "Cline",
        "roo": "Roo", "kilo-code": "Kilo Code",
        "amp-cli": "Amp CLI", "hermes-agent": "Hermes Agent",
        "github-copilot": "GitHub Copilot CLI",
        "imports": "Imported chats",
    }

    def __init__(self, home: Path | None = None):
        self.home = home or Path.home()
        self.codex = Path(os.environ.get("CODEX_HOME", str(self.home / ".codex")))
        self.claude = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(self.home / ".claude")))
        self.data = Path(os.environ.get("XDG_DATA_HOME", str(self.home / ".local/share")))
        self.switchyard_data = data_dir(self.home)

    def scan(self, repository: Path) -> tuple[list[HistoryEntry], list[dict]]:
        repository = repository.resolve()
        entries = []
        sources = []
        readers = {
            "switchyard": self._switchyard, "codex": self._codex,
            "claude-code": self._claude, "opencode": self._opencode,
            "gemini-cli": lambda p: self._gemini(p, "gemini-cli"),
            "qwen-code": lambda p: self._gemini(p, "qwen-code"),
            "cursor": self._cursor, "continue": self._continue,
            "aider": self._aider,
            "amp-cli": self._amp, "hermes-agent": self._hermes,
            "github-copilot": self._copilot,
            "imports": self._imports,
            "cline": lambda p: self._cline_family(p, "cline", ("saoudrizwan.claude-dev",)),
            "roo": lambda p: self._cline_family(p, "roo", ("rooveterinaryinc.roo-cline",)),
            "kilo-code": lambda p: self._cline_family(p, "kilo-code", ("kilocode.kilo-code",)),
        }
        for source, reader in readers.items():
            found = []
            warning = ""
            try:
                for entry in reader(repository):
                    if normalized(entry.repository) == str(repository):
                        found.append(entry)
            except (OSError, ValueError, sqlite3.Error, KeyError, TypeError, AttributeError):
                warning = "Some local history could not be read. Refresh after the tool finishes writing."
            unique = {entry.id: entry for entry in found}
            entries.extend(unique.values())
            sources.append({
                "id": source, "name": self.SOURCES[source], "count": len(unique),
                "status": "partial" if warning else "ready" if unique else "empty",
                "detail": warning or ("Local history connected." if unique else "No matching local history found."),
            })
        return sorted(entries, key=lambda e: (e.updated_at, e.id), reverse=True), sources

    def _entry(self, source, path, repository, *, kind=None, title="", native_id="", updated=None, **kw):
        return HistoryEntry(
            source, native_id or str(path), str(repository), title or "Untitled conversation",
            timestamp(updated) or timestamp(path.stat().st_mtime), kind or source, path, **kw,
        )

    def _codex(self, repository):
        seen = set()
        databases = sorted(self.codex.glob("state_*.sqlite"), reverse=True)
        for path in databases[:1]:
            try:
                with closing(readonly_db(path)) as db:
                    for raw in db.execute("SELECT * FROM threads"):
                        row = dict(raw)
                        if normalized(row.get("cwd")) != str(repository):
                            continue
                        seen.add(row["id"])
                        yield self._entry(
                            "codex", path, repository, kind="codex-db", native_id=row["id"],
                            title=row.get("name") or row.get("title", ""),
                            updated=row.get("updated_at_ms") or row.get("updated_at"),
                            model=row.get("model") or row.get("model_provider", ""),
                            archived=bool(row.get("archived")), extra={"rollout": row.get("rollout_path", "")},
                        )
            except sqlite3.Error:
                pass  # Older CLIs may have only rollouts, or a different state schema.
        for folder in ("sessions", "archived_sessions"):
            for path in (self.codex / folder).glob("**/*.jsonl"):
                meta = {}
                title = ""
                for record in json_lines(path, head=True):
                    if record.get("type") == "session_meta":
                        meta = record.get("payload", {})
                        if meta.get("id") in seen or normalized(meta.get("cwd")) != str(repository):
                            break
                    if record.get("type") == "event_msg" and record.get("payload", {}).get("type") == "user_message":
                        title = record["payload"].get("message", "")
                        break
                if normalized(meta.get("cwd")) == str(repository) and meta.get("id") not in seen:
                    seen.add(meta.get("id"))
                    yield self._entry("codex", path, repository, kind="codex-jsonl",
                                      native_id=meta.get("id", str(path)), title=title,
                                      model=meta.get("model_provider", ""), archived=folder == "archived_sessions")

    def _claude(self, repository):
        for path in (self.claude / "projects").glob("*/*.jsonl"):
            for record in json_lines(path, head=True):
                cwd = record.get("cwd")
                if cwd and normalized(cwd) != str(repository):
                    break
                message = record.get("message", {})
                if cwd and record.get("type") == "user" and isinstance(message, dict):
                    title = text_content(message.get("content"))
                    if title.strip():
                        yield self._entry("claude-code", path, repository, title=title,
                                          native_id=record.get("sessionId", str(path)))
                        break

    def _opencode(self, repository):
        root = self.data / "opencode"
        seen = set()
        path = root / "opencode.db"
        if path.is_file():
            with closing(readonly_db(path)) as db:
                for raw in db.execute("SELECT * FROM session"):
                    row = dict(raw)
                    if normalized(row.get("directory")) == str(repository):
                        seen.add(row["id"])
                        yield self._entry("opencode", path, repository, kind="opencode-db",
                                          native_id=row["id"], title=row.get("title", ""),
                                          updated=row.get("time_updated"))
        for path in (root / "storage/session").glob("*/*.json"):
            row = read_json(path)
            if row.get("id") not in seen and normalized(row.get("directory")) == str(repository):
                yield self._entry("opencode", path, repository, native_id=row["id"],
                                  title=row.get("title", ""), updated=row.get("time", {}).get("updated"))

    def _gemini(self, repository, source):
        root = self.home / (".qwen" if source == "qwen-code" else ".gemini") / "tmp"
        project_hash = hashlib.sha256(str(repository).encode()).hexdigest()
        for path in (root / project_hash / "chats").glob("session-*.json"):
            row = read_json(path)
            if row.get("projectHash", project_hash) != project_hash:
                continue
            title = row.get("summary") or next((text_content(m.get("content"))
                                                for m in row.get("messages", []) if m.get("type") == "user"), "")
            yield self._entry(source, path, repository, kind="gemini", title=title,
                              native_id=row.get("sessionId", str(path)), updated=row.get("lastUpdated"))

    def _cursor(self, repository):
        # Cursor encodes the absolute workspace directory in its project folder name.
        encoded = re.sub(r"[^a-zA-Z0-9]", "-", str(repository)).lstrip("-")
        root = self.home / ".cursor/projects" / encoded / "agent-transcripts"
        for path in root.glob("**/*"):
            if path.is_file() and path.suffix in {".txt", ".jsonl"}:
                yield self._entry("cursor", path, repository, title=path.stem)

    def _continue(self, repository):
        for path in (self.home / ".continue/sessions").glob("*.json"):
            row = read_json(path)
            if isinstance(row, dict) and normalized(row.get("workspaceDirectory")) == str(repository):
                yield self._entry("continue", path, repository, title=row.get("title", ""),
                                  native_id=row.get("sessionId", str(path)))

    def _amp(self, repository):
        unreadable = False
        for path in (self.data / "amp/threads").glob("*.json"):
            try:
                row = read_json(path)
                trees = row.get("env", {}).get("initial", {}).get("trees", [])
                # Multi-root sessions cannot be attributed to one exact workspace.
                if len(trees) != 1:
                    continue
                uri = urlsplit(trees[0].get("uri", ""))
                directory = unquote(uri.path)
                if os.name == "nt" and re.match(r"^/[A-Za-z]:/", directory):
                    directory = directory[1:]
                if uri.scheme != "file" or uri.netloc not in {"", "localhost"}:
                    continue
                if normalized(directory) != str(repository):
                    continue
                messages = row.get("messages", [])
                title = row.get("title") or next((text_content(m.get("content"))
                    for m in messages if isinstance(m, dict) and m.get("role") == "user"), "")
                updated = next((m["usage"]["timestamp"] for m in reversed(messages)
                    if isinstance(m, dict) and isinstance(m.get("usage"), dict)
                    and m["usage"].get("timestamp")), row.get("created"))
                yield self._entry("amp-cli", path, repository, kind="amp", title=title,
                                  native_id=row.get("id", str(path)), updated=updated)
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                unreadable = True
        if unreadable:
            raise ValueError("Some Amp history could not be read")

    def _hermes(self, repository):
        root = Path(os.environ.get("HERMES_HOME", str(self.home / ".hermes")))
        path = root / "state.db"
        if not path.is_file():
            return
        with closing(readonly_db(path)) as db:
            for raw in db.execute("SELECT * FROM sessions"):
                row = dict(raw)
                cwd = row.get("cwd")
                if not cwd and row.get("model_config"):
                    try:
                        config = json.loads(row["model_config"])
                        cwd = config.get("cwd") if isinstance(config, dict) else None
                    except ValueError:
                        continue
                if normalized(cwd) != str(repository):
                    continue
                yield self._entry("hermes-agent", path, repository, kind="hermes",
                                  native_id=row["id"], title=row.get("title", ""),
                                  updated=row.get("ended_at") or row.get("started_at"),
                                  model=row.get("model") or "", archived=bool(row.get("archived")))

    def _copilot(self, repository):
        failed = False
        for path in (self.home / ".copilot/session-state").glob("*/events.jsonl"):
            try:
                if path.stat().st_size > MAX_FILE:
                    raise ValueError("History file exceeds the read limit")
                meta, title, cwd, updated = {}, "", "", None
                matches = False
                for row in json_lines(path):
                    data = row.get("data")
                    if not isinstance(data, dict) or row.get("agentId"):
                        continue
                    if row.get("type") in {"session.start", "session.resume"}:
                        if row["type"] == "session.start":
                            meta = data
                        context = data.get("context", {})
                        if isinstance(context, dict) and "cwd" in context:
                            cwd = normalized(context["cwd"])
                    elif row.get("type") == "session.context_changed":
                        cwd = normalized(data.get("cwd"))
                    elif row.get("type") in {"user.message", "assistant.message"} and cwd == str(repository):
                        matches = True
                        updated = row.get("timestamp")
                        if not title and row["type"] == "user.message":
                            title = text_content(data.get("content"))
                if matches:
                    yield self._entry("github-copilot", path, repository, kind="copilot",
                                      native_id=meta.get("sessionId") or path.parent.name,
                                      title=title, updated=updated, model=meta.get("selectedModel") or "")
            except (OSError, ValueError, TypeError):
                failed = True
        if failed:
            raise ValueError("Some Copilot history could not be read")

    def _aider(self, repository):
        path = repository / ".aider.chat.history.md"
        if not path.is_file():
            return
        if path.resolve().parent != repository:
            raise ValueError("History file must belong to this workspace")
        if path.stat().st_size > MAX_FILE:
            raise ValueError("History file exceeds the read limit")
        content = path.read_text(encoding="utf-8", errors="replace")
        starts = list(re.finditer(r"(?m)^# aider chat started at (.+?)\s*$", content))
        if not starts:
            starts = [re.match(r"", content)]
        for index, match in enumerate(starts):
            start = match.end() if match else 0
            end = starts[index + 1].start() if index + 1 < len(starts) else len(content)
            section = content[start:end]
            title = next(
                (item.group(1).strip() for item in re.finditer(r"(?m)^#### (.+?)\s*$", section)),
                "Aider conversation",
            )
            updated = match.group(1).strip() if match and index + 1 < len(starts) else None
            yield self._entry(
                "aider", path, repository, kind="aider-markdown",
                native_id=f"{path}:{start}", title=title, updated=updated,
                extra={"start": start, "end": end},
            )

    def _global_storage_roots(self):
        names = ("Code", "Code - Insiders", "Cursor", "Windsurf", "Antigravity", "VSCodium")
        config = Path(os.environ.get("XDG_CONFIG_HOME", str(self.home / ".config")))
        roots = [config / name / "User/globalStorage" for name in names]
        roots.extend(self.home / "Library/Application Support" / name / "User/globalStorage"
                     for name in names)
        roaming = Path(os.environ.get("APPDATA", str(self.home / "AppData/Roaming")))
        roots.extend(roaming / name / "User/globalStorage" for name in names)
        roots.extend(self.home / name / "data/User/globalStorage" for name in (
            ".vscode-server", ".vscode-server-insiders", ".cursor-server", ".windsurf-server",
        ))
        profiles = [profile / "globalStorage" for root in roots
                    for profile in (root.parent / "profiles").glob("*") if profile.is_dir()]
        return list(dict.fromkeys([*roots, *profiles]))

    def _extension_history(self, root, extension_ids):
        failed = False
        for path in (root / "tasks").glob("*/history_item.json"):
            try:
                row = read_json(path)
                if not isinstance(row, dict):
                    raise TypeError("Invalid task metadata")
                yield row
            except (OSError, ValueError, TypeError):
                failed = True
        state_path = root / "state/taskHistory.json"
        if state_path.is_file():
            rows = read_json(state_path)
            if isinstance(rows, list):
                yield from (row for row in rows if isinstance(row, dict))
        database = root.parent / "state.vscdb"
        if database.is_file():
            with closing(readonly_db(database)) as db:
                for extension_id in extension_ids:
                    row = db.execute(
                        "SELECT value FROM ItemTable WHERE lower(key) = lower(?)", (extension_id,)
                    ).fetchone()
                    if not row:
                        continue
                    state = json.loads(row["value"])
                    history = state.get("taskHistory", []) if isinstance(state, dict) else []
                    yield from (item for item in history if isinstance(item, dict))
        if failed:
            raise ValueError("Some task metadata could not be read")

    def _cline_family(self, repository, source, extension_ids):
        roots = [root / extension_ids[0] for root in self._global_storage_roots()]
        if source == "cline":
            roots.insert(0, self.home / ".cline/data")
        seen = set()
        failed = False
        for root in roots:
            if not root.is_dir():
                continue
            rows = []
            try:
                rows.extend(self._extension_history(root, extension_ids))
            except (OSError, ValueError, TypeError, sqlite3.Error):
                failed = True
            for row in rows:
                native_id = str(row.get("id", ""))
                workspace = row.get("cwdOnTaskInitialization") or row.get("workspace")
                if not re.fullmatch(r"[A-Za-z0-9_-]+", native_id):
                    failed = True
                    continue
                if native_id in seen or normalized(workspace) != str(repository):
                    continue
                task = cline_content(row.get("task"))
                task_dir = root / "tasks" / native_id
                path = task_dir / "ui_messages.json"
                if not path.is_file():
                    path = task_dir / "api_conversation_history.json"
                if not path.is_file():
                    continue
                if not path.resolve().is_relative_to((root / "tasks").resolve()):
                    failed = True
                    continue
                seen.add(native_id)
                yield self._entry(
                    source, path, repository, kind="cline-family", native_id=native_id,
                    title=task or "Untitled conversation", updated=row.get("ts"),
                    model=row.get("modelId") or row.get("apiConfigName", ""),
                    extra={"task": task},
                )
        if failed:
            raise ValueError("Some extension history could not be read")

    def _imports(self, repository):
        key = hashlib.sha256(str(repository).encode()).hexdigest()
        failed = False
        for path in (self.switchyard_data / "chat-imports" / key).glob("*.json"):
            try:
                row = read_json(path)
                if normalized(row.get("repository")) != str(repository):
                    continue
                yield self._entry(
                    "imports", path, repository, kind="import", native_id=row.get("id", str(path)),
                    title=row.get("title", ""), updated=row.get("updated_at"),
                    model=row.get("source_name", "Imported chat"),
                )
            except (OSError, ValueError, AttributeError):
                failed = True
        if failed:
            raise ValueError("Some imported history could not be read")

    def save_imports(self, repository: Path, source_name: str, conversations: list[dict]) -> int:
        repository = repository.resolve()
        key = hashlib.sha256(str(repository).encode()).hexdigest()
        folder = self.switchyard_data / "chat-imports" / key
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        saved = 0
        for conversation in conversations:
            identifier = hashlib.sha256(f"{source_name}:{conversation['id']}".encode()).hexdigest()
            path = folder / f"{identifier}.json"
            if path.exists():
                continue
            row = {**conversation, "id": identifier,
                   "repository": str(repository), "source_name": source_name}
            descriptor, temporary = tempfile.mkstemp(dir=folder, suffix=".tmp")
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(row, stream, ensure_ascii=False)
                Path(temporary).replace(path)
            finally:
                Path(temporary).unlink(missing_ok=True)
            saved += 1
        return saved

    def _switchyard(self, repository):
        key = hashlib.sha256(str(repository).encode()).hexdigest()
        for path in (self.switchyard_data / "chats" / key).glob("*.json"):
            row = read_json(path)
            if normalized(row.get("repository")) == str(repository):
                yield self._entry("switchyard", path, repository, title=row.get("title", ""),
                                  native_id=row.get("id", str(path)), updated=row.get("updated_at"),
                                  model=row.get("tool", ""))

    def save_exchange(self, repository: Path, session_id: str, user: str, reply: str, tool: str):
        key = hashlib.sha256(str(repository).encode()).hexdigest()
        path = self.switchyard_data / "chats" / key / f"{session_id}.json"
        now = datetime.now(UTC).isoformat()
        row = read_json(path) if path.exists() else {
            "id": session_id, "repository": str(repository), "title": user[:240], "messages": [],
        }
        row.update(updated_at=now, tool=tool)
        row["messages"].extend([
            {"role": "user", "content": user, "timestamp": now},
            {"role": "assistant", "content": reply, "timestamp": now, "label": tool},
        ])
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            json.dump(row, stream, ensure_ascii=False)
        temporary.replace(path)

    def messages(self, entry: HistoryEntry) -> dict:
        messages = []
        truncated = False
        total = 0
        for raw in self._messages(entry):
            if not isinstance(raw, dict):
                continue
            if len(messages) >= MAX_MESSAGES or total >= 2_000_000:
                truncated = True
                break
            role = raw.get("role")
            content = text_content(raw.get("content"))
            if role not in {"user", "assistant"} or not content.strip():
                continue
            if len(content) > MAX_TEXT:
                truncated = True
                content = content[:MAX_TEXT] + "\n[Message shortened for display]"
            total += len(content)
            messages.append({"role": role, "content": content,
                             "timestamp": timestamp(raw.get("timestamp")),
                             "label": raw.get("label") or self.SOURCES[entry.source]})
        if entry.path.suffix in {".jsonl", ".txt", ".md"} and entry.path.stat().st_size > MAX_FILE:
            truncated = True
        return {**entry.summary(), "messages": messages, "truncated": truncated}

    def _messages(self, entry):
        if entry.kind == "codex-db":
            for path in sorted(self.codex.glob("thread_history_*.sqlite"), reverse=True)[:1]:
                found = False
                with closing(readonly_db(path)) as db:
                    for row in db.execute(
                        "SELECT item_json, created_at_ms FROM thread_items WHERE thread_id = ? "
                        "AND item_type IN ('userMessage', 'agentMessage') ORDER BY rollout_ordinal",
                        (entry.native_id,),
                    ):
                        item = json.loads(row["item_json"])
                        found = True
                        yield {"role": "user" if item.get("type") == "userMessage" else "assistant",
                               "content": item.get("content") or item.get("text", ""),
                               "timestamp": row["created_at_ms"]}
                if found:
                    return
            rollout = Path(entry.extra.get("rollout") or "/nonexistent")
            if not rollout.resolve().is_relative_to(self.codex.resolve()):
                raise ValueError("Invalid rollout location")
            yield from self._codex_messages(rollout)
        elif entry.kind == "codex-jsonl":
            yield from self._codex_messages(entry.path)
        elif entry.kind == "claude-code":
            for row in json_lines(entry.path):
                message = row.get("message")
                if row.get("type") in {"user", "assistant"} and isinstance(message, dict):
                    yield {"role": message.get("role"), "content": message.get("content"),
                           "timestamp": row.get("timestamp"), "label": message.get("model")}
        elif entry.kind in {"opencode", "opencode-db"}:
            yield from self._opencode_messages(entry)
        elif entry.kind == "gemini":
            for row in read_json(entry.path).get("messages", []):
                yield {"role": "assistant" if row.get("type") in {"gemini", "assistant"} else row.get("type"),
                       "content": row.get("content"), "timestamp": row.get("timestamp"), "label": row.get("model")}
        elif entry.kind == "switchyard":
            yield from read_json(entry.path).get("messages", [])
        elif entry.kind == "import":
            source = read_json(entry.path)
            for row in source.get("messages", []):
                if isinstance(row, dict):
                    yield {**row, "label": source.get("source_name", "Imported chat")}
        elif entry.kind == "continue":
            for row in read_json(entry.path).get("history", []):
                if isinstance(row.get("message"), dict):
                    yield row["message"]
        elif entry.kind == "cursor":
            if entry.path.suffix == ".jsonl":
                for row in json_lines(entry.path):
                    yield row.get("message", row)
            else:
                with entry.path.open(encoding="utf-8", errors="replace") as stream:
                    content = stream.read(MAX_FILE)
                sections = re.split(r"(?m)^(user|assistant):\s*\n", content)
                for i in range(1, len(sections) - 1, 2):
                    yield {"role": sections[i], "content": sections[i + 1]}
        elif entry.kind == "aider-markdown":
            yield from self._aider_messages(entry)
        elif entry.kind == "cline-family":
            yield from self._cline_messages(entry)
        elif entry.kind == "amp":
            for row in read_json(entry.path).get("messages", []):
                if isinstance(row, dict):
                    usage = row.get("usage", {})
                    yield {"role": row.get("role"), "content": row.get("content"),
                           "timestamp": usage.get("timestamp"), "label": usage.get("model")}
        elif entry.kind == "hermes":
            with closing(readonly_db(entry.path)) as db:
                for row in db.execute(
                    "SELECT role, content, timestamp FROM messages WHERE session_id = ? "
                    "AND role IN ('user', 'assistant') ORDER BY timestamp, id", (entry.native_id,),
                ):
                    message = dict(row)
                    try:
                        content = json.loads(message["content"])
                        if isinstance(content, (list, str)):
                            message["content"] = content
                    except (ValueError, TypeError):
                        pass
                    yield message
        elif entry.kind == "copilot":
            cwd = ""
            for row in json_lines(entry.path):
                data = row.get("data")
                if not isinstance(data, dict) or row.get("agentId"):
                    continue
                kind = row.get("type")
                if kind in {"session.start", "session.resume"}:
                    context = data.get("context", {})
                    if isinstance(context, dict) and "cwd" in context:
                        cwd = normalized(context["cwd"])
                elif kind == "session.context_changed":
                    cwd = normalized(data.get("cwd"))
                elif cwd == entry.repository and kind in {"user.message", "assistant.message"}:
                    yield {"role": "user" if kind == "user.message" else "assistant",
                           "content": data.get("content"), "timestamp": row.get("timestamp"),
                           "label": data.get("model")}

    def _codex_messages(self, path):
        # Event messages avoid displaying duplicated response items and injected system context.
        for row in json_lines(path):
            payload = row.get("payload", {})
            if row.get("type") == "event_msg" and payload.get("type") in {"user_message", "agent_message"}:
                yield {"role": "user" if payload["type"] == "user_message" else "assistant",
                       "content": payload.get("message", ""), "timestamp": row.get("timestamp")}

    def _opencode_messages(self, entry):
        if entry.kind == "opencode-db":
            with closing(readonly_db(entry.path)) as db:
                for row in db.execute("SELECT id, data FROM message WHERE session_id = ? ORDER BY time_created, id", (entry.native_id,)):
                    message = json.loads(row["data"])
                    parts = [json.loads(p[0]) for p in db.execute(
                        "SELECT data FROM part WHERE message_id = ? ORDER BY id", (row["id"],))]
                    yield {"role": message.get("role"), "content": parts,
                           "timestamp": message.get("time", {}).get("created"), "label": message.get("modelID")}
        else:
            root = self.data / "opencode/storage"
            if not re.fullmatch(r"[\w-]+", entry.native_id):
                raise ValueError("Invalid session identifier")
            rows = [read_json(path) for path in (root / "message" / entry.native_id).glob("*.json")]
            for row in sorted(rows, key=lambda item: item.get("time", {}).get("created", 0)):
                message_id = row.get("id", "")
                if not re.fullmatch(r"[\w-]+", message_id):
                    continue
                parts = [read_json(p) for p in sorted((root / "part" / message_id).glob("*.json"))]
                yield {"role": row.get("role"), "content": parts,
                       "timestamp": row.get("time", {}).get("created"), "label": row.get("modelID")}

    def _aider_messages(self, entry):
        with entry.path.open(encoding="utf-8", errors="replace") as stream:
            content = stream.read(MAX_FILE)
        content = content[entry.extra.get("start", 0):entry.extra.get("end", len(content))]
        markers = list(re.finditer(r"(?m)^#### (.+?)\s*$", content))
        pending = []
        for index, marker in enumerate(markers):
            pending.append(marker.group(1).strip())
            end = markers[index + 1].start() if index + 1 < len(markers) else len(content)
            reply = content[marker.end():end].strip()
            if not reply:
                continue
            yield {"role": "user", "content": "\n".join(pending)}
            yield {"role": "assistant", "content": reply}
            pending = []
        if pending:
            yield {"role": "user", "content": "\n".join(pending)}

    def _cline_messages(self, entry):
        rows = read_json(entry.path)
        if not isinstance(rows, list):
            raise TypeError("Invalid conversation messages")
        if entry.path.name == "api_conversation_history.json":
            for row in rows:
                if not isinstance(row, dict):
                    continue
                content = cline_content(row.get("content"))
                if row.get("role") in {"user", "assistant"} and content:
                    yield {"role": row["role"], "content": content}
            return
        title = entry.extra.get("task", "")
        if title:
            yield {"role": "user", "content": title}
        first = True
        for row in rows:
            if not isinstance(row, dict):
                continue
            content = cline_content(row.get("text"))
            if first and row.get("type") == "say" and row.get("say") == "text":
                first = False
                if not title:
                    yield {"role": "user", "content": content, "timestamp": row.get("ts")}
                continue
            if not content:
                continue
            if row.get("type") == "say" and row.get("say") == "user_feedback":
                yield {"role": "user", "content": content, "timestamp": row.get("ts")}
            elif (row.get("type") == "say" and row.get("say") in {"text", "completion_result"}) or (
                row.get("type") == "ask" and row.get("ask") in {"followup", "completion_result"}
            ):
                yield {"role": "assistant", "content": content, "timestamp": row.get("ts")}
