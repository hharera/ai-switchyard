from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from .adapters.codex import CodexAdapter
from .adapters.nine_router import NineRouterExecutor, NineRouterReasoner
from .adapters.workflow_tool import WORKFLOW_TOOL_SPECS, WorkflowToolAdapter
from .cli_config import CliConfiguration
from .config import Settings
from .git import SWITCHYARD_GIT_CONFIG, GitRepository
from .mcp_config import McpConfiguration
from .models import (
    CandidateDecision,
    ExecutionRequest,
    ExecutionResult,
    ForkIntent,
    Plan,
    ReviewReport,
    Ticket,
    ValidationReport,
)
from .process import ProcessError, run_process
from .validation import validate_candidate
from .workflow_config import WorkflowRecipe, WorkflowStep, default_recipe

logger = logging.getLogger(__name__)

INTENTS = (
    ForkIntent.IMPLEMENTATION,
    ForkIntent.ROBUSTNESS,
    ForkIntent.ALTERNATIVE,
)


class EngineeringOrchestrator:
    def __init__(
        self,
        repository: Path,
        settings: Settings,
        workflow: WorkflowRecipe | None = None,
        mcp_config: McpConfiguration | None = None,
        cli_config: CliConfiguration | None = None,
        progress: Callable[[str, dict | None], None] | None = None,
    ) -> None:
        mcp_config = mcp_config or McpConfiguration()
        cli_config = cli_config or CliConfiguration()
        shared_env = cli_config.runtime_env()
        self.repository = GitRepository(repository, timeout=settings.command_timeout_seconds)
        self.settings = settings
        self.planner = CodexAdapter(
            timeout=settings.command_timeout_seconds,
            model=settings.planner_model,
            mcps=mcp_config.for_tool("codex"),
            cli=cli_config.codex,
            shared_env=shared_env,
        )
        self.reviewer = CodexAdapter(
            timeout=settings.command_timeout_seconds,
            model=settings.reviewer_model,
            mcps=mcp_config.for_tool("codex"),
            cli=cli_config.codex,
            shared_env=shared_env,
        )
        self.executor = NineRouterExecutor(
            timeout=settings.command_timeout_seconds,
            mcps=mcp_config.for_tool("opencode"),
            cli=cli_config.opencode,
            shared_env=shared_env,
        )
        self.mcp_config = mcp_config
        self.cli_config = cli_config
        self.cli_tool_context = cli_config.agent_tool_prompt()
        self.workflow = workflow or default_recipe()
        self.state_dir = settings.state_dir(repository.resolve())
        self.progress = progress
        self._worktree_lock = threading.Lock()

    def _report(self, stage: str, run: dict | None = None) -> None:
        if not self.progress:
            return
        try:
            self.progress(stage, run)
        except Exception:
            logger.warning("Unable to publish workflow progress", exc_info=True)

    def preflight(self, *, plan_only: bool = False) -> None:
        self.repository.validate()
        self.repository.git("rev-parse", "HEAD")
        steps = self.workflow.steps[:1] if plan_only else self.workflow.steps
        if any(step.engine == "codex" for step in steps):
            profile = self.cli_config.codex
            if profile.base_url:
                if profile.api_key_env and not os.environ.get(profile.api_key_env):
                    raise ProcessError(f"Set the Codex credential variable {profile.api_key_env}")
            else:
                run_process(
                    [*profile.command, "login", "status"],
                    cwd=self.repository.root,
                    timeout=30,
                    check=True,
                    env=self.cli_config.runtime_env(),
                )
        combos = {step.combo for step in steps if step.combo}
        if any(step.rotates_combos for step in steps):
            combos.update(self.settings.combos)
        for combo in sorted(combos):
            self.executor.verify_combo(combo, cwd=self.repository.root)
        statuses = self.cli_config.statuses()
        profiles = {tool.id: tool for tool in self.cli_config.tools}
        for step in steps:
            if not step.engine.startswith("tool/"):
                continue
            tool_id = step.engine.removeprefix("tool/")
            if tool_id not in WORKFLOW_TOOL_SPECS or tool_id not in profiles:
                raise ProcessError(f"{step.name} uses an unsupported workflow tool: {tool_id}")
            if not statuses.get(f"tool:{tool_id}", {}).get("command_available"):
                raise ProcessError(f"{step.name} needs an installed {profiles[tool_id].name} command")
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def plan(self, request: str) -> Plan:
        if not self.workflow.steps:
            raise ValueError("The workflow has no step available for planning")
        step = self.workflow.steps[0]
        prompt = f"""{self._step_prompt(step)}

Inspect this repository and decompose the request
into dependency-aware implementation tickets. Each ticket must be independently executable, include
objective acceptance criteria, and list deterministic validation commands already supported by the repo.
Do not modify files.

Request:
{request}
"""
        return self._structured(step, prompt, Plan, cwd=self.repository.root)

    def execute_ticket(
        self, run_id: str, ticket: Ticket, start_point: str
    ) -> list[ExecutionResult]:
        requests: list[ExecutionRequest] = []
        step = self.workflow.step("execute")
        combos = self.settings.combos if step.engine == "9router/auto" else (step.combo,)
        for index in range(self.settings.forks_per_ticket):
            fork_number = index + 1
            # Git worktree registration mutates shared repository metadata. Keep that
            # brief setup serialized while the agents themselves run concurrently.
            with self._worktree_lock:
                workspace, _ = self.repository.create_worktree(
                    state_dir=self.state_dir,
                    run_id=run_id,
                    ticket_id=ticket.id,
                    fork_number=fork_number,
                    start_point=start_point,
                )
            requests.append(
                ExecutionRequest(
                    run_id=run_id,
                    ticket=ticket,
                    fork_number=fork_number,
                    intent=INTENTS[index % len(INTENTS)],
                    combo=(combos[index % len(combos)] if combos[0] else "Codex subscription"),
                    workspace=workspace,
                    system_prompt=self._step_prompt(step),
                    engine=step.engine,
                )
            )

        results: list[ExecutionResult] = []
        with ThreadPoolExecutor(max_workers=len(requests)) as pool:
            futures = {pool.submit(self._execute_fork, request): request for request in requests}
            for future in as_completed(futures):
                request = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:  # noqa: BLE001 - isolate failures between peers.
                    results.append(
                        ExecutionResult(
                            request=request,
                            status="failed",
                            summary="Execution failed",
                            validation=ValidationReport(),
                            error=str(exc),
                        )
                    )
                self._report(
                    f"{ticket.id}: candidate {request.fork_number} finished "
                    f"({len(results)} of {len(requests)}): {results[-1].status}"
                )
        return sorted(results, key=lambda result: result.request.fork_number)

    def _execute_ticket_batch(
        self, run: dict, tickets: list[Ticket], start_point: str
    ) -> list[tuple[Ticket, list[ExecutionResult], CandidateDecision]]:
        def execute_and_select(ticket: Ticket):
            results = []
            try:
                results = self.execute_ticket(run["run_id"], ticket, start_point)
                self._report(f"Selecting the best candidate for {ticket.id}")
                return results, self.select_candidate(ticket, results), None
            except Exception as exc:  # noqa: BLE001 - retain every peer's outcome.
                return results, None, str(exc)

        entries = {}
        for ticket in tickets:
            entry = {
                "ticket": ticket.id, "status": "running", "start_point": start_point,
                "candidates": [],
            }
            entries[ticket.id] = entry
            run["tickets"].append(entry)
        self.save_run(run)
        completed = []
        errors = []
        workers = min(self.settings.max_parallel_tickets, len(tickets))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(execute_and_select, ticket): ticket for ticket in tickets}
            for future in as_completed(futures):
                ticket = futures[future]
                results, decision, error = future.result()
                entry = entries[ticket.id]
                entry["candidates"] = [r.model_dump(mode="json") for r in results]
                if error is not None:
                    entry.update(status="failed", error=error)
                    errors.append(f"{ticket.id}: {error}")
                else:
                    entry.update(status="ready", decision=decision.model_dump())
                    completed.append((ticket, results, decision))
                self.save_run(run)
                self._report(f"{ticket.id}: subagent {entry['status']}", run)
        if errors:
            raise RuntimeError("Subagent execution failed: " + "; ".join(sorted(errors)))
        positions = {ticket.id: index for index, ticket in enumerate(tickets)}
        return sorted(completed, key=lambda item: positions[item[0].id])

    def _execute_fork(self, request: ExecutionRequest) -> ExecutionResult:
        self._report(f"{request.ticket.id}: candidate {request.fork_number} started")
        if request.engine == "codex":
            output = self.planner.execute(self.executor._prompt(request), cwd=request.workspace)
        else:
            output = self.executor.execute(request)
        self._report(f"{request.ticket.id}: validating candidate {request.fork_number}")
        validation = validate_candidate(
            self.repository,
            request.workspace,
            request.ticket.validation_commands,
            timeout=self.settings.command_timeout_seconds,
        )
        commit = None
        status = "validation_failed"
        if validation.passed:
            commit = self.repository.commit_all(
                request.workspace,
                f"{request.ticket.id}: {request.ticket.title} ({request.intent.value})",
            )
            status = "completed" if commit else "no_changes"
        return ExecutionResult(
            request=request,
            status=status,
            summary=f"{request.combo} completed {request.intent.value}",
            commit=commit,
            validation=validation,
            agent_output=output[-12000:],
        )

    def select_candidate(self, ticket: Ticket, results: list[ExecutionResult]) -> CandidateDecision:
        step = self.workflow.step("select")
        viable = [result for result in results if result.commit and result.validation.passed]
        if not viable:
            details = "; ".join(
                f"fork {result.request.fork_number}: {result.status} {result.error or ''}"
                for result in results
            )
            raise RuntimeError(f"No viable candidate for {ticket.id}: {details}")
        compact = [
            {
                "fork": result.request.fork_number,
                "combo": result.request.combo,
                "intent": result.request.intent.value,
                "score": result.validation.score,
                "changed_files": result.validation.changed_files,
                "summary": result.summary,
                "workspace": str(result.request.workspace),
                "commit": result.commit,
            }
            for result in viable
        ]
        prompt = f"""{self._step_prompt(step)}

Select the strongest implementation candidate for this ticket. Prefer correctness,
acceptance coverage, maintainability, and low integration risk. Return one selected fork and explain
which ideas from other forks may be useful during final review.

Ticket:
{ticket.model_dump_json(indent=2)}

Candidates:
{json.dumps(compact, indent=2)}
"""
        return self._structured(step, prompt, CandidateDecision, cwd=self.repository.root)

    def review(self, request: str, plan: Plan, workspace: Path, base: str) -> ReviewReport:
        step = self.workflow.step("review")
        prompt = f"""{self._step_prompt(step)}

Perform a final repository review against the original request and plan. Inspect the
current code and git diff. Focus on correctness, missing requirements, regressions, security, and tests.
Do not modify files. Classify each finding as trivial, small, medium, or large.
Review the complete changes with git diff {base} HEAD, not only uncommitted changes.

Original request:
{request}

Plan:
{plan.model_dump_json(indent=2)}
"""
        return self._structured(step, prompt, ReviewReport, cwd=workspace)

    def _step_prompt(self, step: WorkflowStep) -> str:
        return "\n\n".join(
            part
            for part in (
                step.system_prompt,
                self.cli_tool_context,
                (
                    "Do not push Git branches or create pull requests. Publishing is handled only by "
                    "the orchestrator after the workflow using the dispatch delivery settings."
                ),
            )
            if part
        )

    def _structured(self, step: WorkflowStep, prompt: str, schema, *, cwd: Path):
        if step.engine == "codex":
            adapter = CodexAdapter(
                timeout=self.settings.command_timeout_seconds,
                model=step.model,
                effort=step.effort,
                mcps=self.mcp_config.for_tool("codex"),
                cli=self.cli_config.codex,
                shared_env=self.cli_config.runtime_env(),
            )
            return adapter.structured(prompt, schema, cwd=cwd)
        combo = self.settings.combos[0] if step.rotates_combos else step.combo
        if not combo:
            raise RuntimeError(f"{step.name} needs a 9router combo")
        return NineRouterReasoner(
            combo=combo,
            timeout=self.settings.command_timeout_seconds,
            mcps=self.mcp_config.for_tool("opencode"),
            cli=self.cli_config.opencode,
            shared_env=self.executor.shared_env,
        ).structured(prompt, schema, cwd=cwd)

    def _execute_workflow_step(
        self, step: WorkflowStep, prompt: str, *, cwd: Path, index: int
    ) -> str:
        if step.engine == "codex":
            return CodexAdapter(
                timeout=self.settings.command_timeout_seconds,
                model=step.model,
                effort=step.effort,
                mcps=self.mcp_config.for_tool("codex"),
                cli=self.cli_config.codex,
                shared_env=self.cli_config.runtime_env(),
            ).execute(prompt, cwd=cwd)
        if step.engine == "opencode" and step.configuration != "9router":
            return self.executor.run_model(
                prompt, cwd=cwd, model=step.model, effort=step.effort
            )
        if step.engine.startswith("tool/"):
            tool_id = step.engine.removeprefix("tool/")
            profile = next(tool for tool in self.cli_config.tools if tool.id == tool_id)
            return WorkflowToolAdapter(
                tool_id,
                profile,
                timeout=self.settings.command_timeout_seconds,
                shared_env=self.cli_config.runtime_env(),
            ).execute(prompt, cwd=cwd, model=step.model, effort=step.effort)
        if step.rotates_combos and not self.settings.combos:
            raise RuntimeError(f"{step.name} needs at least one 9router combo")
        combo = (
            self.settings.combos[index % len(self.settings.combos)]
            if step.rotates_combos
            else step.combo
        )
        if not combo:
            raise RuntimeError(f"{step.name} needs a 9router combo")
        return self.executor.run_model(
            prompt,
            cwd=cwd,
            model=NineRouterExecutor.model_id(combo),
            effort=step.effort,
        )

    def _record_changes(self, run: dict, workspace: Path, *, target: str | None = None) -> None:
        try:
            run["changes"] = self.repository.change_summary(
                workspace, base=run["base"], target=target
            )
            run.pop("changes_error", None)
        except Exception as exc:  # A display snapshot must not mask an agent failure.
            run["changes_error"] = str(exc)

    def run(self, request: str, *, start_point: str | None = None) -> dict:
        """Run the configured agent calls in order, without interpreting step kinds."""
        if not self.settings.allow_host_execution:
            raise RuntimeError(
                "Execution requires --allow-host-execution: worktrees are not sandboxes"
            )
        run = {
            "run_id": uuid.uuid4().hex[:10],
            "request": request,
            "workflow": self.workflow.model_dump(),
            "steps": [],
            "status": "running",
            "cleanup": {"removed": [], "preserved": []},
        }
        if start_point:
            run["continued_from"] = start_point
        self.save_run(run)
        try:
            if not self.workflow.steps:
                run.update(status="completed", no_op=True)
            else:
                self._report("Preparing the repository", run)
                run["repository_setup"] = self.repository.prepare_dispatch()
                self._report("Checking providers", run)
                self.preflight()
                run["base"] = (
                    self.repository.git("rev-parse", "--verify", f"{start_point}^{{commit}}")
                    .stdout.strip()
                    if start_point
                    else self.repository.head()
                )
                if self.workflow.isolated_worktree:
                    workspace, branch = self.repository.create_worktree(
                        state_dir=self.state_dir, run_id=run["run_id"],
                        ticket_id="workflow", fork_number=0, start_point=run["base"],
                    )
                else:
                    self.repository.require_clean_checkout()
                    branch = self.repository.current_branch()
                    workspace = self.repository.root
                run.update(workspace=str(workspace), integration_branch=branch,
                           isolated_worktree=self.workflow.isolated_worktree)
                for index, step in enumerate(self.workflow.steps):
                    entry = {"index": index, "step_id": step.id, "name": step.name,
                             "kind": step.kind, "engine": step.engine, "status": "running",
                             "started_at": datetime.now(UTC).isoformat()}
                    prior_outputs = [
                        {"name": item["name"], "output": item["output"][-4000:]}
                        for item in run["steps"][-5:]
                    ]
                    run["steps"].append(entry)
                    prompt = f"""{self._step_prompt(step)}

Carry out this workflow step in the current workspace. Follow repository instructions.
The step category is descriptive; follow the instructions above, not a fixed phase contract.
Earlier steps used this same workspace. Do not create commits or push branches; changes will be
committed after the workflow completes.

Original request:
{request}

Recent previous step output excerpts:
{json.dumps(prior_outputs, ensure_ascii=False)}

The complete outputs are in the run record at:
{self.state_dir / 'runs' / (run['run_id'] + '.json')}
"""
                    entry["request"] = prompt
                    self.save_run(run)
                    self._report(f"Step {index + 1} of {len(self.workflow.steps)}: {step.name}", run)
                    try:
                        output = self._execute_workflow_step(
                            step, prompt, cwd=workspace, index=index
                        )
                        entry.update(status="completed", output=output)
                    except Exception as exc:
                        entry.update(status="failed", error=str(exc))
                        raise
                    finally:
                        entry["finished_at"] = datetime.now(UTC).isoformat()
                        self._record_changes(run, workspace)
                    self.save_run(run)
                    self._report(f"Completed: {step.name}", run)
                run["commit"] = self.repository.commit_all(
                    workspace, f"Switchyard: {self.workflow.name} ({run['run_id']})"
                )
                self._record_changes(run, workspace, target=self.repository.git(
                    "rev-parse", "HEAD", cwd=workspace
                ).stdout.strip())
                run["status"] = "completed"
                if self.workflow.isolated_worktree:
                    self._cleanup_worktree(run, workspace)
        except Exception as exc:  # noqa: BLE001 - retain outputs and worktrees on failure.
            run.update(status="failed", error=str(exc))
        self.save_run(run)
        self._report("Run stopped" if run["status"] == "failed" else "Workflow finished", run)
        return run

    def _run_engineering(self, request: str) -> dict:
        """Legacy ticket coordinator; dispatch uses the dynamic run method."""
        if not self.settings.allow_host_execution:
            raise RuntimeError(
                "Execution requires --allow-host-execution: worktrees are not sandboxes"
            )
        self.workflow.require_executable()
        self._report("Preparing the repository")
        repository_setup = self.repository.prepare_dispatch()
        integration_branch = None
        if not self.workflow.isolated_worktree:
            self.repository.require_clean_checkout()
            integration_branch = self.repository.current_branch()
        run = self.create_run(request, plan_only=False)
        run["repository_setup"] = repository_setup
        plan = Plan.model_validate(run["plan"])
        self._report("Implementation plan ready", run)
        if self.workflow.isolated_worktree:
            workspace, integration_branch = self.repository.create_worktree(
                state_dir=self.state_dir,
                run_id=run["run_id"],
                ticket_id="integration",
                fork_number=0,
                start_point=run["base"],
            )
        else:
            workspace = self.repository.root
        run.update(
            integration_branch=integration_branch,
            isolated_worktree=self.workflow.isolated_worktree,
            workspace=str(workspace),
            tickets=[],
            status="running",
            cleanup={"removed": [], "preserved": []},
            max_parallel_tickets=self.settings.max_parallel_tickets,
        )
        checks: list[str] = []
        try:
            ordered_tickets = plan.dependency_order()
            positions = {ticket.id: index for index, ticket in enumerate(ordered_tickets, start=1)}
            for dependency_batch in plan.dependency_batches():
                for offset in range(0, len(dependency_batch), self.settings.max_parallel_tickets):
                    ticket_batch = dependency_batch[
                        offset : offset + self.settings.max_parallel_tickets
                    ]
                    for ticket in ticket_batch:
                        if not ticket.validation_commands:
                            raise RuntimeError(f"{ticket.id} needs explicit validation commands")
                        self._report(
                            f"Running candidates for {ticket.id} "
                            f"({positions[ticket.id]} of {len(ordered_tickets)})",
                            run,
                        )
                    start = self.repository.git("rev-parse", "HEAD", cwd=workspace).stdout.strip()
                    prepared = self._execute_ticket_batch(run, ticket_batch, start)
                    for ticket, results, decision in prepared:
                        entry = next(item for item in run["tickets"] if item["ticket"] == ticket.id)
                        selected = next(
                            (
                                r
                                for r in results
                                if r.request.fork_number == decision.selected_fork
                                and r.commit
                                and r.validation.passed
                            ),
                            None,
                        )
                        if selected is None:
                            raise RuntimeError("Reviewer selected an invalid candidate")
                        self._report(f"Integrating {ticket.id} and running checks", run)
                        self.repository.git(
                            *SWITCHYARD_GIT_CONFIG,
                            "cherry-pick",
                            selected.commit,
                            cwd=workspace,
                        )
                        checks = list(dict.fromkeys(checks + ticket.validation_commands))
                        report = validate_candidate(
                            self.repository,
                            workspace,
                            checks,
                            timeout=self.settings.command_timeout_seconds,
                        )
                        entry["integration"] = report.model_dump()
                        if not report.passed:
                            raise RuntimeError(f"Integration checks failed after {ticket.id}")
                        entry["status"] = "integrated"
                        self._cleanup_execution_worktrees(run, results)
                        self.save_run(run)
                        self._report(f"Integrated {ticket.id}", run)
            self._report("Reviewing the integrated changes", run)
            review = self.review(request, plan, workspace, run["base"])
            run["review"] = review.model_dump(mode="json")
            run["status"] = (
                "approved" if review.approved and not review.findings else "needs_repair"
            )
            if self.workflow.isolated_worktree:
                self._cleanup_worktree(run, workspace)
        except Exception as exc:  # noqa: BLE001 - persist every run failure.
            run.update(status="failed", error=str(exc))
            self._report("Run stopped", run)
        self.save_run(run)
        self._report("Run stopped" if run["status"] == "failed" else "Workflow finished", run)
        return run

    def _cleanup_execution_worktrees(self, run: dict, results: list[ExecutionResult]) -> None:
        for result in results:
            self._cleanup_worktree(run, result.request.workspace)

    def _cleanup_worktree(self, run: dict, workspace: Path) -> None:
        removed, reason = self.repository.remove_worktree(workspace)
        record = {"path": str(workspace)}
        if removed:
            run["cleanup"]["removed"].append(record)
        else:
            record["reason"] = reason
            run["cleanup"]["preserved"].append(record)

    def save_run(self, run: dict) -> None:
        path = self.state_dir / "runs" / f"{run['run_id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(run, indent=2), encoding="utf-8")
        temporary.replace(path)

    def create_run(self, request: str, *, plan_only: bool = True) -> dict:
        self._report("Checking providers")
        self.preflight(plan_only=plan_only)
        run_id = uuid.uuid4().hex[:10]
        self._report("Planning the implementation")
        plan = self.plan(request)
        base = self.repository.head()
        run = {
            "run_id": run_id,
            "request": request,
            "base": base,
            "workflow": self.workflow.model_dump(),
            "plan": plan.model_dump(),
        }
        run_path = self.state_dir / "runs" / f"{run_id}.json"
        run_path.parent.mkdir(parents=True, exist_ok=True)
        run_path.write_text(json.dumps(run, indent=2), encoding="utf-8")
        return run
