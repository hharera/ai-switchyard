import pytest

from ninerouter_orchestrator.adapters.nine_router import NineRouterExecutor
from ninerouter_orchestrator.config import Settings
from ninerouter_orchestrator.git import GitRepository, slug


def test_combo_is_an_opaque_logical_name() -> None:
    assert NineRouterExecutor.model_id("OpenCode-Go") == "9router/OpenCode-Go"


@pytest.mark.parametrize("value", ["", "/broken", "broken/", "  "])
def test_invalid_combo_name_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        NineRouterExecutor.model_id(value)


def test_combo_names_can_contain_namespaces() -> None:
    assert NineRouterExecutor.model_id("dahl/model-id") == "9router/dahl/model-id"


def test_combo_configuration_preserves_names() -> None:
    settings = Settings(execution_combos="OpenCode-Go, Kimi,OpenAI-Low")
    assert settings.combos == ("OpenCode-Go", "Kimi", "OpenAI-Low")


def test_worktree_names_are_isolated(tmp_path) -> None:
    repository = GitRepository(tmp_path)
    assert repository.branch_name("Run 1", "AD-123", 2) == "orchestrator/run-1/ad-123/fork-2"
    assert slug(" Ticket / Weird ") == "ticket-weird"
