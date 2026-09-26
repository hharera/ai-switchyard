import subprocess
from pathlib import Path


def test_dispatch_uses_shared_library_when_switching_workspaces():
    assets = Path("src/ninerouter_orchestrator/web_assets")
    app = (assets / "app.js").read_text(encoding="utf-8")
    workspaces = (assets / "workspaces.js").read_text(encoding="utf-8")
    controls = (assets / "theme-controls.js").read_text(encoding="utf-8")
    functions = "\n".join([
        app[app.index("function renderWorkflowSelector()"):app.index("function renderWorkflowEditor()")],
        app[app.index("function updateDispatchWorkflowLabel()"):app.index("async function loadWorkflowConfiguration()")],
        workspaces[workspaces.index("  function setDispatchWorkflow("):workspaces.index("  function renderList()")],
        controls[controls.index("  function syncSelect("):controls.index("  function renderSelectOptions(")],
    ])
    harness = r'''
const assert = require("node:assert/strict");
const elements = new Map();
const element = selector => {
  if (!elements.has(selector)) elements.set(selector, {value: "", innerHTML: "", textContent: ""});
  return elements.get(selector);
};
const document = {querySelector: element, querySelectorAll: () => []};
const buttonLabel = {textContent: ""};
const button = {querySelector: () => buttonLabel, classList: {toggle() {}}};
const dispatch = element("#dispatch-workflow");
let opened = false, visibleOption = null;
const selectState = new WeakMap([[dispatch, {
  select: dispatch, button, wrapper: {classList: {contains: () => opened}},
}]]);
const renderSelectOptions = state => { visibleOption = state.select.selectedOptions[0]?.value; };
let html = "", options = [], selectedValue = "";
Object.defineProperties(dispatch, {
  innerHTML: {
    get: () => html,
    set(value) {
      html = value;
      options = [...value.matchAll(/<option value="([^"]*)">([^<]*)<\/option>/g)]
        .map(match => ({value: match[1], textContent: match[2]}));
      selectedValue = options[0]?.value || "";
      // Like the theme control's MutationObserver, refresh after DOM mutations.
      queueMicrotask(() => syncSelect({select: dispatch, button}));
    },
  },
  value: {get: () => selectedValue, set(value) { selectedValue = options.some(option => option.value === value) ? value : ""; }},
  selectedOptions: {get: () => options.filter(option => option.value === selectedValue)},
});
const escapeHtml = value => String(value ?? "");
const workflowKinds = ["plan", "execute", "validate", "select", "merge", "review"];
const library = {
  steps: workflowKinds.map(kind => ({id: kind, kind})),
  workflows: ["feature", "repair"].map(id => ({id, name: id, steps: workflowKinds.map(step_id => ({step_id}))})),
  default_workflow_id: "feature",
};
let savedWorkflowConfig = null, workflowConfig, selectedWorkflowId = null;
const config = {default_workspace_id: "alpha", workspaces: [
  {id: "alpha", workflow_id: "feature"},
  {id: "beta", workflow_id: "repair"},
  {id: "builtin", workflow_id: null},
  {id: "missing", workflow_id: "removed"},
]};
let activeId, jobs = [], ready = true;
const active = () => config.workspaces.find(item => item.id === activeId);
const localStorage = {setItem() {}};
const repositoryInput = {}, submitButton = {}, localDelivery = {};
const renderDelivery = () => {}, closeRunDrawer = () => {}, loadJobs = () => {}, renderList = () => {};
const window = {dispatchEvent() {}, ThemeControls: {refreshSelect}};
class CustomEvent {}
'''
    checks = r'''
(async () => {
  // Workspace data may arrive before the global library.
  selectWorkspace("alpha");
  assert.equal(dispatch.value, "feature");
  assert.equal(buttonLabel.textContent, "feature (unavailable)");
  savedWorkflowConfig = structuredClone(library);
  workflowConfig = structuredClone(library);
  renderWorkflowSelector();
  assert.equal(dispatch.value, "feature");
  assert.equal(buttonLabel.textContent, "feature (default)");

  // An already-open custom menu must not retain its old selected row.
  opened = true;
  renderDispatchWorkflowSelector("");
  assert.equal(buttonLabel.textContent, "Without a workflow - built-in steps");
  assert.equal(visibleOption, "");
  renderDispatchWorkflowSelector("feature");
  assert.equal(buttonLabel.textContent, "feature (default)");
  assert.equal(visibleOption, "feature");
  opened = false;

  for (const [workspace, workflow] of [["beta", "repair"], ["alpha", "feature"], ["builtin", ""]]) {
    selectWorkspace(workspace);
    assert.equal(dispatch.value, workflow);
    assert.match(dispatch.innerHTML, /value="feature"/);
    assert.match(dispatch.innerHTML, /value="repair"/);
    assert.equal(buttonLabel.textContent, dispatch.selectedOptions[0].textContent);
    assert.equal(element("#recipe-name").textContent, workflow || "Built-in engineering steps");
    await Promise.resolve();
    assert.equal(buttonLabel.textContent, dispatch.selectedOptions[0].textContent);
  }

  // Unsaved editor changes cannot alter dispatch options or route readiness.
  workflowConfig.steps[0].kind = "execute";
  workflowConfig.workflows.push({id: "draft", name: "Unsaved draft", steps: []});
  renderWorkflowSelector();
  assert.doesNotMatch(dispatch.innerHTML, /Unsaved draft|draft route/);
  assert.equal(dispatch.value, "");

  selectWorkspace("missing");
  renderWorkflowSelector();
  assert.equal(dispatch.value, "removed");
  assert.match(dispatch.innerHTML, /removed \(unavailable\)/);
  selectWorkspace("alpha");
  assert.doesNotMatch(dispatch.innerHTML, /removed/);

  // A library refresh keeps an explicit selection, even if its workflow was deleted.
  savedWorkflowConfig.workflows = savedWorkflowConfig.workflows.filter(flow => flow.id !== "feature");
  renderWorkflowSelector();
  assert.equal(dispatch.value, "feature");
  assert.match(dispatch.innerHTML, /feature \(unavailable\)/);
})().catch(error => { console.error(error); process.exit(1); });
'''
    subprocess.run(["node", "-e", harness + functions + checks], check=True)
