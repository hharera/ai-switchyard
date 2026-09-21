import sqlite3

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .command_console import ConsoleError


class TabRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def valid_text(cls, value):
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("Enter a tab name without control characters.")
        return value.strip()


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=1, max_length=12000)
    timeout: int = Field(default=0, ge=0, le=86400)
    confirmed: bool = False

    @field_validator("command")
    @classmethod
    def valid_command(cls, value):
        if not value.strip() or "\0" in value:
            raise ValueError("Enter a command without null characters.")
        return value.strip()


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: bool = False


def command_router(console, workspace, cli_config, workspace_list):
    router = APIRouter(prefix="/api/commands")

    def call(action, *args, **kwargs):
        try:
            return action(*args, **kwargs)
        except ConsoleError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            raise HTTPException(503, "Command history is unavailable. Check local disk permissions.") from exc

    @router.get("/status")
    def status():
        names = {item.id: item.name for item in call(workspace_list).workspaces}
        return [{**row, "name": names.get(row["workspace_id"], "Removed workspace")}
                for row in call(console.statuses)]

    @router.get("/tabs")
    def tabs(workspace_id: str):
        workspace(workspace_id)
        return call(console.tabs, workspace_id)

    @router.post("/tabs", status_code=201)
    def create_tab(payload: TabRequest, workspace_id: str):
        workspace(workspace_id)
        return call(console.create_tab, workspace_id, payload.name)

    @router.patch("/tabs/{tab_id}")
    def rename_tab(tab_id: str, payload: TabRequest, workspace_id: str):
        workspace(workspace_id)
        return call(console.rename_tab, workspace_id, tab_id, payload.name)

    @router.delete("/tabs/{tab_id}")
    def remove_tab(tab_id: str, payload: ConfirmRequest, workspace_id: str):
        workspace(workspace_id)
        if not payload.confirmed:
            raise HTTPException(422, "Confirm removing this tab and its command history.")
        call(console.remove_tab, workspace_id, tab_id)
        return {"removed": True}

    @router.get("/tabs/{tab_id}/runs")
    def history(tab_id: str, workspace_id: str, before: str | None = Query(None, max_length=80)):
        workspace(workspace_id)
        return call(console.history, workspace_id, tab_id, before)

    @router.post("/tabs/{tab_id}/runs", status_code=202)
    def start(tab_id: str, payload: RunRequest, workspace_id: str):
        selected = workspace(workspace_id)
        if not payload.confirmed:
            raise HTTPException(422, "Confirm running this command on the host machine.")
        return call(console.start, workspace_id, tab_id, selected.repository,
                    payload.command, payload.timeout, cli_config().runtime_env())

    @router.get("/runs/{run_id}")
    def get_run(run_id: str, workspace_id: str):
        workspace(workspace_id)
        return call(console.get_run, workspace_id, run_id)

    @router.post("/runs/{run_id}/stop")
    def stop(run_id: str, workspace_id: str):
        workspace(workspace_id)
        return call(console.stop, workspace_id, run_id)

    return router
