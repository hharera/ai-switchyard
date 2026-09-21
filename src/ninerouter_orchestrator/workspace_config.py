from __future__ import annotations

import threading
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from .delivery_config import DeliveryConfig
from .paths import data_dir


class Workspace(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    name: str = Field(min_length=1, max_length=80)
    repository: str = Field(min_length=1, max_length=4096)
    workflow_id: str | None = Field(default="default", max_length=80)
    forks_per_ticket: int = Field(default=3, ge=1, le=5)
    command_timeout_seconds: int = Field(default=1800, ge=30, le=7200)
    git_base_branch: str = Field(default="main", min_length=1, max_length=240)
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)

    @model_validator(mode="after")
    def normalize(self):
        self.name = self.name.strip()
        if not self.repository.strip() or "\0" in self.repository:
            raise ValueError("Enter an absolute repository path")
        path = Path(self.repository).expanduser()
        if not path.is_absolute():
            raise ValueError("Enter an absolute repository path")
        self.repository = str(path.resolve())
        self.workflow_id = (self.workflow_id.strip() or None) if self.workflow_id else None
        self.git_base_branch = self.git_base_branch.strip()
        if not self.name or not self.git_base_branch:
            raise ValueError("Workspace name and Git base branch are required")
        DeliveryConfig(base_branch=self.git_base_branch)
        return self

    def run_settings(self) -> dict:
        return {
            "forks_per_ticket": self.forks_per_ticket,
            "command_timeout_seconds": self.command_timeout_seconds,
        }


class WorkspaceConfiguration(BaseModel):
    workspaces: list[Workspace] = Field(default_factory=list, max_length=100)
    default_workspace_id: str | None = None

    @model_validator(mode="after")
    def validate_collection(self):
        identifiers = [workspace.id for workspace in self.workspaces]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Workspace identifiers must be unique")
        if self.default_workspace_id and self.default_workspace_id not in identifiers:
            raise ValueError("Default workspace must reference a saved workspace")
        if self.workspaces and not self.default_workspace_id:
            self.default_workspace_id = self.workspaces[0].id
        if not self.workspaces:
            self.default_workspace_id = None
        return self

    def resolve(self, workspace_id: str) -> Workspace:
        for workspace in self.workspaces:
            if workspace.id == workspace_id:
                return workspace
        raise KeyError(workspace_id)


class WorkspaceStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "workspaces.json"
        self.lock = threading.Lock()

    def get(self) -> WorkspaceConfiguration:
        with self.lock:
            if not self.path.exists():
                return WorkspaceConfiguration()
            try:
                return WorkspaceConfiguration.model_validate_json(
                    self.path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError) as exc:
                raise ValueError(
                    "Saved workspaces could not be read. Restore workspaces.json before saving."
                ) from exc

    def save(self, config: WorkspaceConfiguration) -> WorkspaceConfiguration:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(config.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(self.path)
        return config
