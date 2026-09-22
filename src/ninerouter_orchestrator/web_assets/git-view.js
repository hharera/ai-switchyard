(() => {
  const controls = document.querySelector("#git-controls");
  const repository = document.querySelector("#git-repository");
  const refresh = document.querySelector("#refresh-git");
  const baseField = document.querySelector("#git-base-field");
  const baseInput = document.querySelector("#git-base");
  const branches = document.querySelector("#git-branches");
  const content = document.querySelector("#git-content");
  const status = document.querySelector("#git-status");
  const errorBox = document.querySelector("#git-error");
  let snapshot = null;
  let requestNumber = 0;
  let diffLayout = "unified";
  let actionRunning = false;
  const patches = new Map();
  const drafts = new Map();
  let renderedWorkspace = null;
  let reviewResizer = null;
  const confirmDialog = document.querySelector("#git-confirm");

  function clearReviewResizer() {
    reviewResizer?.destroy();
    reviewResizer = null;
  }

  function saveDrafts() {
    if (!renderedWorkspace) return;
    const values = {};
    content.querySelectorAll("[data-git-form] [name]").forEach(input => {
      const key = `${input.closest('form').dataset.gitForm}:${input.name}`;
      values[key] = input.type === "checkbox" ? input.checked : input.value;
    });
    if (Object.keys(values).length) drafts.set(renderedWorkspace, values);
  }

  function restoreDrafts() {
    const values = drafts.get(renderedWorkspace) || {};
    content.querySelectorAll("[data-git-form] [name]").forEach(input => {
      const key = `${input.closest('form').dataset.gitForm}:${input.name}`;
      if (!(key in values) || input.name === "branch" && input.tagName === "SELECT") return;
      if (input.type === "checkbox") input.checked = values[key];
      else input.value = values[key];
    });
  }

  const actionLabels = {
    stage: "Stage files",
    unstage: "Unstage files",
    discard: "Discard changes",
    delete_untracked: "Delete untracked files",
    commit: "Commit",
    checkout: "Switch branch",
    create_branch: "Create branch",
    rename_branch: "Rename branch",
    delete_branch: "Delete branch",
    reset: "Reset branch",
    resolve_ours: "Use current version",
    resolve_theirs: "Use incoming version",
    fetch: "Fetch",
    pull: "Pull",
    push: "Push",
    stash: "Stash changes",
    stash_apply: "Apply stash",
    stash_pop: "Pop stash",
    stash_drop: "Drop stash",
    merge: "Merge",
    rebase: "Rebase",
    cherry_pick: "Cherry-pick",
    revert: "Revert commit",
    abort_operation: "Abort operation",
    continue_operation: "Continue operation",
    skip_operation: "Skip commit",
  };

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, character => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
    })[character]);
  }

  function comparison() {
    return controls.querySelector('input[name="git-comparison"]:checked').value;
  }

  function requestHeaders() {
    return {"Content-Type": "application/json", "X-Switchyard-Client": "local-ui"};
  }

  async function readJson(response, fallback) {
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || fallback);
    return body;
  }

  async function loadSnapshot(notice = "") {
    saveDrafts();
    const workspace = window.Workspaces?.active();
    if (!workspace) {
      clearReviewResizer();
      content.innerHTML = '<div class="git-empty"><b>No workspace selected</b><a href="#workspaces">Add a workspace to manage its repository</a></div>';
      return;
    }
    const currentRequest = ++requestNumber;
    patches.clear();
    refresh.disabled = true;
    errorBox.textContent = "";
    status.textContent = notice || "Reading repository status and changed files.";
    clearReviewResizer();
    content.innerHTML = '<div class="git-empty git-loading"><b>Reading Git</b><span>Collecting branches, worktree status, and recent history.</span></div>';
    const params = new URLSearchParams({workspace_id: workspace.id, comparison: comparison()});
    if (comparison() === "branch" && baseInput.value.trim()) params.set("base", baseInput.value.trim());
    try {
      const response = await fetch(`/api/git/status?${params}`, {headers: requestHeaders()});
      const data = await readJson(response, "Unable to read Git status.");
      if (currentRequest !== requestNumber) return;
      snapshot = data;
      renderedWorkspace = workspace.id;
      repository.removeAttribute("aria-invalid");
      repository.value = data.repository;
      baseInput.value = data.comparison.kind === "branch" ? data.comparison.base : (baseInput.value || data.default_base || "");
      branches.innerHTML = data.branches.map(name => `<option value="${escapeHtml(name)}"></option>`).join("");
      renderSnapshot(data);
      const result = `${data.summary.files} changed ${data.summary.files === 1 ? "file" : "files"}. ${data.comparison.label}.`;
      status.textContent = notice ? `${notice} ${result}` : result;
    } catch (error) {
      if (currentRequest !== requestNumber) return;
      snapshot = null;
      errorBox.textContent = error.message;
      errorBox.focus({preventScroll: true});
      status.textContent = "";
      content.innerHTML = '<div class="git-empty"><b>Changes unavailable</b><span>Check the repository and comparison, then refresh.</span></div>';
    } finally {
      if (currentRequest === requestNumber) refresh.disabled = false;
    }
  }

  function renderSnapshot(data) {
    clearReviewResizer();
    const worktree = data.status_summary;
    const statusChips = [
      worktree.conflicted ? [worktree.conflicted, "conflicted", "danger"] : null,
      worktree.staged ? [worktree.staged, "staged", "safe"] : null,
      worktree.unstaged ? [worktree.unstaged, "unstaged", "signal"] : null,
      worktree.untracked ? [worktree.untracked, "untracked", "muted"] : null,
    ].filter(Boolean).map(([count, label, tone]) => `<span class="git-status-chip ${tone}"><b>${count}</b> ${label}</span>`).join("");
    const commit = data.last_commit
      ? `<p class="git-last-commit"><code>${escapeHtml(data.last_commit.short)}</code><span>${escapeHtml(data.last_commit.subject)}</span><small>${escapeHtml(data.last_commit.author)} · ${escapeHtml(new Date(data.last_commit.authored_at).toLocaleString())}</small></p>`
      : '<p class="git-last-commit"><span>No commits yet</span></p>';
    const sync = data.upstream
      ? `<span>${escapeHtml(data.upstream)}</span><span>${data.ahead} ahead</span><span>${data.behind} behind</span>`
      : '<span>No upstream</span>';
    const truncation = data.files_truncated
      ? '<p class="git-limit-note">Showing the first 500 files. Use Git locally to inspect the complete comparison.</p>'
      : "";
    content.innerHTML = `
      <section class="git-repository-card" aria-labelledby="git-repository-name">
        <div class="git-branch-mark" aria-hidden="true"><i></i><i></i><i></i></div>
        <div class="git-repository-copy">
          <span class="git-comparison-label">${escapeHtml(data.comparison.label)}</span>
          <h3 id="git-repository-name">${escapeHtml(data.name)} <small>${escapeHtml(data.branch || `Detached at ${data.head}`)}</small></h3>
          ${commit}
          <div class="git-worktree-status">${statusChips || '<span class="git-status-chip safe"><b>✓</b> clean worktree</span>'}</div>
        </div>
        <dl class="git-summary">
          <div><dt>Files changed</dt><dd>${data.summary.files}</dd></div>
          <div><dt>Additions</dt><dd class="additions">+${data.summary.additions}</dd></div>
          <div><dt>Deletions</dt><dd class="deletions">−${data.summary.deletions}</dd></div>
        </dl>
        <div class="git-sync" aria-label="Upstream status">${sync}</div>
      </section>
      ${renderControlCenter(data)}
      <p class="field-note">${data.comparison.kind === "branch" ? "Committed changes since the shared ancestor. Worktree actions still apply to the current branch." : "Net changes against HEAD. Line totals cover tracked files; untracked previews load on demand."} Upstream counts use local refs until you fetch.</p>
      ${truncation}
      ${renderReview(data.files, data.comparison.kind !== "branch")}
    `;
    bindSnapshotEvents();
    bindReviewResizer();
    restoreDrafts();
    const first = content.querySelector("details[data-git-path]");
    if (first) loadPatch(first);
  }

  function renderControlCenter(data) {
    const localOptions = data.local_branches.map(name => `<option value="${escapeHtml(name)}" ${name === data.branch ? "selected" : ""}>${escapeHtml(name)}</option>`).join("");
    const remoteOptions = data.remotes.map(name => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join("");
    const refOptions = data.branches.map(name => `<option value="${escapeHtml(name)}"></option>`).join("");
    const operation = data.operation
      ? `<div class="git-operation-alert" role="status"><span><b>${escapeHtml(data.operation)} in progress</b> Resolve conflicts, stage the result, then continue or abort.</span><div><button type="button" data-git-action="continue_operation">Continue</button>${data.operation === "merge" ? "" : '<button type="button" data-git-action="skip_operation">Skip commit</button>'}<button class="danger-button" type="button" data-git-action="abort_operation">Abort ${escapeHtml(data.operation)}</button></div></div>`
      : "";
    const stashes = data.stashes.length
      ? `<ul class="git-stash-list">${data.stashes.map(item => `<li><span><code>${escapeHtml(item.ref)}</code><b>${escapeHtml(item.message)}</b><small>${escapeHtml(item.age)}</small></span><div><button type="button" data-git-action="stash_apply" data-stash-ref="${escapeHtml(item.ref)}">Apply</button><button type="button" data-git-action="stash_pop" data-stash-ref="${escapeHtml(item.ref)}">Pop</button><button class="danger-button" type="button" data-git-action="stash_drop" data-stash-ref="${escapeHtml(item.ref)}">Drop</button></div></li>`).join("")}</ul>`
      : '<p class="git-command-empty">No stashes saved.</p>';
    const history = data.recent_commits.length
      ? `<ol class="git-history-list">${data.recent_commits.map(item => `<li><code>${escapeHtml(item.short)}</code><span><b>${escapeHtml(item.subject)}</b><small>${escapeHtml(item.author)} · ${escapeHtml(new Date(item.authored_at).toLocaleString())}${item.decorations ? ` · ${escapeHtml(item.decorations)}` : ""}</small></span><button type="button" data-git-action="revert" data-ref="${escapeHtml(item.short)}">Revert</button></li>`).join("")}</ol>`
      : '<p class="git-command-empty">No commits yet.</p>';
    return `
      <section class="git-command-center" aria-labelledby="git-command-title">
        <header class="git-command-heading"><div><p class="eyebrow">Git controls</p><h3 id="git-command-title">Work with this repository</h3></div><span>Actions run on the selected workspace</span></header>
        ${operation}
        <div class="git-command-grid">
          <section class="git-command-card">
            <div class="git-command-card-heading"><h4>Commit</h4><span>${data.status_summary.staged} staged</span></div>
            <form data-git-form="commit">
              <label for="git-commit-message">Commit message</label>
              <textarea id="git-commit-message" name="message" rows="3" maxlength="10000" placeholder="Describe the change" required></textarea>
              <label class="git-check"><input type="checkbox" name="amend"> Amend the latest commit</label>
              <button class="git-primary-action" type="submit" data-git-submit>Commit staged changes</button>
            </form>
          </section>
          <details class="git-command-card git-tools-disclosure">
            <summary><b>Manage branches</b><span>${data.local_branches.length} local</span></summary>
            <form class="git-inline-form" data-git-form="checkout">
              <label for="git-current-branch">Switch branch</label>
              <select id="git-current-branch" name="branch" ${data.local_branches.length ? "" : "disabled"}>${localOptions || '<option value="">No local branches</option>'}</select>
              <button type="submit" data-git-submit ${data.local_branches.length ? "" : "disabled"}>Switch branch</button>
            </form>
            <form class="git-inline-form" data-git-form="create_branch">
              <label for="git-new-branch">New branch</label>
              <input id="git-new-branch" name="branch" autocomplete="off" placeholder="feature/name" required>
              <label for="git-start-point">Start point</label>
              <input id="git-start-point" name="start_point" list="git-action-refs" autocomplete="off" placeholder="Current HEAD">
              <button type="submit" data-git-submit>Create branch</button>
            </form>
            <form class="git-inline-form" data-git-form="rename_branch">
              <label for="git-renamed-branch">Rename current branch</label>
              <input id="git-renamed-branch" name="branch" autocomplete="off" placeholder="new-branch-name" required>
              <button type="submit" data-git-submit>Rename branch</button>
            </form>
            <button class="danger-button git-delete-branch" type="button" data-git-action="delete_branch">Delete selected branch</button>
          </details>
          <section class="git-command-card">
            <div class="git-command-card-heading"><h4>Remote sync</h4><span>${data.remotes.length} configured</span></div>
            <label for="git-remote">Remote</label>
            <select id="git-remote"><option value="">${data.upstream ? "Use upstream" : "Choose for pull or first push"}</option>${remoteOptions}</select>
            <label for="git-pull-strategy">Pull strategy</label>
            <select id="git-pull-strategy"><option value="ff_only">Fast-forward only</option><option value="rebase">Rebase local commits</option><option value="merge">Create a merge commit</option></select>
            <div class="git-button-row"><button type="button" data-git-action="fetch">Fetch</button><button type="button" data-git-action="pull">Pull</button><button class="git-primary-action" type="button" data-git-action="push">Push</button></div>
          </section>
          <details class="git-command-card git-tools-disclosure">
            <summary><b>Manage stashes</b><span>${data.stashes.length} saved</span></summary>
            <form data-git-form="stash">
              <label for="git-stash-message">Stash message</label>
              <input id="git-stash-message" name="message" autocomplete="off" placeholder="Optional description">
              <label class="git-check"><input type="checkbox" name="include_untracked"> Include untracked files</label>
              <button type="submit" data-git-submit>Stash changes</button>
            </form>
            ${stashes}
          </details>
          <details class="git-command-card git-command-wide git-tools-disclosure">
            <summary><b>Integrate or reset</b><span>Merge, rebase, cherry-pick, reset</span></summary>
            <label for="git-integrate-ref">Branch or commit</label>
            <input id="git-integrate-ref" list="git-action-refs" autocomplete="off" placeholder="main or a commit hash">
            <datalist id="git-action-refs">${refOptions}</datalist>
            <div class="git-button-row"><button type="button" data-git-action="merge">Merge</button><button type="button" data-git-action="rebase">Rebase</button><button type="button" data-git-action="cherry_pick">Cherry-pick</button></div>
            <div class="git-reset-row"><label for="git-reset-mode">Reset mode</label><select id="git-reset-mode"><option value="soft">Soft: keep index and files</option><option value="mixed" selected>Mixed: unstage changes</option><option value="hard">Hard: discard changes</option></select><button class="danger-button" type="button" data-git-action="reset">Reset to ref</button></div>
          </details>
          <details class="git-command-card git-command-wide git-history">
            <summary><span><b>Recent history</b><small>${data.recent_commits.length} commits shown</small></span><span>Open history</span></summary>
            ${history}
          </details>
        </div>
      </section>`;
  }

  function renderReview(files, editable) {
    if (!files.length) {
      const notes = {working: "The worktree matches HEAD.", staged: "Stage files from All changes to prepare a commit.", unstaged: "No unstaged changes remain.", branch: "The branch has no changes from its merge base."};
      return `<div class="git-empty clean"><b>No changes in this comparison</b><span>${notes[comparison()]}</span></div>`;
    }
    const fileButtons = files.map((file, index) => `
      <div class="git-file-row" data-path="${escapeHtml(file.path)}">
        ${editable ? `<label class="git-file-select"><input type="checkbox" value="${escapeHtml(file.path)}" data-git-path-select><span class="workflow-sr-only">Select ${escapeHtml(file.path)}</span></label>` : ""}
        <button type="button" data-git-file-target="git-file-${index}" data-path="${escapeHtml(file.path)}" title="${escapeHtml(file.path)}">
          <span class="git-file-status ${escapeHtml(file.status.replaceAll(" ", "-"))}" aria-label="${escapeHtml(file.status)}">${escapeHtml(statusLetter(file.status))}</span>
          <span class="git-file-name">${escapeHtml(file.path)}</span>
          <span class="git-file-delta">${file.status === "untracked" ? "New" : file.binary ? "Binary" : `<i>+${file.additions}</i><b>−${file.deletions}</b>`}</span>
        </button>
      </div>`).join("");
    const diffs = files.map((file, index) => `
      <details class="git-file-diff" id="git-file-${index}" data-git-path="${escapeHtml(file.path)}" ${index === 0 ? "open" : ""}>
        <summary>
          <span class="git-file-status ${escapeHtml(file.status.replaceAll(" ", "-"))}" aria-label="${escapeHtml(file.status)}">${escapeHtml(statusLetter(file.status))}</span>
          <span class="git-diff-title"><b>${escapeHtml(file.path)}</b>${file.old_path ? `<small>renamed from ${escapeHtml(file.old_path)}</small>` : ""}</span>
          <span class="git-diff-flags">${file.staged ? "<span>Staged</span>" : ""}${file.unstaged ? "<span>Unstaged</span>" : ""}</span>
          <span class="git-diff-counts">${file.status === "untracked" ? "New file" : file.binary ? "Binary" : `<i>+${file.additions}</i><b>−${file.deletions}</b>`}</span>
        </summary>
        <div class="git-diff-body" data-git-diff-body><div class="git-diff-placeholder">Open to load this diff.</div></div>
      </details>`).join("");
    const fileActions = editable ? `
      <div class="git-file-actions" aria-label="Selected file actions">
        <label><input id="git-select-all" type="checkbox"> Select all</label>
        <span id="git-selected-count">0 selected</span>
        <div><button type="button" data-git-action="stage">Stage</button><button type="button" data-git-action="unstage">Unstage</button>${files.some(file => file.status === "conflicted") ? '<button type="button" data-git-action="resolve_ours">Use ours</button><button type="button" data-git-action="resolve_theirs">Use theirs</button>' : ""}<button class="danger-button" type="button" data-git-action="discard">Discard unstaged</button><button class="danger-button" type="button" data-git-action="delete_untracked">Delete untracked</button></div>
      </div>` : "";
    return `
      <div class="git-review-toolbar"><h3>Files changed <span>${files.length}</span></h3><div class="git-layout-toggle" role="group" aria-label="Diff layout"><button type="button" data-diff-layout="unified" aria-pressed="${diffLayout === "unified"}">Unified</button><button type="button" data-diff-layout="split" aria-pressed="${diffLayout === "split"}">Split</button></div></div>
      ${fileActions}
      <div class="git-review-layout">
        <nav class="git-file-nav" id="git-file-nav" aria-label="Changed files">
          <div class="git-file-nav-heading"><b>Changed files</b><span>${files.length}</span></div>
          <div class="git-file-filter"><label for="git-file-filter">Filter files</label><input id="git-file-filter" type="search" placeholder="Search paths" autocomplete="off"></div>
          <div class="git-file-list">${fileButtons}</div>
          <p id="git-filter-empty" class="field-note" role="status" hidden>No matching paths. Clear the filter to see all files.</p>
        </nav>
        <div class="pane-resizer git-review-resizer" id="git-review-resizer" role="separator" aria-label="Resize changed files list" aria-controls="git-file-nav git-diff-list" aria-orientation="vertical" aria-valuemin="220" aria-valuemax="640" aria-valuenow="280" tabindex="0" title="Drag to resize. Use arrow keys for precise control; double-click to reset."></div>
        <div class="git-diff-list" id="git-diff-list">${diffs}</div>
      </div>`;
  }

  function bindReviewResizer() {
    const layout = content.querySelector(".git-review-layout");
    const handle = content.querySelector("#git-review-resizer");
    if (!layout || !handle || !window.PaneResize) return;
    const inlinePosition = event => {
      const bounds = layout.getBoundingClientRect();
      return getComputedStyle(document.documentElement).direction === "rtl"
        ? bounds.right - event.clientX
        : event.clientX - bounds.left;
    };
    reviewResizer = window.PaneResize.attach({
      handle,
      property: "--git-file-nav-width",
      storage: "switchyard.git-file-nav-width",
      minimum: 220,
      maximum: () => Math.min(640, Math.max(220, layout.clientWidth - 480)),
      initial: 280,
      position: inlinePosition,
      observe: layout,
    });
  }

  function bindSnapshotEvents() {
    content.querySelectorAll("details[data-git-path]").forEach(detail => {
      detail.addEventListener("toggle", () => { if (detail.open) loadPatch(detail); });
    });
    content.querySelectorAll("[data-git-file-target]").forEach(button => {
      button.addEventListener("click", () => {
        const detail = document.querySelector(`#${button.dataset.gitFileTarget}`);
        detail.open = true;
        loadPatch(detail);
        detail.scrollIntoView({behavior: "auto", block: "start"});
        detail.querySelector("summary").focus({preventScroll: true});
      });
    });
    content.querySelector("#git-file-filter")?.addEventListener("input", event => {
      const query = event.target.value.toLowerCase();
      let visible = 0;
      content.querySelectorAll(".git-file-row").forEach(row => {
        row.hidden = !row.dataset.path.toLowerCase().includes(query);
        if (!row.hidden) visible++;
      });
      content.querySelector("#git-filter-empty").hidden = visible > 0;
      updateSelectedCount();
    });
    content.querySelectorAll("[data-diff-layout]").forEach(button => {
      button.addEventListener("click", () => {
        diffLayout = button.dataset.diffLayout;
        content.querySelectorAll("[data-diff-layout]").forEach(item => item.setAttribute("aria-pressed", String(item === button)));
        content.querySelectorAll("details[data-git-path]").forEach(detail => {
          const file = patches.get(detail.dataset.gitPath);
          if (file) detail.querySelector("[data-git-diff-body]").innerHTML = GitDiff.render(file, diffLayout);
        });
      });
    });
    content.querySelector("#git-select-all")?.addEventListener("change", event => {
      visibleFileSelections().forEach(input => { input.checked = event.target.checked; });
      updateSelectedCount();
    });
    content.querySelectorAll("[data-git-path-select]").forEach(input => input.addEventListener("change", updateSelectedCount));
  }

  function selectedPaths() {
    return [...content.querySelectorAll("[data-git-path-select]:checked")].map(input => input.value);
  }

  function visibleFileSelections() {
    return [...content.querySelectorAll("[data-git-path-select]")].filter(input => !input.closest(".git-file-row").hidden);
  }

  function updateSelectedCount() {
    const selected = selectedPaths().length;
    const count = content.querySelector("#git-selected-count");
    if (count) count.textContent = `${selected} selected`;
    const all = content.querySelector("#git-select-all");
    const visible = visibleFileSelections();
    const visibleSelected = visible.filter(input => input.checked).length;
    if (all) {
      all.checked = visible.length > 0 && visibleSelected === visible.length;
      all.indeterminate = visibleSelected > 0 && visibleSelected < visible.length;
      all.disabled = visible.length === 0;
    }
  }

  function statusLetter(value) {
    return {added: "A", copied: "C", deleted: "D", modified: "M", renamed: "R", "type changed": "T", conflicted: "!", untracked: "?"}[value] || "M";
  }

  async function loadPatch(detail) {
    if (detail.dataset.loading === "true" || detail.dataset.loaded === "true" || !snapshot) return;
    detail.dataset.loading = "true";
    const currentRequest = requestNumber;
    const body = detail.querySelector("[data-git-diff-body]");
    body.innerHTML = '<div class="git-diff-placeholder">Loading file changes…</div>';
    const params = new URLSearchParams({
      workspace_id: window.Workspaces.active().id,
      comparison: snapshot.comparison.kind,
      path: detail.dataset.gitPath,
    });
    if (snapshot.comparison.kind === "branch") params.set("base", snapshot.comparison.base);
    try {
      const response = await fetch(`/api/git/diff?${params}`, {headers: requestHeaders()});
      const file = await readJson(response, "Unable to load this file diff.");
      if (currentRequest !== requestNumber) return;
      patches.set(detail.dataset.gitPath, file);
      body.innerHTML = GitDiff.render(file, diffLayout);
      detail.dataset.loaded = "true";
    } catch (error) {
      body.innerHTML = `<div class="git-diff-message error" role="alert"><b>Diff unavailable</b><span>${escapeHtml(error.message)}</span></div>`;
    } finally {
      detail.dataset.loading = "false";
    }
  }

  function confirmationFor(payload) {
    if (payload.action === "discard") return `Discard unstaged changes in ${payload.paths.length} selected file(s)? This cannot be undone.`;
    if (payload.action === "delete_untracked") return `Delete ${payload.paths.length} untracked file(s)? This cannot be undone.`;
    if (payload.action === "stash_drop") return `Drop ${payload.stash_ref}? This cannot be undone.`;
    if (payload.action === "abort_operation") return `Abort the current ${snapshot?.operation || "Git"} operation?`;
    if (payload.action === "skip_operation") return `Skip the current commit in this ${snapshot?.operation || "Git"} operation?`;
    if (payload.action === "rebase") return `Rebase the current branch onto ${payload.ref}? This rewrites local commits.`;
    if (payload.action === "merge") return `Merge ${payload.ref} into the current branch?`;
    if (payload.action === "cherry_pick") return `Cherry-pick ${payload.ref} onto the current branch?`;
    if (payload.action === "revert") return `Create a commit that reverts ${payload.ref}?`;
    if (payload.action === "pull") return "Pull remote changes into the current branch?";
    if (payload.action === "push") return "Push the current branch to its remote?";
    if (payload.action === "delete_branch") return `Delete local branch ${payload.branch}? Only fully merged branches can be deleted.`;
    if (payload.action === "reset") return `Reset the current branch to ${payload.ref} using ${payload.reset_mode} mode?${payload.reset_mode === "hard" ? " Uncommitted changes will be lost." : ""}`;
    if (payload.action === "resolve_ours") return `Replace ${payload.paths.length} conflicted file(s) with Git's ours version? During a rebase, ours is the branch you are rebasing onto. Stage the result to mark it resolved.`;
    if (payload.action === "resolve_theirs") return `Replace ${payload.paths.length} conflicted file(s) with Git's theirs version? During a rebase, theirs is the commit being replayed. Stage the result to mark it resolved.`;
    if (payload.action === "commit" && payload.amend) return "Amend the latest commit? This rewrites that commit.";
    return "";
  }

  async function performAction(payload) {
    const workspace = window.Workspaces?.active();
    if (!workspace || actionRunning) return;
    if (["stage", "unstage", "discard", "delete_untracked", "resolve_ours", "resolve_theirs"].includes(payload.action) && !payload.paths.length) {
      errorBox.textContent = "Select at least one changed file before running this action.";
      errorBox.focus({preventScroll: true});
      return;
    }
    const confirmation = confirmationFor(payload);
    if (confirmation) {
      document.querySelector("#git-confirm-title").textContent = actionLabels[payload.action];
      document.querySelector("#git-confirm-repository").textContent = `${workspace.name} - ${workspace.repository}`;
      document.querySelector("#git-confirm-message").textContent = confirmation + (payload.paths ? `\n${payload.paths.join("\n")}` : "");
      document.querySelector("#git-confirm-accept").textContent = actionLabels[payload.action];
      confirmDialog.returnValue = "cancel";
      const approved = new Promise(resolve => confirmDialog.addEventListener("close", () => resolve(confirmDialog.returnValue === "confirm"), {once: true}));
      confirmDialog.showModal();
      if (!await approved) return;
    }
    if (workspace.id !== window.Workspaces?.active()?.id || actionRunning) return;
    payload.workspace_id = workspace.id;
    payload.confirmed = Boolean(confirmation);
    actionRunning = true;
    saveDrafts();
    errorBox.textContent = "";
    status.textContent = `${actionLabels[payload.action] || "Git operation"} in progress.`;
    content.inert = true;
    content.setAttribute("aria-busy", "true");
    refresh.disabled = true;
    const contextRequest = requestNumber;
    try {
      const response = await fetch("/api/git/action", {
        method: "POST",
        headers: requestHeaders(),
        body: JSON.stringify(payload),
      });
      const result = await readJson(response, "Git could not complete this operation.");
      if (contextRequest !== requestNumber || workspace.id !== window.Workspaces?.active()?.id) return;
      if (payload.action === "commit") {
        const form = content.querySelector('[data-git-form="commit"]');
        if (form) form.reset();
      }
      document.querySelector("#git-output-text").textContent = result.output || "Git completed successfully.";
      document.querySelector("#git-output").hidden = false;
      await loadSnapshot(`${actionLabels[payload.action] || "Git operation"} complete.`);
      refresh.focus({preventScroll: true});
    } catch (error) {
      if (contextRequest !== requestNumber || workspace.id !== window.Workspaces?.active()?.id) return;
      // A failed merge or rebase can still change the worktree and operation state.
      await loadSnapshot();
      errorBox.textContent = error.message;
      errorBox.focus({preventScroll: true});
      status.textContent = "Git operation failed. Follow the error message and try again.";
    } finally {
      actionRunning = false;
      refresh.disabled = false;
      content.inert = false;
      content.removeAttribute("aria-busy");
    }
  }

  content.addEventListener("submit", event => {
    const form = event.target.closest("[data-git-form]");
    if (!form) return;
    event.preventDefault();
    const values = new FormData(form);
    const action = form.dataset.gitForm;
    const payload = {action};
    if (action === "commit") {
      payload.message = values.get("message");
      payload.amend = values.has("amend");
    } else if (action === "checkout") {
      payload.branch = values.get("branch");
    } else if (action === "create_branch") {
      payload.branch = values.get("branch");
      payload.start_point = values.get("start_point");
    } else if (action === "rename_branch") {
      payload.branch = values.get("branch");
    } else if (action === "stash") {
      payload.message = values.get("message");
      payload.include_untracked = values.has("include_untracked");
    }
    performAction(payload);
  });

  content.addEventListener("click", event => {
    const button = event.target.closest("[data-git-action]");
    if (!button) return;
    const action = button.dataset.gitAction;
    const payload = {action};
    if (["stage", "unstage", "discard", "delete_untracked", "resolve_ours", "resolve_theirs"].includes(action)) payload.paths = selectedPaths();
    if (["fetch", "pull", "push"].includes(action)) payload.remote = content.querySelector("#git-remote")?.value || "";
    if (action === "pull") payload.strategy = content.querySelector("#git-pull-strategy")?.value || "ff_only";
    if (["stash_apply", "stash_pop", "stash_drop"].includes(action)) payload.stash_ref = button.dataset.stashRef;
    if (["merge", "rebase", "cherry_pick", "reset"].includes(action)) payload.ref = content.querySelector("#git-integrate-ref")?.value || "";
    if (action === "reset") payload.reset_mode = content.querySelector("#git-reset-mode")?.value || "mixed";
    if (action === "delete_branch") payload.branch = content.querySelector("#git-current-branch")?.value || "";
    if (action === "revert") payload.ref = button.dataset.ref;
    performAction(payload);
  });

  controls.addEventListener("submit", event => { event.preventDefault(); if (!actionRunning) loadSnapshot(); });
  controls.addEventListener("change", event => {
    if (event.target.name !== "git-comparison") return;
    baseField.hidden = comparison() !== "branch";
    loadSnapshot();
  });
  refresh.addEventListener("click", () => loadSnapshot());

  function workspaceChanged() {
    if (confirmDialog.open) confirmDialog.close("cancel");
    saveDrafts();
    renderedWorkspace = null;
    document.querySelector("#git-output").hidden = true;
    requestNumber++;
    snapshot = null;
    patches.clear();
    refresh.disabled = false;
    errorBox.textContent = "";
    status.textContent = "";
    const workspace = window.Workspaces?.active();
    repository.value = workspace?.repository || "";
    baseInput.value = workspace?.git_base_branch || "";
    branches.innerHTML = "";
    content.innerHTML = '<div class="git-empty"><b>Select a workspace to manage its repository</b></div>';
    if (location.hash === "#git") loadSnapshot();
  }

  function openGitView() {
    if (location.hash === "#git" && !snapshot) loadSnapshot();
  }

  window.addEventListener("workspacechange", workspaceChanged);
  window.addEventListener("hashchange", openGitView);
  workspaceChanged();
})();
