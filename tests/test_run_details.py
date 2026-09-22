import json
import subprocess
from pathlib import Path

import pytest


def test_run_details_are_structured_and_escape_untrusted_text():
    script = Path("src/ninerouter_orchestrator/web_assets/run-details.js").read_text(
        encoding="utf-8"
    )
    job = {
        "id": "dispatch-1",
        "status": "completed",
        "repository": "/repo/Example",
        "request": "<img src=x onerror=alert(1)>",
        "result": {
            "plan": {
                "objective": "Close coverage gaps",
                "assumptions": ["No deployment"],
                "tickets": [
                    {
                        "id": "QA-1",
                        "title": "Verify accounts",
                        "description": "Test recovery",
                        "acceptance_criteria": ["Token is single-use"],
                        "dependencies": ["QA-0"],
                        "validation_commands": ["npm test"],
                        "affected_areas": ["src/accounts.ts"],
                    }
                ],
            }
        },
    }
    output = subprocess.check_output(
        ["node", "-e", script + "\nconsole.log(renderRunDetails(" + json.dumps(job) + "));"],
        text=True,
        encoding="utf-8",
    )
    for value in (
        "completed",
        "Workflow run",
        "QA-1",
        "Acceptance criteria",
        "Depends on",
        "npm test",
        "View raw JSON",
    ):
        assert value in output
    assert "<img" not in output
    assert "&lt;img" in output
    assert '<details class="ticket-card"' in output
    assert 'data-detail-key="raw"' in output


def test_failed_run_without_result_renders_error():
    script = Path("src/ninerouter_orchestrator/web_assets/run-details.js").read_text(
        encoding="utf-8"
    )
    output = subprocess.check_output(
        [
            "node",
            "-e",
            script
            + '\nconsole.log(renderRunDetails({status:"failed", error:"Repository is dirty"}));',
        ],
        text=True,
        encoding="utf-8",
    )
    assert "Run stopped" in output
    assert "Repository is dirty" in output
    assert "Workflow run" in output


def test_running_job_renders_streamed_progress_and_candidate_state():
    script = Path("src/ninerouter_orchestrator/web_assets/run-details.js").read_text(
        encoding="utf-8"
    )
    job = {
        "id": "dispatch-live",
        "status": "running",
        "stage": "Selecting the best candidate for UI-1",
        "activity": [{"at": "2026-09-22T00:00:00+00:00", "message": "Candidate 1 finished"}],
        "result": {
            "plan": {"objective": "Stream progress", "tickets": [{"id": "UI-1", "title": "Live details"}]},
            "tickets": [{"ticket": "UI-1", "candidates": [{
                "status": "completed", "request": {"fork_number": 1, "combo": "fast"},
            }]}],
        },
    }
    output = subprocess.check_output(
        ["node", "-e", script + "\nconsole.log(renderRunDetails(" + json.dumps(job) + "));"],
        text=True, encoding="utf-8",
    )

    assert "Live progress" in output
    assert "Selecting the best candidate for UI-1" in output
    assert "1 candidate ready" in output
    assert "Candidate 1 finished" in output
    assert "Candidate results" in output


def test_run_drawer_uses_event_stream_with_polling_fallback():
    script = Path("src/ninerouter_orchestrator/web_assets/app.js").read_text(encoding="utf-8")
    page = Path("src/ninerouter_orchestrator/web_assets/index.html").read_text(encoding="utf-8")

    assert "new EventSource" in script
    assert "/events`" in script
    assert 'source.addEventListener("job"' in script
    assert "Refreshing every 3 seconds" in script
    assert "stopRunStream();" in script
    assert 'id="run-stream-status" role="status" aria-live="polite"' in page


def test_streamed_progress_uses_real_validation_results_and_escapes_activity():
    script = Path("src/ninerouter_orchestrator/web_assets/run-details.js").read_text(
        encoding="utf-8"
    )
    job = {
        "status": "running",
        "stage": "Reviewing changes",
        "activity": [{"message": "<img src=x onerror=alert(1)>"}],
        "result": {
            "plan": {"objective": "Check integration", "tickets": [
                {"id": "T1"}, {"id": "T2"},
            ]},
            "tickets": [
                {"ticket": "T1", "integration": {"commands": [{"return_code": 0}]}},
                {"ticket": "T2", "integration": {"commands": [{"return_code": 1}]}},
            ],
        },
    }
    output = subprocess.check_output(
        ["node", "-e", script + "\nconsole.log(renderRunDetails(" + json.dumps(job) + "));"],
        text=True, encoding="utf-8",
    )
    assert "1 of 2 tasks integrated" in output
    assert "Integration checks failed" in output
    assert "<img" not in output
    assert "&lt;img" in output


def test_plan_json_text_renders_as_structured_ui():
    script = Path("src/ninerouter_orchestrator/web_assets/run-details.js").read_text(
        encoding="utf-8"
    )
    plan = {
        "objective": "Make test OTP reliable",
        "assumptions": ["Dev and test only"],
        "tickets": [{
            "id": "OTP-1",
            "title": "Cover the bypass",
            "description": "Add regression coverage",
            "acceptance_criteria": ["Exactly 000000 succeeds"],
            "dependencies": [],
            "validation_commands": ["./mvnw test"],
            "affected_areas": ["OtpServiceTest.java"],
        }],
    }
    fenced = f"```json\n{json.dumps(plan)}\n```"
    command = script + "\nconsole.log(renderStructuredJson(" + json.dumps(fenced) + "));"
    output = subprocess.check_output(["node", "-e", command], text=True, encoding="utf-8")

    assert 'class="structured-plan"' in output
    assert "Make test OTP reliable" in output
    assert "OTP-1" in output
    assert "Acceptance criteria" in output
    assert "./mvnw test" in output


def test_structured_json_renderer_tolerates_escaped_keys_and_escapes_html():
    script = Path("src/ninerouter_orchestrator/web_assets/run-details.js").read_text(
        encoding="utf-8"
    )
    raw = r'{"objective":"<img src=x>","tickets":[{"acceptance\_criteria":["Safe"]}]}'
    command = script + "\nconsole.log(renderStructuredJson(" + json.dumps(raw) + "));"
    output = subprocess.check_output(["node", "-e", command], text=True, encoding="utf-8")

    assert "&lt;img src=x&gt;" in output
    assert "<img" not in output
    assert "1 acceptance criteria" in output


def test_run_details_accepts_plan_stored_as_json_text():
    script = Path("src/ninerouter_orchestrator/web_assets/run-details.js").read_text(
        encoding="utf-8"
    )
    job = {
        "status": "completed",
        "result": {"plan": json.dumps({
            "objective": "Readable plan",
            "tickets": [{"id": "UI-1", "title": "Render it"}],
        })},
    }
    command = script + "\nconsole.log(renderRunDetails(" + json.dumps(job) + "));"
    output = subprocess.check_output(["node", "-e", command], text=True, encoding="utf-8")

    assert "Readable plan" in output
    assert "UI-1" in output
    assert "Implementation plan" in output


def test_chat_uses_structured_renderer_for_assistant_json_only():
    script = Path("src/ninerouter_orchestrator/web_assets/chat.js").read_text(encoding="utf-8")
    assert 'message.role === "assistant" ? renderStructuredJson(message.content) : ""' in script
    assert 'content.className = "structured-message"' in script
    assert "content.textContent = message.content" in script


@pytest.mark.parametrize("value", [
    "Ordinary assistant text <script>alert(1)</script>",
    '{"objective":"Incomplete",',
    '{"objective":"Malformed tickets","tickets":[null]}',
    '{"unrelated":"JSON"}',
    "null",
])
def test_unrecognized_content_uses_plain_text_fallback(value):
    script = Path("src/ninerouter_orchestrator/web_assets/run-details.js").read_text(
        encoding="utf-8"
    )
    output = subprocess.check_output(
        ["node", "-e", script + "\nconsole.log(renderStructuredJson(" + json.dumps(value) + "));"],
        text=True, encoding="utf-8",
    )
    assert not output.strip()
