import pytest

from ninerouter_orchestrator.adapters.nine_router import NineRouterReasoner
from ninerouter_orchestrator.config import Settings
from ninerouter_orchestrator.orchestrator import EngineeringOrchestrator
from ninerouter_orchestrator.workflow_config import (
    StepCatalog,
    WorkflowCollection,
    WorkflowDefinition,
    WorkflowRecipe,
    WorkflowStep,
    WorkflowStepBinding,
    WorkflowStore,
    default_recipe,
)


def test_default_recipe_has_required_pipeline():
    recipe = default_recipe()
    assert [step.kind for step in recipe.steps] == [
        "plan",
        "execute",
        "validate",
        "select",
        "merge",
        "review",
    ]
    assert recipe.step("execute").engine == "9router/auto"


def test_deterministic_steps_cannot_use_models():
    payload = default_recipe().model_dump()
    payload["steps"][2]["engine"] = "codex"
    with pytest.raises(ValueError, match="deterministic"):
        WorkflowRecipe.model_validate(payload)


def test_recipe_store_persists_custom_prompts(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.json")
    recipe = default_recipe()
    recipe.name = "Security route"
    recipe.step("review").engine = "9router/Kimi"
    recipe.step("review").system_prompt = "Review authentication boundaries and secret handling."
    store.save(recipe)
    loaded = store.get()
    assert loaded.name == "Security route"
    assert loaded.step("review").engine == "9router/Kimi"
    assert loaded.step("review").system_prompt.startswith("Review authentication")


def test_planning_receives_selected_route_and_prompt(monkeypatch, tmp_path):
    recipe = default_recipe()
    recipe.step("plan").engine = "9router/Kimi"
    recipe.step("plan").system_prompt = "Prioritize backward compatibility."
    captured = {}

    def structured(self, prompt, schema, *, cwd):
        captured.update(combo=self.combo, prompt=prompt, cwd=cwd)
        return "test-result"

    monkeypatch.setattr(NineRouterReasoner, "structured", structured)
    orchestrator = EngineeringOrchestrator(tmp_path, Settings(), recipe)
    assert orchestrator.plan("Add documentation") == "test-result"
    assert captured["combo"] == "Kimi"
    assert "Prioritize backward compatibility." in captured["prompt"]
    assert captured["cwd"] == tmp_path


def test_auto_does_not_become_a_literal_combo():
    assert default_recipe().step("execute").combo is None


def test_workflows_reuse_templates_and_apply_overrides(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.json")
    config = store.get_config()
    security_review = WorkflowStep(
        id="security-review",
        kind="review",
        name="Security review",
        engine="codex",
        system_prompt="Review authentication and authorization boundaries.",
    )
    store.save_steps(StepCatalog(steps=[*config.steps, security_review]))
    second = WorkflowDefinition(
        id="security",
        name="Security workflow",
        steps=[
            *[WorkflowStepBinding(step_id=step.id) for step in config.steps[:5]],
            WorkflowStepBinding(
                step_id="security-review",
                engine="9router/Kimi",
                system_prompt="Perform an adversarial security review.",
            ),
        ],
    )
    store.save_workflows(
        WorkflowCollection(workflows=[config.workflows[0], second], default_workflow_id="security")
    )
    resolved = store.get("security")
    assert resolved.step("plan").id == config.steps[0].id
    assert resolved.step("review").name == "Security review"
    assert resolved.step("review").engine == "9router/Kimi"
    assert resolved.step("review").system_prompt.startswith("Perform an adversarial")


def test_referenced_template_cannot_be_deleted(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.json")
    config = store.get_config()
    with pytest.raises(ValueError, match="missing step"):
        store.save_steps(StepCatalog(steps=config.steps[1:]))


def test_empty_partial_and_reordered_workflows_round_trip(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.json")
    config = store.get_config()
    original_templates = [step.model_dump() for step in config.steps]
    workflow = config.workflows[0]
    workflow.name = "Edited workflow"
    workflow.steps.reverse()
    workflow.steps[0].name = "Custom review"
    empty = WorkflowDefinition(id="empty", name="Empty draft", steps=[])
    partial = WorkflowDefinition(id="partial", name="Planning", steps=[workflow.steps[-1]])
    store.save_workflows(WorkflowCollection(
        workflows=[workflow, empty, partial], default_workflow_id=workflow.id,
    ))
    reloaded = WorkflowStore(store.path).get_config()
    assert reloaded.workflows == [workflow, empty, partial]
    assert reloaded.resolve().steps[0].name == "Custom review"
    assert [step.model_dump() for step in reloaded.steps] == original_templates
    assert reloaded.resolve("empty").steps == []
    reloaded.resolve("partial").require_executable(plan_only=True)
    with pytest.raises(ValueError, match="runner currently requires"):
        reloaded.resolve().require_executable()
    with pytest.raises(ValueError, match="exactly one plan"):
        reloaded.resolve("empty").require_executable(plan_only=True)
