# Switchyard

Switchyard is packaged for Windows, macOS, and Linux through npm while its orchestration engine
remains Python. The npm installer creates an isolated Python environment inside the package,
installs the engine, and exposes the `openswitch` and `switchyard` commands in version 0.1.1.

## Public installation

The public npm package is named `openswitch` and is available under the MIT License.
Version 0.1.0 uses the `switchyard` command; version 0.1.1 adds the `openswitch` alias.

After version 0.1.1 is published, installation is the same on every platform:

```bash
npm install --global --allow-scripts=openswitch openswitch
openswitch ui
# The original command remains available:
switchyard --help
```

After installation, npm prints `openswitch ui`, the command that starts Switchyard and opens it in
your default browser.

| Platform | Requirements |
| --- | --- |
| Windows 10/11 | Node.js 22+, Python 3.12+ from python.org with the `py` launcher, and Git |
| macOS | Node.js 22+, Python 3.12+ from python.org or Homebrew, and Git/Xcode command-line tools |
| Linux | Node.js 22+, Python 3.12+, Git, and the matching `python3-venv` package when required |

The installer prefers a compatible Python on `PATH`, then searches versioned commands and Windows
`py` launchers without invoking a shell. If `SWITCHYARD_PYTHON` is set, it must be an absolute path to an existing Python environment
where Switchyard is already installed; the npm installer verifies it and does not modify it.
Installation needs network access to download Python dependencies from PyPI.
The `--allow-scripts=openswitch` option authorizes the Python setup script on npm versions
that require explicit lifecycle-script permission.

If npm lifecycle scripts were disabled, enable them and rebuild the installed package:

```bash
npm rebuild --global --allow-scripts=openswitch openswitch
```

Windows validation commands run through `cmd.exe`; macOS and Linux use Bash when available, with
`/bin/sh` as a fallback. Configure `SWITCHYARD_SHELL` with an absolute compatible shell path when a
repository's validation scripts require a different POSIX shell.

On Windows, agent prompts bypass recognized npm Node.js `.cmd` shims so prompt text is never
interpreted as shell syntax. For other batch wrappers, configure the tool's native executable or
an explicit command such as `["node.exe", "C:\\tools\\agent\\cli.js"]` in CLI settings.

The **CMD** tab uses `cmd.exe` on Windows and Bash on macOS or Linux. Commands and external tools
must support the host OS and be installed and authenticated before use. Windows commands run in
an owned Job Object; stopping a command forcibly terminates its entire process tree. macOS and
Linux send a termination signal, then force termination after one second if needed. These are
cleanup mechanisms, not security sandboxes. Keep long-running servers in the foreground.

Switchyard stores global configuration in the platform-native data directory:

- Windows: `%LOCALAPPDATA%\Switchyard`
- macOS: `~/Library/Application Support/Switchyard`
- Linux: `$XDG_DATA_HOME/9router-orchestrator` or `~/.local/share/9router-orchestrator`

Set `SWITCHYARD_DATA_DIR` to an absolute directory to override this location. Existing
`~/.local/share/9router-orchestrator` data is reused automatically so upgrades do not split settings.

## Development installation

Install this checkout globally during development:

```bash
npm install -g .
switchyard --help
```

Or build and install a portable npm tarball:

```bash
npm pack
npm install -g --allow-scripts=openswitch ./openswitch-0.1.1.tgz
```

Existing `orchestrate` and `orchestrate-ui` Python commands remain available for compatibility.

Run `npm test` after installing the Python development dependencies below. `npm run pack:check`
inspects the distributable without publishing it. Package contents exclude local environments,
credentials, tests, and Python bytecode.

Cross-platform CI runs the Python suite, Node package tests, and a real packed global-install smoke
test on Windows, macOS, and Linux with Python 3.12 and 3.13. Native Windows/macOS results must be
confirmed in CI before claiming those platforms are verified.

## Publishing a release

1. Verify that your npm account can publish `openswitch` and that the version is not already published.
   The installed commands are `openswitch` and `switchyard`.
2. Keep the MIT license metadata and `LICENSE` file consistent across the npm and Python packages.
3. Keep versions in `package.json` and `pyproject.toml` equal. Run `npm test`,
   `npm run test:install`, and `npm run release:check` before releasing.
4. Configure the repository secret `NPM_TOKEN` with permission to publish the chosen package.
   Use a narrowly scoped, short-lived npm token and review the repository's Actions permissions.
5. Publish a GitHub release tagged `v<version>`, or manually run **Publish npm package** in Actions.
   That workflow waits for all cross-platform CI jobs, validates release metadata and the release
   tag when present, then publishes with npm provenance.

Version 0.1.1 has not been published yet. The metadata guard checks required fields, not npm ownership
or legal rights; the release owner must verify both. No PyPI publication or native installers are
configured. The supported distribution is an npm launcher with a locally created Python environment.

## Saved workspaces

Use **Workspaces** to save a name, local Git repository path, default workflow,
attempts per ticket, parallel ticket limit, command timeout, Git comparison base, and delivery settings. Configuration is
stored in the platform data directory documented under Public installation. The active workspace is shared by
Overview, Dispatch, Git, and Runs, and its selection is remembered in the browser. Previously used
repository paths are suggested when adding a workspace; they are not saved automatically.

Dispatch can override workflow and delivery for one run. Each job records a workspace and
run-settings snapshot, so later edits do not change existing jobs. Host execution always needs fresh
confirmation. Runs filters by workspace and includes older jobs with the same repository path.
Removing a workspace only removes its configuration, never repository files or run history.
Workflows, step templates, MCPs, and CLI access are shared across all workspaces. A workspace's
default workflow is only a reference to the shared library, never a workspace-owned copy.

Open a completed dispatch in **Runs** and use **Follow up on this run** to continue its work.
The follow-up creates a linked dispatch with the saved workflow, original objective, previous
request, and recent response excerpts. Isolated workflows start from the previous run's committed
result in a new worktree; direct workflows use the current clean workspace checkout. Confirm host
execution again before sending. Follow-ups stay local and do not push or open pull requests.
Running jobs must finish first; failed jobs require recovery of their preserved worktree.

Saving a workspace initializes Git if its folder is not already a repository, using the configured
Git comparison base as the initial branch. Saving alone does not stage or commit files.
At the start of every authorized dispatch (UI or CLI), Switchyard initializes Git if needed
and, when there are no commits, commits the existing non-ignored files as a baseline. Empty
folders receive an empty baseline commit. Review your ignore rules before the first dispatch:
files not ignored by Git become part of local history. No remote is added or pushed by setup.
If the selected folder is inside another repository, Switchyard initializes an independent nested
repository so workspace history and changes remain scoped to the exact selected path. Broken or
inaccessible repositories are rejected rather than overwritten.

## Delivery settings

Each workspace saves its delivery defaults: local only (the initial default), push to a named remote
branch, or push and open a GitHub PR. Use **Copy global delivery defaults** in Workspaces to reuse
the **Delivery** tab's settings. Dispatch can override these settings;
each job saves its own snapshot. Branch names support `{run_id}` and `{repository}` placeholders.
Only completed workflows publish. Failed workflows never publish.
Pushes are non-forced. PRs use authenticated `gh` and an explicit base branch, with draft enabled by
default. A failed PR attempt records any successful push; local integration branches remain recoverable.
These controls govern orchestrator delivery; agents are instructed not to publish independently,
but host-executing agents and commands are not a security sandbox.

## Pull request review

The web console's **Pull requests** tab reads review queues from GitHub.com, GitLab.com, and
Bitbucket Cloud using the active workspace's delivery remote. It shows the request description,
discussion preview, changed files, approval count, and provider merge status. Comments, approvals,
change requests, merges, closes, and reopens are available where the provider offers a guarded API.
Every write requires an in-app confirmation and rechecks the request state and head commit first.
Bitbucket merges and reopens stay on Bitbucket because those actions cannot use the same commit guard.

For GitHub.com, install and authenticate the [GitHub CLI](https://cli.github.com/) with `gh auth login`.
For GitLab.com, install and authenticate the [GitLab CLI](https://gitlab.com/gitlab-org/cli) with
`glab auth login`. Switchyard detects either CLI and uses its local credential store without sending
the token to the browser or saving it in workspace configuration. This is optional: set `GH_TOKEN` or
`GITHUB_TOKEN`, `GITLAB_TOKEN`, or `BITBUCKET_TOKEN` in the Switchyard server environment instead.
Set `BITBUCKET_EMAIL` as well when using a Bitbucket app password. Use a credential with the narrowest
repository scope; the feature acts with that credential's provider identity and permissions. Bitbucket
Cloud uses environment credentials because it has no equivalent first-party CLI. Self-hosted providers
are not supported yet.

A local, subscription-first orchestration MVP. Codex uses the existing ChatGPT login for planning and
review. Repository-aware execution runs through OpenCode using stable `9router/<combo>` identifiers.
The workflow never stores or depends on the provider/model membership behind a combo.

The web console also includes a global **MCPs** tab shared by every repository. Define a remote HTTP
or local stdio server once and Switchyard makes it available to both Codex and OpenCode/9router.
Same-named tool overrides can replace a shared definition when one runtime needs different settings.
Each new run stores the resolved MCP snapshot. Secret values are never stored; configure
environment-variable names in the UI and provide their values to the process that starts Switchyard.
OAuth authorization remains separate in each tool CLI.

The MCP tab can generate secret-free native configuration fragments for Codex, OpenCode, Claude Code,
and Cursor. It never edits native settings or assumes their unrelated configuration. The same exports
are available from `switchyard mcp export <codex|opencode|claude-code|cursor>`. Merge the fragment into
the destination shown by the command and authorize the server in that client when required.

The searchable catalog includes all 100 entries in the supplied list, from Playwright, Firebase,
Browser Use, Context7, and Chrome DevTools through database, cloud, search, observability, productivity,
and device integrations. Search or select **Show more servers** to browse the whole list. Use
`switchyard mcp catalog --search "memory"` to discover entries from a terminal. Catalog identities
are retained in saved definitions but excluded from native exports.

Catalog entries are **manual-setup drafts, not verified integrations or installers**. They provide
stable identity and the user-supplied popularity snapshot (not live statistics), then ask for the
current HTTP URL or stdio command from the server's official source. Switchyard does not guess
package names or endpoints. No packages are installed, no servers are launched, and no credentials
are requested when browsing or adding a draft. Complete the connection details before saving; use
the tool's native authentication flow as required. Legacy SSE-only servers and other unsupported
transports require a compatible HTTP/stdio bridge configured by the user.

The global **CLIs** tab defines the shared Codex and OpenCode launch commands used by every repository and workflow step.
It includes 42 built-in profiles, covering all 30 requested AI tools while retaining the existing
catalog. Codex and OpenCode are the directly integrated workflow runtimes. The catalog also includes
ChatGPT Desktop, Claude Desktop, GitHub Copilot, Cursor, Devin Desktop / Windsurf, Kiro, Claude Code,
Gemini CLI, JetBrains Junie, Aider, Cline, Kilo Code, Continue, Ollama, LM Studio, Open WebUI,
AnythingLLM, Jan, GPT4All, Msty Studio, Chatbox AI, llama.cpp, KoboldCpp, LocalAI, Open Interpreter,
Fabric, ComfyUI, InvokeAI, and AUTOMATIC1111 WebUI.

Platform badges reflect the supplied compatibility list, not verified installation guarantees:
ChatGPT on Linux is marked preview, Claude Desktop on Linux beta, LocalAI on Windows WSL/Docker,
and InvokeAI/AUTOMATIC1111 on macOS limited. Check the tool's current requirements, especially for
GPU support. Container, remote, and nonstandard installations may need a custom command or path.
Detection checks executables, common binary locations, and known application/extension files without
executing tools. Files left after uninstalling are reported separately from an available CLI command;
detection is not an authentication or connectivity test.

Each profile stores a provider, model, endpoint, and API-key environment-variable **name**, never its
value. Codex settings are applied to new Switchyard calls (explicit planner/reviewer model overrides
take precedence). OpenCode receives provider configuration, while explicit workflow/chat
`9router/<combo>` routes still take precedence over the profile's standalone default model.
Use **View setup instructions** for secret-free launch commands or tool-specific manual setup.
Other tools' settings are saved preferences until applied in the native tool; SwitchYard does not
overwrite native configurations, install certificates, or enable MITM interception. Existing saved
CLI files automatically gain new catalog entries without removing custom tools.

It can prepend additional executable directories and explicitly forward named environment variables.
The UI stores names and command arguments only, never environment-variable values.
You can also register agent CLI tools such as Jira, Wrangler, GitHub, or database clients; describe
what each command does; and give the AI usage boundaries. Enabled tools are included in every AI
step's instructions. This registers the configured command only; it does not add desktop control or
another workflow runtime. Enable a profile only after configuring a usable CLI command.
Availability checks resolve the executable without running it, and registration
does not install, authenticate, or grant permission to use a tool.

## Start the web interface

```bash
switchyard ui
```

The command opens `http://127.0.0.1:8765` in your default browser. The interface starts trusted
workflow runs after explicit host-execution confirmation.

## Workspace commands

The **CMD** view provides workspace-scoped command tabs. Each tab runs one non-interactive command
at a time from the workspace root (`cmd.exe` on Windows; Bash on macOS and Linux).
Commands keep running while you switch views, tabs, or
workspaces, and the interface shows live status for every workspace with active commands. Output,
exit status, timestamps, and command history are stored locally in
`commands.sqlite3` inside the platform data directory documented under Public installation.

Every launch requires confirmation because commands run directly on the host and are not sandboxed.
Tabs use fresh shells, so shell state such as `cd` or exported variables does not carry between runs;
combine dependent operations in one command. Interactive programs are not supported. Stopping the
SwitchYard server stops its active commands. Saved output is limited to the most recent 262,144
characters per command.

Dispatches may run without selecting a saved workflow, which uses Switchyard's built-in engineering
steps with no saved template overrides. Selecting a named workflow uses its configured ordered
templates, tools, prompts, and overrides. Every run records which path was chosen and keeps the
resolved step snapshot.

Create and edit **Workflows** without selecting a workspace, then choose any workspace at dispatch
time. The workflow library is stored globally, not inside a repository. Saving a shared workflow
updates future runs in every workspace that uses it; existing runs keep their snapshots.

Select **Steps** to configure independent, reusable step templates. The catalog has no execution
order; **Workflows** reference templates by ID and arrange the route, with optional per-workflow
overrides that leave the shared template unchanged. Every step can use the Codex subscription or
any available stable `9router` combo, and every step has its own system prompt. Step categories are
descriptive labels rather than runner-controlled phases. Every
dispatch stores a snapshot of the workflow recipe it used, so later global edits do not change existing runs.
Switchyard reflects the live 9router combo registry every 15 seconds. Use **Refresh 9router** in
the System panel or workflow editor for an immediate read. Added and removed combo names are
reported, and saved steps that reference a removed combo are flagged without being silently changed.
Templates can be added independently. New workflows start with an empty route. Select **Edit
workflow** to rename a saved workflow, add or remove templates, adjust overrides, and drag the
step handles to change its order. Move-up/down buttons provide keyboard and touch alternatives.
Each workflow can run in an isolated worktree (the default) or apply changes directly to the current
branch of a clean repository checkout. Save changes to persist the route, or discard all unsaved
workflow edits. Steps execute strictly in the saved order and may be repeated or omitted. An empty
workflow is valid and completes without changing the repository.
The field labeled system prompt is passed as stage instructions through the CLI task prompt;
it does not replace the coding CLI's own system-level safety instructions.

## Architecture

```text
request
  -> ordered workflow step
  -> selected Codex or OpenCode/9router tool
  -> next configured step with prior output and current workspace
  -> commit completed workspace changes
```

Each step gets the original request and recent output excerpts; complete outputs are saved in the
run record. A failed tool stops later steps and leaves the workspace available for inspection.
There is no implicit planning, validation, selection, merge, or review step. Existing saved
`deterministic` engines load as Codex-backed tools with their original prompts; select a different
AI route in Steps if desired. Migration does not overwrite configuration until you save it.

The legacy internal ticket coordinator remains available to existing integrations, but is not used
by dispatch or `switchyard run`. The following ticket settings apply only to that legacy coordinator:

The three execution attempts use different intents: implementation-first, robustness-first, and an
alternative approach. Combo assignment rotates independently through the configured stable names.
Tickets in the same dependency layer can run concurrently. Set `ORCH_MAX_PARALLEL_TICKETS`, or use
the workspace's **Parallel tickets** setting, to cap them; the default is 1 and the maximum is 8.
The CLI also accepts `--parallel-tickets`:

```bash
switchyard run --repo /path/to/repository --allow-host-execution --parallel-tickets 3 "Implement the requested feature"
```

Each bounded batch starts from the same integrated commit in separate worktrees. Candidate selection
runs concurrently; selected commits are then integrated in stable ticket order with accumulated
checks. Dependent tickets start only after the preceding dependency layer passes integration.
The maximum number of simultaneous implementation agents is the parallel ticket limit multiplied
by attempts per ticket. Use 1 for sequential tickets; attempts within each ticket still run in parallel.
Failures stop later batches and final review; completed peer results and failed worktrees remain
available for inspection. Merge conflicts stop the run without automatic conflict resolution.

## Local setup

For the built-in Codex/OpenCode workflow, install and configure these separately:

- `codex` authenticated with ChatGPT (`codex login status`)
- `9router` running on port `20128`
- `opencode` configured with the `9router` provider
- Git; Docker is optional unless you use the provided container-based setup

For Python-only development, create an editable environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

On Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Use `.venv\Scripts\switchyard.exe` in place of `.venv/bin/switchyard` in the examples on Windows.

Choose logical combos in `.env`:

```bash
cp .env.example .env
```

In PowerShell, use `Copy-Item .env.example .env`.

Changing providers or models inside a combo is a `9router` dashboard operation. Keep the combo name
stable and this workflow needs no changes.

Verify a clean target repository and all integrations:

```bash
.venv/bin/switchyard doctor --repo /path/to/repository
```

Create a structured implementation plan:

```bash
.venv/bin/switchyard plan --repo /path/to/repository "Implement the requested feature"
```

Print the LangGraph pipeline:

```bash
.venv/bin/switchyard graph
```

## Infrastructure

PostgreSQL, Redis, and Langfuse are defined for local production-style operation:

```bash
docker compose up -d
```

The current CLI keeps run manifests in a sibling `.<repository-name>-orchestrator/` directory. Queue-backed
workers and Postgres checkpoints are the next deployment step; Temporal remains intentionally deferred.

## Safety boundaries

- After the first-commit setup described above, workflows using the default isolated integration
  worktree start from the original checkout's committed `HEAD` and do not stage, stash, commit, or
  discard its local changes. Workflows configured without integration isolation require a clean,
  attached checkout and apply selected commits to its current branch.
- Each executor has its own worktree, but can access host files: this is NOT a security sandbox.
- Executors are instructed not to merge or commit; host execution cannot enforce that instruction.
- Workflow Codex calls use a workspace-write sandbox; the standalone planning command is read-only.
- No OpenAI API key is required or read.
- Provider credentials remain in `9router` and OpenCode's existing configuration.

## Current implementation limits

`switchyard run --repo /path/to/repository --allow-host-execution "request"` executes trusted
tasks through the configured sequence of AI steps. Isolated workflows preserve the original
checkout and never pushes. Tests and agents can execute arbitrary host commands, so opt in only
for trusted repositories and tasks. Runtime status is saved after each step; it is not resumable yet.

This is a foundation, not the full production spec. Automated repair, cross-candidate code synthesis,
Docker execution isolation, Redis workers, Postgres checkpoints, and Langfuse tracing remain unwired.
The LangGraph command displays topology only; unbound stages fail explicitly if invoked.
The run command uses a small Python coordinator to prepare the repository, call each configured AI
tool, record outputs, and commit completed changes. Compose services are optional and not started by setup.
No provider billing policy is enforced here: ensure your 9router combos contain only desired
subscription-backed providers if you want to avoid usage-based charges.

Completed runs automatically remove clean workflow worktrees. Their Git branches
remain available as recovery points. Worktrees containing uncommitted files are preserved and listed
in the run record instead of being force-deleted.
