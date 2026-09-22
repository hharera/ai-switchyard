import subprocess
from pathlib import Path


def test_template_editor_preserves_edits_and_confirms_removal():
    script = Path("src/ninerouter_orchestrator/web_assets/app.js").read_text(encoding="utf-8")
    editor = script[script.index("function engineOptions("):script.index("function currentWorkflow()")]
    route_label = script[script.index("function templateRouteLabel("):script.index("function captureWorkflowEditor()")]
    harness = r'''
const assert = require("node:assert/strict");
const elements = new Map();
let rows = [];
function element(selector) {
  if (!elements.has(selector)) elements.set(selector, {
    innerHTML: "", textContent: "", handlers: {}, focused: false,
    addEventListener(type, handler) { this.handlers[type] = handler; },
    focus() { this.focused = true; },
    querySelector() { return element("save"); },
  });
  return elements.get(selector);
}
const document = {querySelector: element, querySelectorAll: () => rows};
const requestAnimationFrame = callback => callback();
const stepsForm = element("#steps-form");
const stepsError = element("#steps-error");
const escapeHtml = value => String(value ?? "").replaceAll("<", "&lt;").replaceAll('"', "&quot;");
const availableCombos = ["OpenAI-High"];
const template = (id, kind = "plan") => ({id, kind, name: id, engine: ["validate", "merge"].includes(kind) ? "deterministic" : "codex", system_prompt: `Prompt for ${id}`});
let stepCatalog = [template("used"), template("unused"), template("gate", "validate")];
const workflowConfig = {steps: structuredClone(stepCatalog), workflows: []};
const savedWorkflowConfig = {workflows: [{name: "Saved route", steps: [{step_id: "used"}]}]};
let confirmResult = false, confirmCalls = 0, savedPayload;
const ThemeControls = {confirm: async options => { confirmCalls++; assert.equal(options.confirmLabel, "Remove template"); return confirmResult; }};
const captureWorkflowEditor = () => {};
const renderWorkflowEditor = () => {};
const fetch = async (url, options) => {
  savedPayload = JSON.parse(options.body);
  return {ok: true, json: async () => structuredClone(savedPayload)};
};
const fieldEvent = (field, index, value) => ({target: {
  dataset: {field}, value,
  matches(selector) { return selector === "[data-field]" || selector === `[data-field="${field}"]`; },
  closest() { return {dataset: {step: String(index)}, querySelector: element}; },
}});
const removeEvent = index => ({target: {closest: () => ({dataset: {removeTemplate: String(index)}})}});
function editedRow(index, fields) {
  return {dataset: {step: String(index)}, querySelectorAll: () => Object.entries(fields).map(([field, value]) => ({dataset: {field}, value}))};
}
'''
    checks = r'''
(async () => {
  renderSteps();
  let html = element("#step-catalog").innerHTML;
  assert.equal(element("#template-count").textContent, "3 templates");
  assert.match(html, /Used in 1 saved workflow/);
  assert.match(html, /Code safety gate/);
  assert.match(html, /id="step-engine-2"[^>]*disabled/);
  assert.match(html, /id="step-prompt-2"[^>]*readonly/);
  assert.match(html, /id="step-prompt-0"[^>]*aria-describedby="step-prompt-help-0"/);

  // Saved references still protect a template while its workflow has draft edits.
  await stepsForm.handlers.click(removeEvent(0));
  assert.match(stepsError.textContent, /Saved route/);
  assert.equal(confirmCalls, 0);
  assert.equal(stepCatalog.length, 3);

  rows = [editedRow(1, {name: "Edited template", system_prompt: "Keep my draft", kind: "merge"})];
  stepsForm.handlers.change(fieldEvent("kind", 1, "merge"));
  rows = [];
  assert.equal(stepCatalog[1].engine, "deterministic");
  assert.equal(stepCatalog[1].system_prompt, "Keep my draft");
  assert.equal(element("#step-kind-1-control").focused, true);
  assert.match(element("#steps-save-status").textContent, /Unsaved/);

  rows = [editedRow(1, {kind: "execute"})];
  stepsForm.handlers.change(fieldEvent("kind", 1, "execute"));
  rows = [];
  assert.equal(stepCatalog[1].engine, "codex");
  assert.equal(stepCatalog[1].name, "Edited template");
  stepsForm.handlers.change(fieldEvent("engine", 1, "9router/OpenAI-High"));
  assert.equal(element("[data-template-route]").textContent, "9router / OpenAI-High");

  await stepsForm.handlers.click(removeEvent(1));
  assert.equal(confirmCalls, 1);
  assert.equal(stepCatalog.length, 3);
  confirmResult = true;
  await stepsForm.handlers.click(removeEvent(1));
  assert.deepEqual(stepCatalog.map(step => step.id), ["used", "gate"]);
  assert.equal(element('#template-count').textContent, "2 templates");
  assert.equal(element('[data-step="1"] input').focused, true);

  rows = [editedRow(0, {name: 'Safe <title>', system_prompt: 'Instructions <script>'})];
  await stepsForm.handlers.submit({preventDefault() {}});
  rows = [];
  assert.equal(savedPayload.steps[0].system_prompt, 'Instructions <script>');
  assert.equal(workflowConfig.steps[0].name, 'Safe <title>');
  assert.match(element("#step-catalog").innerHTML, /Safe &lt;title>/);
  assert.match(element("#steps-save-status").textContent, /Templates saved/);
  assert.equal(element("save").disabled, false);

  stepCatalog = [];
  renderSteps();
  assert.match(element("#step-catalog").innerHTML, /No step templates/);
  element("#add-step-template").handlers.click();
  assert.equal(stepCatalog.length, 1);
  assert.equal(element("#template-count").textContent, "1 template");
})().catch(error => { console.error(error); process.exit(1); });
'''
    subprocess.run(["node"], input=harness + route_label + editor + checks, text=True, encoding="utf-8", check=True)
