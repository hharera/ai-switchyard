from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .models import StrictModel
from .paths import data_dir


class DeliveryConfig(StrictModel):
    mode: Literal["none", "push", "pr"] = "none"
    remote: str = Field(default="origin", min_length=1, max_length=100)
    branch: str = Field(default="orchestrator/{run_id}", min_length=1, max_length=240)
    base_branch: str = Field(default="main", min_length=1, max_length=240)
    draft: bool = True

    @model_validator(mode="after")
    def validate_names(self):
        for value, label in (
            (self.remote, "remote"),
            (self.branch, "branch"),
            (self.base_branch, "base branch"),
        ):
            if any(character.isspace() for character in value) or value.startswith("-"):
                raise ValueError(f"Delivery {label} cannot contain whitespace or start with '-'")
        try:
            self.branch.format(run_id="run-id", repository="repository")
        except (KeyError, ValueError, IndexError, AttributeError) as exc:
            raise ValueError("Branch supports only {run_id} and {repository} placeholders") from exc
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.remote):
            raise ValueError("Choose a named Git remote, not a URL or path")
        for branch in (
            self.branch.format(run_id="run-id", repository="repository"),
            self.base_branch,
        ):
            if (
                any(c in branch for c in " ~^:?*[\\")
                or ".." in branch
                or "@{" in branch
                or "//" in branch
                or branch.startswith(("-", "/"))
                or branch.endswith(("/", "."))
                or any(part.startswith(".") or part.endswith(".lock") for part in branch.split("/"))
            ):
                raise ValueError("Choose a valid Git branch name")
        return self

    def target_branch(self, *, run_id: str, repository: str) -> str:
        return self.branch.format(run_id=run_id, repository=repository)


class DeliveryConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "delivery.json"
        self.lock = threading.Lock()

    def get(self) -> DeliveryConfig:
        with self.lock:
            if not self.path.exists():
                return DeliveryConfig()
            try:
                return DeliveryConfig.model_validate_json(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return DeliveryConfig()

    def save(self, config: DeliveryConfig) -> DeliveryConfig:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(config.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(self.path)
        return config
