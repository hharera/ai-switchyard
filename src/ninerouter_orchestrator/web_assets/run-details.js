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
  const activity = list(job.activity);
  const workflow = job.workflow || result.workflow || {};
  const repository = String(job.repository || "").split("/").filter(Boolean).pop() || "Repository";
  const status = String(job.status || "queued");
  const statusLabel = status.replaceAll("_", " ");
  const active = ["queued", "running"].includes(status);
  const checksPassed = report => list(report?.commands).length > 0
    && report.commands.every(command => command.return_code === 0);
  const integratedCount = executions.filter(entry => checksPassed(entry.integration)).length;
  const tone = ["completed", "approved"].includes(status) ? "success" : ["failed", "needs_repair", "delivery_failed"].includes(status) ? "error" : "pending";
  const metadata = entries => `<dl class="run-metadata">${entries.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value || "Not recorded")}</dd></div>`).join("")}</dl>`;
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
  const recentActivity = activity.slice(-8).reverse().map(item => `<li><span>${esc(item.message)}</span><time datetime="${esc(item.at)}">${esc(item.at ? new Date(item.at).toLocaleTimeString() : "")}</time></li>`).join("");
  return `<div class="run-summary-heading"><span class="detail-status ${tone}">${esc(statusLabel)}</span><span class="detail-note">Workflow run</span></div>
    <h2 class="run-summary-title">${esc(job.workspace?.name || repository)}</h2><p class="run-workflow-name">${esc(workflow.name || "Workflow unavailable")}</p>
    <section class="run-live-progress ${active ? "is-live" : ""}">
      <div><span>${active ? "Live progress" : "Run progress"}</span>${tickets.length ? `<b>${integratedCount} of ${tickets.length} tasks integrated</b>` : ""}</div>
      <p>${esc(job.stage || "Waiting for a worker")}</p>
      ${tickets.length ? `<progress aria-label="Tasks integrated" max="${tickets.length}" value="${integratedCount}">${integratedCount} of ${tickets.length}</progress>` : ""}
    </section>
    ${recentActivity ? `<section class="run-activity"><div class="detail-section-heading"><h3>Activity</h3><span>Latest updates</span></div><ol>${recentActivity}</ol></section>` : ""}
    ${job.workspace ? disclosure("workspace", "Workspace settings at dispatch", metadata([["Workspace", job.workspace.name], ["Repository", job.repository], ["Attempts per ticket", job.run_settings?.forks_per_ticket], ["Command timeout (seconds)", job.run_settings?.command_timeout_seconds], ["Delivery", job.delivery?.mode]])) : ""}
    ${job.error ? `<section class="run-error"><h3>Run stopped</h3><p>${esc(job.error)}</p></section>` : ""}
    ${plan.objective ? `<section class="run-objective"><h3>Objective</h3><p>${esc(plan.objective)}</p></section>` : ""}
    ${tickets.length ? `<section class="planned-tickets"><div class="detail-section-heading"><h3>Implementation plan</h3><span>${tickets.length} tasks</span></div><p class="detail-note">Expand a task to review its scope and checks.</p>${ticketCards}</section>` : ""}
    ${list(plan.assumptions).length ? disclosure("assumptions", `Assumptions and limits (${plan.assumptions.length})`, bullets(plan.assumptions)) : ""}
    ${disclosure("request", "Original request", `<p class="original-request">${esc(job.request)}</p>`)}
    ${disclosure("workflow", `Workflow configuration (${list(workflow.steps).length} steps)`, `<ol class="workflow-snapshot">${list(workflow.steps).map(step => `<li><b>${esc(step.name)}</b><span>${esc(step.engine)}</span><p>${esc(step.system_prompt)}</p></li>`).join("")}</ol>`)}
    ${disclosure("metadata", "Run metadata", metadata([["Dispatch ID", job.id], ["Run ID", result.run_id], ["Repository", job.repository], ["Base commit", result.base], ["Stage", job.stage], ["Updated", job.updated_at ? new Date(job.updated_at).toLocaleString() : null]]))}
    ${disclosure("raw", "View raw JSON", `<pre class="raw-run-record"><code>${esc(JSON.stringify(job, null, 2))}</code></pre>`)}
  `;
}
