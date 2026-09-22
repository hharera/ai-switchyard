from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .paths import data_dir


def now() -> str:
    return datetime.now(UTC).isoformat()


class JobStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "jobs.json"
        self.lock = threading.Lock()

    def list(self) -> list[dict]:
        with self.lock:
            return sorted(self._read(), key=lambda job: job["created_at"], reverse=True)

    def get(self, job_id: str) -> dict | None:
        return next((job for job in self.list() if job["id"] == job_id), None)

    def create(self, *, repository: str, request: str, workflow: dict) -> dict:
        job = {
            "id": uuid.uuid4().hex[:10],
            "repository": repository,
            "request": request,
            "workflow": workflow,
            "status": "queued",
            "stage": "Waiting for a worker",
            "created_at": now(),
            "updated_at": now(),
            "result": None,
            "error": None,
            "revision": 1,
            "activity": [],
        }
        with self.lock:
            jobs = self._read()
            jobs.append(job)
            self._write(jobs)
        return job

    def update(self, job_id: str, **changes) -> dict:
        with self.lock:
            jobs = self._read()
            for job in jobs:
                if job["id"] == job_id:
                    timestamp = now()
                    revision = job.get("revision", 0) + 1
                    if changes.get("stage") and changes["stage"] != job.get("stage"):
                        activity = job.get("activity", [])
                        activity.append({
                            "id": revision, "at": timestamp, "message": changes["stage"],
                        })
                        job["activity"] = activity[-200:]
                    job.update(changes, updated_at=timestamp, revision=revision)
                    self._write(jobs)
                    return job
        raise KeyError(job_id)

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _write(self, jobs: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
        temporary.replace(self.path)
