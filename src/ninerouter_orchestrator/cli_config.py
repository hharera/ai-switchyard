from __future__ import annotations

import json
import os
import re
import shutil
import threading
from glob import glob
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from .models import StrictModel
from .paths import data_dir
from .process import tool_path


class CliProfile(StrictModel):
    command: list[str] = Field(min_length=1)
    provider: str = Field(default="", max_length=120)
    model: str = Field(default="", max_length=200)
    base_url: str = Field(default="", max_length=2000)
    api_key_env: str = Field(default="", max_length=120, pattern=r"^$|^[A-Za-z_][A-Za-z0-9_]*$")

    @model_validator(mode="after")
    def clean_command(self):
        self.command = [part.strip() for part in self.command]
        if any(not part for part in self.command):
            raise ValueError("CLI command parts cannot be empty")
        self.command[0] = os.path.expanduser(self.command[0])
        self.provider = self.provider.strip()
        self.model = self.model.strip()
        self.base_url = self.base_url.strip().rstrip("/")
        if self.base_url:
            url = urlsplit(self.base_url)
            if url.scheme not in {"http", "https"} or not url.hostname:
                raise ValueError("Provider endpoint must be an http:// or https:// URL")
            if url.username or url.password or url.query or url.fragment:
                raise ValueError("Provider endpoints cannot contain credentials, queries, or fragments")
        if any(ord(char) < 32 for char in self.provider + self.model + self.base_url):
            raise ValueError("Provider, model, and endpoint cannot contain control characters")
        return self

    def codex_args(self) -> list[str]:
        args: list[str] = []
        provider = self.provider or ("switchyard" if self.base_url else "")
        if provider.lower() == "openai":
            provider = "openai"
        if provider and not re.fullmatch(r"[A-Za-z0-9_-]+", provider):
            raise ValueError("Codex provider IDs can contain only letters, numbers, underscores, and hyphens")
        if not self.base_url and (provider not in {"", "openai"} or self.api_key_env):
            raise ValueError("Set a Codex provider endpoint when using a custom provider or API key variable")
        if provider:
            args.extend(["-c", f"model_provider={json.dumps(provider)}"])
        if self.base_url:
            prefix = f"model_providers.{provider}"
            for key, value in [
                ("name", provider), ("base_url", self.base_url), ("wire_api", "responses"),
                ("requires_openai_auth", False),
                *([("env_key", self.api_key_env)] if self.api_key_env else []),
            ]:
                args.extend(["-c", f"{prefix}.{key}={json.dumps(value)}"])
        return args

    def opencode_config(self) -> dict:
        config: dict = {}
        provider = self.provider or "9router"
        if self.model:
            config["model"] = f"{provider}/{self.model}"
        if self.base_url or self.api_key_env:
            options = {"baseURL": self.base_url} if self.base_url else {}
            if self.api_key_env:
                options["apiKey"] = f"{{env:{self.api_key_env}}}"
            entry: dict = {"options": options}
            if self.base_url:
                entry.update({"npm": "@ai-sdk/openai-compatible", "name": provider})
            if self.model:
                entry["models"] = {self.model: {"name": self.model}}
            config["provider"] = {provider: entry}
        return config


class ManagedCli(CliProfile):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=80)
    category: Literal["cli", "ide", "desktop", "runtime", "creative", "mitm"] = "cli"
    description: str = Field(default="", max_length=300)
    instructions: str = Field(default="", max_length=2000)
    enabled: bool = False
    detection_commands: list[str] = Field(default_factory=list)
    detection_paths: list[str] = Field(default_factory=list)
    platforms: dict[
        Literal["linux", "windows", "macos"],
        Literal["supported", "preview", "beta", "wsl-docker", "limited"],
    ] = Field(default_factory=lambda: {
        "linux": "supported", "windows": "supported", "macos": "supported",
    })

    @model_validator(mode="after")
    def clean_detection(self):
        self.detection_commands = list(
            dict.fromkeys(item.strip() for item in self.detection_commands if item.strip())
        )
        self.detection_paths = list(
            dict.fromkeys(item.strip() for item in self.detection_paths if item.strip())
        )
        return self


class AdditionalCli(CliProfile):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    description: str = Field(default="", max_length=300)
    instructions: str = Field(default="", max_length=2000)
    enabled: bool = True


def managed_cli_catalog() -> list[ManagedCli]:
    common_ide_paths = [
        "~/.vscode/extensions/{extension}-*",
        "~/.vscode-insiders/extensions/{extension}-*",
        "~/.vscode-server/extensions/{extension}-*",
        "~/.cursor/extensions/{extension}-*",
        "~/.windsurf/extensions/{extension}-*",
    ]

    def ide_paths(extension: str, *extra: str) -> list[str]:
        return [path.format(extension=extension) for path in common_ide_paths] + list(extra)

    return [
        ManagedCli(
            id="claude-code", name="Claude Code", command=["claude"],
            provider="Anthropic", detection_commands=["claude"],
            detection_paths=["~/.claude/settings.json"],
            description="Anthropic's terminal coding agent.",
        ),
        ManagedCli(
            id="open-claw", name="Open Claw", command=["openclaw"],
            detection_commands=["openclaw", "open-claw"],
            detection_paths=["~/.openclaw/openclaw.json"],
            description="Open-source coding agent CLI.",
        ),
        ManagedCli(
            id="github-copilot", name="GitHub Copilot", command=["copilot"],
            provider="GitHub", detection_commands=["copilot"],
            detection_paths=ide_paths("github.copilot"),
            description="GitHub's AI coding assistant and CLI.",
        ),
        ManagedCli(
            id="claude-cowork", name="Claude Desktop", command=["claude-desktop"],
            provider="Anthropic", detection_commands=["claude-desktop"], category="desktop",
            detection_paths=[
                "/Applications/Claude.app",
                "~/Applications/Claude.app",
                "~/.config/Claude",
                "~/.config/Claude-3p",
                "~/Library/Application Support/Claude",
                "~/AppData/Roaming/Claude",
                "~/AppData/Local/AnthropicClaude",
            ],
            platforms={"linux": "beta", "windows": "supported", "macos": "supported"},
            description="Claude desktop app for documents, coding, and computer use.",
        ),
        ManagedCli(
            id="hermes-agent", name="Hermes Agent", command=["hermes"],
            detection_commands=["hermes", "hermes-agent"],
            detection_paths=["~/.hermes/config.yaml"],
            description="Hermes autonomous agent CLI.",
        ),
        ManagedCli(
            id="factory-droid", name="Factory Droid", command=["droid"],
            provider="Factory", detection_commands=["droid", "factory-droid"],
            detection_paths=["~/.factory/settings.json"],
            description="Factory's Droid software development agent.",
        ),
        ManagedCli(
            id="cursor", name="Cursor", command=["cursor"], category="ide",
            detection_commands=["cursor"],
            detection_paths=[
                "/Applications/Cursor.app",
                "~/Applications/Cursor.app",
                "~/.config/Cursor",
                "~/.cursor",
            ],
            description="Cursor AI code editor.",
        ),
        ManagedCli(
            id="cline", name="Cline", command=["cline"], category="ide",
            detection_commands=["cline"],
            detection_paths=ide_paths("saoudrizwan.claude-dev", "~/.cline/data"),
            description="Cline autonomous coding extension.",
        ),
        ManagedCli(
            id="kilo-code", name="Kilo Code", command=["kilo"], category="ide",
            detection_commands=["kilo", "kilocode"],
            detection_paths=ide_paths("kilocode.kilo-code", "~/.local/share/kilo"),
            description="Kilo Code open-source coding assistant.",
        ),
        ManagedCli(
            id="roo", name="Roo", command=["roo"], category="ide",
            detection_commands=["roo"],
            detection_paths=ide_paths("rooveterinaryinc.roo-cline"),
            description="Roo Code autonomous coding extension.",
        ),
        ManagedCli(
            id="continue", name="Continue", command=["cn"], category="ide",
            detection_commands=["cn", "continue"],
            detection_paths=ide_paths("continue.continue"),
            description="Continue open-source AI coding assistant.",
        ),
        ManagedCli(
            id="amp-cli", name="Amp CLI", command=["amp"],
            provider="Sourcegraph", detection_commands=["amp"],
            description="Sourcegraph's agentic coding CLI.",
        ),
        ManagedCli(
            id="qwen-code", name="Qwen Code", command=["qwen"],
            provider="Alibaba Cloud", detection_commands=["qwen", "qwen-code"],
            detection_paths=["~/.qwen/settings.json"],
            description="Qwen's terminal coding agent.",
        ),
        ManagedCli(
            id="deepseek-tui", name="DeepSeek TUI", command=["deepseek"],
            provider="DeepSeek", detection_commands=["deepseek", "deepseek-tui"],
            detection_paths=["~/.deepseek/config.toml"],
            description="Terminal interface for DeepSeek models.",
        ),
        ManagedCli(
            id="jcode", name="jcode", command=["jcode"],
            detection_commands=["jcode"], description="jcode coding agent CLI.",
            detection_paths=["~/.jcode/config.toml"],
        ),
        ManagedCli(
            id="grok-build", name="Grok Build", command=["grok"],
            provider="xAI", detection_commands=["grok", "grok-build"],
            detection_paths=["~/.grok/config.toml"],
            description="xAI Grok coding and build agent.",
        ),
        ManagedCli(
            id="devin-cli", name="Devin Desktop / Windsurf", command=["windsurf"],
            provider="Cognition", category="ide", detection_commands=["windsurf", "devin"],
            detection_paths=[
                "/Applications/Windsurf.app", "~/Applications/Windsurf.app",
                "~/.config/Windsurf", "~/.windsurf",
                "~/AppData/Roaming/Windsurf", "~/AppData/Local/Programs/Windsurf",
                "/Applications/Devin.app", "~/Applications/Devin.app",
            ],
            description="Agentic coding environment for local and cloud development agents.",
        ),
        ManagedCli(
            id="open-design", name="OpenDesign", command=["opendesign"],
            detection_commands=["opendesign", "open-design"],
            description="AI-assisted design tool.",
        ),
        ManagedCli(
            id="antigravity", name="Antigravity", command=["antigravity"], category="mitm",
            provider="Google", detection_commands=["antigravity"],
            detection_paths=["~/.config/Antigravity", "~/.antigravity", "/Applications/Antigravity.app"],
            description="Google Antigravity IDE integration through MITM.",
        ),
        ManagedCli(
            id="kiro", name="Kiro", command=["kiro-cli"], category="mitm",
            provider="AWS", detection_commands=["kiro-cli", "kiro"],
            detection_paths=[
                "/Applications/Kiro.app",
                "~/Applications/Kiro.app",
                "~/.kiro",
            ],
            description="Kiro IDE integration through MITM.",
        ),
        ManagedCli(
            id="chatgpt-desktop", name="ChatGPT Desktop", command=["chatgpt"],
            category="desktop", provider="OpenAI", detection_commands=["chatgpt"],
            detection_paths=[
                "/Applications/ChatGPT.app", "~/Applications/ChatGPT.app",
                "~/.config/ChatGPT", "~/Library/Application Support/ChatGPT",
                "~/AppData/Local/Packages/OpenAI.ChatGPT-Desktop_*",
            ],
            platforms={"linux": "preview", "windows": "supported", "macos": "supported"},
            description="Desktop assistant for research, files, agents, and coding.",
        ),
        ManagedCli(
            id="gemini-cli", name="Gemini CLI", command=["gemini"], provider="Google",
            detection_commands=["gemini"], detection_paths=["~/.gemini/settings.json"],
            description="Google's terminal AI and coding agent.",
        ),
        ManagedCli(
            id="jetbrains-junie", name="JetBrains Junie", command=["junie"], category="ide",
            provider="JetBrains", detection_commands=["junie"],
            detection_paths=[
                "~/.local/share/JetBrains/*/plugins/*[Jj]unie*",
                "~/Library/Application Support/JetBrains/*/plugins/*[Jj]unie*",
                "~/AppData/Roaming/JetBrains/*/plugins/*[Jj]unie*",
            ],
            description="AI coding agent for JetBrains IDEs.",
        ),
        ManagedCli(
            id="aider", name="Aider", command=["aider"], detection_commands=["aider"],
            detection_paths=["~/.aider.conf.yml"],
            description="Git-aware terminal pair programmer.",
        ),
        ManagedCli(
            id="ollama", name="Ollama", command=["ollama"], category="runtime",
            detection_commands=["ollama"],
            detection_paths=[
                "~/.ollama", "/Applications/Ollama.app", "~/Applications/Ollama.app",
                "~/AppData/Local/Programs/Ollama",
            ],
            description="Run and serve local language models.",
        ),
        ManagedCli(
            id="lm-studio", name="LM Studio", command=["lms"], category="runtime",
            detection_commands=["lms"],
            detection_paths=[
                "~/.lmstudio", "~/.cache/lm-studio", "/Applications/LM Studio.app",
                "~/Applications/LM Studio.app", "~/AppData/Local/Programs/LM Studio",
            ],
            description="Local model desktop app and API server.",
        ),
        ManagedCli(
            id="open-webui", name="Open WebUI", command=["open-webui"], category="runtime",
            detection_commands=["open-webui"],
            detection_paths=["~/.cache/open-webui", "~/.config/open-webui"],
            description="Self-hosted chat interface for local and remote models.",
        ),
        ManagedCli(
            id="anythingllm", name="AnythingLLM", command=["anythingllm"], category="desktop",
            detection_commands=["anythingllm"],
            detection_paths=[
                "/Applications/AnythingLLM.app", "~/Applications/AnythingLLM.app",
                "~/.config/anythingllm-desktop", "~/AppData/Roaming/anythingllm-desktop",
            ],
            description="Local AI workspace for documents, retrieval, and agents.",
        ),
        ManagedCli(
            id="jan", name="Jan", command=["jan"], category="desktop",
            detection_commands=["jan"],
            detection_paths=[
                "/Applications/Jan.app", "~/Applications/Jan.app", "~/.config/Jan",
                "~/jan", "~/AppData/Roaming/Jan",
            ],
            description="Open-source desktop assistant for local and remote models.",
        ),
        ManagedCli(
            id="gpt4all", name="GPT4All", command=["gpt4all"], category="desktop",
            detection_commands=["gpt4all"],
            detection_paths=[
                "~/gpt4all/bin/chat", "/Applications/GPT4All.app",
                "~/.local/share/nomic.ai/GPT4All", "~/AppData/Local/nomic.ai/GPT4All",
            ],
            description="Desktop chat with offline local language models.",
        ),
        ManagedCli(
            id="msty-studio", name="Msty Studio", command=["msty"], category="desktop",
            detection_commands=["msty", "msty-studio"],
            detection_paths=[
                "/Applications/Msty Studio.app", "/Applications/Msty.app",
                "~/.config/Msty", "~/.config/Msty Studio", "~/AppData/Roaming/Msty Studio",
            ],
            description="Multi-model workspace for local and remote AI.",
        ),
        ManagedCli(
            id="chatbox-ai", name="Chatbox AI", command=["chatbox"], category="desktop",
            detection_commands=["chatbox"],
            detection_paths=[
                "/Applications/Chatbox.app", "~/Applications/Chatbox.app",
                "~/.config/Chatbox", "~/AppData/Roaming/Chatbox",
            ],
            description="Desktop chat client for multiple model providers.",
        ),
        ManagedCli(
            id="llama-cpp", name="llama.cpp", command=["llama-cli"], category="runtime",
            detection_commands=["llama-cli", "llama-server"],
            detection_paths=["~/llama.cpp/build/bin/llama-cli", "~/llama.cpp/build/bin/llama-cli.exe"],
            description="Lightweight local language-model inference and serving.",
        ),
        ManagedCli(
            id="koboldcpp", name="KoboldCpp", command=["koboldcpp"], category="runtime",
            detection_commands=["koboldcpp", "koboldcpp.py"],
            detection_paths=["~/koboldcpp/koboldcpp.py", "~/KoboldCpp/koboldcpp.exe"],
            description="GGUF local-model runtime with a browser interface.",
        ),
        ManagedCli(
            id="localai", name="LocalAI", command=["local-ai"], category="runtime",
            detection_commands=["local-ai", "localai"],
            detection_paths=["~/.config/localai"],
            platforms={"linux": "supported", "windows": "wsl-docker", "macos": "supported"},
            description="Self-hosted OpenAI-compatible inference server.",
        ),
        ManagedCli(
            id="open-interpreter", name="Open Interpreter", command=["interpreter"],
            detection_commands=["interpreter"],
            detection_paths=["~/.config/open-interpreter", "~/.config/Open Interpreter"],
            description="AI agent for files, terminal commands, and applications.",
        ),
        ManagedCli(
            id="fabric", name="Fabric", command=["fabric"], detection_commands=["fabric"],
            detection_paths=["~/.config/fabric"],
            description="CLI workflows, reusable prompts, and AI automation.",
        ),
        ManagedCli(
            id="comfyui", name="ComfyUI", command=["comfy"], category="creative",
            detection_commands=["comfy"],
            detection_paths=[
                "~/ComfyUI/main.py", "/Applications/ComfyUI.app", "~/.config/ComfyUI",
                "~/AppData/Roaming/ComfyUI",
            ],
            description="Node-based image and video generation workflows.",
        ),
        ManagedCli(
            id="invokeai", name="InvokeAI", command=["invokeai-web"], category="creative",
            detection_commands=["invokeai-web", "invokeai"],
            detection_paths=["~/invokeai/invokeai.yaml"],
            platforms={"linux": "supported", "windows": "supported", "macos": "limited"},
            description="Image generation and creative workflows with local models.",
        ),
        ManagedCli(
            id="automatic1111", name="AUTOMATIC1111 WebUI",
            command=["~/stable-diffusion-webui/webui.sh"], category="creative",
            detection_paths=[
                "~/stable-diffusion-webui/webui.sh", "~/stable-diffusion-webui/webui-user.bat",
            ],
            platforms={"linux": "supported", "windows": "supported", "macos": "limited"},
            description="Stable Diffusion browser interface for image generation.",
        ),
    ]


class CliConfiguration(StrictModel):
    codex: CliProfile = Field(
        default_factory=lambda: CliProfile(command=["codex"])
    )
    opencode: CliProfile = Field(
        default_factory=lambda: CliProfile(command=["opencode"])
    )
    tools: list[ManagedCli] = Field(default_factory=managed_cli_catalog)
    path_entries: list[str] = Field(default_factory=list)
    environment_vars: list[str] = Field(default_factory=list)
    additional: list[AdditionalCli] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def clean_shared_access(self):
        self.codex.codex_args()
        names = [cli.name.lower() for cli in self.additional]
        reserved = {"codex", "opencode"}
        if len(names) != len(set(names)) or set(names) & reserved:
            raise ValueError("CLI names must be unique and cannot be codex or opencode")
        tool_ids = [tool.id for tool in self.tools]
        if len(tool_ids) != len(set(tool_ids)):
            raise ValueError("Managed CLI tool IDs must be unique")
        configured = {tool.id: tool for tool in self.tools}
        catalog = managed_cli_catalog()
        if set(configured) - {tool.id for tool in catalog}:
            raise ValueError("Unknown managed tool ID; register custom commands as additional tools")
        self.tools = [
            configured[tool.id].model_copy(update={
                key: getattr(tool, key)
                for key in (
                    "name", "category", "description", "detection_commands", "detection_paths",
                    "platforms",
                )
            }) if tool.id in configured else tool
            for tool in catalog
        ]
        self.path_entries = list(
            dict.fromkeys(
                str(Path(item.strip()).expanduser()) for item in self.path_entries if item.strip()
            )
        )
        self.environment_vars = list(
            dict.fromkeys(item.strip() for item in self.environment_vars if item.strip())
        )
        return self

    def runtime_env(self) -> dict[str, str]:
        env = {name: os.environ[name] for name in self.environment_vars if name in os.environ}
        if self.path_entries:
            env["PATH"] = os.pathsep.join([*self.path_entries, tool_path()])
        return env

    def statuses(self) -> dict[str, dict]:
        path = self.runtime_env().get("PATH", tool_path())
        profiles = [
            ("codex", self.codex, [self.codex.command[0]], [
                "~/.codex/config.toml", "/Applications/Codex.app", "~/.config/Codex",
            ]),
            ("opencode", self.opencode, [self.opencode.command[0]], [
                "~/.config/opencode/opencode.json", "~/.config/opencode/opencode.jsonc",
            ]),
            *((tool.id, tool, tool.detection_commands, tool.detection_paths) for tool in self.tools),
            *((cli.name, cli, [cli.command[0]], []) for cli in self.additional),
        ]
        statuses = {
            name: self._status(profile, commands, paths, path=path)
            for name, profile, commands, paths in profiles
        }
        # Keep legacy custom names compatible, but expose catalog status separately on collisions.
        for tool in self.tools:
            statuses[f"tool:{tool.id}"] = self._status(
                tool, tool.detection_commands, tool.detection_paths, path=path
            )
        return statuses

    @staticmethod
    def _status(
        profile: CliProfile,
        commands: list[str],
        patterns: list[str],
        *,
        path: str,
    ) -> dict:
        command_path = shutil.which(os.path.expanduser(profile.command[0]), path=path)
        local_bins = ["~/.local/bin", "~/.cargo/bin", "~/.bun/bin", "~/.grok/bin"]
        candidates = [profile.command[0], *commands]
        candidates += [str(Path(directory).expanduser() / command)
                       for directory in local_bins for command in commands if "/" not in command]
        for command in dict.fromkeys(candidates):
            resolved = shutil.which(command, path=path)
            if resolved:
                return {
                    "command": profile.command,
                    "resolved": resolved,
                    "available": True,
                    "detected_by": "command",
                    "matched": command,
                    "command_available": command_path is not None,
                }
        for pattern in patterns:
            expanded = os.path.expandvars(os.path.expanduser(pattern))
            matches = sorted(glob(expanded))
            if matches:
                return {
                    "command": profile.command,
                    "resolved": matches[0],
                    "available": True,
                    "detected_by": "path",
                    "matched": pattern,
                    "command_available": command_path is not None,
                }
        return {
            "command": profile.command,
            "resolved": None,
            "available": False,
            "detected_by": None,
            "matched": None,
            "command_available": False,
        }

    def agent_tool_prompt(self) -> str:
        enabled: list[ManagedCli | AdditionalCli] = [
            *[tool for tool in self.tools if tool.enabled],
            *[cli for cli in self.additional if cli.enabled],
        ]
        if not enabled:
            return ""
        tools = "\n".join(
            f"- {cli.name}: {cli.description or 'No description provided.'}\n"
            f"  Command prefix: {cli.command!r}\n"
            f"  Provider: {cli.provider or 'Use the tool configuration.'}\n"
            f"  Model: {cli.model or 'Use the tool default.'}\n"
            f"  Endpoint: {cli.base_url or 'Use the tool configuration.'}\n"
            f"  API key environment variable: {cli.api_key_env or 'Use existing authentication.'}\n"
            f"  Usage: {cli.instructions or 'Use only when it directly helps the assigned task.'}"
            for cli in enabled
        )
        return f"""Registered command-line tools:
{tools}

Provider, model, and endpoint entries are preferences, not proof of applied native settings.
Registration exposes only the configured command, not a desktop-control or workflow integration.
Verify that the command supports the requested operation; app files alone do not prove CLI access.
Do not rewrite native tool configuration or change providers without explicit user approval.
Use these tools only when relevant and within the assigned task. Do not assume authentication,
network access, or permission to modify external systems. Never print credentials or secret values.
"""

    def command_for(self, name: str, fallback: list[str]) -> list[str]:
        match = next(
            (cli for cli in self.additional if cli.enabled and cli.name.lower() == name.lower()),
            None,
        )
        return match.command if match else fallback


class CliConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "clis.json"
        self.lock = threading.Lock()

    def get(self) -> CliConfiguration:
        with self.lock:
            if not self.path.exists():
                return CliConfiguration()
            try:
                return CliConfiguration.model_validate_json(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return CliConfiguration()

    def save(self, config: CliConfiguration) -> CliConfiguration:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(config.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(self.path)
        return config
