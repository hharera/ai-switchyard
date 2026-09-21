"""Generate explicit, secret-free setup instructions; never modify native tool settings."""

from __future__ import annotations

import json
import shlex

from .cli_config import (
    CliProfile,
    managed_cli_catalog,
)

GUIDES = {
    "open-claw": "Open ~/.openclaw/openclaw.json. Add the provider under models.providers and select provider/model in agents.defaults.model.primary.",
    "github-copilot": "In VS Code, configure the 9Router for GitHub Copilot extension with the endpoint and API key. In Copilot Chat, open Manage Models and select the model. The standalone Copilot CLI has separate settings.",
    "claude-cowork": "Open Claude Desktop settings. Configure a supported provider, credentials, and model there. Claude Desktop configuration is separate from Claude Code.",
    "hermes-agent": "In ~/.hermes/config.yaml, configure model.provider, model.base_url, and model.default. Configure credentials through Hermes authentication or its environment.",
    "factory-droid": "In ~/.factory/settings.json, add the endpoint and model to customModels, then select that custom model in Droid. Use the API protocol supported by your provider.",
    "cursor": "Open Cursor Settings > Models. Configure an OpenAI-compatible endpoint and API key, add the custom model, then select it. Cursor may require a publicly reachable endpoint; localhost may not work.",
    "cline": "Open Cline settings. Select the provider (OpenAI Compatible for a compatible custom endpoint), enter the endpoint and API key, and select the model. Apply to both plan and act modes if they have separate settings.",
    "kilo-code": "Open Kilo Code provider settings. Select the provider or OpenAI-compatible endpoint, enter credentials, then select the model for the active profile.",
    "roo": "Open Roo Code settings. Select the provider or OpenAI-compatible endpoint, enter credentials, and choose the model for the active configuration profile.",
    "continue": "Open Continue's model configuration. Add a models entry with the provider, model, apiBase, and credential reference, then select it in Continue.",
    "amp-cli": "Configure the endpoint and credentials using the setup supported by your Amp version. Model selection may be controlled by Amp modes or gateway aliases; a saved model preference does not override Amp routing automatically.",
    "deepseek-tui": "In ~/.deepseek/config.toml, set the model and provider. For a compatible gateway use the openai provider with its base_url and credentials. Restart DeepSeek TUI after saving.",
    "jcode": "In ~/.jcode/config.toml, add a provider with the endpoint and credential environment reference, then select its model. Restart jcode after saving.",
    "grok-build": "Open ~/.grok/config.toml. Configure the model endpoint and credentials using the schema supported by your Grok Build version, then restart it.",
    "devin-cli": "Open the installed Devin or Windsurf settings and complete its native sign-in. Configure only the provider and model options supported by that app; custom endpoints may not be available.",
    "open-design": "Open OpenDesign's provider settings. Select the provider, enter the endpoint and credentials if supported, and choose the model. Available providers depend on the installed version.",
    "antigravity": "Configure the provider/model alias in your existing 9router MITM integration for Antigravity. This page does not install certificates, alter proxy settings, or enable traffic interception.",
    "kiro": "Configure the provider/model alias in your existing 9router MITM integration for Kiro. This page does not install certificates, alter proxy settings, or enable traffic interception.",
    "chatgpt-desktop": "Open ChatGPT Desktop settings and use its native account, workspace, and model controls. The app may not support a custom provider endpoint.",
    "gemini-cli": "Use Gemini CLI's native authentication and settings. Configure a supported model or endpoint through the options provided by your installed version.",
    "jetbrains-junie": "Open the Junie settings in your JetBrains IDE. Complete its native sign-in, then choose from the models and providers supported by the installed plugin.",
    "aider": "Configure Aider in ~/.aider.conf.yml or with its documented environment variables. Set the model and compatible endpoint, then launch Aider in the target Git repository.",
    "ollama": "Start Ollama with its native service or desktop app, then pull the model you want to use. Its local API normally listens on http://127.0.0.1:11434.",
    "lm-studio": "Open LM Studio, download or select a model, then start the local server if another tool needs API access. Copy the endpoint shown by LM Studio rather than assuming a port.",
    "open-webui": "Start Open WebUI using its documented package or container flow. Configure model connections and authentication in Open WebUI's administration settings.",
    "anythingllm": "Open AnythingLLM settings. Configure the language-model provider, embedding provider, and workspace access using its native credential fields.",
    "jan": "Open Jan settings, choose a local model or supported remote provider, and use Jan's secure credential controls when a provider key is required.",
    "gpt4all": "Open GPT4All settings, download or select a local model, and enable its local API server only if another application needs it.",
    "msty-studio": "Open Msty Studio settings. Add or select a provider, model, or local connection using the options supported by your installed version.",
    "chatbox-ai": "Open Chatbox AI settings. Choose a provider, enter its endpoint and credentials in the app, then select the model for the active conversation.",
    "llama-cpp": "Build or install llama.cpp, then run llama-cli for terminal inference or llama-server for API access. Supply a local model path using llama.cpp's documented arguments.",
    "koboldcpp": "Launch KoboldCpp with a local GGUF model and review its host and port settings before exposing the browser interface or API beyond this machine.",
    "localai": "Start LocalAI using its documented binary or container setup. Configure models in LocalAI, then use the endpoint exposed by that instance.",
    "open-interpreter": "Run Open Interpreter's native setup, choose a supported local or remote model, and review command-execution confirmation settings before use.",
    "fabric": "Run Fabric's native setup, configure the provider credentials it requests, and select a pattern when invoking the fabric command.",
    "comfyui": "Start ComfyUI with its documented launcher, install models and custom nodes from trusted sources, then open the local workflow interface.",
    "invokeai": "Run InvokeAI's configuration flow, select the model storage and host settings, then start its web service with the installed InvokeAI command.",
    "automatic1111": "Install and start AUTOMATIC1111 with webui.sh on Linux/macOS or webui-user.bat on Windows. Configure models and launch arguments in its native files.",
}


def setup_for(tool_id: str, profile: CliProfile) -> dict[str, str]:
    if tool_id not in {"codex", "opencode", *(tool.id for tool in managed_cli_catalog())}:
        raise ValueError("Unknown managed tool")
    instructions = ""
    content = ""
    kind = "Manual setup"
    if tool_id == "codex":
        command = [*profile.command, *profile.codex_args()]
        if profile.model:
            command.extend(["--model", profile.model])
        content = shlex.join(command)
        kind = "Launch command"
        instructions = (
            "Saved settings apply to new Switchyard Codex calls. Explicit planner/reviewer model "
            "overrides take precedence. Custom endpoints must support the Responses API. "
            "To use the same settings outside Switchyard, run this command. "
            "The Codex app's own settings are not changed."
        )
    elif tool_id == "opencode":
        content = json.dumps(profile.opencode_config(), indent=2)
        kind = "OpenCode configuration fragment"
        instructions = (
            "Saved settings are passed to Switchyard OpenCode processes. Workflow and chat "
            "routes explicitly select 9router/<combo> and take precedence over this default. "
            "Use provider ID 9router when configuring that gateway. For standalone OpenCode, "
            "merge this fragment into ~/.config/opencode/opencode.json; preserve existing keys. "
            "Custom endpoints must support an OpenAI-compatible API."
        )
    elif tool_id in {"claude-code", "qwen-code"}:
        prefix = "ANTHROPIC" if tool_id == "claude-code" else "OPENAI"
        environment = {}
        if profile.base_url:
            environment[f"{prefix}_BASE_URL"] = profile.base_url
        if profile.model:
            environment[f"{prefix}_MODEL"] = profile.model
        assignments = [f"{key}={shlex.quote(value)}" for key, value in environment.items()]
        if profile.api_key_env:
            key = "ANTHROPIC_AUTH_TOKEN" if tool_id == "claude-code" else "OPENAI_API_KEY"
            assignments.append(f'{key}="${{{profile.api_key_env}:?Set {profile.api_key_env} first}}"')
        content = " ".join([*assignments, shlex.join(profile.command)])
        kind = "POSIX shell launch command"
        protocol = "Anthropic Messages" if tool_id == "claude-code" else "OpenAI-compatible"
        instructions = (
            f"Run this command in a terminal to apply the selected endpoint and model for one "
            f"session. The endpoint must support the {protocol} API. "
            "Set the named secret variable first; no secret value is included here. "
            "Provider names are labels; the endpoint determines the actual provider. "
            "Native tool settings are not modified."
        )
    else:
        instructions = GUIDES[tool_id]
        content = "\n".join([
            f"Provider: {profile.provider or 'Use existing provider'}",
            f"Model: {profile.model or 'Use existing model'}",
            f"Endpoint: {profile.base_url or 'Use existing endpoint'}",
            f"Credential variable: {profile.api_key_env or 'Use existing authentication'}",
        ])
        instructions += (
            " These are saved preferences, not applied native settings. "
            "Transfer them to the tool's settings and verify a request there. "
            "Use the value of the credential variable only in the tool's secure credential field."
        )
    return {"kind": kind, "instructions": instructions, "content": content}
