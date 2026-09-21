import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../src/ninerouter_orchestrator/web_assets/workspaces.js", import.meta.url), "utf8");

function picker() {
  const elements = new Map();
  const element = () => ({
    value: "", children: [], disabled: false, textContent: "", dataset: {}, scrollTop: 0,
    replaceChildren() { this.children = []; },
    append(child) { this.children.push(child); },
    removeAttribute() {}, setAttribute() {}, focus() {}, querySelectorAll() { return []; },
  });
  const get = id => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  let response = {ok: true, body: {path: "/repos", parent: "/", folders: []}};
  const context = vm.createContext({
    document: {querySelector: get, createElement: element},
    AbortController, setTimeout, clearTimeout,
    fetch: async () => ({ok: response.ok, json: async () => response.body}),
  });
  // Exercise the actual folder-picker functions without starting unrelated workspace requests.
  vm.runInContext(source.slice(0, source.indexOf("  function workflowOptions")) +
    "globalThis.picker = {browseFolder, renderFolders}; })();", context);
  return {
    get,
    load: async (folders, path = "/repos") => {
      response = {ok: true, body: {path, parent: "/", folders}};
      await context.picker.browseFolder(path);
    },
    fail: async () => {
      response = {ok: false, body: {detail: "Cannot read this folder."}};
      await context.picker.browseFolder("/missing");
    },
    search: value => { get("#folder-browser-search").value = value; context.picker.renderFolders(); },
    names: () => get("#folder-browser-list").children.map(child => child.textContent),
  };
}

test("folder search filters case-insensitively and preserves folder paths", async () => {
  const ui = picker();
  await ui.load([{name: ".codex", path: "/repos/.codex"}, {name: "IdeaProjects", path: "/repos/IdeaProjects"}]);
  ui.search("  IDEA  ");
  assert.deepEqual(ui.names(), ["IdeaProjects"]);
  assert.equal(ui.get("#folder-browser-list").children[0].dataset.folderPath, "/repos/IdeaProjects");
  assert.equal(ui.get("#folder-browser-status").textContent, "1 of 2 subfolders match");
  ui.search("");
  assert.deepEqual(ui.names(), [".codex", "IdeaProjects"]);
  assert.equal(ui.get("#folder-browser-clear-search").disabled, true);
});

test("folder search supports Unicode names and distinguishes empty states", async () => {
  const ui = picker();
  const name = "\u0645\u0634\u0631\u0648\u0639";
  await ui.load([{name, path: `/repos/${name}`}]);
  ui.search(name);
  assert.deepEqual(ui.names(), [name]);
  ui.search("missing");
  assert.match(ui.get("#folder-browser-list").children[0].innerHTML, /No matching subfolders/);
  assert.equal(ui.get("#folder-browser-status").textContent, "0 of 1 subfolders match");
  assert.equal(ui.get("#folder-browser-select").disabled, false);
  await ui.load([]);
  assert.match(ui.get("#folder-browser-list").children[0].innerHTML, /No subfolders/);
});

test("navigation resets search and failed loads disable stale folder results", async () => {
  const ui = picker();
  await ui.load([{name: "Alpha", path: "/repos/Alpha"}]);
  ui.search("Alpha");
  await ui.load([{name: "Beta", path: "/other/Beta"}], "/other");
  assert.equal(ui.get("#folder-browser-search").value, "");
  assert.deepEqual(ui.names(), ["Beta"]);
  assert.equal(ui.get("#folder-browser-search").disabled, false);
  await ui.fail();
  assert.deepEqual(ui.names(), []);
  assert.equal(ui.get("#folder-browser-search").disabled, true);
  assert.equal(ui.get("#folder-browser-clear-search").disabled, true);
  assert.equal(ui.get("#folder-browser-select").disabled, true);
});
