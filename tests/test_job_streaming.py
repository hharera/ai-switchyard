import asyncio
import copy
import json
import subprocess
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ninerouter_orchestrator import web
from ninerouter_orchestrator.config import Settings
from ninerouter_orchestrator.job_store import JobStore
from ninerouter_orchestrator.models import Plan, Ticket
from ninerouter_orchestrator.orchestrator import EngineeringOrchestrator
from ninerouter_orchestrator.workflow_config import default_recipe


class Connection:
    disconnected = False

    async def is_disconnected(self):
        return self.disconnected


@pytest.fixture
def stored_job(monkeypatch, tmp_path):
    storage = JobStore(tmp_path / "jobs.json")
    monkeypatch.setattr(web, "store", storage)
    job = storage.create(repository=str(tmp_path), request="Live progress", workflow={})
    return storage, job["id"]


def decode(frame):
    return json.loads(frame.split("data: ", 1)[1])


def test_stream_delivers_partial_results_then_terminal_state(stored_job):
    storage, job_id = stored_job

    async def consume():
        response = await web.job_events(job_id, Connection())
        events = response.body_iterator
        assert decode(await anext(events))["status"] == "queued"
        storage.update(job_id, status="running", stage="Planning", result={"plan": {}})
        partial = decode(await anext(events))
        assert partial["result"] == {"plan": {}}
        assert partial["status"] == "running"
        storage.update(job_id, status="failed", stage="Stopped", error="Provider failed")
        final = decode(await anext(events))
        assert final["result"] == partial["result"]
        assert final["error"] == "Provider failed"
        with pytest.raises(StopAsyncIteration):
            await anext(events)

    asyncio.run(consume())


def test_stream_disconnect_does_not_stop_worker(stored_job):
    storage, job_id = stored_job

    async def consume():
        connection = Connection()
        response = await web.job_events(job_id, connection)
        await anext(response.body_iterator)
        connection.disconnected = True
        with pytest.raises(StopAsyncIteration):
            await anext(response.body_iterator)

    asyncio.run(consume())
    assert storage.get(job_id)["status"] == "queued"
    assert storage.update(job_id, stage="Still working")["stage"] == "Still working"


def test_stream_missing_job_and_reconnect(stored_job):
    storage, job_id = stored_job
    client = TestClient(web.app)
    assert client.get("/api/jobs/missing/events").status_code == 404
    storage.update(job_id, status="completed", stage="Finished")
    first = client.get(f"/api/jobs/{job_id}/events")
    reconnect = client.get(f"/api/jobs/{job_id}/events", headers={"Last-Event-ID": "old"})
    assert first.text == reconnect.text
    assert first.headers["x-accel-buffering"] == "no"
    assert first.headers["cache-control"] == "no-store"


def test_idle_stream_sends_heartbeat_without_duplicate_snapshot(stored_job, monkeypatch):
    _, job_id = stored_job

    async def no_wait(_):
        pass

    monkeypatch.setattr(web.asyncio, "sleep", no_wait)

    async def consume():
        response = await web.job_events(job_id, Connection())
        assert "event: job" in await anext(response.body_iterator)
        assert await anext(response.body_iterator) == ": keep-alive\n\n"
        await response.body_iterator.aclose()

    asyncio.run(consume())


def test_activity_is_thread_safe_bounded_and_persisted(stored_job):
    storage, job_id = stored_job
    threads = [threading.Thread(target=storage.update, args=(job_id,), kwargs={
        "stage": f"Candidate {index}",
    }) for index in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(storage.get(job_id)["activity"]) == 12
    for index in range(205):
        storage.update(job_id, stage=f"Stage {index}")
    stored = JobStore(storage.path).get(job_id)
    assert len(stored["activity"]) == 200
    assert stored["revision"] == 218
    assert stored["activity"][-1]["message"] == "Stage 204"


def test_worker_persists_progress_without_finishing_early(monkeypatch, tmp_path):
    storage = JobStore(tmp_path / "jobs.json")
    monkeypatch.setattr(web, "store", storage)
    monkeypatch.setattr(web, "dispatch_lock", threading.Lock())
    created = storage.create(
        repository=str(tmp_path), request="Progress", workflow=default_recipe().model_dump(),
    )
    observed = []

    class Runner:
        def __init__(self, repository, settings, workflow, mcps, clis, progress):
            self.progress = progress

        def run(self, request):
            self.progress("Plan ready", {"plan": {"objective": "Progress", "tickets": []}})
            observed.append(storage.get(created["id"]))
            raise RuntimeError("Agent failed after planning")

    monkeypatch.setattr(web, "EngineeringOrchestrator", Runner)
    web._work(created["id"], web.JobRequest(
        repository=str(tmp_path), request="Track progress", allow_host_execution=True,
    ), tmp_path)
    assert observed[0]["status"] == "running"
    assert observed[0]["result"]["plan"]["objective"] == "Progress"
    final = storage.get(created["id"])
    assert final["status"] == "failed"
    assert final["result"] == observed[0]["result"]
    assert final["error"] == "Agent failed after planning"
    assert not web.dispatch_lock.locked()


def test_orchestrator_reports_planning_before_publishing_plan(monkeypatch, tmp_path):
    reports = []
    runner = EngineeringOrchestrator(
        tmp_path, Settings(allow_host_execution=True),
        progress=lambda stage, result: reports.append((stage, copy.deepcopy(result))),
    )
    monkeypatch.setattr(runner, "preflight", lambda **kwargs: None)
    monkeypatch.setattr(runner, "plan", lambda _: Plan(
        objective="No changes", tickets=[Ticket(
            id="T1", title="Inspect", description="Inspect the project",
            acceptance_criteria=["Inspection complete"],
            validation_commands=["git diff --check"],
        )],
    ))
    monkeypatch.setattr(runner.repository, "head", lambda: "abc123")

    run = runner.create_run("Inspect the project", plan_only=False)

    assert run["plan"]["objective"] == "No changes"
    stages = [stage for stage, _ in reports]
    assert stages == ["Checking providers", "Planning the implementation"]


def test_run_stream_lifecycle_and_stale_response_guards():
    source = Path("src/ninerouter_orchestrator/web_assets/app.js").read_text()
    functions = source[source.index("function runIsActive"):source.index("function showJob")]
    script = r'''
const assert = require("node:assert/strict");
const window = {EventSource: true};
const runDrawer = {classList: {contains: () => true}};
const runStreamStatus = {textContent: "", dataset: {}};
let selectedJobId = "a", runEventSource = null, streamedJobId = null;
let jobs = [{id: "a", status: "running", revision: 1}];
const seen = [];
function renderJobs() {}
function showJob(id) { seen.push(id); startRunStream(jobs.find(job => job.id === id)); }
class EventSource {
  constructor(url) { this.url = url; this.handlers = {}; }
  addEventListener(name, handler) { this.handlers[name] = handler; }
  close() { this.closed = true; }
  emit(job) { this.handlers.job({data: JSON.stringify(job)}); }
}
'''
    checks = r'''
startRunStream(jobs[0]);
const first = runEventSource;
assert.equal(first.url, "/api/jobs/a/events");
first.emit({id: "a", status: "running", revision: 3, stage: "Planning"});
assert.equal(runEventSource, first);
assert.equal(runStreamStatus.textContent, "Planning");
first.emit({id: "a", status: "queued", revision: 2});
assert.equal(jobs[0].revision, 3);
first.onerror();
assert.equal(runStreamStatus.dataset.state, "reconnecting");
first.emit({id: "a", status: "failed", revision: 4});
assert.equal(first.closed, true);
assert.equal(runEventSource, null);
assert.match(runStreamStatus.textContent, /failed/);
selectedJobId = "b";
jobs.push({id: "b", status: "queued", revision: 1});
startRunStream(jobs[1]);
const second = runEventSource;
first.emit({id: "a", status: "running", revision: 5});
assert.equal(runEventSource, second);
assert.equal(jobs[0].revision, 4);
stopRunStream();
assert.equal(second.closed, true);
second.emit({id: "b", status: "completed", revision: 2});
assert.equal(jobs[1].revision, 1);
delete window.EventSource;
startRunStream(jobs[1]);
assert.equal(runStreamStatus.dataset.state, "fallback");
'''
    subprocess.run(["node", "-e", script + functions + checks], check=True)
