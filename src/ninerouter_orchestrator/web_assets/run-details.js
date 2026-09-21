function renderRunDetails(job) {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[char]);
  const list = values => Array.isArray(values) ? values : [];
  const bullets = values => `<ul class="detail-list">${list(values).map(value => `<li>${esc(value)}</li>`).join("")}</ul>`;
  const disclosure = (key, label, content) => `<details class="run-disclosure" data-detail-key="${esc(key)}"><summary>${esc(label)}</summary>${content}</details>`;
  const result = job.result || {};
  const plan = result.plan || {};
  const tickets = list(plan.tickets);
  const workflow = job.workflow || result.workflow || {};
  const repository = String(job.repository || "").split("/").filter(Boolean).pop() || "Repository";
  const status = String(job.status || "queued");
  const statusLabel = status.replaceAll("_", " ");
  const tone = ["completed", "approved"].includes(status) ? "success" : ["failed", "needs_repair", "delivery_failed"].includes(status) ? "error" : "pending";
  const metadata = entries => `<dl class="run-metadata">${entries.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value || "Not recorded")}</dd></div>`).join("")}</dl>`;
  const ticketCards = tickets.map((ticket, index) => {
    const criteria = list(ticket.acceptance_criteria);
    const commands = list(ticket.validation_commands);
    const dependencies = list(ticket.dependencies);
    return `<details class="ticket-card" data-detail-key="ticket-${index}">
      <summary><span class="ticket-kicker">${esc(ticket.id || `Task ${index + 1}`)}<span>Planned</span></span><span class="ticket-title">${esc(ticket.title || "Untitled task")}</span><span class="ticket-counts">${criteria.length} acceptance criteria · ${commands.length} validation commands</span></summary>
      <div class="ticket-content"><p>${esc(ticket.description)}</p>
        <div class="ticket-dependencies"><span>Depends on</span>${dependencies.length ? dependencies.map(id => `<span class="dependency-chip">${esc(id)}</span>`).join("") : '<span class="dependency-empty">No dependencies</span>'}</div>
        ${criteria.length ? `<h4>Acceptance criteria</h4>${bullets(criteria)}` : ""}
        ${commands.length ? `<h4>Validation commands</h4><p class="detail-note">Planned checks, not test results.</p>${commands.map(command => `<pre class="validation-command"><code>${esc(command)}</code></pre>`).join("")}` : ""}
        ${list(ticket.affected_areas).length ? disclosure(`areas-${index}`, `Affected files and areas (${ticket.affected_areas.length})`, bullets(ticket.affected_areas)) : ""}
      </div></details>`;
  }).join("");
  return `<div class="run-summary-heading"><span class="detail-status ${tone}">${esc(statusLabel)}</span><span class="detail-note">Workflow run</span></div>
    <h2 class="run-summary-title">${esc(job.workspace?.name || repository)}</h2><p class="run-workflow-name">${esc(workflow.name || "Workflow unavailable")}</p>
    ${job.workspace ? disclosure("workspace", "Workspace settings at dispatch", metadata([["Workspace", job.workspace.name], ["Repository", job.repository], ["Attempts per ticket", job.run_settings?.forks_per_ticket], ["Command timeout (seconds)", job.run_settings?.command_timeout_seconds], ["Delivery", job.delivery?.mode]])) : ""}
    ${job.error ? `<section class="run-error"><h3>Run stopped</h3><p>${esc(job.error)}</p></section>` : ""}
    ${plan.objective ? `<section class="run-objective"><h3>Objective</h3><p>${esc(plan.objective)}</p></section>` : `<p class="detail-note">${esc(job.stage || "Waiting for results")}</p>`}
    ${tickets.length ? `<section class="planned-tickets"><div class="detail-section-heading"><h3>Implementation plan</h3><span>${tickets.length} tasks</span></div><p class="detail-note">Expand a task to review its scope and checks.</p>${ticketCards}</section>` : ""}
    ${list(plan.assumptions).length ? disclosure("assumptions", `Assumptions and limits (${plan.assumptions.length})`, bullets(plan.assumptions)) : ""}
    ${disclosure("request", "Original request", `<p class="original-request">${esc(job.request)}</p>`)}
    ${disclosure("workflow", `Workflow configuration (${list(workflow.steps).length} steps)`, `<ol class="workflow-snapshot">${list(workflow.steps).map(step => `<li><b>${esc(step.name)}</b><span>${esc(step.engine)}</span><p>${esc(step.system_prompt)}</p></li>`).join("")}</ol>`)}
    ${disclosure("metadata", "Run metadata", metadata([["Dispatch ID", job.id], ["Run ID", result.run_id], ["Repository", job.repository], ["Base commit", result.base], ["Stage", job.stage], ["Updated", job.updated_at ? new Date(job.updated_at).toLocaleString() : null]]))}
    ${disclosure("raw", "View raw JSON", `<pre class="raw-run-record"><code>${esc(JSON.stringify(job.result || job, null, 2))}</code></pre>`)}
  `;
}
