"""Secret-free workflow choices derived from the shared CLI catalog and local metadata."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .adapters.workflow_tool import WORKFLOW_TOOL_SPECS
from .cli_config import CliConfiguration, CliProfile


def _json_file(path: Path) -> dict:
    try:
        if path.stat().st_size > 8_000_000:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _models(tool_id: str, profile: CliProfile) -> list[dict]:
    models = {}
    if tool_id == "codex":
        directory = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        entries = _json_file(directory / "models_cache.json").get("models", [])
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or not isinstance(entry.get("slug"), str):
                continue
            if entry.get("visibility") == "hide":
                continue
            efforts = entry.get("supported_reasoning_levels", [])
            models[entry["slug"]] = {
                "value": entry["slug"], "label": entry["slug"],
                "efforts": [item["effort"] for item in efforts
                            if isinstance(item, dict) and isinstance(item.get("effort"), str)]
                if isinstance(efforts, list) else [],
            }
    if tool_id == "opencode":
        directory = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        config_path = Path(os.environ.get("OPENCODE_CONFIG", str(directory / "opencode/opencode.json")))
        providers = _json_file(config_path).get("provider", {})
        for provider, settings in providers.items() if isinstance(providers, dict) else []:
            definitions = settings.get("models", {}) if isinstance(settings, dict) else {}
            for model_id, metadata in definitions.items() if isinstance(definitions, dict) else []:
                variants = metadata.get("variants", {}) if isinstance(metadata, dict) else {}
                value = f"{provider}/{model_id}"
                models[value] = {"value": value, "label": value, "efforts": [
                    name for name, variant in variants.items()
                    if not isinstance(variant, dict) or not variant.get("disabled")
                ] if isinstance(variants, dict) else []}
    if profile.model:
        value = profile.model
        if tool_id == "opencode" and not value.startswith(f"{profile.provider or '9router'}/"):
            value = f"{profile.provider or '9router'}/{value}"
        models.setdefault(value, {"value": value, "label": value, "efforts": []})
    return list(models.values())


def workflow_tools(config: CliConfiguration, combos: list[str]) -> dict:
    statuses = config.statuses()
    tools, excluded = [], []
    profiles = [
        ("codex", "Codex", config.codex, ["CLI", "TUI", "AI tool", "Harness"], "codex"),
        ("opencode", "OpenCode", config.opencode, ["CLI", "TUI", "AI tool", "Harness"], "opencode"),
        *[(tool.id, tool.name, tool, [WORKFLOW_TOOL_SPECS[tool.id].tool_type], f"tool:{tool.id}")
          for tool in config.tools if tool.id in WORKFLOW_TOOL_SPECS],
    ]
    for tool in config.tools:
        if tool.id not in WORKFLOW_TOOL_SPECS and statuses.get(f"tool:{tool.id}", {}).get("available"):
            excluded.append({"name": tool.name, "reason": "No non-interactive workflow adapter"})
    for tool_id, name, profile, types, status_key in profiles:
        if not statuses.get(status_key, {}).get("command_available"):
            continue
        models = _models(tool_id, profile)
        spec = WORKFLOW_TOOL_SPECS.get(tool_id)
        configurations = [{
            "id": "default", "name": "Saved tool configuration",
            "models": models, "efforts": list(spec.efforts) if spec else [],
        }]
        if tool_id == "opencode" and combos:
            configurations.append({
                "id": "9router", "name": "9router routes",
                "models": [{"value": "auto", "label": "Rotate configured routes", "efforts": []},
                           *[{"value": combo, "label": combo, "efforts": []} for combo in combos]],
                "efforts": [],
            })
        tools.append({
            "id": tool_id, "name": name, "types": types,
            "engine": tool_id if tool_id in {"codex", "opencode"} else f"tool/{tool_id}",
            "configurations": configurations,
            "profile": {
                "command": profile.command, "provider": profile.provider,
                "model": profile.model, "base_url": profile.base_url,
                "api_key_env": profile.api_key_env,
            },
        })
    return {"tools": tools, "excluded": excluded}


def validate_tool_selection(step, catalog: dict, *, installed: bool = True) -> None:
    """Legacy engines stay loadable; explicit new selections must match the catalog."""
    engine = "opencode" if step.engine.startswith("9router/") else step.engine
    tool = next((item for item in catalog["tools"] if item["engine"] == engine), None)
    if not tool:
        if installed:
            raise ValueError(f"{step.name}: install a supported tool and refresh tools.")
        return
    configuration = "9router" if step.engine.startswith("9router/") else step.configuration
    config = next((item for item in tool["configurations"] if item["id"] == configuration), None)
    if not config:
        raise ValueError(f"{step.name}: select an available tool configuration.")
    model = step.engine.removeprefix("9router/") if step.engine.startswith("9router/") else step.model
    selected = next((item for item in config["models"] if item["value"] == model), None)
    if model and not selected:
        raise ValueError(f"{step.name}: select a model from this tool's configuration.")
    efforts = selected.get("efforts", []) if selected else []
    efforts = efforts or config.get("efforts", [])
    if step.effort and step.effort not in efforts:
        raise ValueError(f"{step.name}: select a supported effort or use the tool default.")
