# Switchyard

Switchyard is packaged for Windows, macOS, and Linux through npm while its orchestration engine
remains Python. The npm installer creates an isolated Python environment inside the package,
installs the engine, and exposes the `switchyard` command.

## Public installation

Public npm publication is prepared under the `openswitch` package name but remains blocked until
the release owner chooses a distribution license. Run `npm run release:check` to see the outstanding
owner decision; publication cannot proceed accidentally while the package is marked `UNLICENSED`.

After those fields are set and the package is published, installation is the same on every platform:

```bash
npm install --global openswitch
switchyard --help
switchyard ui
```

| Platform | Requirements |
| --- | --- |
| Windows 10/11 | Node.js 22+, Python 3.12+ from python.org with the `py` launcher, and Git |
| macOS | Node.js 22+, Python 3.12+ from python.org or Homebrew, and Git/Xcode command-line tools |
| Linux | Node.js 22+, Python 3.12+, Git, and the matching `python3-venv` package when required |

The installer prefers a compatible Python on `PATH`, then searches versioned commands and Windows
`py` launchers without invoking a shell. If `SWITCHYARD_PYTHON` is set, it must be an absolute path to an existing Python environment
where Switchyard is already installed; the npm installer verifies it and does not modify it.
Installation needs network access to download Python dependencies from PyPI.

If npm lifecycle scripts were disabled, enable them and rebuild the installed package:

```bash
npm rebuild --global openswitch
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
npm install -g ./openswitch-0.1.0.tgz
```

Existing `orchestrate` and `orchestrate-ui` Python commands remain available for compatibility.

Run `npm test` after installing the Python development dependencies below. `npm run pack:check`
inspects the distributable without publishing it. Package contents exclude local environments,
credentials, tests, and Python bytecode.

Cross-platform CI runs the Python suite, Node package tests, and a real packed global-install smoke
test on Windows, macOS, and Linux with Python 3.12 and 3.13. Native Windows/macOS results must be
confirmed in CI before claiming those platforms are verified.

## Publishing a release

1. Verify that the `openswitch` npm name is still available and that you have the right to use it.
   The installed command remains `switchyard`.
2. Choose a distribution license, add its `LICENSE` file, and set the npm and Python license
   metadata consistently.
3. Keep versions in `package.json` and `pyproject.toml` equal. Run `npm test`,
   `npm run test:install`, and `npm run release:check` before releasing.
4. Configure the repository secret `NPM_TOKEN` with permission to publish the chosen package.
   Use a narrowly scoped, short-lived npm token and review the repository's Actions permissions.
5. Publish a GitHub release tagged `v<version>`, or manually run **Publish npm package** in Actions.
   That workflow waits for all cross-platform CI jobs, validates release metadata and the release
   tag when present, then publishes with npm provenance.

This checkout has not been published. The metadata guard checks required fields, not npm ownership
or legal rights; the release owner must verify both. No PyPI publication or native installers are
configured. The supported distribution is an npm launcher with a locally created Python environment.

## Saved workspaces

Use **Workspaces** to save a name, local Git repository path, default workflow,
attempts per ticket, command timeout, Git comparison base, and delivery settings. Configuration is
stored in the platform data directory documented under Public installation. The active workspace is shared by
Overview, Dispatch, Git, and Runs, and its selection is remembered in the browser. Previously used
repository paths are suggested when adding a workspace; they are not saved automatically.

Dispatch can override workflow and delivery for one run. Each job records a workspace and
run-settings snapshot, so later edits do not change existing jobs. Host execution always needs fresh
confirmation. Runs filters by workspace and includes older jobs with the same repository path.
Removing a workspace only removes its configuration, never repository files or run history.
Workflows, step templates, MCPs, and CLI access are shared across all workspaces. A workspace's
default workflow is only a reference to the shared library, never a workspace-owned copy.

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
Only runs with an approved final review publish. Failed or unapproved runs never publish.
Pushes are non-forced. PRs use authenticated `gh` and an explicit base branch, with draft enabled by
default. A failed PR attempt records any successful push; local integration branches remain recoverable.
These controls govern orchestrator delivery; agents are instructed not to publish independently,
but host-executing agents and commands are not a security sandbox.

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

Open `http://127.0.0.1:8765`. The interface starts trusted workflow runs after explicit
host-execution confirmation.

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
steps with no saved template overrides. Selecting a named workflow uses its configured phase
templates, tools, prompts, and overrides. Every run records which path was chosen and keeps the
resolved step snapshot.

Create and edit **Workflows** without selecting a workspace, then choose any workspace at dispatch
time. The workflow library is stored globally, not inside a repository. Saving a shared workflow
updates future runs in every workspace that uses it; existing runs keep their snapshots.

Select **Steps** to configure independent, reusable step templates. The catalog has no execution
order; **Workflows** reference templates by ID and arrange the route, with optional per-workflow
overrides that leave the shared template unchanged. Planning, execution, candidate selection,
and final review can use the Codex subscription or any available stable `9router` combo. Each AI
stage has its own system prompt. Validation and merge remain deterministic safety gates. Every
dispatch stores a snapshot of the workflow recipe it used, so later global edits do not change existing runs.
Switchyard reflects the live 9router combo registry every 15 seconds. Use **Refresh 9router** in
the System panel or workflow editor for an immediate read. Added and removed combo names are
reported, and saved steps that reference a removed combo are flagged without being silently changed.
Templates can be added independently. New workflows start with an empty route. Select **Edit
workflow** to rename a saved workflow, add or remove templates, adjust overrides, and drag the
step handles to change its order. Move-up/down buttons provide keyboard and touch alternatives.
Each workflow can keep integration in an isolated worktree (the default) or apply selected candidate
commits directly to the current branch of a clean repository checkout. Candidate attempts always use
separate worktrees.
Save changes to persist the route, or discard all unsaved workflow edits. Empty and partial routes
can be saved as drafts. Full dispatch requires exactly one plan, execute, validate, select, merge,
and review step. Complete workflows may arrange those steps freely in the editor; the engineering
runner resolves their semantic dependencies when it executes them. Routes with a missing or repeated
phase are rejected before execution.
The field labeled system prompt is passed as stage instructions through the CLI task prompt;
it does not replace the coding CLI's own system-level safety instructions.

## Architecture

```text
request
  -> Codex planner
  -> dependency-aware tickets
  -> three isolated git worktrees per ticket
  -> OpenCode -> 9router/<stable-combo>
  -> deterministic validation
  -> Codex candidate decision
  -> dependency-first merge and integration tests
  -> Codex final review
  -> repair tickets -> 9router
```

The three execution attempts use different intents: implementation-first, robustness-first, and an
alternative approach. Combo assignment rotates independently through the configured stable names.

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
- Planner and reviewer Codex calls use a read-only sandbox.
- No OpenAI API key is required or read.
- Provider credentials remain in `9router` and OpenCode's existing configuration.

## Current implementation limits

`switchyard run --repo /path/to/repository --allow-host-execution "request"` executes trusted
tasks through candidate selection, integration checks, and final review. It preserves the original
checkout and never pushes. Tests and agents can execute arbitrary host commands, so opt in only
for trusted repositories and tasks. Runtime status is saved after each ticket; it is not resumable yet.

This is a foundation, not the full production spec. Automated repair, cross-candidate code synthesis,
Docker execution isolation, Redis workers, Postgres checkpoints, and Langfuse tracing remain unwired.
The LangGraph command displays topology only; unbound stages fail explicitly if invoked.
The run command currently uses the deterministic Python coordinator. Review findings stop the run
with `needs_repair` instead of claiming success. Compose services are optional and not started by setup.
No provider billing policy is enforced here: ensure your 9router combos contain only desired
subscription-backed providers if you want to avoid usage-based charges.

Completed runs automatically remove clean candidate and integration worktrees. Their Git branches
remain available as recovery points. Worktrees containing uncommitted files are preserved and listed
in the run record instead of being force-deleted.
