from __future__ import annotations

import json
import threading
from pathlib import Path

from pydantic import Field, model_validator

from .models import StrictModel
from .paths import data_dir

DEFAULT_KINDS = ("plan", "execute", "validate", "select", "merge", "review")
# Keep the advanced passes available as reusable templates, but do not spend quota on
# them in every newly created default workflow.
DEFAULT_WORKFLOW_KINDS = ("plan", "execute", "validate", "review")

DEFAULT_PROMPTS = {
    "plan": "Act as the engineering lead. Produce small dependency-aware tickets with objective acceptance criteria and repository-native validation commands.",
    "execute": "Implement the ticket completely. Keep changes scoped, preserve compatibility, run relevant checks, and report assumptions and risks.",
    "validate": "Inspect the current implementation, run the relevant checks, diagnose failures, and fix issues you find.",
    "select": "Evaluate the current implementation against the request and improve it where another approach would be stronger.",
    "merge": "Make the current implementation cohesive and complete, resolving inconsistencies and integration problems.",
    "review": "Review the integrated result for correctness, missing requirements, regressions, security, and test coverage.",
}


class WorkflowStep(StrictModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    kind: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=80)
    engine: str = Field(min_length=1)
    configuration: str = Field(default="default", min_length=1, max_length=80)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    effort: str | None = Field(default=None, min_length=1, max_length=40)
    system_prompt: str = Field(min_length=1, max_length=12000)

    @property
    def combo(self) -> str | None:
        if self.engine == "opencode" and self.configuration == "9router":
            return None if self.model in {None, "auto"} else self.model
        if self.engine == "9router/auto":
            return None
        return self.engine.removeprefix("9router/") if self.engine.startswith("9router/") else None

    @property
    def rotates_combos(self) -> bool:
        return self.engine == "9router/auto" or (
            self.engine == "opencode"
            and self.configuration == "9router"
            and self.model in {None, "auto"}
        )

    @model_validator(mode="after")
    def validate_engine(self):
        supported = (
            self.engine in {"codex", "opencode"}
            or self.engine.startswith("tool/")
            or self.engine == "9router/auto"
            or self.combo
        )
        if not supported:
            raise ValueError(f"Unsupported engine for {self.name}: {self.engine}")
        return self


class WorkflowRecipe(StrictModel):
    name: str = Field(default="Default engineering route", min_length=1, max_length=100)
    isolated_worktree: bool = True
    steps: list[WorkflowStep]

    def require_executable(self, *, plan_only: bool = False) -> None:
        """Retained for API compatibility; workflows have no required shape."""

    def step(self, kind: str) -> WorkflowStep:
        return next(step for step in self.steps if step.kind == kind)


class WorkflowStepBinding(StrictModel):
    step_id: str
    name: str | None = Field(default=None, min_length=1, max_length=80)
    engine: str | None = Field(default=None, min_length=1)
    configuration: str | None = Field(default=None, min_length=1, max_length=80)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    effort: str | None = Field(default=None, min_length=1, max_length=40)
    system_prompt: str | None = Field(default=None, min_length=1, max_length=12000)


class WorkflowDefinition(StrictModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=100)
    isolated_worktree: bool = True
    steps: list[WorkflowStepBinding]


class WorkflowConfiguration(StrictModel):
    steps: list[WorkflowStep]
    workflows: list[WorkflowDefinition]
    default_workflow_id: str

    @model_validator(mode="after")
    def validate_relations(self):
        step_ids = [step.id for step in self.steps]
        workflow_ids = [workflow.id for workflow in self.workflows]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("Reusable step IDs must be unique")
        if len(workflow_ids) != len(set(workflow_ids)):
            raise ValueError("Workflow IDs must be unique")
        if self.default_workflow_id not in workflow_ids:
            raise ValueError("Default workflow must reference an existing workflow")
        templates = {step.id: step for step in self.steps}
        for workflow in self.workflows:
            try:
                for binding in workflow.steps:
                    templates[binding.step_id]
            except KeyError as exc:
                raise ValueError(f"Workflow {workflow.name} references a missing step") from exc
            self.resolve(workflow.id)
        return self

    def resolve(self, workflow_id: str | None = None) -> WorkflowRecipe:
        selected_id = workflow_id or self.default_workflow_id
        workflow = next((item for item in self.workflows if item.id == selected_id), None)
        if workflow is None:
            raise KeyError(selected_id)
        templates = {step.id: step for step in self.steps}
        resolved = []
        for binding in workflow.steps:
            template = templates[binding.step_id]
            resolved.append(
                WorkflowStep(
                    id=template.id,
                    kind=template.kind,
                    name=binding.name or template.name,
                    engine=binding.engine or template.engine,
                    configuration=binding.configuration or template.configuration,
                    model=binding.model if binding.model is not None else template.model,
                    effort=binding.effort if binding.effort is not None else template.effort,
                    system_prompt=binding.system_prompt or template.system_prompt,
                )
            )
        return WorkflowRecipe(
            name=workflow.name,
            isolated_worktree=workflow.isolated_worktree,
            steps=resolved,
        )


class StepCatalog(StrictModel):
    steps: list[WorkflowStep]


class WorkflowCollection(StrictModel):
    workflows: list[WorkflowDefinition]
    default_workflow_id: str


def default_configuration() -> WorkflowConfiguration:
    labels = {
        "plan": "Plan and decompose",
        "execute": "Implement changes",
        "validate": "Validate changes",
        "select": "Evaluate implementation",
        "merge": "Integrate changes",
        "review": "Final review",
    }
    engines = {
        "plan": "codex",
        "execute": "9router/auto",
        "validate": "codex",
        "select": "codex",
        "merge": "codex",
        "review": "codex",
    }
    steps = [
        WorkflowStep(
            id=f"default-{kind}",
            kind=kind,
            name=labels[kind],
            engine=engines[kind],
            system_prompt=DEFAULT_PROMPTS[kind],
        )
        for kind in DEFAULT_KINDS
    ]
    return WorkflowConfiguration(
        steps=steps,
        workflows=[
            WorkflowDefinition(
                id="default",
                name="Default engineering route",
                isolated_worktree=True,
                steps=[WorkflowStepBinding(step_id=f"default-{kind}") for kind in DEFAULT_WORKFLOW_KINDS],
            )
        ],
        default_workflow_id="default",
    )


def default_recipe() -> WorkflowRecipe:
    return default_configuration().resolve()


def configuration_from_recipe(recipe: WorkflowRecipe) -> WorkflowConfiguration:
    return WorkflowConfiguration(
        steps=recipe.steps,
        workflows=[
            WorkflowDefinition(
                id="default",
                name=recipe.name,
                isolated_worktree=recipe.isolated_worktree,
                steps=[WorkflowStepBinding(step_id=step.id) for step in recipe.steps],
            )
        ],
        default_workflow_id="default",
    )


class WorkflowStore:
    """Shared workflow library; repositories and workspaces are bound only at dispatch."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "workflow.json"
        self.lock = threading.RLock()

    def _read(self) -> WorkflowConfiguration:
        if not self.path.exists():
            return default_configuration()
        text = self.path.read_text(encoding="utf-8")
        payload = json.loads(text)
        # Older releases reserved validation and merge for an internal deterministic
        # engine. Migrate those saved routes to an agent-backed tool when loading.
        for step in payload.get("steps", []):
            if step.get("engine") == "deterministic":
                step["engine"] = "codex"
        for workflow in payload.get("workflows", []):
            for binding in workflow.get("steps", []):
                if binding.get("engine") == "deterministic":
                    binding["engine"] = "codex"
        try:
            return WorkflowConfiguration.model_validate(payload)
        except ValueError:
            return configuration_from_recipe(WorkflowRecipe.model_validate(payload))

    def get_config(self) -> WorkflowConfiguration:
        with self.lock:
            return self._read()

    def get(self, workflow_id: str | None = None) -> WorkflowRecipe:
        return self.get_config().resolve(workflow_id)

    def save_config(self, config: WorkflowConfiguration) -> WorkflowConfiguration:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(config.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(self.path)
        return config

    def save_steps(self, catalog: StepCatalog) -> WorkflowConfiguration:
        with self.lock:
            current = self.get_config()
            return self.save_config(
                WorkflowConfiguration(
                    steps=catalog.steps,
                    workflows=current.workflows,
                    default_workflow_id=current.default_workflow_id,
                )
            )

    def save_workflows(self, collection: WorkflowCollection) -> WorkflowConfiguration:
        with self.lock:
            current = self.get_config()
            return self.save_config(
                WorkflowConfiguration(
                    steps=current.steps,
                    workflows=collection.workflows,
                    default_workflow_id=collection.default_workflow_id,
                )
            )

    def save(self, recipe: WorkflowRecipe) -> WorkflowRecipe:
        with self.lock:
            current = self.get_config()
            if len(current.workflows) != 1 or len(current.steps) != 6:
                raise ValueError(
                    "Use the Steps and Workflows tabs to edit a reusable configuration"
                )
            self.save_config(configuration_from_recipe(recipe))
            return recipe

    def reset(self) -> WorkflowRecipe:
        return self.save(default_recipe())
