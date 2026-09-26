import subprocess
from pathlib import Path


def test_workflow_editor_creates_edits_reorders_and_saves():
    script = Path("src/ninerouter_orchestrator/web_assets/app.js").read_text(encoding="utf-8")
    editor = script[script.index("function currentWorkflow()"):script.index("\nloadWorkflowConfiguration();")]
    harness = r'''
const assert = require("node:assert/strict");
const elements = new Map();
function element(selector) {
  if (!elements.has(selector)) elements.set(selector, {
    innerHTML: "", value: "", checked: false, disabled: false, hidden: false,
    handlers: {}, textContent: "", focused: false,
    focus() { this.focused = true; },
    addEventListener(type, handler) { this.handlers[type] = handler; },
    querySelector() { return null; }, querySelectorAll() { return []; },
  });
  return elements.get(selector);
}
const document = {querySelector: element, querySelectorAll: () => []};
const workflowsForm = element("#workflows-form");
workflowsForm.querySelector = () => element("save");
workflowsForm.reportValidity = () => {};
const workflowsError = element("#workflows-error");
const stepsError = element("#steps-error");
const window = {confirm: () => true};
const ThemeControls = {confirm: async () => true, validateForm: () => true};
const escapeHtml = value => String(value ?? "");
const engineOptions = () => '<option value="codex">Codex subscription</option>';
const renderSteps = () => {};
const kinds = ["plan", "execute", "validate", "select", "merge", "review"];
const templates = kinds.map(kind => ({id: kind, kind, name: kind, engine: "codex", system_prompt: "instructions"}));
const saved = {default_workflow_id: "default", steps: templates, workflows: [{
  id: "default", name: "Saved workflow", steps: kinds.map(step_id => ({step_id, name: null, engine: null, system_prompt: null})),
}]};
let workflowConfig, savedWorkflowConfig, stepCatalog, selectedWorkflowId, savedPayload;
const fetch = async (url, options = {}) => {
  if (options.method === "PUT") {
    savedPayload = JSON.parse(options.body);
    return {ok: true, json: async () => ({...structuredClone(savedPayload), steps: structuredClone(templates)})};
  }
  return {ok: true, json: async () => structuredClone(saved)};
};
'''
    checks = r'''
(async () => {
  await loadWorkflowConfiguration();
  assert.equal(selectedWorkflowId, null);
  assert.match(element("#workflow-editor").innerHTML, /Edit workflow/);
  assert.match(element("#workflow-editor").innerHTML, /Saved workflow/);
  assert.match(element("#dispatch-workflow").innerHTML, /Without a workflow/);
  assert.equal(element("#dispatch-workflow").value, "");
  assert.equal(element("#recipe-name").textContent, "Built-in engineering steps");
  element("#dispatch-workflow").value = "default";
  renderWorkflowSelector();
  assert.equal(element("#dispatch-workflow").value, "default");
  assert.equal(element("#recipe-name").textContent, "Saved workflow");
  element("#dispatch-workflow").value = "";
  renderWorkflowSelector();
  assert.equal(element("#dispatch-workflow").value, "");
  assert.match(element("#dispatch-workflow-help").textContent, /No saved workflow/);

  editWorkflow("default");
  assert.match(element("#workflow-editor").innerHTML, /<h3>Edit workflow<\/h3>/);
  assert.match(element("#workflow-editor").innerHTML, /data-binding-drag="0"/);
  assert.match(element("#workflow-editor").innerHTML, /Move plan up/);
  assert.equal(currentWorkflow().steps.length, 6);

  moveWorkflowStep(0, 2);
  assert.deepEqual(currentWorkflow().steps.slice(0, 2).map(item => item.step_id), ["execute", "plan"]);
  assert.match(element("#workflow-editor").innerHTML, /Steps run through their selected AI tool in this order/);
  assert.doesNotMatch(element("#workflow-editor").innerHTML, /draft route|Full dispatch requires/);
  moveWorkflowStep(1, 0);
  assert.deepEqual(currentWorkflow().steps.map(item => item.step_id), kinds);

  workflowsForm.handlers.click({target: {closest: selector => selector === "[data-create-workflow]" ? element("#add-workflow") : null}});
  const draft = currentWorkflow();
  assert.equal(draft.name, "");
  assert.equal(draft.steps.length, 0);
  assert.match(element("#workflow-editor").innerHTML, /Start with an empty route/);
  assert.match(element("#workflow-editor").innerHTML, /New workflow/);

  assert.equal(addTemplateToWorkflow("execute", 0), true);
  assert.equal(addTemplateToWorkflow("plan", 0), true);
  assert.deepEqual(draft.steps.map(item => item.step_id), ["plan", "execute"]);
  draft.steps[0].name = "Custom planning";
  moveWorkflowStep(0, 2);
  assert.deepEqual(draft.steps.map(item => item.step_id), ["execute", "plan"]);
  assert.equal(draft.steps[1].name, "Custom planning");
  removeWorkflowStep(0);
  assert.deepEqual(draft.steps.map(item => item.step_id), ["plan"]);

  await discardWorkflowChanges();
  assert.equal(selectedWorkflowId, null);
  assert.equal(workflowConfig.workflows.length, 1);
  assert.deepEqual(workflowConfig, saved);

  editWorkflow("default");
  currentWorkflow().name = "Edited workflow";
  moveWorkflowStep(0, 2);
  await workflowsForm.handlers.submit({preventDefault() {}});
  assert.equal(savedPayload.workflows[0].name, "Edited workflow");
  assert.deepEqual(savedPayload.workflows[0].steps.slice(0, 2).map(item => item.step_id), ["execute", "plan"]);
  assert.equal(savedWorkflowConfig.workflows[0].name, "Edited workflow");
  assert.match(element("#workflows-status").textContent, /Workflows saved/);
  assert.match(element("#workflows-status").textContent, /Available in every workspace/);
  assert.equal("workspace_id" in savedPayload, false);

  // Capture actual form values before reordering, rather than just mutating the model.
  const current = currentWorkflow();
  const editorElement = element("#workflow-editor");
  const controls = current.steps.map(binding => {
    const fields = {"[data-template]": {value: binding.step_id}};
    for (const field of ["name", "engine", "system_prompt"]) {
      fields[`[data-override="${field}"]`] = {checked: field === "system_prompt"};
      fields[`[data-override-field="${field}"]`] = {value: field === "system_prompt" ? `Override ${binding.step_id}` : ""};
    }
    return {querySelector: selector => fields[selector]};
  });
  editorElement.querySelector = selector => selector === "[data-workflow-name]" ? {value: "Live form edit"} : null;
  editorElement.querySelectorAll = () => controls;
  moveWorkflowStep(0, current.steps.length);
  assert.equal(current.name, "Live form edit");
  assert.equal(current.steps.at(-1).system_prompt, "Override execute");
  assert.deepEqual(workflowConfig.steps, templates);
  editorElement.querySelector = () => null;
  editorElement.querySelectorAll = () => [];

  const transfer = {setData(type, value) { this[type] = value; }};
  const card = {dataset: {templateCard: "plan"}, textContent: "plan", classList: {add() {}}};
  const targetFor = (selector, node) => ({closest: query => query === selector ? node : null});
  editorElement.handlers.dragstart({target: targetFor("[data-template-card]", card), dataTransfer: transfer});
  assert.equal(transfer.effectAllowed, "copy");
  const dropTarget = {dataset: {dropIndex: "0"}, classList: {add() {}}};
  let prevented = false;
  const dropEvent = {target: targetFor("[data-drop-index]", dropTarget), dataTransfer: transfer, preventDefault() { prevented = true; }};
  editorElement.handlers.dragover(dropEvent);
  assert.equal(prevented, true);
  const count = current.steps.length;
  editorElement.handlers.drop(dropEvent);
  assert.equal(current.steps.length, count + 1);
  assert.equal(current.steps[0].step_id, "plan");
  assert.equal(draggedTemplateId, null);
  const handle = {dataset: {bindingDrag: "0"}, closest: () => ({classList: {add() {}}})};
  editorElement.handlers.dragstart({target: targetFor("[data-binding-drag]", handle), dataTransfer: transfer});
  assert.equal(transfer.effectAllowed, "move");
  dropTarget.dataset.dropIndex = String(current.steps.length);
  editorElement.handlers.drop(dropEvent);
  assert.equal(current.steps.at(-1).step_id, "plan");
  assert.equal(current.steps.length, count + 1);
  assert.equal(draggedBindingIndex, null);
  editorElement.handlers.dragstart({target: targetFor("[data-binding-drag]", handle), dataTransfer: transfer});
  editorElement.handlers.dragend();
  assert.match(element("#workflow-dnd-status").textContent, /Drag cancelled/);

  createWorkflow();
  currentWorkflow().name = "Empty draft";
  await workflowsForm.handlers.submit({preventDefault() {}});
  assert.deepEqual(savedPayload.workflows.at(-1).steps, []);
})().catch(error => { console.error(error); process.exit(1); });
'''
    subprocess.run(["node"], input=harness + editor + checks, text=True, encoding="utf-8", check=True)


def test_navigation_does_not_toggle_global_workspace_context():
    script = Path("src/ninerouter_orchestrator/web_assets/app.js").read_text(encoding="utf-8")
    navigation = script[script.index("const views ="):script.index("\nlet mcpConfig =")]
    assert '"workspace-context":' not in navigation
    harness = r'''
const assert = require("node:assert/strict");
const elements = new Map();
const document = {
  querySelector(selector) {
    if (!elements.has(selector)) elements.set(selector, {hidden: false, dataset: {}, focus() {}});
    return elements.get(selector);
  },
  querySelectorAll() { return []; },
};
const window = {addEventListener() {}, scrollTo() {}};
const location = {hash: "#workflows"};
'''
    checks = r'''
for (const view of Object.keys(views)) {
  location.hash = `#${view}`;
  navigatePanel();
  assert.equal(document.querySelector("#workspace-context").hidden, false, view);
}
location.hash = "#workflows";
navigatePanel();
assert.equal(document.querySelector("#workflows-panel").hidden, false);
'''
    subprocess.run(["node"], input=harness + navigation + checks, text=True, encoding="utf-8", check=True)
