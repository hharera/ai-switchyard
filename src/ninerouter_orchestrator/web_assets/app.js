const form = document.querySelector("#dispatch-form");
const errorBox = document.querySelector("#form-error");
const submitButton = form.querySelector("button[type=submit]");
const runDrawer = document.querySelector("#run-drawer");
const runDrawerClose = document.querySelector("#run-drawer-close");
const runStreamStatus = document.querySelector("#run-stream-status");
let selectedJobId = null;
let runEventSource = null;
let streamedJobId = null;
let jobs = [];
let stepCatalog = [];
let workflowConfig = null;
let savedWorkflowConfig = null;
let selectedWorkflowId = null;
let availableCombos = [];
const stepsForm = document.querySelector("#steps-form");
const stepsError = document.querySelector("#steps-error");
const workflowsForm = document.querySelector("#workflows-form");
const workflowsError = document.querySelector("#workflows-error");

const repositoryInput = document.querySelector("#repository");
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  })[character]);
}

async function loadHealth() {
  try {
    const response = await fetch("/api/health");
    const health = await response.json();
    applyRegistry(health);
    document.querySelector("#status-list").innerHTML = [
      ["Codex login", health.codex], ["9router gateway", health.router], ["OpenCode", health.opencode]
    ].map(([label, ok]) => `<div class="status-row"><span>${label}</span><b class="${ok ? "ok" : "fail"}">${ok ? "Ready" : "Check"}</b></div>`).join("");
  } catch { /* System view retains its last known state during a transient failure. */ }
}

function applyRegistry(registry) {
  if (registry.error) {
    document.querySelector("#combo-meta").textContent = registry.error;
    return;
  }
  availableCombos = registry.combos || [];
  document.querySelectorAll('[data-field="engine"], [data-override-field="engine"]').forEach(select => {
    const value = select.value;
    const kind = select.closest("[data-kind]")?.dataset.kind || "plan";
    select.innerHTML = engineOptions({kind, engine: value});
  });
  document.querySelector("#combo-list").innerHTML = availableCombos.length
    ? availableCombos.map((combo) => `<span class="combo-chip">${escapeHtml(combo)}</span>`).join("")
    : '<span class="combo-chip pending">No routes available</span>';
  const time = registry.refreshed_at ? new Date(registry.refreshed_at).toLocaleTimeString() : "not available";
  document.querySelector("#combo-meta").textContent = `Read from ${registry.source || "9router"} at ${time}`;
  const warning = document.querySelector("#route-warning");
  const issues = registry.workflow_issues || [];
  warning.classList.toggle("visible", issues.length > 0);
  warning.textContent = issues.length
    ? `Update the workflow: ${issues.map(issue => `${issue.step} uses missing route ${issue.combo}`).join("; ")}.`
    : "";
}

async function refreshRegistry(button, status) {
  button.disabled = true;
  const original = button.textContent;
  button.textContent = "Refreshing...";
  if (status) status.textContent = "Reading the current combo registry.";
  try {
    const response = await fetch("/api/9router/refresh", { method: "POST", headers: { "X-Switchyard-Client": "local-ui" } });
    const registry = await response.json();
    if (!response.ok) throw new Error(registry.detail || "Unable to read 9router combos.");
    if (registry.error) throw new Error(registry.error);
    applyRegistry(registry);
    const changes = [
      registry.added?.length ? `Added: ${registry.added.join(", ")}` : "",
      registry.removed?.length ? `Removed: ${registry.removed.join(", ")}` : ""
    ].filter(Boolean).join(". ");
    if (status) status.textContent = changes || `Routes are current. ${availableCombos.length} available.`;
  } catch (error) {
    if (status) status.textContent = error.message;
    else document.querySelector("#combo-meta").textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function renderJobs() {
  const list = document.querySelector("#run-list");
  if (!jobs.length) {
    list.innerHTML = '<div class="empty-state"><b>No dispatches for this workspace</b><span>Run a workflow to start its history.</span></div>';
    return;
  }
  list.innerHTML = jobs.map((job) => `
      <button class="run-card ${job.id === selectedJobId ? "selected" : ""}" data-job="${job.id}" aria-pressed="${job.id === selectedJobId}">
        <span class="run-top"><span class="run-id">${escapeHtml(job.id)}</span><span class="run-status ${escapeHtml(job.status)}">${escapeHtml(job.status.replaceAll("_", " "))}</span></span>
        <p>${escapeHtml(job.request)}</p>
        <small>${escapeHtml(job.error || job.stage)} - ${new Date(job.updated_at).toLocaleString()}</small>
      </button>`).join("");
  list.querySelectorAll("[data-job]").forEach((button) => button.addEventListener("click", () => showJob(button.dataset.job)));
}

async function loadJobs() {
  try {
    const workspaceId = window.Workspaces?.active()?.id;
    const response = await fetch(`/api/jobs${workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ""}`);
    if (!response.ok) throw new Error("Unable to load runs");
    const loaded = await response.json();
    if (workspaceId !== window.Workspaces?.active()?.id) return;
    // A list poll can finish after a newer stream event. Never roll a run back.
    const nextJobs = loaded.map(job => {
      const current = jobs.find(item => item.id === job.id);
      return current && (current.revision || 0) > (job.revision || 0) ? current : job;
    });
    if (!nextJobs.length || JSON.stringify(nextJobs) !== JSON.stringify(jobs)) {
      jobs = nextJobs;
      renderJobs();
    }
    if (selectedJobId && runDrawer.classList.contains("open")) showJob(selectedJobId, false);
  } catch { /* Keep the last visible list during a transient poll failure. */ }
}

function runIsActive(job) {
  return ["queued", "running"].includes(job?.status);
}

function setRunStreamStatus(message, state = "") {
  if (runStreamStatus.textContent !== message) runStreamStatus.textContent = message;
  runStreamStatus.dataset.state = state;
}

function stopRunStream(message = "") {
  runEventSource?.close();
  runEventSource = null;
  streamedJobId = null;
  setRunStreamStatus(message);
}

function applyStreamedJob(job) {
  if (!job || job.id !== selectedJobId) return;
  const index = jobs.findIndex(item => item.id === job.id);
  if (index !== -1 && (jobs[index].revision || 0) > (job.revision || 0)) return;
  if (index === -1) jobs.unshift(job);
  else jobs[index] = job;
  renderJobs();
  showJob(job.id, false);
  if (runIsActive(job)) setRunStreamStatus(job.stage || "Live updates", "live");
  else stopRunStream(`Updates finished: ${String(job.status).replaceAll("_", " ")}`);
}

function startRunStream(job) {
  if (!runIsActive(job)) {
    stopRunStream(`Updates finished: ${String(job.status).replaceAll("_", " ")}`);
    return;
  }
  if (runEventSource && streamedJobId === job.id) return;
  stopRunStream();
  if (!("EventSource" in window)) {
    setRunStreamStatus("Refreshing every 3 seconds", "fallback");
    return;
  }
  streamedJobId = job.id;
  setRunStreamStatus("Connecting to live updates", "connecting");
  const source = new EventSource(`/api/jobs/${encodeURIComponent(job.id)}/events`);
  runEventSource = source;
  source.onopen = () => {
    if (source === runEventSource) setRunStreamStatus("Live updates", "live");
  };
  source.addEventListener("job", event => {
    if (source !== runEventSource || !runDrawer.classList.contains("open")) return;
    try { applyStreamedJob(JSON.parse(event.data)); }
    catch { setRunStreamStatus("Waiting for a valid update", "reconnecting"); }
  });
  source.addEventListener("unavailable", () => {
    if (source === runEventSource) stopRunStream("Run is no longer available");
  });
  source.onerror = () => {
    if (source === runEventSource && streamedJobId === selectedJobId) {
      if (source.readyState === 2) {
        stopRunStream("Live connection closed; refreshing every 3 seconds");
      } else {
        setRunStreamStatus("Reconnecting; refreshing every 3 seconds", "reconnecting");
      }
    }
  };
}

function showJob(id, focus = true) {
  const job = jobs.find((item) => item.id === id);
  if (!job) return;
  const sameJob = selectedJobId === id;
  selectedJobId = id;
  const detail = document.querySelector("#run-detail");
  const fingerprint = JSON.stringify(job);
  if (!sameJob || detail.dataset.snapshot !== fingerprint) {
    const scrollTop = runDrawer.scrollTop;
    const rawScroll = sameJob ? detail.querySelector(".raw-run-record")?.scrollTop : 0;
    const expanded = new Set(sameJob ? [...detail.querySelectorAll("details[open]")].map(item => item.dataset.detailKey) : []);
    const focusedKey = sameJob && detail.contains(document.activeElement)
      ? document.activeElement?.closest("details")?.dataset.detailKey : null;
    detail.innerHTML = renderRunDetails(job);
    detail.dataset.snapshot = fingerprint;
    detail.querySelectorAll("details").forEach(item => {
      item.open = expanded.has(item.dataset.detailKey);
      if (item.dataset.detailKey === focusedKey) item.querySelector("summary").focus({preventScroll: true});
    });
    runDrawer.scrollTop = sameJob ? scrollTop : 0;
    const raw = detail.querySelector(".raw-run-record");
    if (raw) raw.scrollTop = rawScroll || 0;
  }
  document.querySelectorAll("[data-job]").forEach(button => {
    const selected = button.dataset.job === id;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  runDrawer.classList.add("open");
  document.body.classList.add("run-details-open");
  runDrawer.setAttribute("aria-hidden", "false");
  startRunStream(job);
  if (focus) runDrawerClose.focus();
}

function closeRunDrawer() {
  runDrawer.classList.remove("open");
  document.body.classList.remove("run-details-open");
  runDrawer.setAttribute("aria-hidden", "true");
  stopRunStream();
  document.querySelector(`[data-job="${selectedJobId}"]`)?.focus();
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.textContent = "";
  const data = new FormData(form);
  if (!window.Workspaces?.active()) {
    errorBox.textContent = "Add and select a saved workspace before dispatching.";
    return;
  }
  const payload = {
    workspace_id: window.Workspaces.active().id, request: data.get("request"),
    workflow_id: data.get("workflow_id") || null,
    allow_host_execution: document.querySelector("#allow-host").checked,
    delivery: readDelivery("dispatch")
  };
  if (!payload.allow_host_execution) {
    errorBox.textContent = "Confirm host execution before running implementation agents.";
    return;
  }
  submitButton.disabled = true;
  submitButton.querySelector("span").textContent = "Starting workflow...";
  try {
    const response = await fetch("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json", "X-Switchyard-Client": "local-ui" }, body: JSON.stringify(payload) });
    const body = await response.json();
    if (!response.ok) throw new Error(Array.isArray(body.detail) ? body.detail.map(item => item.msg).join("; ") : body.detail || "Unable to start. Check the repository and try again.");
    form.querySelector("textarea").value = "";
    await loadJobs();
    showJob(body.id);
  } catch (error) {
    errorBox.textContent = error.message;
  } finally {
    submitButton.disabled = false;
    submitButton.querySelector("span").textContent = "Send to switchyard";
    document.querySelector("#allow-host").checked = false;
  }
});

runDrawerClose.addEventListener("click", closeRunDrawer);
window.addEventListener("pagehide", () => stopRunStream());
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && runDrawer.classList.contains("open")) closeRunDrawer();
});

loadHealth();
loadJobs();
setInterval(loadJobs, 3000);
setInterval(loadHealth, 15000);

function engineOptions(step) {
  if (["validate", "merge"].includes(step.kind)) {
    return '<option value="deterministic">Deterministic code</option>';
  }
  const engines = [
    ["codex", "Codex subscription"],
    ["9router/auto", "9router / rotate defaults"],
    ...availableCombos.map((combo) => [`9router/${combo}`, `9router / ${combo}`])
  ];
  if (!engines.some(([value]) => value === step.engine)) engines.push([step.engine, `${step.engine} (unavailable)`]);
  return engines.map(([value, label]) => `<option value="${escapeHtml(value)}" ${value === step.engine ? "selected" : ""}>${escapeHtml(label)}</option>`).join("");
}

function renderSteps() {
  document.querySelector("#template-count").textContent = `${stepCatalog.length} ${stepCatalog.length === 1 ? "template" : "templates"}`;
  document.querySelector("#step-catalog").innerHTML = stepCatalog.map((step, index) => {
    const deterministic = ["validate", "merge"].includes(step.kind);
    const usage = savedWorkflowConfig?.workflows.filter(flow => flow.steps.some(binding => binding.step_id === step.id)).length || 0;
    return `<li class="template-card" data-step="${index}" data-kind="${step.kind}">
      <header class="template-heading">
        <div class="template-identity"><h3 data-template-title>${escapeHtml(step.name || "Untitled template")}</h3><span class="template-id">ID / <code>${escapeHtml(step.id)}</code></span></div>
        <span class="template-mode ${deterministic ? "is-deterministic" : ""}">${deterministic ? "Code safety gate" : "AI agent"}</span>
      </header>
      <p class="template-route"><span>${escapeHtml(step.kind)}</span><span aria-hidden="true">/</span><span data-template-route>${escapeHtml(templateRouteLabel(step))}</span></p>
      <div class="template-fields">
      <div class="step-field template-wide">
        <label for="step-name-${index}">Template name</label>
        <input id="step-name-${index}" data-field="name" value="${escapeHtml(step.name)}" required maxlength="80">
      </div>
      <div class="step-field">
        <label for="step-kind-${index}">Step type</label>
        <select id="step-kind-${index}" data-field="kind" aria-describedby="step-kind-help-${index}">${["plan", "execute", "validate", "select", "merge", "review"].map(kind => `<option value="${kind}" ${kind === step.kind ? "selected" : ""}>${kind}</option>`).join("")}</select>
        <span class="engine-note" id="step-kind-help-${index}">Determines compatible workflow phases, not a template order.</span>
      </div>
      <div class="step-field">
        <label for="step-engine-${index}">${deterministic ? "Execution" : "Default route"}</label>
        <select id="step-engine-${index}" data-field="engine" aria-describedby="step-engine-help-${index}" ${deterministic ? "disabled" : ""}>${engineOptions(step)}</select>
        <span class="engine-note" id="step-engine-help-${index}">${deterministic ? "Runs in code. No model is called." : "Codex uses its configured model; 9router routes run through OpenCode."}</span>
      </div>
      <div class="step-field template-wide template-prompt">
        <div class="template-prompt-heading"><label for="step-prompt-${index}">${deterministic ? "Step contract" : "System prompt"}</label><span>${deterministic ? "Read-only" : "Editable instructions"}</span></div>
        <textarea id="step-prompt-${index}" data-field="system_prompt" aria-describedby="step-prompt-help-${index}" required maxlength="12000" ${deterministic ? "readonly" : ""}>${escapeHtml(step.system_prompt)}</textarea>
        <span class="engine-note" id="step-prompt-help-${index}">${deterministic ? "Describes the gate. Safety checks are controlled by the runner, not this text." : "Set the objective, constraints, and expected output. Workflows inherit this prompt unless overridden."}</span>
      </div>
      </div>
      <footer class="template-footer"><span>${usage ? `Used in ${usage} saved ${usage === 1 ? "workflow" : "workflows"}` : "Not used in a saved workflow"}</span><button class="secondary-button remove-template" type="button" data-remove-template="${index}" aria-label="Remove template: ${escapeHtml(step.name)}">Remove template</button></footer>
    </li>`;
  }).join("") || '<li class="template-empty"><h3>No step templates</h3><p>Add a template to define a reusable agent or code safety gate.</p></li>';
}

function readSteps() {
  const edited = structuredClone(stepCatalog);
  document.querySelectorAll("[data-step]").forEach((row) => {
    const step = edited[Number(row.dataset.step)];
    row.querySelectorAll("[data-field]").forEach((field) => { step[field.dataset.field] = field.value.trim(); });
  });
  return edited;
}

async function openWorkflow() {
  if (!workflowConfig) await loadWorkflowConfiguration();
  location.hash = "workflows";
}

document.querySelector("#recipe-name").addEventListener("click", openWorkflow);
document.querySelector("#refresh-combos").addEventListener("click", (event) => refreshRegistry(event.currentTarget));
document.querySelector("#refresh-workflow-combos").addEventListener("click", (event) => refreshRegistry(event.currentTarget, document.querySelector("#workflow-refresh-status")));

stepsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  stepsError.textContent = "";
  const button = stepsForm.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    captureWorkflowEditor();
    const response = await fetch("/api/steps", { method: "PUT", headers: { "Content-Type": "application/json", "X-Switchyard-Client": "local-ui" }, body: JSON.stringify({steps: readSteps()}) });
    const body = await response.json();
    if (!response.ok) throw new Error(Array.isArray(body.detail) ? body.detail.map((item) => item.msg).join("; ") : body.detail);
    stepCatalog = body.steps;
    workflowConfig.steps = structuredClone(stepCatalog);
    renderSteps(); renderWorkflowEditor();
    document.querySelector("#steps-save-status").textContent = "Templates saved. Workflows without overrides now inherit these defaults.";
  } catch (error) {
    stepsError.textContent = error.message || "Unable to save templates.";
  } finally {
    button.disabled = false;
  }
});

stepsForm.addEventListener("input", event => {
  document.querySelector("#steps-save-status").textContent = "Unsaved template changes";
  const row = event.target.closest("[data-step]");
  if (event.target.dataset.field === "name") {
    const name = event.target.value.trim() || "Untitled template";
    row.querySelector("[data-template-title]").textContent = name;
    row.querySelector("[data-remove-template]").setAttribute("aria-label", `Remove template: ${name}`);
  }
});
stepsForm.addEventListener("change", event => {
  if (event.target.matches("[data-field]")) document.querySelector("#steps-save-status").textContent = "Unsaved template changes";
  if (event.target.matches('[data-field="engine"]')) {
    event.target.closest("[data-step]").querySelector("[data-template-route]").textContent = templateRouteLabel({engine: event.target.value});
  }
  if (!event.target.matches('[data-field="kind"]')) return;
  stepCatalog = readSteps();
  const index = Number(event.target.closest("[data-step]").dataset.step);
  const step = stepCatalog[index];
  if (["validate", "merge"].includes(step.kind)) step.engine = "deterministic";
  else if (step.engine === "deterministic") step.engine = "codex";
  renderSteps();
  // The shared control enhancer replaces native selects after the DOM update.
  requestAnimationFrame(() => (document.querySelector(`#step-kind-${index}-control`) || document.querySelector(`#step-kind-${index}`))?.focus());
});
stepsForm.addEventListener("click", async event => {
  const remove = event.target.closest("[data-remove-template]");
  if (!remove) return;
  try { stepCatalog = readSteps(); } catch { return; }
  const index = Number(remove.dataset.removeTemplate);
  const id = stepCatalog[index].id;
  const referencingFlows = [...workflowConfig.workflows, ...(savedWorkflowConfig?.workflows || [])].filter(flow => flow.steps.some(binding => binding.step_id === id));
  if (referencingFlows.length) {
    const names = [...new Set(referencingFlows.map(flow => flow.name || "Untitled workflow"))].join(", ");
    stepsError.textContent = `This template is used by: ${names}. Replace those references and save the workflows before removing it.`; return;
  }
  stepsError.textContent = "";
  const confirmed = await ThemeControls.confirm({title: "Remove template?", message: `Remove "${stepCatalog[index].name}" from the catalog? This takes effect when you save step templates.`, confirmLabel: "Remove template"});
  if (!confirmed) return;
  stepCatalog.splice(index, 1); renderSteps();
  (document.querySelector(`[data-step="${Math.min(index, stepCatalog.length - 1)}"] input`) || document.querySelector("#add-step-template")).focus();
  document.querySelector("#steps-save-status").textContent = "Unsaved template changes";
});

document.querySelector("#add-step-template").addEventListener("click", () => {
  try { stepCatalog = readSteps(); } catch { /* Preserve the last valid state. */ }
  const base = `step-${Date.now().toString(36)}`;
  stepCatalog.push({id: base, kind: "plan", name: "New step template", engine: "codex", system_prompt: "Describe the objective, constraints, and expected output for this step."});
  renderSteps();
  document.querySelector(`[data-step="${stepCatalog.length - 1}"] input`).focus();
  document.querySelector("#steps-save-status").textContent = "Unsaved template changes";
});

function currentWorkflow() { return workflowConfig?.workflows.find(item => item.id === selectedWorkflowId); }
function templateFor(id) { return workflowConfig.steps.find(step => step.id === id); }
function phaseTemplates(kind) { return workflowConfig.steps.filter(step => step.kind === kind); }
const workflowKinds = ["plan", "execute", "validate", "select", "merge", "review"];
let draggedTemplateId = null;
let draggedBindingIndex = null;

function templateRouteLabel(step) {
  if (step.engine === "deterministic") return "Deterministic";
  if (step.engine === "codex") return "Codex";
  if (step.engine === "9router/auto") return "9router / rotate";
  return step.engine.replace("9router/", "9router / ");
}

function captureWorkflowEditor() {
  const workflow = currentWorkflow();
  const editor = document.querySelector("#workflow-editor");
  if (!workflow || !editor.querySelector("[data-workflow-name]")) return;
  workflow.name = editor.querySelector("[data-workflow-name]").value.trim();
  editor.querySelectorAll("[data-binding]").forEach((row, index) => {
    const binding = workflow.steps[index];
    binding.step_id = row.querySelector("[data-template]").value;
    ["name", "engine", "system_prompt"].forEach(field => {
      const enabled = row.querySelector(`[data-override="${field}"]`).checked;
      binding[field] = enabled ? row.querySelector(`[data-override-field="${field}"]`).value.trim() : null;
    });
  });
}

function renderWorkflowSelector() {
  const select = document.querySelector("#workflow-selector");
  select.innerHTML = `<option value="">Choose a workflow</option>${workflowConfig.workflows.map(flow => `<option value="${escapeHtml(flow.id)}" ${flow.id === selectedWorkflowId ? "selected" : ""}>${escapeHtml(flow.name || "Untitled workflow")}</option>`).join("")}`;
  select.value = selectedWorkflowId || "";
  window.ThemeControls?.refreshSelect(select);
  const count = workflowConfig.workflows.length;
  document.querySelector("#workflow-count").textContent = `${count} shared ${count === 1 ? "workflow" : "workflows"}`;
  renderDispatchWorkflowSelector();
  window.Workspaces?.workflowsChanged();
}

function renderDispatchWorkflowSelector(value = document.querySelector("#dispatch-workflow").value) {
  const dispatch = document.querySelector("#dispatch-workflow");
  const workflows = savedWorkflowConfig?.workflows || [];
  const missing = value && !workflows.some(flow => flow.id === value);
  // Value assignments do not emit change events; refresh the enhanced control explicitly.
  dispatch.innerHTML = `<option value="">Without a workflow - built-in steps</option>`
    + (missing ? `<option value="${escapeHtml(value)}">${escapeHtml(value)} (unavailable)</option>` : "")
    + workflows.map(flow => `<option value="${escapeHtml(flow.id)}">${escapeHtml(flow.name)}${flow.id === savedWorkflowConfig.default_workflow_id ? " (default)" : ""}${routeIsDispatchable(flow, savedWorkflowConfig) ? "" : " (draft route)"}</option>`).join("");
  dispatch.value = value || "";
  window.ThemeControls?.refreshSelect(dispatch);
  updateDispatchWorkflowLabel();
}

function renderWorkflowEditor() {
  draggedTemplateId = null;
  draggedBindingIndex = null;
  const workflow = currentWorkflow();
  const editor = document.querySelector("#workflow-editor");
  const defaultToggle = document.querySelector("#default-workflow");
  const isolatedWorktreeToggle = document.querySelector("#workflow-isolated-worktree");
  const deleteButton = document.querySelector("#delete-workflow");
  const actions = document.querySelector("#workflow-actions");
  const saveButton = workflowsForm.querySelector('button[type="submit"]');
  if (!workflow) {
    editor.innerHTML = `<div class="workflow-empty-state"><div class="empty-route" aria-hidden="true"><span></span><i></i><i></i><i></i></div><h3>Choose where to start</h3><p>Edit an existing workflow or create a blank route.</p><button class="create-workflow-button" data-create-workflow type="button"><span aria-hidden="true">+</span> Create workflow</button></div><div class="workflow-saved-list" aria-label="Available workflows">${workflowConfig.workflows.map(flow => `<div class="workflow-saved-item"><div><b>${escapeHtml(flow.name || "Untitled workflow")}</b><span>${flow.steps.length} steps${flow.id === workflowConfig.default_workflow_id ? " / Default" : ""}</span></div><button class="secondary-button" type="button" data-edit-workflow="${escapeHtml(flow.id)}" aria-label="Edit ${escapeHtml(flow.name || "Untitled workflow")}">Edit workflow</button></div>`).join("")}</div>`;
    defaultToggle.checked = false; defaultToggle.disabled = true;
    isolatedWorktreeToggle.checked = true; isolatedWorktreeToggle.disabled = true;
    deleteButton.hidden = true; actions.hidden = true; saveButton.disabled = true;
    return;
  }
  deleteButton.hidden = false; actions.hidden = false;
  defaultToggle.disabled = false; saveButton.disabled = false;
  defaultToggle.checked = workflow.id === workflowConfig.default_workflow_id;
  isolatedWorktreeToggle.disabled = false;
  isolatedWorktreeToggle.checked = workflow.isolated_worktree !== false;
  deleteButton.disabled = workflowConfig.workflows.length === 1;
  const isSaved = savedWorkflowConfig.workflows.some(flow => flow.id === workflow.id);
  editor.innerHTML = `<div class="workflow-editing-bar"><div><h3>${isSaved ? "Edit workflow" : "New workflow"}</h3><p>${isSaved ? "Update the name, steps, and overrides. Shared templates stay unchanged." : "Name the workflow and add steps from the library."}</p></div><button class="secondary-button" type="button" data-discard-workflows>Discard all changes</button></div><div class="workflow-name-row step-field"><label for="workflow-edit-name">Workflow name</label><input id="workflow-edit-name" data-workflow-name value="${escapeHtml(workflow.name)}" required maxlength="100" placeholder="Release readiness"><span class="engine-note">ID: ${escapeHtml(workflow.id)}</span></div>
    <div class="workflow-compose">
      <aside class="template-library" aria-labelledby="template-library-title">
        <div class="template-library-heading"><span class="step-kind">Reusable building blocks</span><h3 id="template-library-title">Template library</h3><p>Drag templates into the route, or select a card to add it at the end.</p></div>
        <div class="template-library-list">${workflowConfig.steps.map(step => `<button class="workflow-template-option" type="button" draggable="true" data-template-card="${escapeHtml(step.id)}" data-kind="${step.kind}" aria-label="Add ${escapeHtml(step.name)} to the workflow"><span class="drag-grip" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i></span><span class="template-option-copy"><b>${escapeHtml(step.name)}</b><small>${step.kind} / ${escapeHtml(templateRouteLabel(step))}</small></span></button>`).join("")}</div>
        <p id="workflow-dnd-status" class="field-note dnd-status" role="status"></p>
      </aside>
      <div class="workflow-route"><div class="workflow-route-heading"><span class="step-kind">Execution route</span><h3>Workflow steps</h3><p>Add templates, then drag the step handles to arrange the route.</p><p class="route-order-note">${routeIsDispatchable(workflow) ? "Ready for full dispatch. The runner resolves phase dependencies." : `You can save this route as a draft. Full dispatch requires one of each phase: ${workflowKinds.join(", ")}.`}</p></div>
      <div class="workflow-bindings ${workflow.steps.length ? "" : "is-empty"}" data-workflow-route>${workflow.steps.length ? `<div class="workflow-drop-target" data-drop-index="0"><span>Drop at start</span></div>` : `<div class="workflow-route-empty" data-drop-index="0"><span class="phase-number">+</span><div><h4>Start with an empty route</h4><p>Drag a template here, or select one from the library.</p></div></div>`}${workflow.steps.map((binding, index) => {
      const template = templateFor(binding.step_id);
      if (!template) return "";
      const kind = template.kind;
      const override = (field, label, control) => `<div class="override-field"><label class="override-toggle"><input type="checkbox" data-override="${field}" ${binding[field] !== null ? "checked" : ""}>Override ${label}</label>${control}</div>`;
      return `<section class="workflow-binding" data-binding="${index}" data-kind="${kind}"><div class="binding-head"><span class="phase-number">${String(index + 1).padStart(2, "0")}</span><div class="binding-title"><span class="step-kind">${kind}</span><h3>${escapeHtml(template.name)}</h3></div><div class="binding-order-controls"><button class="binding-drag-handle" type="button" draggable="true" data-binding-drag="${index}" aria-label="Drag ${escapeHtml(template.name)} to reorder"><span class="drag-grip" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i></span></button><button type="button" data-move-binding="-1" aria-label="Move ${escapeHtml(template.name)} up" ${index === 0 ? "disabled" : ""}>↑</button><button type="button" data-move-binding="1" aria-label="Move ${escapeHtml(template.name)} down" ${index === workflow.steps.length - 1 ? "disabled" : ""}>↓</button><button class="remove-binding" type="button" data-remove-binding aria-label="Remove ${escapeHtml(template.name)}">Remove</button></div></div>
        <p class="drop-hint">Drag the handle to change this step's position.</p>
        <div class="step-field"><label for="workflow-template-${index}">Template</label><select id="workflow-template-${index}" data-template required>${phaseTemplates(kind).map(step => `<option value="${escapeHtml(step.id)}" ${step.id === binding.step_id ? "selected" : ""}>${escapeHtml(step.name)}</option>`).join("")}</select></div>
        <details class="binding-settings" ${[binding.name, binding.engine, binding.system_prompt].some(value => value != null) ? "open" : ""}><summary>Customize this step</summary><div class="override-grid">
          ${override("name", "name", `<input aria-label="Name override for step ${index + 1}" data-override-field="name" value="${escapeHtml(binding.name ?? template.name)}" maxlength="80">`)}
          ${override("engine", "tool / model", `<select aria-label="Tool / model override for step ${index + 1}" data-override-field="engine">${engineOptions({kind, engine: binding.engine || template.engine})}</select>`)}
          ${override("system_prompt", "system prompt", `<textarea aria-label="System prompt override for step ${index + 1}" data-override-field="system_prompt" maxlength="12000">${escapeHtml(binding.system_prompt ?? template.system_prompt)}</textarea>`)}
        </div></details></section><div class="workflow-drop-target" data-drop-index="${index + 1}"><span>Drop here</span></div>`;
    }).join("")}</div></div></div>`;
  document.querySelectorAll("[data-override-field]").forEach(field => { field.disabled = !field.closest(".override-field").querySelector("[data-override]").checked; });
}

function setWorkflowDragStatus(message) {
  const status = document.querySelector("#workflow-dnd-status");
  if (status && status.textContent !== message) status.textContent = message;
  document.querySelector("#workflow-dnd-announcement").textContent = message;
}

function addTemplateToWorkflow(templateId, insertIndex, focus = false) {
  captureWorkflowEditor();
  const workflow = currentWorkflow();
  const template = templateFor(templateId);
  if (!workflow || !template) return false;
  const targetIndex = Math.max(0, Math.min(insertIndex ?? workflow.steps.length, workflow.steps.length));
  workflow.steps.splice(targetIndex, 0, {step_id: template.id, name: null, engine: null, system_prompt: null});
  renderWorkflowEditor();
  setWorkflowDragStatus(`${template.name} added as step ${targetIndex + 1}.`);
  document.querySelector("#workflows-status").textContent = "Unsaved workflow changes";
  if (focus) document.querySelector(`#workflow-template-${targetIndex}`).focus();
  return true;
}

function moveWorkflowStep(fromIndex, toIndex, focus = false) {
  captureWorkflowEditor();
  const workflow = currentWorkflow();
  if (!workflow || !Number.isInteger(fromIndex) || !Number.isInteger(toIndex) || fromIndex < 0 || fromIndex >= workflow.steps.length) return false;
  const [binding] = workflow.steps.splice(fromIndex, 1);
  const adjustedIndex = Math.max(0, Math.min(toIndex > fromIndex ? toIndex - 1 : toIndex, workflow.steps.length));
  workflow.steps.splice(adjustedIndex, 0, binding);
  const template = templateFor(binding.step_id);
  renderWorkflowEditor();
  setWorkflowDragStatus(`${template?.name || "Step"} moved to position ${adjustedIndex + 1}.`);
  document.querySelector("#workflows-status").textContent = "Unsaved workflow changes";
  if (focus) document.querySelector(`[data-binding-drag="${adjustedIndex}"]`)?.focus();
  return true;
}

function removeWorkflowStep(index) {
  captureWorkflowEditor();
  const workflow = currentWorkflow();
  if (!workflow?.steps[index]) return false;
  const [binding] = workflow.steps.splice(index, 1);
  const template = templateFor(binding.step_id);
  renderWorkflowEditor();
  setWorkflowDragStatus(`${template?.name || "Step"} removed from the route.`);
  document.querySelector("#workflows-status").textContent = "Unsaved workflow changes";
  const nextHandle = document.querySelector(`[data-binding-drag="${Math.min(index, workflow.steps.length - 1)}"]`);
  (nextHandle || document.querySelector("[data-template-card]") || document.querySelector("#workflow-edit-name"))?.focus();
  return true;
}

function routeIsDispatchable(workflow, config = workflowConfig) {
  const kinds = workflow.steps.map(binding => config.steps.find(step => step.id === binding.step_id)?.kind);
  return kinds.length === workflowKinds.length
    && workflowKinds.every(kind => kinds.filter(candidate => candidate === kind).length === 1);
}

function editWorkflow(id) {
  captureWorkflowEditor();
  if (!workflowConfig.workflows.some(flow => flow.id === id)) return;
  selectedWorkflowId = id;
  renderWorkflowSelector(); renderWorkflowEditor();
  workflowsError.textContent = "";
  document.querySelector("#workflow-edit-name").focus();
}

async function discardWorkflowChanges() {
  captureWorkflowEditor();
  if (JSON.stringify(workflowConfig) !== JSON.stringify(savedWorkflowConfig) && !await ThemeControls.confirm({
    title: "Discard workflow changes?",
    message: "All unsaved changes to this workflow will be lost.",
    confirmLabel: "Discard changes",
  })) return;
  workflowConfig = structuredClone(savedWorkflowConfig);
  if (!currentWorkflow()) selectedWorkflowId = null;
  renderWorkflowSelector(); renderWorkflowEditor();
  workflowsError.textContent = "";
  document.querySelector("#workflows-status").textContent = "Unsaved workflow changes discarded.";
  document.querySelector(currentWorkflow() ? "#workflow-edit-name" : "#workflow-selector").focus();
}

function clearWorkflowDrag() {
  document.querySelectorAll(".dragging, .drop-ready").forEach(item => item.classList.remove("dragging", "drop-ready"));
  draggedTemplateId = null;
  draggedBindingIndex = null;
}

function workflowDropTarget(event) {
  return event.target.closest("[data-drop-index]");
}

function markWorkflowChanged() {
  document.querySelector("#workflows-status").textContent = "Unsaved workflow changes";
}

function updateDispatchWorkflowLabel() {
  const id = document.querySelector("#dispatch-workflow").value;
  const workflow = savedWorkflowConfig?.workflows.find(item => item.id === id);
  document.querySelector("#recipe-name").textContent = workflow?.name || "Built-in engineering steps";
  document.querySelector("#dispatch-workflow-help").textContent = workflow
    ? `This shared workflow runs in the selected workspace using its saved steps and overrides. Integration runs ${workflow.isolated_worktree === false ? "on the selected checkout's current branch" : "in an isolated worktree"}. A snapshot is kept with the run.`
    : "No saved workflow is used. The built-in engineering steps run without workflow or template overrides; normal execution checks still apply.";
}

async function loadWorkflowConfiguration() {
  try {
    const response = await fetch("/api/workflows");
    if (!response.ok) throw new Error("Unable to load workflows.");
    workflowConfig = await response.json(); savedWorkflowConfig = structuredClone(workflowConfig); stepCatalog = structuredClone(workflowConfig.steps);
    selectedWorkflowId = null;
    renderSteps(); renderWorkflowSelector(); renderWorkflowEditor();
  } catch (error) { stepsError.textContent = error.message; workflowsError.textContent = error.message; }
}

document.querySelector("#workflow-selector").addEventListener("change", event => { captureWorkflowEditor(); selectedWorkflowId = event.target.value; renderWorkflowEditor(); });
document.querySelector("#dispatch-workflow").addEventListener("change", updateDispatchWorkflowLabel);
document.querySelector("#workflow-editor").addEventListener("change", event => {
  if (event.target.matches("[data-override]")) {
    event.target.closest(".override-field").querySelector("[data-override-field]").disabled = !event.target.checked;
  }
  if (event.target.matches("[data-template]")) { captureWorkflowEditor(); renderWorkflowEditor(); }
  markWorkflowChanged();
});
document.querySelector("#workflow-editor").addEventListener("input", markWorkflowChanged);
document.querySelector("#workflow-editor").addEventListener("keydown", event => {
  const handle = event.target.closest("[data-binding-drag]");
  if (!handle || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
  event.preventDefault();
  const index = Number(handle.dataset.bindingDrag);
  moveWorkflowStep(index, event.key === "ArrowUp" ? index - 1 : index + 2, true);
});
document.querySelector("#workflow-editor").addEventListener("click", event => {
  const edit = event.target.closest("[data-edit-workflow]");
  if (edit) { editWorkflow(edit.dataset.editWorkflow); return; }
  if (event.target.closest("[data-discard-workflows]")) { discardWorkflowChanges(); return; }
  const card = event.target.closest("[data-template-card]");
  if (card) { addTemplateToWorkflow(card.dataset.templateCard, currentWorkflow()?.steps.length, true); return; }
  const remove = event.target.closest("[data-remove-binding]");
  if (remove) { removeWorkflowStep(Number(remove.closest("[data-binding]").dataset.binding)); return; }
  const move = event.target.closest("[data-move-binding]");
  if (!move) return;
  const index = Number(move.closest("[data-binding]").dataset.binding);
  const direction = Number(move.dataset.moveBinding);
  moveWorkflowStep(index, direction < 0 ? index - 1 : index + 2, true);
});
document.querySelector("#workflow-editor").addEventListener("dragstart", event => {
  const handle = event.target.closest("[data-binding-drag]");
  if (handle) {
    draggedBindingIndex = Number(handle.dataset.bindingDrag);
    draggedTemplateId = null;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", `workflow-step:${draggedBindingIndex}`);
    handle.closest("[data-binding]").classList.add("dragging");
    setWorkflowDragStatus("Drag to a gap in the route to move this step.");
    return;
  }
  const card = event.target.closest("[data-template-card]");
  if (!card) return;
  draggedTemplateId = card.dataset.templateCard;
  draggedBindingIndex = null;
  event.dataTransfer.effectAllowed = "copy";
  event.dataTransfer.setData("text/plain", draggedTemplateId);
  card.classList.add("dragging");
  setWorkflowDragStatus(`Drag ${card.textContent.trim()} to any gap in the route.`);
});
document.querySelector("#workflow-editor").addEventListener("dragover", event => {
  const target = workflowDropTarget(event);
  if (!target || (!draggedTemplateId && draggedBindingIndex === null)) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = draggedBindingIndex === null ? "copy" : "move";
  document.querySelectorAll(".drop-ready").forEach(item => item.classList.remove("drop-ready"));
  target.classList.add("drop-ready");
  setWorkflowDragStatus(`Release at position ${Number(target.dataset.dropIndex) + 1}.`);
});
document.querySelector("#workflow-editor").addEventListener("dragleave", event => {
  const target = workflowDropTarget(event);
  if (target && !target.contains(event.relatedTarget)) target.classList.remove("drop-ready");
});
document.querySelector("#workflow-editor").addEventListener("drop", event => {
  const target = workflowDropTarget(event);
  if (!target || (!draggedTemplateId && draggedBindingIndex === null)) return;
  event.preventDefault();
  const dropIndex = Number(target.dataset.dropIndex);
  const templateId = draggedTemplateId;
  const bindingIndex = draggedBindingIndex;
  clearWorkflowDrag();
  if (bindingIndex !== null) moveWorkflowStep(bindingIndex, dropIndex);
  else addTemplateToWorkflow(templateId, dropIndex);
});
document.querySelector("#workflow-editor").addEventListener("dragend", () => {
  if (draggedTemplateId || draggedBindingIndex !== null) setWorkflowDragStatus("Drag cancelled. The route was not changed.");
  clearWorkflowDrag();
});

function createWorkflow() {
  captureWorkflowEditor();
  const root = `workflow-${Date.now().toString(36)}`;
  let id = root; let suffix = 2; while (workflowConfig.workflows.some(flow => flow.id === id)) id = `${root}-${suffix++}`;
  const steps = [];
  workflowConfig.workflows.push({id, name: "", isolated_worktree: true, steps}); selectedWorkflowId = id;
  renderWorkflowSelector(); renderWorkflowEditor();
  document.querySelector("#workflow-edit-name").focus();
  document.querySelector("#workflows-status").textContent = "Unsaved empty workflow";
}

document.querySelector("#workflows-form").addEventListener("click", event => {
  if (event.target.closest("[data-create-workflow]")) createWorkflow();
});

document.querySelector("#delete-workflow").addEventListener("click", async () => {
  const workflow = currentWorkflow();
  if (!workflow || workflowConfig.workflows.length === 1 || !await ThemeControls.confirm({
    title: "Delete workflow?",
    message: `${workflow.name || "This untitled workflow"} will be removed from the shared library when you save. Workspaces using it as their default will need to choose another workflow. Existing runs are kept.`,
    confirmLabel: "Delete workflow",
  })) return;
  const deleted = selectedWorkflowId; workflowConfig.workflows = workflowConfig.workflows.filter(flow => flow.id !== deleted);
  if (workflowConfig.default_workflow_id === deleted) workflowConfig.default_workflow_id = workflowConfig.workflows[0].id;
  selectedWorkflowId = workflowConfig.workflows[0].id; renderWorkflowSelector(); renderWorkflowEditor();
  document.querySelector("#workflows-status").textContent = "Unsaved workflow deletion";
});

document.querySelector("#default-workflow").addEventListener("change", event => {
  if (!selectedWorkflowId) { event.target.checked = false; return; }
  if (event.target.checked) workflowConfig.default_workflow_id = selectedWorkflowId;
  else event.target.checked = true;
  renderWorkflowSelector(); document.querySelector("#workflows-status").textContent = "Unsaved default workflow change";
});

document.querySelector("#workflow-isolated-worktree").addEventListener("change", event => {
  const workflow = currentWorkflow();
  if (!workflow) { event.target.checked = true; return; }
  workflow.isolated_worktree = event.target.checked;
  document.querySelector("#workflows-status").textContent = "Unsaved worktree setting change";
});

workflowsForm.addEventListener("submit", async event => {
  event.preventDefault(); captureWorkflowEditor(); workflowsError.textContent = "";
  if (!currentWorkflow()) return;
  const incomplete = workflowConfig.workflows.find(flow => !flow.name.trim() || flow.steps.some(binding => !binding.step_id));
  if (incomplete) {
    selectedWorkflowId = incomplete.id;
    renderWorkflowSelector(); renderWorkflowEditor();
    workflowsError.textContent = "Enter a name for every workflow before saving. Empty routes can be saved as drafts.";
    ThemeControls.validateForm(workflowsForm);
    return;
  }
  const button = workflowsForm.querySelector('button[type="submit"]'); button.disabled = true;
  try {
    const payload = {workflows: workflowConfig.workflows, default_workflow_id: workflowConfig.default_workflow_id};
    const response = await fetch("/api/workflows", {method: "PUT", headers: {"Content-Type": "application/json", "X-Switchyard-Client": "local-ui"}, body: JSON.stringify(payload)});
    const body = await response.json();
    if (!response.ok) throw new Error(Array.isArray(body.detail) ? body.detail.map(item => item.msg).join("; ") : body.detail || "Unable to save workflows.");
    workflowConfig = body; savedWorkflowConfig = structuredClone(body); renderWorkflowSelector(); renderWorkflowEditor();
    document.querySelector("#workflows-status").textContent = "Workflows saved to the shared library. Available in every workspace; existing runs keep their snapshots.";
  } catch (error) { workflowsError.textContent = error.message; } finally { button.disabled = false; }
});

loadWorkflowConfiguration();

const views = {
  overview: ["Overview", "Dispatch work, configure routes, and inspect results."],
  workspaces: ["Workspaces", "Save repository paths and defaults for future runs."],
  dispatch: ["Dispatch", "Start a trusted workflow."],
  chat: ["Chat", "Ask a selected AI tool about the active workspace."],
  commands: ["CMD", "Run commands and return later to check their output."],
  git: ["Git", "Review changes, create commits, manage branches, and sync repository remotes."],
  worktrees: ["Worktrees", "Inspect, protect, and clean up isolated workflow checkouts."],
  steps: ["Steps", "Configure shared building blocks for workflows in any workspace."],
  workflows: ["Workflows", "Build shared pipelines that any workspace can use."],
  mcps: ["MCPs", "Connect external tools once and reuse them across workspaces."],
  clis: ["CLIs", "Configure global command profiles and runtime access shared by every repository."],
  delivery: ["Delivery", "Set global push and pull-request defaults for completed workflows."],
  runs: ["Runs", "Inspect plans, execution results, and saved workflow snapshots."],
  system: ["System", "Check tool availability and stable 9router routes."]
};
function navigatePanel(focus = false) {
  const key = location.hash.slice(1) in views ? location.hash.slice(1) : "overview";
  document.querySelector("#workspace").dataset.view = key;
  const visibility = {
    "overview-panel": ["overview"],
    "workspaces-panel": ["workspaces"],
    "dispatch-panel": ["overview", "dispatch"],
    "chat-panel": ["chat"],
    "commands-panel": ["commands"],
    "git-panel": ["git"],
    "worktrees-panel": ["worktrees"],
    "system-panel": ["overview", "system"],
    "runs-panel": ["overview", "runs"],
    "steps-panel": ["steps"],
    "workflows-panel": ["workflows"],
    "mcps-panel": ["mcps"],
    "clis-panel": ["clis"],
    "delivery-panel": ["delivery"]
  };
  Object.entries(visibility).forEach(([id, visibleIn]) => {
    document.querySelector(`#${id}`).hidden = !visibleIn.includes(key);
  });
  document.querySelector("#view-title").textContent = views[key][0];
  document.querySelector("#view-description").textContent = views[key][1];
  document.querySelectorAll("[data-panel]").forEach(link => {
    if (link.dataset.panel === key) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  document.title = `${views[key][0]} | 9router Switchyard`;
  if (focus) document.querySelector(key === "chat" ? "#chat-heading" : key === "commands" ? "#commands-heading" : "#view-title").focus({preventScroll: true});
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", () => navigatePanel(true));
navigatePanel();

let mcpConfig = null;
let savedMcpConfig = null;
let mcpCatalog = [];
let mcpCatalogLimit = 12;
const mcpForm = document.querySelector("#mcp-form");
const mcpStatus = document.querySelector("#mcp-status");
const mcpError = document.querySelector("#mcp-error");

function readMcps() {
  const config = {shared: {servers: []}, codex: {servers: []}, opencode: {servers: []}};
  mcpForm.querySelectorAll("[data-mcp-tool]").forEach(section => {
    section.querySelectorAll("[data-mcp-server]").forEach(row => {
      const value = key => row.querySelector(`[data-mcp-field="${key}"]`).value.trim();
      const command = value("command");
      let parsed;
      try { parsed = command ? JSON.parse(command) : []; }
      catch { throwMcpFieldError(row, "command", "Enter the command as a JSON array, for example [\"npx\", \"-y\", \"package\"]."); }
      if (!Array.isArray(parsed) || parsed.some(item => typeof item !== "string" || !item.trim())) {
        throwMcpFieldError(row, "command", "Command must be a JSON array of nonempty strings, e.g. [\"npx\", \"-y\", \"package\"].");
      }
      config[section.dataset.mcpTool].servers.push({
        name: value("name"), catalog_id: row.dataset.catalogId || null,
        transport: value("transport"),
        enabled: row.querySelector('[data-mcp-field="enabled"]').checked,
        url: value("url"), command: parsed,
        env_vars: value("env_vars").split(",").map(v => v.trim()).filter(Boolean),
        bearer_token_env_var: value("bearer_token_env_var") || null
      });
    });
  });
  return config;
}

function throwMcpFieldError(row, field, message) {
  const disclosure = row.closest("details");
  if (disclosure) disclosure.open = true;
  const control = row.querySelector(`[data-mcp-field="${field}"]`);
  control.setAttribute("aria-invalid", "true");
  control.setAttribute("aria-describedby", "mcp-error");
  control.focus();
  throw new Error(message);
}

function validateMcpConnections() {
  mcpForm.querySelectorAll("[data-mcp-tool]").forEach(section => {
    const names = new Set();
    section.querySelectorAll("[data-mcp-server]").forEach(row => {
      const value = field => row.querySelector(`[data-mcp-field="${field}"]`).value.trim();
      const name = value("name");
      if (names.has(name)) throwMcpFieldError(row, "name", `Use a unique server name in this section; ${name} is already used.`);
      names.add(name);
      if (value("transport") === "http" && !/^https?:\/\//.test(value("url"))) {
        throwMcpFieldError(row, "url", `Enter an http:// or https:// server URL for ${name}.`);
      }
      if (value("transport") === "stdio" && !JSON.parse(value("command") || "[]").length) {
        throwMcpFieldError(row, "command", `Enter the official launch command for ${name} before saving.`);
      }
    });
  });
}

function renderMcps(expandTool = null) {
  const overrideCount = count => `${count} override${count === 1 ? "" : "s"}`;
  const sections = {
    shared: "Shared servers",
    codex: "Codex overrides",
    opencode: "OpenCode / 9router overrides",
  };
  document.querySelector("#mcp-tools").innerHTML = Object.entries(sections).map(([tool, title]) => {
    const shared = tool === "shared";
    const previous = mcpForm.querySelector(`details[data-mcp-tool="${tool}"]`);
    const open = expandTool === tool || (previous ? previous.open : mcpConfig[tool].servers.length > 0);
    return `
    <${shared ? 'section class="mcp-tool"' : `details class="mcp-tool mcp-overrides" ${open ? "open" : ""}`} data-mcp-tool="${tool}">
      ${shared ? `<div class="mcp-tool-heading"><h3>${title}</h3><span>Codex + OpenCode</span></div>` : `<summary><h3>${title}</h3><span data-mcp-count>${overrideCount(mcpConfig[tool].servers.length)}</span></summary>`}
      <div class="mcp-tool-body">
      ${mcpConfig[tool].servers.map((server, index) => {
        const id = `mcp-${tool}-${index}`;
        const field = (key, label, value, extra = "") => `<div class="step-field"><label for="${id}-${key}">${label}</label><input id="${id}-${key}" data-mcp-field="${key}" value="${escapeHtml(value)}" ${extra}></div>`;
        const catalog = mcpCatalog.find(item => item.id === server.catalog_id);
        return `<div class="mcp-server" data-mcp-server="${index}" data-catalog-id="${escapeHtml(server.catalog_id || "")}" data-new-mcp="${server._new ? "true" : "false"}">
          ${server.catalog_id ? `<p class="mcp-catalog-source">Catalog server: ${escapeHtml(catalog?.name || server.catalog_id)}. Confirm connection details with its official source.</p>` : ""}
          ${field("name", "Server name", server.name, 'required pattern="[A-Za-z0-9_-]+" maxlength="64"')}
          <div class="step-field"><label for="${id}-transport">Transport</label><select id="${id}-transport" data-mcp-field="transport"><option value="http" ${server.transport === "http" ? "selected" : ""}>Remote HTTP</option><option value="stdio" ${server.transport === "stdio" ? "selected" : ""}>Local command (stdio)</option></select></div>
          ${field("url", "HTTP server URL", server.url)}
          ${field("bearer_token_env_var", "Bearer token environment variable (not its value)", server.bearer_token_env_var || "")}
          ${field("command", "Command and arguments (JSON array)", JSON.stringify(server.command))}
          ${field("env_vars", "Forward environment variables (comma-separated names)", server.env_vars.join(", "))}
          <label class="mcp-enabled"><input type="checkbox" data-mcp-field="enabled" ${server.enabled ? "checked" : ""}>Enabled</label>
          <button type="button" class="secondary-button" data-remove-mcp="${index}">${server._new ? "Cancel" : "Remove server"}</button>
        </div>`;
      }).join("")}
      <button type="button" class="secondary-button" data-add-mcp="${tool}">${shared ? "Add server" : "Add override"}</button>
      </div>
    </${shared ? "section" : "details"}>`;
  }).join("");
}

function syncMcpDraft() {
  if (!mcpConfig) throw new Error("Wait for MCP settings to load before adding a server.");
  const draftFlags = {};
  mcpForm.querySelectorAll("[data-mcp-tool]").forEach(section => {
    draftFlags[section.dataset.mcpTool] = [...section.querySelectorAll("[data-mcp-server]")].map(row => row.dataset.newMcp === "true");
  });
  mcpConfig = readMcps();
  Object.entries(draftFlags).forEach(([tool, flags]) => flags.forEach((isNew, index) => { mcpConfig[tool].servers[index]._new = isNew; }));
}

function renderMcpCatalog() {
  const query = document.querySelector("#mcp-catalog-search").value.trim().toLowerCase();
  const matches = mcpCatalog.filter(item => !query || item.name.toLowerCase().includes(query));
  const visible = matches.slice(0, mcpCatalogLimit);
  const result = document.querySelector("#mcp-catalog-results");
  document.querySelector("#mcp-catalog-count").textContent = `Showing ${visible.length} of ${matches.length} matching servers. ${mcpCatalog.length} catalog entries total.`;
  document.querySelector("#mcp-catalog-more").hidden = visible.length >= matches.length;
  result.innerHTML = visible.length ? visible.map(item => `
    <article class="mcp-catalog-item">
      <span class="mcp-catalog-rank">#${item.rank}</span>
      <div><b>${escapeHtml(item.name)}</b><span>${Number(item.weekly_installs).toLocaleString()} installs/week</span></div>
      <button class="secondary-button" type="button" data-catalog-mcp="${item.id}" aria-label="Add shared draft for ${escapeHtml(item.name)}">Add shared draft</button>
    </article>`).join("") : `<p class="mcp-empty">No catalog servers match “${escapeHtml(query)}”. You can still add a custom server below.</p>`;
}

mcpForm.addEventListener("invalid", event => {
  const disclosure = event.target.closest("details");
  if (disclosure) disclosure.open = true;
}, true);
mcpForm.addEventListener("input", event => {
  event.target.removeAttribute("aria-invalid");
  event.target.removeAttribute("aria-describedby");
  mcpStatus.textContent = "Unsaved MCP changes";
  document.querySelector("#mcp-export-result").hidden = true;
});
mcpForm.addEventListener("click", event => {
  const add = event.target.closest("[data-add-mcp]");
  const remove = event.target.closest("[data-remove-mcp]");
  if (!add && !remove) return;
  if (remove) {
    const section = remove.closest("[data-mcp-tool]");
    remove.closest("[data-mcp-server]").remove();
    const count = section.querySelector("[data-mcp-count]");
    if (count) {
      const remaining = section.querySelectorAll("[data-mcp-server]").length;
      count.textContent = `${remaining} override${remaining === 1 ? "" : "s"}`;
    }
    section.querySelector("[data-add-mcp]").focus();
    mcpStatus.textContent = "Unsaved MCP changes";
    mcpError.textContent = "";
    return;
  }
  try {
    syncMcpDraft();
    mcpConfig[add.dataset.addMcp].servers.push({name: "", catalog_id: null, transport: "http", enabled: true, url: "", command: [], env_vars: [], bearer_token_env_var: null, _new: true});
    renderMcps(add.dataset.addMcp);
    document.querySelector(`[data-mcp-tool="${add.dataset.addMcp}"] [data-mcp-server]:last-of-type input`).focus();
    mcpStatus.textContent = "Unsaved MCP changes";
    mcpError.textContent = "";
  } catch (error) { mcpError.textContent = error.message; }
});

document.querySelector("#mcp-catalog-search").addEventListener("input", () => {
  mcpCatalogLimit = 12;
  renderMcpCatalog();
});
document.querySelector("#mcp-catalog-more").addEventListener("click", () => {
  const previousCount = document.querySelectorAll("[data-catalog-mcp]").length;
  mcpCatalogLimit += 12;
  renderMcpCatalog();
  document.querySelectorAll("[data-catalog-mcp]")[previousCount]?.focus();
});
document.querySelector("#mcp-catalog-results").addEventListener("click", event => {
  const button = event.target.closest("[data-catalog-mcp]");
  if (!button) return;
  try {
    syncMcpDraft();
    const item = mcpCatalog.find(candidate => candidate.id === button.dataset.catalogMcp);
    if (!item) throw new Error("This catalog entry is no longer available. Reload and try again.");
    if (mcpConfig.shared.servers.some(server => server.catalog_id === item.id || server.name === item.id)) {
      throw new Error(`${item.name} is already in the shared server list. Edit that definition below.`);
    }
    mcpConfig.shared.servers.push({name: item.id, catalog_id: item.id, transport: "stdio", enabled: true, url: "", command: [], env_vars: [], bearer_token_env_var: null, _new: true});
    renderMcps();
    mcpStatus.textContent = `Added ${item.name}. Enter its official connection details, then save.`;
    mcpError.textContent = "";
    document.querySelector('[data-mcp-tool="shared"] [data-mcp-server]:last-of-type input').focus();
  } catch (error) { mcpError.textContent = error.message; }
});

mcpForm.addEventListener("submit", async event => {
  event.preventDefault();
  const button = document.querySelector("#save-mcps");
  button.disabled = true;
  mcpError.textContent = "";
  try {
    const config = readMcps();
    validateMcpConnections();
    const response = await fetch("/api/mcps", {method: "PUT", headers: {"Content-Type": "application/json", "X-Switchyard-Client": "local-ui"}, body: JSON.stringify(config)});
    const body = await response.json();
    if (!response.ok) throw new Error(Array.isArray(body.detail) ? body.detail.map(item => item.msg).join("; ") : body.detail || "Unable to save MCP settings.");
    mcpConfig = body;
    savedMcpConfig = structuredClone(body);
    renderMcps();
    mcpStatus.textContent = "Global MCP settings saved for future runs across every repository. Connectivity and OAuth have not been tested.";
    document.querySelector("#mcp-export-result").hidden = true;
  } catch (error) { mcpError.textContent = error.message; }
  finally { button.disabled = false; }
});

async function loadMcps() {
  try {
    const configResponse = await fetch("/api/mcps");
    if (!configResponse.ok) throw new Error("Unable to load MCP settings. Reload to retry.");
    mcpConfig = await configResponse.json();
    savedMcpConfig = structuredClone(mcpConfig);
    renderMcps();
    document.querySelector("#save-mcps").disabled = false;
    mcpStatus.textContent = "Settings loaded. Secrets should be supplied through the server process environment.";
  } catch (error) { mcpError.textContent = error.message; mcpStatus.textContent = ""; }
}
loadMcps();

async function loadMcpCatalog() {
  try {
    const response = await fetch("/api/mcps/catalog");
    if (!response.ok) throw new Error("Unable to load the MCP catalog. Reload to retry, or add a custom server below.");
    mcpCatalog = (await response.json()).servers;
    renderMcpCatalog();
  } catch (error) { document.querySelector("#mcp-catalog-count").textContent = error.message; }
}
loadMcpCatalog();

document.querySelector("#cancel-mcps").addEventListener("click", () => {
  if (!savedMcpConfig) return;
  mcpConfig = structuredClone(savedMcpConfig);
  renderMcps(); mcpError.textContent = "";
  mcpStatus.textContent = "Unsaved MCP changes cancelled. Saved settings are unchanged.";
});

document.querySelector("#export-mcps").addEventListener("click", async () => {
  const button = document.querySelector("#export-mcps");
  const tool = document.querySelector("#mcp-export-tool").value;
  button.disabled = true;
  mcpError.textContent = "";
  try {
    if (!savedMcpConfig || JSON.stringify(readMcps()) !== JSON.stringify(savedMcpConfig)) {
      throw new Error("Save or cancel MCP changes before generating an export of saved settings.");
    }
    const response = await fetch(`/api/mcps/export/${encodeURIComponent(tool)}`);
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Unable to generate the MCP export.");
    document.querySelector("#mcp-export-destination").textContent = `Merge into ${body.destination}`;
    document.querySelector("#mcp-export-output").textContent = body.content || "No enabled shared servers to export.";
    document.querySelector("#mcp-export-warning").textContent = body.warnings.join(" ");
    document.querySelector("#mcp-export-result").hidden = false;
  } catch (error) { mcpError.textContent = error.message; }
  finally { button.disabled = false; }
});

let cliConfig = null;
const cliForm = document.querySelector("#cli-form");
const cliStatus = document.querySelector("#cli-status");
const cliError = document.querySelector("#cli-error");

function parseCommand(value) {
  const command = JSON.parse(value);
  if (!Array.isArray(command) || !command.length || command.some(part => typeof part !== "string" || !part.trim())) throw new Error("Each CLI command must be a nonempty JSON array of strings.");
  return command;
}

function readClis() {
  return {
    codex: {
      command: parseCommand(document.querySelector("#codex-command").value),
      provider: document.querySelector("#codex-provider").value.trim(),
      model: document.querySelector("#codex-model").value.trim(),
      base_url: document.querySelector("#codex-base-url").value.trim(),
      api_key_env: document.querySelector("#codex-api-key-env").value.trim()
    },
    opencode: {
      command: parseCommand(document.querySelector("#opencode-command").value),
      provider: document.querySelector("#opencode-provider").value.trim(),
      model: document.querySelector("#opencode-model").value.trim(),
      base_url: document.querySelector("#opencode-base-url").value.trim(),
      api_key_env: document.querySelector("#opencode-api-key-env").value.trim()
    },
    tools: [...document.querySelectorAll("[data-managed-cli]")].map(row => {
      const existing = cliConfig.tools.find(tool => tool.id === row.dataset.managedCli);
      const enabledControl = row.querySelector('[data-cli-field="enabled"]');
      return {
        ...existing,
        command: parseCommand(row.querySelector('[data-cli-field="command"]').value),
        provider: row.querySelector('[data-cli-field="provider"]').value.trim(),
        model: row.querySelector('[data-cli-field="model"]').value.trim(),
        base_url: row.querySelector('[data-cli-field="base_url"]').value.trim(),
        api_key_env: row.querySelector('[data-cli-field="api_key_env"]').value.trim(),
        instructions: row.querySelector('[data-cli-field="instructions"]').value.trim(),
        enabled: Boolean(enabledControl?.checked)
      };
    }),
    path_entries: document.querySelector("#cli-paths").value.split("\n").map(value => value.trim()).filter(Boolean),
    environment_vars: document.querySelector("#cli-env").value.split(",").map(value => value.trim()).filter(Boolean),
    additional: [...document.querySelectorAll("[data-additional-cli]")].map(row => ({
      name: row.querySelector('[data-cli-field="name"]').value.trim(),
      command: parseCommand(row.querySelector('[data-cli-field="command"]').value),
      provider: row.querySelector('[data-cli-field="provider"]').value.trim(),
      model: row.querySelector('[data-cli-field="model"]').value.trim(),
      base_url: row.querySelector('[data-cli-field="base_url"]').value.trim(),
      api_key_env: row.querySelector('[data-cli-field="api_key_env"]').value.trim(),
      description: row.querySelector('[data-cli-field="description"]').value.trim(),
      instructions: row.querySelector('[data-cli-field="instructions"]').value.trim(),
      enabled: row.querySelector('[data-cli-field="enabled"]').checked
    }))
  };
}

function cliDetection(status) {
  if (!status) return {className: "unchecked", label: "Not checked", detail: "Select Check availability to scan this machine."};
  if (!status.available) return {className: "missing", label: "Not detected", detail: "No matching command or application path was found."};
  const source = status.detected_by === "path" ? "application path" : `command ${status.matched || ""}`.trim();
  const commandNote = status.command_available ? "" : " The configured CLI command is unavailable. Installation files can remain after uninstalling.";
  return {className: "available", label: status.command_available ? "Detected" : "Files found", detail: `Detected by ${source}: ${status.resolved}.${commandNote}`};
}

function cliCategoryLabel(category) {
  return ({
    cli: "CLI",
    ide: "IDE",
    desktop: "Desktop app",
    runtime: "Local runtime",
    creative: "Creative tool",
    mitm: "Integration"
  })[category] || "Tool";
}

function cliPlatformLabels(platforms = {}) {
  const names = {linux: "Linux", windows: "Windows", macos: "macOS"};
  const qualifiers = {
    supported: "",
    preview: " preview",
    beta: " beta",
    "wsl-docker": " WSL/Docker",
    limited: " limited"
  };
  return ["linux", "windows", "macos"].map(platform => ({
    platform,
    label: `${names[platform]}${qualifiers[platforms[platform] || "supported"] || ""}`
  }));
}

function renderClis() {
  const opened = new Set([...document.querySelectorAll("[data-managed-cli][open]")].map(row => row.dataset.managedCli));
  document.querySelector("#codex-command").value = JSON.stringify(cliConfig.codex.command);
  document.querySelector("#codex-provider").value = cliConfig.codex.provider || "";
  document.querySelector("#codex-model").value = cliConfig.codex.model || "";
  document.querySelector("#codex-base-url").value = cliConfig.codex.base_url || "";
  document.querySelector("#codex-api-key-env").value = cliConfig.codex.api_key_env || "";
  document.querySelector("#opencode-command").value = JSON.stringify(cliConfig.opencode.command);
  document.querySelector("#opencode-provider").value = cliConfig.opencode.provider || "";
  document.querySelector("#opencode-model").value = cliConfig.opencode.model || "";
  document.querySelector("#opencode-base-url").value = cliConfig.opencode.base_url || "";
  document.querySelector("#opencode-api-key-env").value = cliConfig.opencode.api_key_env || "";
  document.querySelector("#cli-paths").value = cliConfig.path_entries.join("\n");
  document.querySelector("#cli-env").value = cliConfig.environment_vars.join(", ");
  const managedTools = cliConfig.tools || [];
  const detectedCount = managedTools.filter(tool => cliConfig.statuses?.[`tool:${tool.id}`]?.available).length;
  document.querySelector("#managed-cli-count").textContent = `${detectedCount} of ${managedTools.length} detected`;
  document.querySelector("#managed-cli-list").innerHTML = managedTools.map((profile, index) => {
    const id = `managed-cli-${index}`;
    const detection = cliDetection(cliConfig.statuses?.[`tool:${profile.id}`] || cliConfig.statuses?.[profile.id]);
    const mark = profile.name.split(/\s+/).map(part => part[0]).join("").slice(0, 2).toUpperCase();
    const platformBadges = cliPlatformLabels(profile.platforms).map(({platform, label}) =>
      `<span class="cli-profile-badge platform-${escapeHtml(platform)}">${escapeHtml(label)}</span>`
    ).join("");
    const agentAccess = `<label class="cli-enable"><input type="checkbox" data-cli-field="enabled" ${profile.enabled ? "checked" : ""}>Share this command with agents</label>
      <p class="cli-access-note">Enable only after configuring a usable CLI command. This does not add desktop control or a workflow runtime.</p>`;
    return `<details class="managed-cli-card" data-managed-cli="${escapeHtml(profile.id)}" ${opened.has(profile.id) ? "open" : ""}>
      <summary>
        <span class="managed-cli-mark" aria-hidden="true">${escapeHtml(mark)}</span>
        <span class="managed-cli-title">
          <b>${escapeHtml(profile.name)}</b>
          <small>${escapeHtml(profile.description || "AI tool")}</small>
          <span class="managed-cli-meta">
            <span class="cli-profile-badge category">${escapeHtml(cliCategoryLabel(profile.category))}</span>
            ${platformBadges}
          </span>
        </span>
        <span class="cli-detection-badge ${detection.className}">${escapeHtml(detection.label)}</span>
        <span class="managed-cli-chevron" aria-hidden="true"></span>
      </summary>
      <div class="managed-cli-config">
        <p class="managed-cli-detection">${escapeHtml(detection.detail)}</p>
        <div class="cli-config-fields">
          <div class="step-field wide"><label for="${id}-command">Command (JSON array)</label><input id="${id}-command" data-cli-field="command" value="${escapeHtml(JSON.stringify(profile.command))}" required></div>
          <div class="step-field"><label for="${id}-provider">Provider</label><input id="${id}-provider" data-cli-field="provider" list="cli-provider-options" value="${escapeHtml(profile.provider || "")}" maxlength="120" placeholder="Tool default"></div>
          <div class="step-field"><label for="${id}-model">Model</label><input id="${id}-model" data-cli-field="model" value="${escapeHtml(profile.model || "")}" maxlength="200" placeholder="Tool default"></div>
          <div class="step-field wide"><label for="${id}-base-url">Provider endpoint</label><input id="${id}-base-url" data-cli-field="base_url" type="url" value="${escapeHtml(profile.base_url || "")}" maxlength="2000" placeholder="https://api.example.com/v1"></div>
          <div class="step-field wide"><label for="${id}-api-key-env">API key environment variable</label><input id="${id}-api-key-env" data-cli-field="api_key_env" value="${escapeHtml(profile.api_key_env || "")}" maxlength="120" pattern="[A-Za-z_][A-Za-z0-9_]*" placeholder="OPENAI_API_KEY"><span class="engine-note">Only the variable name is saved. Apply endpoint credentials in the tool's own settings.</span></div>
          <div class="step-field wide"><label for="${id}-instructions">Instructions for agents</label><textarea id="${id}-instructions" data-cli-field="instructions" maxlength="2000" placeholder="Use only when this tool directly helps the assigned task.">${escapeHtml(profile.instructions || "")}</textarea></div>
        </div>
        ${agentAccess}
        <button class="secondary-button cli-setup-button" type="button" data-cli-setup="${escapeHtml(profile.id)}">View setup instructions</button>
      </div>
    </details>`;
  }).join("");
  document.querySelector("#additional-cli-list").innerHTML = (cliConfig.additional || []).length
    ? cliConfig.additional.map((profile, index) => {
      const id = `additional-cli-${index}`;
      const status = cliConfig.statuses?.[profile.name];
      const detection = cliDetection(status);
      return `<article class="additional-cli-card" data-additional-cli="${index}">
        <div class="step-field"><label for="${id}-name">CLI name</label><input id="${id}-name" data-cli-field="name" value="${escapeHtml(profile.name)}" required pattern="[A-Za-z][A-Za-z0-9_-]*" maxlength="64"><span class="engine-note">Letters, numbers, underscores, and hyphens</span></div>
        <div class="step-field"><label for="${id}-command">Command (JSON array)</label><input id="${id}-command" data-cli-field="command" value="${escapeHtml(JSON.stringify(profile.command))}" required><span class="engine-note">${escapeHtml(detection.detail)}</span></div>
        <label class="mcp-enabled"><input type="checkbox" data-cli-field="enabled" ${profile.enabled ? "checked" : ""}>Enabled</label>
        <button class="secondary-button" type="button" data-remove-cli="${index}">Remove tool</button>
        <div class="tool-copy">
          <div class="step-field"><label for="${id}-description">What this tool does</label><input id="${id}-description" data-cli-field="description" value="${escapeHtml(profile.description || "")}" maxlength="300" placeholder="Read and update Jira issues"></div>
          <div class="step-field"><label for="${id}-instructions">Instructions for the AI</label><textarea id="${id}-instructions" data-cli-field="instructions" maxlength="2000" placeholder="Use for ticket context and status updates. Ask before transitions or destructive changes.">${escapeHtml(profile.instructions || "")}</textarea></div>
        </div>
        <div class="tool-copy">
          <div class="step-field"><label for="${id}-provider">Provider</label><input id="${id}-provider" data-cli-field="provider" list="cli-provider-options" value="${escapeHtml(profile.provider || "")}" maxlength="120" placeholder="Tool default"></div>
          <div class="step-field"><label for="${id}-model">Model</label><input id="${id}-model" data-cli-field="model" value="${escapeHtml(profile.model || "")}" maxlength="200" placeholder="Tool default"></div>
        </div>
        <div class="tool-copy">
          <div class="step-field"><label for="${id}-base-url">Provider endpoint</label><input id="${id}-base-url" data-cli-field="base_url" type="url" value="${escapeHtml(profile.base_url || "")}" maxlength="2000" placeholder="https://api.example.com/v1"></div>
          <div class="step-field"><label for="${id}-api-key-env">API key environment variable</label><input id="${id}-api-key-env" data-cli-field="api_key_env" value="${escapeHtml(profile.api_key_env || "")}" maxlength="120" pattern="[A-Za-z_][A-Za-z0-9_]*" placeholder="OPENAI_API_KEY"></div>
        </div>
      </article>`;
    }).join("")
    : '<p class="field-note">No custom tools registered. Select Add custom tool to create one.</p>';
  ["codex", "opencode"].forEach(tool => {
    const badge = document.querySelector(`#${tool}-cli-status`);
    const detection = cliDetection(cliConfig.statuses?.[tool]);
    badge.className = `cli-detection-badge ${detection.className}`;
    badge.textContent = detection.label;
    badge.title = detection.detail;
  });
}

cliForm.addEventListener("input", () => { cliStatus.textContent = "Unsaved CLI changes"; });
cliForm.addEventListener("invalid", event => {
  const card = event.target.closest("details");
  if (card) card.open = true;
}, true);
const cliSetupDialog = document.querySelector("#cli-setup-dialog");
let cliSetupTrigger = null;
cliForm.addEventListener("click", async event => {
  const button = event.target.closest("[data-cli-setup]");
  if (!button || !cliConfig) return;
  cliSetupTrigger = button;
  document.querySelector("#cli-setup-title").textContent = "Setup instructions";
  document.querySelector("#cli-setup-instructions").textContent = "Preparing instructions from the current form values...";
  document.querySelector("#cli-setup-output").hidden = true;
  document.querySelector("#cli-setup-status").textContent = "";
  cliSetupDialog.showModal();
  try {
    const config = readClis();
    const id = button.dataset.cliSetup;
    const profile = config[id] || config.tools.find(tool => tool.id === id);
    const {command, provider, model, base_url, api_key_env} = profile;
    const response = await fetch(`/api/clis/setup/${encodeURIComponent(id)}`, {
      method: "POST", headers: {"Content-Type": "application/json", "X-Switchyard-Client": "local-ui"},
      body: JSON.stringify({command, provider, model, base_url, api_key_env})
    });
    const body = await response.json();
    if (!response.ok) throw new Error(Array.isArray(body.detail) ? body.detail.map(item => item.msg).join("; ") : body.detail || "Unable to prepare setup instructions.");
    if (!cliSetupDialog.open || cliSetupTrigger !== button) return;
    document.querySelector("#cli-setup-title").textContent = `${profile.name || id} setup`;
    document.querySelector("#cli-setup-instructions").textContent = body.instructions;
    document.querySelector("#cli-setup-kind").textContent = body.kind;
    document.querySelector("#cli-setup-content").textContent = body.content;
    document.querySelector("#cli-setup-output").hidden = false;
  } catch (error) {
    document.querySelector("#cli-setup-instructions").textContent = error.message;
  }
});
document.querySelector("#close-cli-setup").addEventListener("click", () => cliSetupDialog.close());
cliSetupDialog.addEventListener("close", () => cliSetupTrigger?.focus());
document.querySelector("#copy-cli-setup").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(document.querySelector("#cli-setup-content").textContent);
    document.querySelector("#cli-setup-status").textContent = "Copied. No native tool settings were changed.";
  } catch {
    document.querySelector("#cli-setup-status").textContent = "Clipboard unavailable. Select the configuration text and copy it manually.";
  }
});
document.querySelector("#add-cli").addEventListener("click", () => {
  try {
    cliConfig = {...readClis(), statuses: cliConfig.statuses || {}};
    cliConfig.additional.push({name: "", command: ["command"], provider: "", model: "", base_url: "", api_key_env: "", description: "", instructions: "", enabled: true});
    renderClis();
    const row = document.querySelector("[data-additional-cli]:last-child");
    row?.querySelector('[data-cli-field="name"]').focus();
    cliStatus.textContent = "Unsaved CLI changes";
    cliError.textContent = "";
  } catch (error) { cliError.textContent = error.message; }
});
document.querySelector("#additional-cli-list").addEventListener("click", event => {
  const button = event.target.closest("[data-remove-cli]");
  if (!button) return;
  button.closest("[data-additional-cli]").remove();
  cliStatus.textContent = "Unsaved CLI changes"; cliError.textContent = "";
  document.querySelector("#add-cli").focus();
});
document.querySelector("#check-clis").addEventListener("click", async event => {
  const button = event.currentTarget; button.disabled = true; cliError.textContent = "";
  try {
    const config = readClis();
    const response = await fetch("/api/clis/check", {method: "POST", headers: {"Content-Type": "application/json", "X-Switchyard-Client": "local-ui"}, body: JSON.stringify(config)});
    const body = await response.json();
    if (!response.ok) throw new Error(Array.isArray(body.detail) ? body.detail.map(item => item.msg).join("; ") : body.detail || "Unable to check CLI commands.");
    cliConfig = {...config, statuses: body.statuses}; renderClis(); cliStatus.textContent = "Availability checked without running the commands.";
  } catch (error) { cliError.textContent = error.message; }
  finally { button.disabled = false; }
});
cliForm.addEventListener("submit", async event => {
  event.preventDefault(); const button = document.querySelector("#save-clis"); button.disabled = true; cliError.textContent = "";
  try {
    const response = await fetch("/api/clis", {method: "PUT", headers: {"Content-Type": "application/json", "X-Switchyard-Client": "local-ui"}, body: JSON.stringify(readClis())});
    const body = await response.json();
    if (!response.ok) throw new Error(Array.isArray(body.detail) ? body.detail.map(item => item.msg).join("; ") : body.detail || "Unable to save CLI settings.");
    cliConfig = body; renderClis(); cliStatus.textContent = "Global CLI settings saved for future runs across every repository. Existing runs keep their snapshot.";
  } catch (error) { cliError.textContent = error.message; }
  finally { button.disabled = false; }
});

async function loadClis() {
  try {
    const response = await fetch("/api/clis"); if (!response.ok) throw new Error("Unable to load CLI settings.");
    cliConfig = await response.json(); renderClis(); document.querySelector("#save-clis").disabled = false; cliStatus.textContent = "Global CLI access loaded for all repositories.";
  } catch (error) { cliError.textContent = error.message; cliStatus.textContent = ""; }
}
loadClis();

let deliveryDefaults = null;
const deliveryForm = document.querySelector("#delivery-form");
const deliveryError = document.querySelector("#delivery-error");
const deliveryStatus = document.querySelector("#delivery-status");

function deliveryModeMarkup(scope, selected) {
  return [["none", "Local only", "Keep the integration branch on this machine."], ["push", "Push branch", "Push to the named remote branch without a PR."], ["pr", "Open PR", "Push the branch, then create a pull request with gh."]].map(([value, label, hint]) => `<label class="delivery-toggle"><input type="radio" name="${scope}-delivery-mode" value="${value}" ${selected === value ? "checked" : ""}><span><b>${label}</b><small>${hint}</small></span></label>`).join("");
}

function deliveryFieldsMarkup(scope, config) {
  return `<div class="step-field"><label for="${scope}-delivery-remote">Git remote</label><input id="${scope}-delivery-remote" value="${escapeHtml(config.remote)}" required></div>
    <div class="step-field"><label for="${scope}-delivery-branch">Remote branch</label><input id="${scope}-delivery-branch" value="${escapeHtml(config.branch)}" required><span class="engine-note">Supports {run_id} and {repository}</span></div>
    <div class="step-field pr-only"><label for="${scope}-delivery-base">PR base branch</label><input id="${scope}-delivery-base" value="${escapeHtml(config.base_branch)}" required></div>
    <label class="mcp-enabled pr-only"><input id="${scope}-delivery-draft" type="checkbox" ${config.draft ? "checked" : ""}>Open as draft PR</label>`;
}

function renderDelivery(scope, config) {
  document.querySelector(`[data-delivery-mode="${scope}"]`).innerHTML = deliveryModeMarkup(scope, config.mode);
  document.querySelector(`[data-delivery-fields="${scope}"]`).innerHTML = deliveryFieldsMarkup(scope, config);
  updateDeliveryFields(scope);
}

function updateDeliveryFields(scope) {
  const mode = document.querySelector(`input[name="${scope}-delivery-mode"]:checked`)?.value || "none";
  const fields = document.querySelector(`[data-delivery-fields="${scope}"]`);
  fields.classList.toggle("is-none", mode === "none");
  fields.classList.toggle("is-pr", mode === "pr");
  fields.querySelectorAll("input").forEach(input => {
    input.disabled = mode === "none" || (input.closest(".pr-only") && mode !== "pr");
  });
}

function readDelivery(scope) {
  const selectedMode = document.querySelector(`input[name="${scope}-delivery-mode"]:checked`)?.value || "none";
  return {
    mode: selectedMode,
    remote: document.querySelector(`#${scope}-delivery-remote`).value.trim(),
    branch: document.querySelector(`#${scope}-delivery-branch`).value.trim(),
    base_branch: document.querySelector(`#${scope}-delivery-base`).value.trim(),
    draft: document.querySelector(`#${scope}-delivery-draft`).checked
  };
}

document.querySelectorAll("[data-delivery-mode]").forEach(container => container.addEventListener("change", () => updateDeliveryFields(container.dataset.deliveryMode)));
document.querySelector("#reset-dispatch-delivery").addEventListener("click", () => {
  const workspace = window.Workspaces?.active();
  if (workspace) renderDelivery("dispatch", workspace.delivery);
});
deliveryForm.addEventListener("input", () => { deliveryStatus.textContent = "Unsaved delivery defaults"; });
deliveryForm.addEventListener("submit", async event => {
  event.preventDefault(); const button = document.querySelector("#save-delivery"); button.disabled = true; deliveryError.textContent = "";
  try {
    const response = await fetch("/api/delivery", {method: "PUT", headers: {"Content-Type": "application/json", "X-Switchyard-Client": "local-ui"}, body: JSON.stringify(readDelivery("default"))});
    const body = await response.json();
    if (!response.ok) throw new Error(Array.isArray(body.detail) ? body.detail.map(item => item.msg).join("; ") : body.detail || "Unable to save delivery defaults.");
    deliveryDefaults = body; renderDelivery("default", body); deliveryStatus.textContent = "Global defaults saved. Copy them in Workspaces to apply them to a saved workspace; current dispatch choices are unchanged.";
  } catch (error) { deliveryError.textContent = error.message; }
  finally { button.disabled = false; }
});

async function loadDelivery() {
  try {
    const response = await fetch("/api/delivery"); if (!response.ok) throw new Error("Unable to load delivery defaults.");
    deliveryDefaults = await response.json(); renderDelivery("default", deliveryDefaults);
    if (!window.Workspaces?.active()) renderDelivery("dispatch", deliveryDefaults);
    document.querySelector("#save-delivery").disabled = false; deliveryStatus.textContent = "Global delivery defaults loaded.";
  } catch (error) { deliveryError.textContent = error.message; deliveryStatus.textContent = ""; }
}
loadDelivery();
