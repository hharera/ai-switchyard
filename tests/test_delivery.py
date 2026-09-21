from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ninerouter_orchestrator import web
from ninerouter_orchestrator.cli_config import CliConfiguration
from ninerouter_orchestrator.delivery_config import DeliveryConfig, DeliveryConfigStore
from ninerouter_orchestrator.models import CommandResult
from ninerouter_orchestrator.web import app

client = TestClient(app, headers={"x-switchyard-client": "local-ui"})


def test_delivery_defaults_round_trip(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "delivery_store", DeliveryConfigStore(tmp_path / "delivery.json"))
    config = DeliveryConfig(
        mode="pr",
        remote="upstream",
        branch="automation/{repository}/{run_id}",
        base_branch="develop",
    )
    assert client.put("/api/delivery", json=config.model_dump()).status_code == 200
    assert client.get("/api/delivery").json() == config.model_dump()


def test_delivery_rejects_unsafe_names():
    with pytest.raises(ValueError, match="named Git remote"):
        DeliveryConfig(mode="push", remote="https://example.com/repo", branch="safe")
    with pytest.raises(ValueError, match="valid Git branch"):
        DeliveryConfig(mode="push", branch="../unsafe")
    with pytest.raises(ValueError, match="placeholders"):
        DeliveryConfig(mode="push", branch="feature/{unknown}")


class FakeRepository:
    root = Path("/tmp/example")

    def __init__(self):
        self.pushed = None

    def push_branch(self, local, target, remote):
        self.pushed = (local, target, remote)

    def git(self, *args):
        assert args == ("remote", "get-url", "--push", "origin")
        return SimpleNamespace(stdout="git@github.com:owner/example.git\n")


def runner(repository):
    return SimpleNamespace(
        repository=repository,
        settings=SimpleNamespace(command_timeout_seconds=30),
        save_run=lambda result: None,
    )


def test_push_delivery_uses_explicit_remote_branch():
    repository = FakeRepository()
    result = {"status": "approved", "run_id": "abc123", "integration_branch": "local/run"}
    delivered = web._deliver(
        runner(repository),
        result,
        DeliveryConfig(mode="push", branch="review/{run_id}"),
        "Implement feature",
        CliConfiguration(),
    )
    assert repository.pushed == ("local/run", "review/abc123", "origin")
    assert delivered["status"] == "pushed"


def test_pr_delivery_pushes_then_uses_gh(monkeypatch):
    repository = FakeRepository()
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return CommandResult(
            command="gh pr create",
            return_code=0,
            stdout="https://github.com/owner/example/pull/1\n",
            stderr="",
            duration_seconds=0.1,
        )

    monkeypatch.setattr(web, "run_process", fake_run)
    result = {"status": "approved", "run_id": "abc123", "integration_branch": "local/run"}
    delivered = web._deliver(
        runner(repository),
        result,
        DeliveryConfig(mode="pr", branch="review/{run_id}", base_branch="main", draft=True),
        "Implement feature",
        CliConfiguration(),
    )
    assert repository.pushed == ("local/run", "review/abc123", "origin")
    assert captured["command"][:5] == ["gh", "pr", "create", "--repo", "github.com/owner/example"]
    assert "--draft" in captured["command"]
    assert delivered["status"] == "pr_opened"


def test_unapproved_run_is_never_delivered():
    repository = FakeRepository()
    delivered = web._deliver(
        runner(repository),
        {"status": "needs_repair", "run_id": "abc", "integration_branch": "local/run"},
        DeliveryConfig(mode="push", branch="review/{run_id}"),
        "Implement feature",
        CliConfiguration(),
    )
    assert delivered["status"] == "skipped"
    assert repository.pushed is None


def test_delivery_ui_is_served():
    page = client.get("/").text
    assert 'href="#delivery"' in page
    assert 'id="dispatch-delivery"' in page
    assert 'id="delivery-form"' in page
