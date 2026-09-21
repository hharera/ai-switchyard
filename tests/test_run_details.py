import json
import subprocess
from pathlib import Path


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
