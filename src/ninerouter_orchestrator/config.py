from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ORCH_",
        extra="ignore",
    )

    execution_combos: str = "OpenCode-Go,Kimi,OpenCode-Free"
    planner_model: str | None = None
    reviewer_model: str | None = None
    forks_per_ticket: int = Field(default=3, ge=1, le=5)
    max_repair_rounds: int = Field(default=2, ge=0, le=5)
    command_timeout_seconds: int = Field(default=1800, ge=30)
    state_dir_name: str = ".orchestrator"
    allow_host_execution: bool = False

    @property
    def combos(self) -> tuple[str, ...]:
        values = tuple(item.strip() for item in self.execution_combos.split(",") if item.strip())
        if not values:
            raise ValueError("At least one 9router combo must be configured")
        return values

    def state_dir(self, repository: Path) -> Path:
        return repository.parent / f".{repository.name}-orchestrator"
