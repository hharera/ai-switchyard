import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../src/ninerouter_orchestrator/web_assets/worktrees.js", import.meta.url), "utf8");
const entry = {
  path: "/repo/worktrees/run/ticket/fork-1", run_id: "run", ticket_id: "ticket",
  attempt: "fork-1", branch: "orchestrator/run/ticket/fork-1", head: "abcdef123456",
  exists: true, changed_files: 0, removable: true, locked: false,
};
const snapshot = worktrees => ({
  worktrees, summary: {total: worktrees.length, changed: 0, protected: 0, missing: 0}, busy: false,
});

function ui() {
  const elements = new Map();
  const events = new Map();
  const calls = [];
  let workspace = {id: "one"};
  let reply = async () => ({ok: true, json: async () => snapshot([entry])});
  const get = id => {
    if (!elements.has(id)) elements.set(id, {
      value: id === "#worktree-filter" ? "all" : "", innerHTML: "", textContent: "",
      disabled: false, hidden: false, events: new Map(),
      replaceChildren() { this.innerHTML = ""; }, setAttribute() {}, focus() {},
      addEventListener(event, callback) { this.events.set(event, callback); },
      dispatchEvent(event) { this.events.get(event.type)?.(event); },
      querySelectorAll() { return []; },
    });
    return elements.get(id);
  };
  const context = vm.createContext({
    document: {querySelector: get}, location: {hash: "#overview"}, Event,
    window: {
      Workspaces: {active: () => workspace},
      addEventListener: (event, callback) => events.set(event, callback),
    },
    fetch: async (...args) => { calls.push(args); return reply(...args); },
    ThemeControls: {confirm: async () => true},
    escapeHtml: value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;"),
  });
  vm.runInContext(source.replace(/\}\)\(\);\s*$/, "globalThis.testUi = {loadWorktrees, performAction, render}; })();"), context);
  return {
    get, calls, context,
    load: () => context.testUi.loadWorktrees(),
    action: (action = "remove") => context.testUi.performAction(action, entry.path),
    respond: fn => { reply = fn; },
    switchWorkspace: id => { workspace = id ? {id} : null; events.get("workspacechange")(); },
  };
}

test("worktrees fetch includes local UI header and escapes repository data", async () => {
  const view = ui();
  view.respond(async () => ({ok: true, json: async () => snapshot([{...entry, ticket_id: '<img src=x onerror="bad()">'}])}));
  await view.load();
  assert.equal(view.calls[0][1].headers["X-Switchyard-Client"], "local-ui");
  assert.match(view.get("#worktree-list").innerHTML, /&lt;img/);
  assert.doesNotMatch(view.get("#worktree-list").innerHTML, /<img/);
  assert.equal(view.get("#worktree-status").textContent, "1 retained worktree.");
});

test("filters reset the themed select and show no-results state", async () => {
  const view = ui();
  await view.load();
  view.get("#worktree-search").value = "missing";
  view.get("#worktree-search").dispatchEvent(new Event("input"));
  assert.match(view.get("#worktree-list").innerHTML, /No matching worktrees/);
  view.get("#worktree-filter").value = "locked";
  view.get("#clear-worktree-filters").dispatchEvent(new Event("click"));
  assert.equal(view.get("#worktree-filter").value, "all");
  assert.match(view.get("#worktree-list").innerHTML, /worktree-card/);
});

test("late responses cannot repopulate a different workspace", async () => {
  const view = ui();
  let resolve;
  view.respond(() => new Promise(done => { resolve = done; }));
  const pending = view.load();
  view.switchWorkspace("two");
  resolve({ok: true, json: async () => snapshot([entry])});
  await pending;
  assert.doesNotMatch(view.get("#worktree-list").innerHTML, /worktree-card/);
});

test("changing workspace during confirmation cancels removal", async () => {
  const view = ui();
  view.context.ThemeControls.confirm = async () => { view.switchWorkspace("two"); return true; };
  await view.action();
  assert.equal(view.calls.length, 0);
});

test("successful removal refreshes and preserves the completion notice", async () => {
  const view = ui();
  view.respond(async (_url, options) => ({ok: true, json: async () => options?.method === "POST" ? {} : snapshot([])}));
  await view.action();
  assert.equal(view.calls.length, 2);
  assert.equal(JSON.parse(view.calls[0][1].body).confirmed, true);
  assert.equal(view.get("#worktree-status").textContent, "Checkout removed. Its branch and commits were kept.");
  assert.match(view.get("#worktree-list").innerHTML, /No retained worktrees/);
});
