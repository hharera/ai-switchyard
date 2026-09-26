function parseStructuredJson(value) {
  if (value && typeof value === "object") return value;
  if (typeof value !== "string") return null;
  let candidate = value.trim();
  const fence = candidate.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  if (fence) candidate = fence[1].trim();
  for (const text of [candidate, candidate.replace(/\\_/g, "_")]) {
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === "object") return parsed;
    } catch { /* Fall back to the original message when it is not JSON. */ }
  }
  return null;
}

function structuredPlan(value) {
  const parsed = parseStructuredJson(value);
  if (!parsed) return null;
  const plan = parseStructuredJson(parsed.plan) || parsed;
  return typeof plan.objective === "string" && Array.isArray(plan.tickets)
    && plan.tickets.every(ticket => ticket && typeof ticket === "object" && !Array.isArray(ticket))
    ? plan : null;
}

function renderStructuredJson(value) {
  const plan = structuredPlan(value);
  if (!plan) return "";
  const esc = item => String(item ?? "").replace(/[&<>"']/g, char => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[char]);
  const list = items => Array.isArray(items) ? items : [];
  const bullets = items => `<ul class="structured-list">${list(items).map(item => `<li>${esc(item)}</li>`).join("")}</ul>`;
  const tickets = list(plan.tickets).map((ticket, index) => {
    const criteria = list(ticket.acceptance_criteria);
    const commands = list(ticket.validation_commands);
    const dependencies = list(ticket.dependencies);
    const areas = list(ticket.affected_areas);
    return `<details class="structured-ticket">
      <summary><span class="structured-ticket-id">${esc(ticket.id || `Task ${index + 1}`)}</span><span class="structured-ticket-title">${esc(ticket.title || "Untitled task")}</span><span class="structured-ticket-meta">${criteria.length} acceptance criteria · ${commands.length} validation commands</span></summary>
      <div class="structured-ticket-body">
        ${ticket.description ? `<p>${esc(ticket.description)}</p>` : ""}
        <div class="structured-dependencies"><b>Depends on</b>${dependencies.length ? dependencies.map(item => `<span>${esc(item)}</span>`).join("") : "<em>No dependencies</em>"}</div>
        ${criteria.length ? `<h5>Acceptance criteria</h5>${bullets(criteria)}` : ""}
        ${commands.length ? `<h5>Validation commands</h5>${commands.map(command => `<pre><code>${esc(command)}</code></pre>`).join("")}` : ""}
        ${areas.length ? `<details class="structured-areas"><summary>Affected files and areas (${areas.length})</summary>${bullets(areas)}</details>` : ""}
      </div>
    </details>`;
  }).join("");
  const assumptions = list(plan.assumptions);
  return `<article class="structured-plan">
    <header><span>Implementation plan</span><b>${list(plan.tickets).length} tasks</b></header>
    <h4>${esc(plan.objective)}</h4>
    ${assumptions.length ? `<details class="structured-assumptions"><summary>Assumptions and limits (${assumptions.length})</summary>${bullets(assumptions)}</details>` : ""}
    <section class="structured-tickets">${tickets}</section>
  </article>`;
}

function renderRunDetails(job) {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[char]);
  const list = values => Array.isArray(values) ? values : [];
  const bullets = values => `<ul class="detail-list">${list(values).map(value => `<li>${esc(value)}</li>`).join("")}</ul>`;
  const disclosure = (key, label, content) => `<details class="run-disclosure" data-detail-key="${esc(key)}"><summary>${esc(label)}</summary>${content}</details>`;
  const result = job.result || {};
  const plan = structuredPlan(result.plan) || structuredPlan(result) || {};
  const tickets = list(plan.tickets);
  const executions = list(result.tickets);
  const stepRuns = list(result.steps);
  const activity = list(job.activity);
  const workflow = job.workflow || result.workflow || {};
  const workflowSteps = list(workflow.steps);
  const changes = result.changes || {};
  const changedFiles = list(changes.files);
  const repository = String(job.repository || "").split("/").filter(Boolean).pop() || "Repository";
  const status = String(job.status || "queued");
  const statusLabel = status.replaceAll("_", " ");
  const active = ["queued", "running"].includes(status);
  const checksPassed = report => list(report?.commands).length > 0
    && report.commands.every(command => command.return_code === 0);
  const integratedCount = executions.filter(entry => checksPassed(entry.integration)).length;
  const tone = ["completed", "approved"].includes(status) ? "success" : ["failed", "needs_repair", "delivery_failed"].includes(status) ? "error" : "pending";
  const metadata = entries => `<dl class="run-metadata">${entries.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value === null || value === undefined || value === "" ? "Not recorded" : value)}</dd></div>`).join("")}</dl>`;
  const completedSteps = stepRuns.filter(step => step.status === "completed").length;
  const totalSteps = workflowSteps.length || stepRuns.length;
  const latestResponse = [...stepRuns].reverse().find(step => step.output);
  const currentStep = [...stepRuns].reverse().find(step => step.status === "running") || latestResponse;
  const responseBody = latestResponse
    ? renderStructuredJson(latestResponse.output) || `<p class="run-response-text">${esc(latestResponse.output)}</p>`
    : `<div class="run-response-pending"><b>${active ? "The agent is working" : "No response was recorded"}</b><span>${active ? "The response will appear here as soon as the current step finishes." : "Open the technical details to inspect the saved run record."}</span></div>`;
  const changeStatus = {A: "Added", C: "Copied", D: "Deleted", M: "Modified", R: "Renamed", T: "Type changed", U: "Unmerged"};
  const changeRows = changedFiles.map(file => {
    const code = String(file.status || "M").slice(0, 1).toUpperCase();
    const additions = Number.isInteger(file.additions) ? `<span class="change-additions">+${file.additions}</span>` : "";
    const deletions = Number.isInteger(file.deletions) ? `<span class="change-deletions">-${file.deletions}</span>` : "";
    const countNote = additions || deletions ? `${additions}${deletions}` : `<span class="change-unavailable">${file.untracked ? "New file" : "Counts unavailable"}</span>`;
    return `<li class="run-change-file"><span class="change-status ${esc(code.toLowerCase())}" aria-label="${esc(changeStatus[code] || "Changed")}">${esc(code)}</span><span class="change-path">${file.previous_path ? `<small>${esc(file.previous_path)} →</small>` : ""}<code>${esc(file.path)}</code></span><span class="change-counts">${countNote}</span></li>`;
  }).join("");
  const changeSummary = changedFiles.length
    ? `<section class="run-changes" aria-labelledby="run-changes-title"><div class="detail-section-heading"><div><span class="section-kicker">Repository</span><h3 id="run-changes-title">Changes made</h3></div><span>${esc(changes.file_count ?? changedFiles.length)} ${changedFiles.length === 1 ? "file" : "files"}</span></div><div class="run-change-totals"><span><b>${esc(changes.file_count ?? changedFiles.length)}</b> files changed</span><span class="change-additions"><b>+${esc(changes.additions ?? 0)}</b> additions</span><span class="change-deletions"><b>-${esc(changes.deletions ?? 0)}</b> deletions</span>${result.commit ? `<span><b>${esc(String(result.commit).slice(0, 8))}</b> commit</span>` : ""}</div><ul class="run-change-list">${changeRows}</ul></section>`
    : `<section class="run-changes" aria-labelledby="run-changes-title"><div class="detail-section-heading"><div><span class="section-kicker">Repository</span><h3 id="run-changes-title">Changes made</h3></div></div><div class="run-empty-detail"><b>${active ? "No file changes yet" : "No file changes recorded"}</b><span>${active ? "Files will appear here as the workflow updates the repository." : result.commit ? "The commit did not change tracked files." : "This run did not save a repository change summary."}</span></div></section>`;
  const stepCards = stepRuns.map((step, index) => {
    const stepStatus = String(step.status || "queued").replaceAll("_", " ");
    return `<details class="run-step-card ${esc(step.status || "queued")}" data-detail-key="step-${index}"${step.status === "failed" ? " data-default-open" : ""}><summary><span class="run-step-index">${index + 1}</span><span class="run-step-name"><b>${esc(step.name || `Step ${index + 1}`)}</b><small>${esc(step.engine || "Engine not recorded")}</small></span><span class="run-step-status">${esc(stepStatus)}</span></summary><div class="run-step-body">${step.error ? `<div class="run-step-error"><b>Step failed</b><p>${esc(step.error)}</p></div>` : ""}${step.output ? `<h4>Response from this step</h4><p class="run-response-text compact">${esc(step.output)}</p>` : `<p class="detail-note">${step.status === "running" ? "This step is still working. Its response will appear when it finishes." : "No response was saved for this step."}</p>`}</div></details>`;
  }).join("");
  const ticketCards = tickets.map((ticket, index) => {
    const criteria = list(ticket.acceptance_criteria);
    const commands = list(ticket.validation_commands);
    const dependencies = list(ticket.dependencies);
    const execution = executions.find(entry => entry.ticket === ticket.id);
    const candidates = list(execution?.candidates);
    const ticketState = checksPassed(execution?.integration) ? "Integrated"
      : execution?.integration ? "Integration checks failed"
      : execution?.decision ? "Candidate selected"
      : candidates.length ? `${candidates.length} ${candidates.length === 1 ? "candidate" : "candidates"} ready`
      : "Planned";
    const candidateRows = candidates.map(candidate => `<li><b>Candidate ${esc(candidate.request?.fork_number)}</b><span>${esc(candidate.request?.combo)} · ${esc(String(candidate.status || "finished").replaceAll("_", " "))}</span></li>`).join("");
    return `<details class="ticket-card" data-detail-key="ticket-${index}">
      <summary><span class="ticket-kicker">${esc(ticket.id || `Task ${index + 1}`)}<span>${esc(ticketState)}</span></span><span class="ticket-title">${esc(ticket.title || "Untitled task")}</span><span class="ticket-counts">${criteria.length} acceptance criteria · ${commands.length} validation commands</span></summary>
      <div class="ticket-content"><p>${esc(ticket.description)}</p>
        ${candidateRows ? `<div class="candidate-results"><h4>Candidate results</h4><ul>${candidateRows}</ul></div>` : ""}
        <div class="ticket-dependencies"><span>Depends on</span>${dependencies.length ? dependencies.map(id => `<span class="dependency-chip">${esc(id)}</span>`).join("") : '<span class="dependency-empty">No dependencies</span>'}</div>
        ${criteria.length ? `<h4>Acceptance criteria</h4>${bullets(criteria)}` : ""}
        ${commands.length ? `<h4>Validation commands</h4><p class="detail-note">Planned checks, not test results.</p>${commands.map(command => `<pre class="validation-command"><code>${esc(command)}</code></pre>`).join("")}` : ""}
        ${list(ticket.affected_areas).length ? disclosure(`areas-${index}`, `Affected files and areas (${ticket.affected_areas.length})`, bullets(ticket.affected_areas)) : ""}
      </div></details>`;
  }).join("");
  const recentActivity = activity.slice(-10).reverse().map(item => `<li><span>${esc(item.message)}</span><time datetime="${esc(item.at)}">${esc(item.at ? new Date(item.at).toLocaleTimeString() : "")}</time></li>`).join("");
  const continuity = job.followup_of
    ? `<div class="run-continuity"><span>Follow-up run</span><p>This run continues the work and context from dispatch <button type="button" data-related-job="${esc(job.followup_of)}">${esc(job.followup_of)}</button>.</p></div>`
    : "";
  return `<div class="run-summary-heading"><span class="detail-status ${tone}">${esc(statusLabel)}</span><span class="detail-note">${totalSteps ? `${completedSteps} of ${totalSteps} steps complete` : "Workflow run"}</span></div>
    <h2 class="run-summary-title">${esc(job.workspace?.name || repository)}</h2><p class="run-workflow-name">${esc(workflow.name || "Workflow unavailable")}${currentStep ? ` · ${esc(currentStep.name)}` : ""}</p>
    ${continuity}
    <section class="run-live-progress ${active ? "is-live" : ""}">
      <div><span>${active ? "Live progress" : "Run progress"}</span>${totalSteps ? `<b>${completedSteps} of ${totalSteps} steps completed</b>` : tickets.length ? `<b>${integratedCount} of ${tickets.length} tasks integrated</b>` : ""}</div>
      <p>${esc(job.stage || "Waiting for a worker")}</p>
      ${totalSteps ? `<progress aria-label="Workflow steps completed" max="${totalSteps}" value="${completedSteps}">${completedSteps} of ${totalSteps}</progress>` : tickets.length ? `<progress aria-label="Tasks integrated" max="${tickets.length}" value="${integratedCount}">${integratedCount} of ${tickets.length}</progress>` : ""}
    </section>
    ${job.error ? `<section class="run-error"><h3>Run stopped</h3><p>${esc(job.error)}</p></section>` : ""}
    <section class="run-conversation" aria-labelledby="run-conversation-title"><div class="detail-section-heading"><div><span class="section-kicker">Conversation</span><h3 id="run-conversation-title">Request and response</h3></div>${latestResponse ? `<span>${esc(latestResponse.name)}</span>` : ""}</div><article class="run-message request"><header><span class="message-avatar" aria-hidden="true">Y</span><div><b>You asked</b><small>Original request</small></div></header><p>${esc(job.request || result.request || "No request was recorded.")}</p></article><article class="run-message response"><header><span class="message-avatar" aria-hidden="true">9</span><div><b>${esc(latestResponse?.name || "Agent response")}</b><small>${latestResponse ? `${esc(latestResponse.engine)} · ${esc(String(latestResponse.status).replaceAll("_", " "))}` : active ? "Waiting for the current step" : "Workflow response"}</small></div></header>${responseBody}</article></section>
    ${changeSummary}
    ${stepRuns.length ? `<section class="run-steps"><div class="detail-section-heading"><div><span class="section-kicker">Workflow</span><h3>Step details</h3></div><span>${completedSteps} of ${totalSteps} completed</span></div><p class="detail-note">Expand a step to inspect the response produced by that agent.</p><div class="run-step-list">${stepCards}</div></section>` : ""}
    ${plan.objective ? `<section class="run-objective"><h3>Objective</h3><p>${esc(plan.objective)}</p></section>` : ""}
    ${tickets.length ? `<section class="planned-tickets"><div class="detail-section-heading"><h3>Implementation plan</h3><span>${tickets.length} tasks</span></div><p class="detail-note">Expand a task to review its scope and checks.</p>${ticketCards}</section>` : ""}
    ${list(plan.assumptions).length ? disclosure("assumptions", `Assumptions and limits (${plan.assumptions.length})`, bullets(plan.assumptions)) : ""}
    ${recentActivity ? `<details class="run-activity run-primary-disclosure" data-detail-key="activity"><summary><span>Activity</span><small>${activity.length} updates</small></summary><ol>${recentActivity}</ol></details>` : ""}
    <details class="run-technical run-primary-disclosure" data-detail-key="technical"><summary><span>Technical details</span><small>Configuration, IDs, and raw data</small></summary><div class="run-technical-body">
      ${job.workspace ? disclosure("workspace", "Workspace settings at dispatch", metadata([["Workspace", job.workspace.name], ["Repository", job.repository], ["Attempts per ticket", job.run_settings?.forks_per_ticket], ["Parallel tickets", job.run_settings?.max_parallel_tickets || 1], ["Command timeout (seconds)", job.run_settings?.command_timeout_seconds], ["Delivery", job.delivery?.mode]])) : ""}
      ${disclosure("workflow", `Workflow configuration (${workflowSteps.length} ${workflowSteps.length === 1 ? "step" : "steps"})`, `<ol class="workflow-snapshot">${workflowSteps.map(step => `<li><b>${esc(step.name)}</b><span>${esc(step.engine)}</span><p>${esc(step.system_prompt)}</p></li>`).join("")}</ol>`)}
      ${disclosure("metadata", "Run metadata", metadata([["Dispatch ID", job.id], ["Follows dispatch", job.followup_of], ["Run ID", result.run_id], ["Repository", job.repository], ["Base commit", result.base], ["Commit", result.commit], ["Stage", job.stage], ["Updated", job.updated_at ? new Date(job.updated_at).toLocaleString() : null]]))}
      ${disclosure("raw", "View raw JSON", `<pre class="raw-run-record"><code>${esc(JSON.stringify(job, null, 2))}</code></pre>`)}
    </div></details>
  `;
}
