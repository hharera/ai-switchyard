import json
import subprocess
from pathlib import Path

ASSETS = Path("src/ninerouter_orchestrator/web_assets")


def test_pull_request_javascript_parses():
    result = subprocess.run(
        ["node", "--check", str(ASSETS / "pull-requests.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, json.dumps(result.stderr)


def test_review_ui_keeps_external_data_escaped_and_actions_guarded():
    script = (ASSETS / "pull-requests.js").read_text(encoding="utf-8")
    assert "escape(request.title)" in script
    assert "escape(request.body" in script
    assert "escape(comment.body" in script
    assert 'rel="noopener noreferrer"' in script
    assert "version !== generation" in script
    assert "chosen !== selection" in script
    assert "if (writing" in script
    assert "head_sha: request.head_sha" in script
    assert "expected_state: request.state" in script
    assert "expected_repository: request.repository.web_url" in script
    assert "confirmed: true" in script
    assert 'repository: $("pr-repository-select").value' in script
    assert "/api/git/repositories?workspace_id=${encodeURIComponent(workspaceId)}" in script


def test_review_ui_has_labeled_controls_and_responsive_layout():
    html = (ASSETS / "index.html").read_text(encoding="utf-8")
    css = (ASSETS / "pull-requests.css").read_text(encoding="utf-8")
    for identifier in ("pr-repository-select", "pr-provider", "pr-state", "pr-search", "pr-message"):
        assert f'for="{identifier}"' in html or identifier == "pr-message"
    assert 'role="status"' in html
    assert 'role="alert"' in html
    assert "@media (max-width: 800px)" in css
    assert ":focus-visible" in css


def test_setup_states_and_recovery_when_provider_fetch_fails():
    # Exercise the shipped script with a minimal DOM, including async event handlers.
    result = subprocess.run(["node", "-e", r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("src/ninerouter_orchestrator/web_assets/pull-requests.js", "utf8");
async function scenario(provider, cli, credential, fail = false) {
  const elements = new Map(), requests = [];
  const el = id => {
    if (!elements.has(id)) elements.set(id, {textContent: "", value: "", hidden: false, options: [{}], handlers: {},
      addEventListener(event, handler) { this.handlers[event] = handler; },
      setAttribute() {}, querySelectorAll() { return []; }, replaceChildren() {}});
    return elements.get(id);
  };
  el("pr-provider").value = "auto";
  el("pr-state").value = "open";
  const repository = {provider, cli, credential_available: credential, path: "team/repo", remote: "origin",
    host: {github: "github.com", gitlab: "gitlab.com", bitbucket: "bitbucket.org"}[provider]};
  let copied;
  const context = {document: {getElementById: el}, location: {hash: ""},
    window: {Workspaces: {active: () => ({id: "workspace"})}, addEventListener() {}},
    URL, URLSearchParams, AbortController, Option: function() {},
    navigator: {clipboard: {writeText: async text => {copied = text;}}},
    fetch: async url => {
      requests.push(url);
      if (url.startsWith("/api/git/repositories")) return {ok: true, json: async () => ({repositories: [{name: "repo", relative_path: ".", path: "/repo"}]})};
      if (url.startsWith("/api/pull-requests/setup")) return {ok: true, json: async () => repository};
      return {ok: !fail, json: async () => fail ? {detail: "Private repository unavailable"} : {repository, items: [], has_more: false}};
    }};
  vm.runInNewContext(source, context);
  await el("pr-refresh").handlers.click();
  assert.equal(requests[1].startsWith("/api/pull-requests/setup"), true);
  assert.equal(el("pr-provider").options[0].textContent.startsWith("Detected:"), true);
  if (fail) assert.equal(el("pr-error").textContent, "Private repository unavailable");
  return {el, requests, repository, copy: async () => {await el("pr-copy-auth").handlers.click(); return copied;}};
}
(async () => {
  for (const [provider, command, host] of [["github", "gh", "github.com"], ["gitlab", "glab", "gitlab.com"]]) {
    const missing = await scenario(provider, {command, installed: false, credential_available: false}, false, true);
    assert.equal(missing.el("pr-setup").hidden, false);
    assert.equal(missing.el("pr-install-cli").hidden, false);
    assert.equal(await missing.copy(), `${command} auth login --hostname ${host}`);
    missing.repository.cli.installed = true;
    missing.repository.cli.credential_available = true;
    missing.repository.credential_available = true;
    await missing.el("pr-check-setup").handlers.click();
    assert.equal(missing.el("pr-setup").hidden, true);
    const unsigned = await scenario(provider, {command, installed: true, credential_available: false}, false);
    assert.equal(unsigned.el("pr-install-cli").hidden, true);
    assert.equal(unsigned.el("pr-copy-auth").hidden, false);
    const ready = await scenario(provider, {command, installed: true, credential_available: true}, true);
    assert.equal(ready.el("pr-setup").hidden, true);
    const env = await scenario(provider, {command, installed: false, credential_available: false}, true);
    assert.equal(env.el("pr-setup").hidden, false);
    assert.match(env.el("pr-setup-message").textContent, /environment credential/);
  }
  const bitbucket = await scenario("bitbucket", null, false);
  assert.equal(bitbucket.el("pr-copy-auth").hidden, true);
  assert.match(bitbucket.el("pr-setup-message").textContent, /no supported first-party CLI/);
  assert.equal((await scenario("bitbucket", null, true)).el("pr-setup").hidden, true);
})().catch(error => {console.error(error); process.exitCode = 1;});
'''], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
