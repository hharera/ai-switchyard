from __future__ import annotations

import codecs
import os
import queue
import signal
import sqlite3
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .job_store import now
from .paths import data_dir
from .process import find_tool, shell_argv, tool_path
from .windows_job import WindowsJob, launch_in_job

ACTIVE = {"running", "stopping"}
OUTPUT_LIMIT = 262_144


class ConsoleError(ValueError):
    pass


@dataclass
class RunningCommand:
    process: subprocess.Popen
    workspace_id: str
    reason: str | None = None
    stopped_at: float | None = None
    thread: threading.Thread | None = None
    job: WindowsJob | None = None


def command_argv(command: str, *, windows: bool | None = None) -> list[str]:
    is_windows = windows if windows is not None else os.name == "nt"
    if is_windows:
        return shell_argv(command, windows=True)
    bash = find_tool("bash")
    if not bash:
        raise ConsoleError("Bash is unavailable. Install Bash or run the command in a terminal.")
    return [bash, "--noprofile", "--norc", "-c", command]


class CommandConsole:
    """Single-server process ownership; history survives, processes do not survive restart."""

    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "commands.sqlite3"
        self.lock = threading.RLock()
        self.db: sqlite3.Connection | None = None
        self.running: dict[str, RunningCommand] = {}
        self.closing = False

    def _db(self):
        if self.db is None:
            self.closing = False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(self.path, check_same_thread=False)
            self.path.chmod(0o600)
            self.db.row_factory = sqlite3.Row
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS tabs (
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                    name TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, tab_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
                    repository TEXT NOT NULL, command TEXT NOT NULL, status TEXT NOT NULL,
                    started_at TEXT NOT NULL, ended_at TEXT, exit_code INTEGER,
                    output TEXT NOT NULL DEFAULT '', truncated INTEGER NOT NULL DEFAULT 0,
                    timeout INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS runs_tab ON runs(tab_id, started_at);
            """)
            self.db.execute(
                "UPDATE runs SET status='interrupted', ended_at=? WHERE status IN ('running','stopping')",
                (now(),),
            )
            self.db.commit()
        return self.db

    def _tab(self, workspace_id, tab_id):
        row = self._db().execute(
            "SELECT * FROM tabs WHERE workspace_id=? AND id=?", (workspace_id, tab_id)
        ).fetchone()
        if row is None:
            raise ConsoleError("Command tab not found in this workspace. Refresh the console.")
        return dict(row)

    def tabs(self, workspace_id):
        with self.lock:
            rows = self._db().execute(
                "SELECT * FROM tabs WHERE workspace_id=? ORDER BY created_at", (workspace_id,)
            ).fetchall()
            tabs = []
            for row in rows:
                tab = dict(row)
                latest = self._db().execute(
                    "SELECT id, command, status, exit_code FROM runs WHERE tab_id=? "
                    "ORDER BY started_at DESC LIMIT 1", (tab["id"],)
                ).fetchone()
                tab["latest"] = dict(latest) if latest else None
                tabs.append(tab)
            return tabs

    def create_tab(self, workspace_id, name):
        with self.lock:
            if len(self.tabs(workspace_id)) >= 24:
                raise ConsoleError("This workspace has 24 tabs. Remove an idle tab before adding one.")
            tab = {
                "id": uuid.uuid4().hex,
                "workspace_id": workspace_id,
                "name": name,
                "created_at": now(),
            }
            self._db().execute("INSERT INTO tabs VALUES (:id,:workspace_id,:name,:created_at)", tab)
            self._db().commit()
            return tab

    def rename_tab(self, workspace_id, tab_id, name):
        with self.lock:
            self._tab(workspace_id, tab_id)
            self._db().execute("UPDATE tabs SET name=? WHERE id=?", (name, tab_id))
            self._db().commit()
            return self._tab(workspace_id, tab_id)

    def remove_tab(self, workspace_id, tab_id):
        with self.lock:
            self._tab(workspace_id, tab_id)
            if self._db().execute(
                "SELECT 1 FROM runs WHERE tab_id=? AND status IN ('running','stopping')", (tab_id,)
            ).fetchone():
                raise ConsoleError("Stop the running command before removing this tab.")
            self._db().execute("DELETE FROM runs WHERE tab_id=?", (tab_id,))
            self._db().execute("DELETE FROM tabs WHERE id=?", (tab_id,))
            self._db().commit()

    def history(self, workspace_id, tab_id, before=None):
        with self.lock:
            self._tab(workspace_id, tab_id)
            rows = self._db().execute(
                "SELECT id,tab_id,workspace_id,repository,command,status,started_at,ended_at,"
                "exit_code,truncated,timeout FROM runs WHERE tab_id=? AND (? IS NULL OR started_at<?) "
                "ORDER BY started_at DESC LIMIT 51", (tab_id, before, before)
            ).fetchall()
            return {"runs": [dict(row) for row in rows[:50]],
                    "next": rows[49]["started_at"] if len(rows) > 50 else None}

    def get_run(self, workspace_id, run_id):
        with self.lock:
            row = self._db().execute(
                "SELECT * FROM runs WHERE workspace_id=? AND id=?", (workspace_id, run_id)
            ).fetchone()
            if row is None:
                raise ConsoleError("Command not found in this workspace. Refresh the console.")
            return dict(row)

    def statuses(self):
        with self.lock:
            return [dict(row) for row in self._db().execute(
                "SELECT workspace_id, status, COUNT(*) AS count FROM runs "
                "WHERE status IN ('running','stopping') GROUP BY workspace_id,status"
            )]

    def has_active_workspace(self, workspace_id):
        with self.lock:
            return any(item.workspace_id == workspace_id for item in self.running.values())

    def start(self, workspace_id, tab_id, repository, command, timeout, env=None):
        if not Path(repository).is_dir():
            raise ConsoleError("Workspace folder is unavailable. Check its repository path.")
        with self.lock:
            self._tab(workspace_id, tab_id)
            if self.closing or len(self.running) >= 16:
                raise ConsoleError("Cannot start another command now. Stop a command and retry.")
            if any(row["status"] in ACTIVE for row in self.history(workspace_id, tab_id)["runs"]):
                raise ConsoleError("This tab already has a running command. Stop it or open another tab.")
            run_id = uuid.uuid4().hex
            db = self._db()
            db.execute(
                "INSERT INTO runs (id,tab_id,workspace_id,repository,command,status,started_at,timeout) "
                "VALUES (?,?,?,?,?,'running',?,?)",
                (run_id, tab_id, workspace_id, str(repository), command, now(), timeout),
            )
            db.commit()
            try:
                command_env = {
                    **os.environ, "PATH": tool_path(), **(env or {}),
                    "TERM": "dumb", "NO_COLOR": "1", "PYTHONUNBUFFERED": "1",
                }
                job = None
                if os.name == "nt":
                    argv = command_argv(command)
                    # cmd /s /c removes these outer quotes; it does not understand the
                    # backslash-escaped quotes that list2cmdline applies to normal argv.
                    command_line = subprocess.list2cmdline(argv[:-1]) + ' "' + argv[-1] + '"'
                    process, job = launch_in_job(
                        command_line, cwd=repository, env=command_env
                    )
                else:
                    process = subprocess.Popen(
                        command_argv(command), cwd=repository, env=command_env,
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        bufsize=0, start_new_session=True,
                    )
            except (ConsoleError, OSError) as exc:
                db.execute(
                    "UPDATE runs SET status='failed', ended_at=?, output=? WHERE id=?",
                    (now(), f"Unable to start the command shell. {exc}", run_id),
                )
                db.commit()
                return self.get_run(workspace_id, run_id)
            owned = RunningCommand(process, workspace_id, job=job)
            self.running[run_id] = owned
            owned.thread = threading.Thread(
                target=self._watch, args=(run_id, owned, timeout), daemon=True,
                name=f"command-{run_id[:8]}",
            )
            owned.thread.start()
            return self.get_run(workspace_id, run_id)

    @staticmethod
    def _terminate(owned, *, force: bool):
        process = owned.process
        if owned.job is not None:
            owned.job.terminate()
            return
        try:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            # macOS may report EPERM for a group whose leader has already exited.
            if process.poll() is None:
                raise

    def stop(self, workspace_id, run_id, reason="stopped"):
        with self.lock:
            run = self.get_run(workspace_id, run_id)
            owned = self.running.get(run_id)
            if owned and owned.reason is None:
                owned.reason = reason
                owned.stopped_at = time.monotonic()
                self._terminate(owned, force=False)
                self._db().execute("UPDATE runs SET status='stopping' WHERE id=?", (run_id,))
                self._db().commit()
                return self.get_run(workspace_id, run_id)
            return run

    def _watch(self, run_id, owned, timeout):
        started = last_flush = time.monotonic()
        output = ""
        truncated = False
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        process = owned.process
        chunks: queue.Queue[bytes | None | Exception] = queue.Queue(maxsize=32)
        reading_stopped = threading.Event()

        def enqueue(chunk):
            while not reading_stopped.is_set():
                try:
                    chunks.put(chunk, timeout=0.1)
                    return
                except queue.Full:
                    pass

        def read_output():
            try:
                while chunk := process.stdout.read(8192):
                    enqueue(chunk)
                    if reading_stopped.is_set():
                        return
            except Exception as exc:  # noqa: BLE001 - report pipe failures to the supervisor.
                enqueue(exc)
            finally:
                enqueue(None)

        reader = threading.Thread(
            target=read_output, daemon=True, name=f"command-output-{run_id[:8]}"
        )
        reader.start()
        output_finished = False
        tree_terminated = False
        status = "failed"
        try:
            while True:
                try:
                    chunk = chunks.get(timeout=0.1)
                    if chunk is None:
                        output_finished = True
                    elif isinstance(chunk, Exception):
                        raise chunk
                    else:
                        output += decoder.decode(chunk)
                        truncated |= len(output) > OUTPUT_LIMIT
                        output = output[-OUTPUT_LIMIT:]
                except queue.Empty:
                    pass
                current = time.monotonic()
                if timeout and current - started >= timeout and owned.reason is None:
                    self.stop(owned.workspace_id, run_id, "timed_out")
                if (owned.stopped_at is not None and current - owned.stopped_at >= 1
                        and not tree_terminated):
                    self._terminate(owned, force=True)
                    tree_terminated = True
                if process.poll() is not None:
                    if not tree_terminated:
                        self._terminate(owned, force=True)
                        tree_terminated = True
                    if output_finished:
                        break
                if current - last_flush >= 0.25:
                    self._output(run_id, output, truncated)
                    last_flush = current
            output += decoder.decode(b"", final=True)
            self._output(run_id, output[-OUTPUT_LIMIT:], truncated or len(output) > OUTPUT_LIMIT)
            status = owned.reason or ("completed" if process.returncode == 0 else "failed")
        except Exception:  # noqa: BLE001 - preserve failures and clean up owned processes.
            self._terminate(owned, force=True)
            process.wait()
            status = "failed"
        finally:
            reading_stopped.set()
            if owned.job is not None:
                owned.job.close()
            reader.join(timeout=2)
            process.stdout.close()
            with self.lock:
                self._db().execute(
                    "UPDATE runs SET status=?, ended_at=?, exit_code=? WHERE id=?",
                    (status, now(), process.returncode, run_id),
                )
                self._db().commit()
                self.running.pop(run_id, None)

    def _output(self, run_id, output, truncated):
        with self.lock:
            self._db().execute(
                "UPDATE runs SET output=?, truncated=? WHERE id=?", (output, truncated, run_id)
            )
            self._db().commit()

    def close(self):
        with self.lock:
            self.closing = True
            owned = list(self.running.items())
            for run_id, item in owned:
                self.stop(item.workspace_id, run_id, "interrupted")
        for _, item in owned:
            item.thread.join(timeout=5)
        with self.lock:
            if self.db is not None and not self.running:
                self.db.close()
                self.db = None
