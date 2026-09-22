(() => {
  const list = document.querySelector("#worktree-list");
  const summary = document.querySelector("#worktree-summary");
  const status = document.querySelector("#worktree-status");
  const error = document.querySelector("#worktree-error");
  const busyNote = document.querySelector("#worktree-busy");
  const refresh = document.querySelector("#refresh-worktrees");
  const search = document.querySelector("#worktree-search");
  const filter = document.querySelector("#worktree-filter");
  const clearFilters = document.querySelector("#clear-worktree-filters");
  let snapshot = null;
  let requestNumber = 0;
  let actionRunning = false;

  const activeWorkspace = () => window.Workspaces?.active();
  const message = body => Array.isArray(body.detail)
    ? body.detail.map(item => item.msg).join("; ")
    : body.detail;

  function matches(item) {
    const query = search.value.trim().toLocaleLowerCase();
    const haystack = [item.run_id, item.ticket_id, item.attempt, item.branch, item.path]
      .filter(Boolean).join(" ").toLocaleLowerCase();
    if (query && !haystack.includes(query)) return false;
    return {
      all: true,
      changed: Boolean(item.changed_files),
      locked: item.locked,
      removable: item.removable,
      missing: !item.exists,
    }[filter.value];
  }

  function statusBadges(item) {
    const badges = [];
    if (!item.exists) badges.push('<span class="worktree-badge danger">Missing folder</span>');
    else if (item.status_error) badges.push('<span class="worktree-badge danger">Status unavailable</span>');
    else if (item.changed_files) badges.push(`<span class="worktree-badge signal">${item.changed_files} local ${item.changed_files === 1 ? "item" : "items"}</span>`);
    else badges.push('<span class="worktree-badge safe">Clean</span>');
    if (item.locked) badges.push('<span class="worktree-badge protected">Locked</span>');
    return badges.join("");
  }

  function render() {
    if (!snapshot) return;
    const items = snapshot.worktrees.filter(matches);
    const totals = snapshot.summary;
    summary.innerHTML = [
      [totals.total, "retained"],
      [totals.changed, "with local files"],
      [totals.protected, "locked"],
      [snapshot.worktrees.filter(item => item.removable).length, "ready to remove"],
    ].map(([value, label]) => `<span><b>${value}</b>${label}</span>`).join("");
    busyNote.hidden = !snapshot.busy;
    clearFilters.disabled = !search.value && filter.value === "all";
    if (!items.length) {
      list.innerHTML = snapshot.worktrees.length
        ? '<div class="empty-state"><b>No matching worktrees</b><span>Clear the filters to see every retained checkout.</span></div>'
        : '<div class="empty-state"><b>No retained worktrees</b><span>Clean worktrees are removed automatically after integration. Check again after a workflow stops if local files need attention.</span></div>';
      return;
    }
    list.innerHTML = items.map(item => {
      const integration = item.attempt === "fork-0";
      const disabled = snapshot.busy || actionRunning;
      return `<article class="worktree-card">
        <div class="worktree-card-heading">
          <div><span class="worktree-kind">${integration ? "Integration" : "Candidate"}</span><h3>${escapeHtml(item.ticket_id || "Isolated worktree")} <small>${escapeHtml(item.attempt || "")}</small></h3></div>
          <div class="worktree-badges">${statusBadges(item)}</div>
        </div>
        <dl class="worktree-details">
          <div><dt>Run</dt><dd>${escapeHtml(item.run_id || "Unknown")}</dd></div>
          <div><dt>Branch</dt><dd>${escapeHtml(item.branch || (item.detached ? "Detached HEAD" : "Unavailable"))}</dd></div>
          <div><dt>Commit</dt><dd>${escapeHtml(item.head || "Unavailable")}</dd></div>
          <div class="worktree-path"><dt>Path</dt><dd>${escapeHtml(item.path)}</dd></div>
        </dl>
        ${item.locked ? `<p class="worktree-note">Locked: ${escapeHtml(item.lock_reason || "Unlock this worktree before removing its checkout.")}</p>` : ""}
        ${item.detached ? '<p class="worktree-note">Create a branch for this detached HEAD before removing its checkout.</p>' : ""}
        ${item.status_error ? `<p class="worktree-note danger">${escapeHtml(item.status_error)}</p>` : ""}
        ${item.prunable ? `<p class="worktree-note">Git reports this entry as prunable: ${escapeHtml(item.prune_reason || "the folder is unavailable")}.</p>` : ""}
        ${item.changed_files ? '<p class="worktree-note">Preserve or remove every local and ignored item before removing this worktree.</p>' : ""}
        <div class="worktree-actions">
          ${item.exists ? `<button class="secondary-button" type="button" data-worktree-action="${item.locked ? "unprotect" : "protect"}" data-worktree-path="${escapeHtml(item.path)}" ${disabled ? "disabled" : ""}>${item.locked ? "Unlock worktree" : "Lock worktree"}</button>` : ""}
          <button class="secondary-button danger-button" type="button" data-worktree-action="remove" data-worktree-path="${escapeHtml(item.path)}" ${disabled || !item.removable ? "disabled" : ""}>Remove checkout</button>
        </div>
      </article>`;
    }).join("");
  }

  async function loadWorktrees({notice = ""} = {}) {
    const workspace = activeWorkspace();
    const request = ++requestNumber;
    snapshot = null;
    error.textContent = "";
    summary.replaceChildren();
    busyNote.hidden = true;
    list.setAttribute("aria-busy", "true");
    list.innerHTML = workspace
      ? '<div class="empty-state"><b>Reading worktrees</b><span>Checking retained isolated checkouts and local files.</span></div>'
      : '<div class="empty-state"><b>Select a workspace</b><span>Choose a saved workspace to inspect its isolated checkouts.</span></div>';
    if (!workspace) {
      refresh.disabled = true;
      list.setAttribute("aria-busy", "false");
      status.textContent = "";
      return;
    }
    refresh.disabled = true;
    status.textContent = notice || "Reading retained worktrees...";
    try {
      const response = await fetch(`/api/worktrees?workspace_id=${encodeURIComponent(workspace.id)}`, {
        headers: {"X-Switchyard-Client": "local-ui"},
      });
      const body = await response.json();
      if (request !== requestNumber || workspace.id !== activeWorkspace()?.id) return;
      if (!response.ok) throw new Error(message(body) || "Unable to read worktrees.");
      snapshot = body;
      render();
      status.textContent = notice || `${body.summary.total} retained ${body.summary.total === 1 ? "worktree" : "worktrees"}.`;
    } catch (failure) {
      if (request !== requestNumber) return;
      error.textContent = failure.message;
      list.innerHTML = '<div class="empty-state"><b>Worktrees unavailable</b><span>Check the repository, then refresh this tab.</span></div>';
      status.textContent = "";
    } finally {
      if (request === requestNumber) {
        refresh.disabled = false;
        list.setAttribute("aria-busy", "false");
      }
    }
  }

  async function performAction(action, path) {
    const workspace = activeWorkspace();
    if (!workspace || actionRunning) return;
    const context = requestNumber;
    const isCurrent = () => context === requestNumber && workspace.id === activeWorkspace()?.id;
    if (action === "remove") {
      const confirmed = await ThemeControls.confirm({
        title: "Remove this checkout?",
        message: `Remove ${path}? The checkout folder will be deleted. Its Git branch and commits will remain available.`,
        confirmLabel: "Remove checkout",
      });
      if (!confirmed || !isCurrent()) return;
    }
    actionRunning = true;
    error.textContent = "";
    list.querySelectorAll("button").forEach(control => { control.disabled = true; });
    refresh.disabled = true;
    status.textContent = action === "remove" ? "Removing checkout..." : action === "protect" ? "Locking worktree..." : "Unlocking worktree...";
    try {
      const response = await fetch("/api/worktrees/action", {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-Switchyard-Client": "local-ui"},
        body: JSON.stringify({workspace_id: workspace.id, path, action, confirmed: action === "remove"}),
      });
      const body = await response.json();
      if (!isCurrent()) return;
      if (!response.ok) throw new Error(message(body) || "Unable to update the worktree.");
      const notice = action === "remove" ? "Checkout removed. Its branch and commits were kept." : action === "protect" ? "Worktree locked." : "Worktree unlocked.";
      await loadWorktrees({notice});
      if (workspace.id === activeWorkspace()?.id && location.hash === "#worktrees") refresh.focus();
    } catch (failure) {
      if (isCurrent()) {
        error.textContent = failure.message;
        status.textContent = "";
        error.focus();
      }
    } finally {
      actionRunning = false;
      refresh.disabled = !activeWorkspace();
      if (snapshot) render();
    }
  }

  list.addEventListener("click", event => {
    const button = event.target.closest("[data-worktree-action]");
    if (button) performAction(button.dataset.worktreeAction, button.dataset.worktreePath);
  });
  search.addEventListener("input", render);
  filter.addEventListener("change", render);
  clearFilters.addEventListener("click", () => {
    search.value = "";
    filter.value = "all";
    filter.dispatchEvent(new Event("change", {bubbles: true}));
    search.focus();
  });
  refresh.addEventListener("click", () => loadWorktrees());
  window.addEventListener("workspacechange", () => {
    requestNumber++;
    snapshot = null;
    search.value = "";
    filter.value = "all";
    filter.dispatchEvent(new Event("change", {bubbles: true}));
    if (location.hash === "#worktrees") loadWorktrees();
  });
  window.addEventListener("hashchange", () => {
    if (location.hash === "#worktrees") loadWorktrees();
  });
  if (location.hash === "#worktrees") loadWorktrees();
})();
