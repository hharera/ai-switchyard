from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ForkIntent(StrEnum):
    IMPLEMENTATION = "implementation-first"
    ROBUSTNESS = "robustness-first"
    ALTERNATIVE = "alternative-approach"


class Ticket(StrictModel):
    id: str
    title: str
    description: str
    acceptance_criteria: list[str] = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    validation_commands: list[str] = Field(default_factory=list)
    affected_areas: list[str] = Field(default_factory=list)


class Plan(StrictModel):
    objective: str
    assumptions: list[str] = Field(default_factory=list)
    tickets: list[Ticket] = Field(min_length=1)

    @model_validator(mode="after")
    def dependencies_exist(self) -> Plan:
        ids = {ticket.id for ticket in self.tickets}
        if len(ids) != len(self.tickets):
            raise ValueError("Ticket IDs must be unique")
        for ticket in self.tickets:
            missing = set(ticket.dependencies) - ids
            if missing:
                raise ValueError(f"Ticket {ticket.id} has unknown dependencies: {sorted(missing)}")
        self.dependency_order()
        return self

    def dependency_order(self) -> list[Ticket]:
        pending = {ticket.id: ticket for ticket in self.tickets}
        completed: set[str] = set()
        ordered: list[Ticket] = []
        while pending:
            ready = sorted(
                (ticket for ticket in pending.values() if set(ticket.dependencies) <= completed),
                key=lambda ticket: ticket.id,
            )
            if not ready:
                raise ValueError("Ticket dependency graph contains a cycle")
            for ticket in ready:
                ordered.append(ticket)
                completed.add(ticket.id)
                pending.pop(ticket.id)
        return ordered


class ExecutionRequest(StrictModel):
    run_id: str
    ticket: Ticket
    fork_number: int = Field(ge=1)
    intent: ForkIntent
    combo: str
    workspace: Path
    system_prompt: str = ""
    engine: str = "9router/auto"


class CommandResult(StrictModel):
    command: str
    return_code: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0

    @property
    def passed(self) -> bool:
        return self.return_code == 0


class ValidationReport(StrictModel):
    commands: list[CommandResult] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.commands) and all(command.passed for command in self.commands)

    @property
    def score(self) -> int:
        passed = sum(command.passed for command in self.commands)
        failed = len(self.commands) - passed
        return passed * 100 - failed * 250 - len(self.changed_files)


class ExecutionResult(StrictModel):
    request: ExecutionRequest
    status: str
    summary: str
    commit: str | None = None
    validation: ValidationReport
    agent_output: str = ""
    error: str | None = None


class CandidateDecision(StrictModel):
    selected_fork: int
    rationale: str
    useful_elements_from_other_forks: list[str] = Field(default_factory=list)


class FindingSeverity(StrEnum):
    TRIVIAL = "trivial"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class ReviewFinding(StrictModel):
    title: str
    description: str
    severity: FindingSeverity
    files: list[str] = Field(default_factory=list)


class ReviewReport(StrictModel):
    approved: bool
    summary: str
    findings: list[ReviewFinding] = Field(default_factory=list)


class RepairPolicy(StrictModel):
    forks: int
    reviewer_can_fix: bool = False
    replan: bool = False


def repair_policy(severity: FindingSeverity) -> RepairPolicy:
    policies = {
        FindingSeverity.TRIVIAL: RepairPolicy(forks=0, reviewer_can_fix=True),
        FindingSeverity.SMALL: RepairPolicy(forks=1),
        FindingSeverity.MEDIUM: RepairPolicy(forks=2),
        FindingSeverity.LARGE: RepairPolicy(forks=3, replan=True),
    }
    return policies[severity]
