(() => {
  const controls = document.querySelector("#git-controls");
  const repository = document.querySelector("#git-repository");
  const repositorySelect = document.querySelector("#git-repository-select");
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
  let messageGenerating = false;
  let activeView = "changes";
  let selectedLogRef = "";
  let selectedLogRepository = "";
  let allLogRepositories = true;
  let commitRequestNumber = 0;
  let commitPatchRequestNumber = 0;
  const logFilters = {query: "", author: "", since: "", until: "", path: ""};
  let repositoryOptions = [];
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

  async function loadRepositories(currentRequest) {
    const workspace = window.Workspaces?.active();
    if (!workspace) return false;
    const response = await fetch(`/api/git/repositories?workspace_id=${encodeURIComponent(workspace.id)}`, {headers: requestHeaders()});
    const data = await readJson(response, "Unable to find Git repositories in this workspace.");
    if (currentRequest !== requestNumber || workspace.id !== window.Workspaces?.active()?.id) return false;
    repositoryOptions = data.repositories;
    const previous = repository.value;
    repositorySelect.innerHTML = repositoryOptions.map(item =>
      `<option value="${escapeHtml(item.path)}">${escapeHtml(item.relative_path === "." ? `${item.name} (workspace root)` : item.relative_path)}</option>`
    ).join("");
    if (!repositoryOptions.length) throw new Error("No Git repositories were found in this workspace.");
    const selected = repositoryOptions.find(item => item.path === previous)?.path || repositoryOptions[0].path;
    if (selected !== previous) selectedLogRef = "";
    repositorySelect.value = selected;
    repository.value = selected;
    return true;
  }

  async function loadSnapshot(notice = "") {
    commitRequestNumber++;
    commitPatchRequestNumber++;
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
    try {
      if (!repositoryOptions.length && !await loadRepositories(currentRequest)) return;
      const params = new URLSearchParams({workspace_id: workspace.id, repository: repository.value, comparison: comparison()});
      if (comparison() === "branch" && baseInput.value.trim()) params.set("base", baseInput.value.trim());
      if (activeView === "log") {
        params.set("all_repositories", String(allLogRepositories));
        if (selectedLogRef) {
          params.set("ref", selectedLogRef);
          params.set("ref_repository", selectedLogRepository);
        }
        Object.entries(logFilters).forEach(([key, value]) => { if (value) params.set(key, value); });
        const response = await fetch(`/api/git/log?${params}`, {headers: requestHeaders()});
        const data = await readJson(response, "Unable to load commit history.");
        if (currentRequest !== requestNumber) return;
        snapshot = {...data, commit_log: data.commits, selected_ref: data.selected_ref || ""};
        renderedWorkspace = `${workspace.id}:${data.repository}`;
        content.innerHTML = renderLog(snapshot);
        bindLogEvents();
        const scope = data.selected_ref ? data.selected_ref : data.multi_repository ? "all workspace repositories" : "all local and remote refs";
        status.textContent = `${data.commits.length} commits shown for ${scope}.${data.repository_errors?.length ? ` ${data.repository_errors.length} repositories could not be read.` : ""}`;
        return;
      }
      const response = await fetch(`/api/git/status?${params}`, {headers: requestHeaders()});
      const data = await readJson(response, "Unable to read Git status.");
      if (currentRequest !== requestNumber) return;
      snapshot = data;
      renderedWorkspace = `${workspace.id}:${data.repository}`;
      repository.removeAttribute("aria-invalid");
      repository.value = data.repository;
      baseInput.value = data.comparison.kind === "branch" ? data.comparison.base : (baseInput.value || data.default_base || "");
      branches.innerHTML = data.branches.map(name => `<option value="${escapeHtml(name)}"></option>`).join("");
      renderSnapshot(data);
      const result = activeView === "log"
        ? `${data.commit_log.length} commits shown across local and remote refs.`
        : `${data.summary.files} changed ${data.summary.files === 1 ? "file" : "files"}. ${data.comparison.label}.`;
      status.textContent = notice ? `${notice} ${result}` : result;
    } catch (error) {
      if (currentRequest !== requestNumber) return;
      snapshot = null;
      errorBox.textContent = error.message;
      errorBox.focus({preventScroll: true});
      status.textContent = "";
      if (activeView === "log") {
        snapshot = {repository: repository.value, commit_log: [], refs: [], multi_repository: allLogRepositories, repository_errors: []};
        content.innerHTML = renderLog(snapshot);
        bindLogEvents();
      } else {
        content.innerHTML = '<div class="git-empty"><b>Changes unavailable</b><span>Check the repository and comparison, then refresh.</span></div>';
      }
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
    const body = activeView === "log" ? renderLog(data) : `
      ${renderControlCenter(data)}
      <p class="field-note">${data.comparison.kind === "branch" ? "Committed changes since the shared ancestor. Worktree actions still apply to the current branch." : "Net changes against HEAD. Line totals cover tracked files; untracked previews load on demand."} Upstream counts use local refs until you fetch.</p>
      ${truncation}
      ${renderReview(data.files, data.comparison.kind !== "branch")}`;
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
      ${body}
    `;
    bindSnapshotEvents();
    if (activeView === "changes") bindReviewResizer();
    else bindLogEvents();
    restoreDrafts();
    const first = activeView === "changes" ? content.querySelector("details[data-git-path]") : null;
    if (first) loadPatch(first);
  }

  function graphRows(commits) {
    const lanes = [];
    return commits.map(commit => {
      const prefix = commit.repository ? `${commit.repository}\0` : "";
      const key = `${prefix}${commit.hash}`;
      const parentKeys = (commit.parents || []).map(parent => `${prefix}${parent}`);
      let lane = lanes.indexOf(key);
      const incoming = lane >= 0;
      if (lane < 0) {
        lane = lanes.length;
        lanes.push(key);
      }
      const before = [...lanes];
      const next = before.filter(hash => hash !== key && !parentKeys.includes(hash));
      next.splice(lane, 0, ...parentKeys);
      lanes.splice(0, lanes.length, ...next);
      return {commit, key, parentKeys, lane, incoming, before, next, width: Math.max(before.length, next.length, 1)};
    });
  }

  function graphColor(hash) {
    const colors = ["var(--signal-dark)", "var(--safe)", "var(--ink)", "var(--danger)"];
    const value = [...hash].reduce((total, character) => total + character.charCodeAt(0), 0);
    return colors[value % colors.length];
  }

  function graphSvg(row) {
    const step = 16;
    const x = index => 10 + index * step;
    const paths = [];
    if (row.incoming) paths.push(`<path d="M${x(row.lane)} 0 V12" stroke="${graphColor(row.key)}"/>`);
    row.before.forEach((hash, index) => {
      if (hash === row.key) return;
      const target = row.next.indexOf(hash);
      if (target >= 0) paths.push(`<path d="M${x(index)} 0 C${x(index)} 14 ${x(target)} 22 ${x(target)} 36" stroke="${graphColor(hash)}"/>`);
    });
    row.parentKeys.forEach(parent => {
      const target = row.next.indexOf(parent);
      if (target >= 0) paths.push(`<path d="M${x(row.lane)} 12 C${x(row.lane)} 22 ${x(target)} 25 ${x(target)} 36" stroke="${graphColor(parent)}"/>`);
    });
    return `<svg class="git-graph-svg" viewBox="0 0 ${row.graphWidth} 36" preserveAspectRatio="none" width="${row.graphWidth}" height="36" aria-hidden="true">${paths.join("")}<circle cx="${x(row.lane)}" cy="12" r="4" fill="${graphColor(row.key)}"/></svg>`;
  }

  function decorationTags(value) {
    return value.split(",").map(item => item.trim()).filter(Boolean).map(item => {
      const label = item.replace(/^HEAD -> /, "");
      const type = item.startsWith("HEAD ->") ? "head" : item.startsWith("tag:") ? "tag" : item.includes("/") ? "remote" : "branch";
      return `<span class="git-ref ${type}">${escapeHtml(label)}</span>`;
    }).join("");
  }

  function branchTree(refs) {
    const root = {children: new Map()};
    refs.forEach(ref => {
      const parts = ref.name.split("/");
      let node = root;
      parts.forEach((part, index) => {
        if (!node.children.has(part)) node.children.set(part, {name: part, children: new Map(), ref: null});
        node = node.children.get(part);
        if (index === parts.length - 1) node.ref = ref;
      });
    });
    return root;
  }

  function renderBranchNodes(node) {
    return [...node.children.values()].sort((left, right) => {
      if (Boolean(left.children.size) !== Boolean(right.children.size)) return left.children.size ? -1 : 1;
      return left.name.localeCompare(right.name);
    }).map(child => {
      if (child.children.size) {
        return `<details class="git-branch-folder" open data-branch-folder><summary><span class="git-tree-chevron" aria-hidden="true"></span><span class="git-tree-folder" aria-hidden="true"></span><span>${escapeHtml(child.name)}</span></summary><div>${renderBranchNodes(child)}</div></details>`;
      }
      const ref = child.ref;
      const active = ref.full_name === selectedLogRef && (ref.repository || snapshot.repository) === selectedLogRepository;
      const tracking = ref.tracking ? `<small>${escapeHtml(ref.tracking)}</small>` : "";
      return `<button class="git-branch-leaf${ref.current ? " current" : ""}" type="button" data-git-log-ref="${escapeHtml(ref.full_name)}" data-git-log-repository="${escapeHtml(ref.repository || snapshot.repository)}" data-branch-name="${escapeHtml(ref.name.toLowerCase())}" aria-pressed="${active}" title="${escapeHtml(ref.name)}${ref.upstream ? ` - tracks ${escapeHtml(ref.upstream)}` : ""}"><span class="git-tree-branch" aria-hidden="true"></span><span>${escapeHtml(child.name)}</span><span class="git-branch-badges">${ref.current ? '<b title="Current branch">HEAD</b>' : ""}${tracking}</span></button>`;
    }).join("");
  }

  function renderBranchPanel(data) {
    const refs = data.refs || [];
    const section = (title, refs, open) => `
      <details class="git-branch-section" ${open ? "open" : ""}>
        <summary><span class="git-tree-chevron" aria-hidden="true"></span><b>${title}</b><small>${refs.length}</small></summary>
        <div class="git-branch-tree">${refs.length ? renderBranchNodes(branchTree(refs)) : `<p>No ${title.toLowerCase()} branches.</p>`}</div>
      </details>`;
    const repositorySections = data.multi_repository
      ? [...new Map(refs.map(ref => [ref.repository, ref])).entries()].map(([path, sample]) => {
          const repositoryRefs = refs.filter(ref => ref.repository === path);
          return `<details class="git-repository-branch-group" open><summary><span class="git-tree-chevron" aria-hidden="true"></span><b>${escapeHtml(sample.repository_label === "." ? sample.repository_name : sample.repository_label)}</b><small>${repositoryRefs.length}</small></summary><div>${section("Local", repositoryRefs.filter(ref => ref.kind === "local"), true)}${section("Remote", repositoryRefs.filter(ref => ref.kind === "remote"), true)}</div></details>`;
        }).join("")
      : `${section("Local", refs.filter(ref => ref.kind === "local"), true)}${section("Remote", refs.filter(ref => ref.kind === "remote"), true)}`;
    const repositoryName = repositoryOptions.find(item => item.path === data.repository);
    return `
      <aside class="git-branches-panel" aria-labelledby="git-branches-heading">
        <header><h4 id="git-branches-heading">Branches</h4><span>${refs.length}</span></header>
        <p class="git-branch-repository" title="${escapeHtml(data.repository)}">${data.multi_repository ? "All workspace repositories" : escapeHtml(repositoryName?.relative_path === "." ? data.repository.split("/").pop() : repositoryName?.relative_path || data.repository)}</p>
        <div class="git-branch-search"><label for="git-branch-search">Filter branches</label><input id="git-branch-search" type="search" placeholder="Branch name" autocomplete="off"></div>
        <button class="git-all-branches" type="button" data-git-log-ref="" aria-pressed="${!selectedLogRef}"><span class="git-tree-all" aria-hidden="true"></span><span>All branches</span></button>
        <div class="git-branch-scroll">${repositorySections}<p id="git-branch-filter-empty" class="field-note" role="status" hidden>No matching branches. Clear the filter to see all branches.</p>${refs.length >= 500 ? '<p class="field-note">Showing up to 500 branches per repository.</p>' : ""}</div>
      </aside>`;
  }

  function renderLogFilters(data) {
    return `<form class="git-log-filters" id="git-log-filters">
      <label><span>Text or hash</span><input name="query" type="search" value="${escapeHtml(logFilters.query)}" placeholder="Commit text or hash" autocomplete="off"></label>
      <label><span>Branch</span><select name="branch"><option value="">All branches</option>${(data.refs || []).map((ref, index) => `<option value="${index}" ${ref.full_name === selectedLogRef && (ref.repository || data.repository) === selectedLogRepository ? "selected" : ""}>${escapeHtml(data.multi_repository ? `${ref.repository_label === "." ? ref.repository_name : ref.repository_label}: ${ref.name}` : ref.name)}</option>`).join("")}</select></label>
      <label><span>Author</span><input name="author" type="search" value="${escapeHtml(logFilters.author)}" placeholder="Author name" autocomplete="off"></label>
      <label><span>From</span><input name="since" type="date" value="${escapeHtml(logFilters.since)}"></label>
      <label><span>To</span><input name="until" type="date" value="${escapeHtml(logFilters.until)}"></label>
      <label class="git-log-path"><span>Path</span><input name="path" value="${escapeHtml(logFilters.path)}" placeholder="src/ or package.json" autocomplete="off"></label>
      <label class="git-log-all-repositories"><input name="all_repositories" type="checkbox" ${allLogRepositories ? "checked" : ""}><span>All repositories</span></label>
      <button type="submit">Apply filters</button><button type="button" data-clear-log-filters>Clear</button>
      <span class="git-log-filter-result" role="status">${data.commit_log.length} commits</span>
    </form>`;
  }

  function renderLog(data) {
    const keys = new Set(data.commit_log.map(commit => `${commit.repository || ""}\0${commit.hash}`));
    const graph = graphRows(data.commit_log.map(commit => ({...commit, parents: commit.parents.filter(parent => keys.has(`${commit.repository || ""}\0${parent}`))})));
    const graphWidth = (graph.length ? Math.max(...graph.map(row => row.width)) : 1) * 16 + 12;
    graph.forEach(row => { row.graphWidth = graphWidth; });
    const rows = graph.map((row, index) => `
      <li class="git-log-row${index === 0 ? " selected" : ""}" data-git-commit="${escapeHtml(row.commit.hash)}" data-git-repository="${escapeHtml(row.commit.repository || data.repository)}">
        <button class="git-log-select" type="button" data-git-log-select="${escapeHtml(row.commit.hash)}" data-git-repository="${escapeHtml(row.commit.repository || data.repository)}" aria-pressed="${index === 0}">
          <span class="git-graph-cell">${graphSvg(row)}</span>
          <span class="git-log-subject">${data.multi_repository ? `<small class="git-repo-label">${escapeHtml(row.commit.repository_label === "." ? row.commit.repository_name : row.commit.repository_label)}</small>` : ""}<b>${escapeHtml(row.commit.subject)}</b><span>${decorationTags(row.commit.decorations)}</span></span>
          <code>${escapeHtml(row.commit.short)}</code>
          <span class="git-log-author">${escapeHtml(row.commit.author)}</span>
          <time datetime="${escapeHtml(row.commit.authored_at)}">${escapeHtml(new Date(row.commit.authored_at).toLocaleString())}</time>
        </button>
      </li>`).join("") || '<li class="git-log-empty"><b>No commits for this branch</b><span>Select another branch or show all branches.</span></li>';
    return `
      <section class="git-log-workbench" aria-labelledby="git-log-heading" style="--git-graph-width: ${graphWidth}px">
        <header class="git-log-heading"><div><p class="eyebrow">Repository history</p><h3 id="git-log-heading">Commit graph</h3></div><span>${data.multi_repository ? "Workspace history" : "Repository history"}</span></header>
        ${renderLogFilters(data)}
        <p class="git-log-limit">Showing up to 100 matches per repository${data.multi_repository ? ", 300 total" : ""}. Dates use the server timezone. Branches filter by reachable history; paths are literal and repository-relative.</p>
        ${data.repository_errors?.length ? `<details class="git-log-errors"><summary>Some repositories could not be read</summary>${data.repository_errors.map(item => `<p><b>${escapeHtml(item.repository)}</b>: ${escapeHtml(item.message)}</p>`).join("")}</details>` : ""}
        <div class="git-log-layout">
          ${renderBranchPanel(data)}
          <div class="git-log-scroll" tabindex="0" role="region" aria-label="Commit history"><div class="git-log-columns" aria-hidden="true"><span>Graph and subject</span><span>Commit</span><span>Author</span><span>Date</span></div><ol class="git-log-list">${rows}</ol></div>
          <aside class="git-commit-detail" id="git-commit-detail" aria-live="polite">${data.commit_log.length ? '<div class="git-detail-empty"><b>Loading commit</b><span>Reading changed files and metadata.</span></div>' : '<div class="git-detail-empty"><b>No commit selected</b><span>Adjust the filters or select another branch.</span></div>'}</aside>
        </div>
      </section>`;
  }

  function renderCommitDetail(detail, summary) {
    const panel = content.querySelector("#git-commit-detail");
    if (!panel || !summary) return;
    const files = detail.files.map((file, index) => `<button type="button" class="git-commit-file${index === 0 ? " selected" : ""}" data-commit-file="${escapeHtml(file.path)}" aria-pressed="${index === 0}" title="${escapeHtml(file.path)}${file.old_path ? ` (from ${escapeHtml(file.old_path)})` : ""}"><span class="git-file-status ${escapeHtml(file.status.replaceAll(" ", "-"))}" aria-label="${escapeHtml(file.status)}">${escapeHtml(statusLetter(file.status))}</span><span>${escapeHtml(file.path)}</span><small>${file.binary ? "Binary" : `<i>+${file.additions}</i> <b>-${file.deletions}</b>`}</small></button>`).join("");
    panel.innerHTML = `
      <span class="git-detail-kicker">Selected commit</span>
      <h4>${escapeHtml(summary.subject)}</h4>
      <span class="git-detail-repository">${escapeHtml(summary.repository_label || summary.repository_name || detail.repository)}</span>
      <div class="git-detail-refs">${decorationTags(summary.decorations)}</div>
      <pre class="git-commit-message">${escapeHtml(detail.message)}</pre>
      <dl><div><dt>Commit</dt><dd><code>${escapeHtml(detail.hash)}</code></dd></div><div><dt>Author</dt><dd>${escapeHtml(detail.author)} &lt;${escapeHtml(detail.author_email)}&gt;</dd></div><div><dt>Authored</dt><dd>${escapeHtml(new Date(detail.authored_at).toLocaleString())}</dd></div><div><dt>Committed</dt><dd>${escapeHtml(new Date(detail.committed_at).toLocaleString())} by ${escapeHtml(detail.committer)}</dd></div><div><dt>Parents</dt><dd>${detail.parents.length ? detail.parents.map(parent => `<code>${escapeHtml(parent.slice(0, 12))}</code>`).join(" ") : "Initial commit"}</dd></div><div><dt>Diff base</dt><dd>${escapeHtml(detail.comparison_label)}</dd></div></dl>
      <div class="git-commit-files-heading"><b>Changed files</b><span>${detail.file_count}</span></div>
      <div class="git-commit-files">${files || '<p>No file changes in this commit.</p>'}${detail.files_truncated ? '<p>Showing the first 500 files.</p>' : ""}</div>
      <div class="git-commit-diff" id="git-commit-diff"><div class="git-diff-placeholder">${files ? "Select a changed file to view its diff." : "This commit has no file diff."}</div></div>
      <button class="secondary-button danger-button" type="button" data-git-action="revert" data-ref="${escapeHtml(detail.hash)}" data-repository="${escapeHtml(detail.repository)}">Revert commit</button>`;
    panel.querySelectorAll("[data-commit-file]").forEach(button => button.addEventListener("click", () => loadCommitPatch(detail.hash, detail.repository, button.dataset.commitFile, button)));
    const first = panel.querySelector("[data-commit-file]");
    if (first) loadCommitPatch(detail.hash, detail.repository, first.dataset.commitFile, first);
  }

  async function loadCommitDetail(commit) {
    const detail = content.querySelector("#git-commit-detail");
    if (!detail) return;
    const generation = ++commitRequestNumber;
    commitPatchRequestNumber++;
    detail.innerHTML = '<div class="git-detail-empty"><b>Loading commit</b><span>Reading changed files and metadata.</span></div>';
    const workspace = window.Workspaces?.active();
    const params = new URLSearchParams({workspace_id: workspace.id, repository: commit.repository || snapshot.repository, commit: commit.hash});
    try {
      const response = await fetch(`/api/git/commit?${params}`, {headers: requestHeaders()});
      const data = await readJson(response, "Unable to load this commit.");
      if (generation !== commitRequestNumber || activeView !== "log") return;
      renderCommitDetail(data, commit);
    } catch (error) {
      if (generation !== commitRequestNumber) return;
      detail.innerHTML = `<div class="git-diff-message error" role="alert"><b>Commit unavailable</b><span>${escapeHtml(error.message)}</span></div>`;
    }
  }

  async function loadCommitPatch(commit, commitRepository, path, button) {
    const target = content.querySelector("#git-commit-diff");
    if (!target) return;
    const generation = commitRequestNumber;
    const patchGeneration = ++commitPatchRequestNumber;
    content.querySelectorAll("[data-commit-file]").forEach(item => {
      item.classList.toggle("selected", item === button);
      item.setAttribute("aria-pressed", String(item === button));
    });
    target.innerHTML = '<div class="git-diff-placeholder">Loading file diff...</div>';
    const params = new URLSearchParams({workspace_id: window.Workspaces.active().id, repository: commitRepository, commit, path});
    try {
      const response = await fetch(`/api/git/commit-diff?${params}`, {headers: requestHeaders()});
      const data = await readJson(response, "Unable to load this file diff.");
      if (generation !== commitRequestNumber || patchGeneration !== commitPatchRequestNumber) return;
      target.innerHTML = GitDiff.render(data, "unified");
    } catch (error) {
      if (generation !== commitRequestNumber || patchGeneration !== commitPatchRequestNumber) return;
      target.innerHTML = `<div class="git-diff-message error" role="alert"><b>Diff unavailable</b><span>${escapeHtml(error.message)}</span></div>`;
    }
  }

  function bindLogEvents() {
    const select = (hash, commitRepository) => {
      content.querySelectorAll(".git-log-row").forEach(row => row.classList.toggle("selected", row.dataset.gitCommit === hash && row.dataset.gitRepository === commitRepository));
      content.querySelectorAll("[data-git-log-select]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.gitLogSelect === hash && button.dataset.gitRepository === commitRepository)));
      const commit = snapshot.commit_log.find(item => item.hash === hash && (item.repository || snapshot.repository) === commitRepository);
      if (commit) loadCommitDetail(commit);
    };
    content.querySelectorAll("[data-git-log-select]").forEach(button => button.addEventListener("click", () => select(button.dataset.gitLogSelect, button.dataset.gitRepository)));
    content.querySelectorAll("[data-git-log-ref]").forEach(button => button.addEventListener("click", () => {
      const ref = button.dataset.gitLogRef;
      const refRepository = button.dataset.gitLogRepository || "";
      if (ref === selectedLogRef && refRepository === selectedLogRepository || actionRunning) return;
      selectedLogRef = ref;
      selectedLogRepository = ref ? refRepository : "";
      loadSnapshot().then(() => {
        if (selectedLogRef !== ref || activeView !== "log") return;
        [...content.querySelectorAll("[data-git-log-ref]")].find(item => item.dataset.gitLogRef === ref && (item.dataset.gitLogRepository || "") === refRepository)?.focus({preventScroll: true});
      });
    }));
    content.querySelector("#git-branch-search")?.addEventListener("input", event => {
      const query = event.target.value.trim().toLowerCase();
      let visible = 0;
      content.querySelectorAll(".git-branch-leaf").forEach(button => {
        button.hidden = Boolean(query) && !button.dataset.branchName.includes(query);
        if (!button.hidden) visible++;
      });
      [...content.querySelectorAll("[data-branch-folder]")].reverse().forEach(folder => {
        folder.hidden = !folder.querySelector(".git-branch-leaf:not([hidden])");
        if (query && !folder.hidden) folder.open = true;
      });
      content.querySelectorAll(".git-branch-section").forEach(section => {
        section.hidden = Boolean(query) && !section.querySelector(".git-branch-leaf:not([hidden])");
        if (query && !section.hidden) section.open = true;
      });
      content.querySelector("#git-branch-filter-empty").hidden = !query || visible > 0;
    });
    content.querySelector("#git-log-filters")?.addEventListener("submit", event => {
      event.preventDefault();
      const values = new FormData(event.currentTarget);
      for (const key of Object.keys(logFilters)) logFilters[key] = String(values.get(key) || "").trim();
      allLogRepositories = values.has("all_repositories");
      const chosen = values.get("branch") === "" ? null : snapshot.refs[Number(values.get("branch"))];
      selectedLogRef = chosen?.full_name || "";
      selectedLogRepository = chosen ? chosen.repository || snapshot.repository : "";
      if (!allLogRepositories && selectedLogRepository !== repository.value) selectedLogRef = selectedLogRepository = "";
      loadSnapshot();
    });
    content.querySelector("[data-clear-log-filters]")?.addEventListener("click", () => {
      for (const key of Object.keys(logFilters)) logFilters[key] = "";
      selectedLogRef = "";
      selectedLogRepository = "";
      loadSnapshot();
    });
    if (snapshot.commit_log.length) {
      const first = snapshot.commit_log[0];
      select(first.hash, first.repository || snapshot.repository);
    }
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
    return `
      <section class="git-command-center" aria-labelledby="git-command-title">
        <header class="git-command-heading"><div><p class="eyebrow">Git controls</p><h3 id="git-command-title">Work with this repository</h3></div><span>Actions run on the selected workspace</span></header>
        ${operation}
        <div class="git-command-grid">
          <section class="git-command-card" id="git-commit-card">
            <div class="git-command-card-heading"><h4>Commit</h4><span>${data.status_summary.staged} staged</span></div>
            <form data-git-form="commit">
              <label for="git-commit-message">Commit message</label>
              <textarea id="git-commit-message" name="message" rows="3" maxlength="10000" placeholder="Describe the change" required></textarea>
              <button class="git-generate-message" type="button" data-git-generate-commit-message ${data.status_summary.staged ? "" : "disabled"}>Generate message with workflow model</button>
              <label class="git-check"><input type="checkbox" name="amend"> Amend the latest commit</label>
              <button class="git-primary-action" type="submit" data-git-submit>Commit staged changes</button>
            </form>
          </section>
          <details class="git-command-card git-tools-disclosure" id="git-branches-card">
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
          <section class="git-command-card" id="git-remote-card">
            <div class="git-command-card-heading"><h4>Remote sync</h4><span>${data.remotes.length} configured</span></div>
            <label for="git-remote">Remote</label>
            <select id="git-remote"><option value="">${data.upstream ? "Use upstream" : "Choose for pull or first push"}</option>${remoteOptions}</select>
            <label for="git-pull-strategy">Pull strategy</label>
            <select id="git-pull-strategy"><option value="ff_only">Fast-forward only</option><option value="rebase">Rebase local commits</option><option value="merge">Create a merge commit</option></select>
            <div class="git-button-row"><button type="button" data-git-action="fetch">Fetch</button><button type="button" data-git-action="pull">Pull</button><button class="git-primary-action" type="button" data-git-action="push">Push</button></div>
          </section>
          <details class="git-command-card git-tools-disclosure" id="git-stashes-card">
            <summary><b>Manage stashes</b><span>${data.stashes.length} saved</span></summary>
            <form data-git-form="stash">
              <label for="git-stash-message">Stash message</label>
              <input id="git-stash-message" name="message" autocomplete="off" placeholder="Optional description">
              <label class="git-check"><input type="checkbox" name="include_untracked"> Include untracked files</label>
              <button type="submit" data-git-submit>Stash changes</button>
            </form>
            ${stashes}
          </details>
          <details class="git-command-card git-command-wide git-tools-disclosure" id="git-integrate-card">
            <summary><b>Integrate or reset</b><span>Merge, rebase, cherry-pick, reset</span></summary>
            <label for="git-integrate-ref">Branch or commit</label>
            <input id="git-integrate-ref" list="git-action-refs" autocomplete="off" placeholder="main or a commit hash">
            <datalist id="git-action-refs">${refOptions}</datalist>
            <div class="git-button-row"><button type="button" data-git-action="merge">Merge</button><button type="button" data-git-action="rebase">Rebase</button><button type="button" data-git-action="cherry_pick">Cherry-pick</button></div>
            <div class="git-reset-row"><label for="git-reset-mode">Reset mode</label><select id="git-reset-mode"><option value="soft">Soft: keep index and files</option><option value="mixed" selected>Mixed: unstage changes</option><option value="hard">Hard: discard changes</option></select><button class="danger-button" type="button" data-git-action="reset">Reset to ref</button></div>
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
        <div class="git-file-actions-dropdown"><button id="git-file-actions-toggle" type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="git-file-actions-menu" disabled>Actions</button><div class="git-file-actions-menu" id="git-file-actions-menu" role="menu" hidden><button type="button" role="menuitem" data-git-action="stage">Stage</button><button type="button" role="menuitem" data-git-action="unstage">Unstage</button>${files.some(file => file.status === "conflicted") ? '<button type="button" role="menuitem" data-git-action="resolve_ours">Use ours</button><button type="button" role="menuitem" data-git-action="resolve_theirs">Use theirs</button>' : ""}<button class="danger-button" type="button" role="menuitem" data-git-action="discard">Discard unstaged</button><button class="danger-button" type="button" role="menuitem" data-git-action="delete_untracked">Delete untracked</button></div></div>
      </div>` : "";
    return `
      <div class="git-review-toolbar"><h3>Files changed <span>${files.length}</span></h3><div class="git-layout-toggle" role="group" aria-label="Diff layout"><button type="button" data-diff-layout="unified" aria-pressed="${diffLayout === "unified"}">Unified</button><button type="button" data-diff-layout="split" aria-pressed="${diffLayout === "split"}">Split</button></div></div>
      ${fileActions}
      <div class="git-review-layout" id="git-review-layout">
        <nav class="git-file-nav" id="git-file-nav" aria-label="Changed files">
          <div class="git-file-nav-heading"><b>Changed files</b><span>${files.length}</span></div>
          <div class="git-file-filter"><label for="git-file-filter">Filter files</label><input id="git-file-filter" type="search" placeholder="Search paths" autocomplete="off"></div>
          <div class="git-file-list">${fileButtons}</div>
          <p id="git-filter-empty" class="field-note" role="status" hidden>No matching paths. Clear the filter to see all files.</p>
        </nav>
        <div class="pane-resizer git-review-resizer" id="git-review-resizer" role="separator" aria-label="Resize changed files list" aria-controls="git-file-nav git-diff-list" aria-orientation="vertical" aria-valuemin="220" aria-valuemax="640" aria-valuenow="280" tabindex="0" title="Drag to resize. Use arrow keys for precise control; double-click to reset."></div>
        <div class="git-diff-list" id="git-diff-list">${diffs}</div>
      </div>
      <div class="git-file-context-menu" id="git-file-context-menu" role="menu" hidden><button type="button" role="menuitem" data-git-action="stage">Stage</button><button type="button" role="menuitem" data-git-action="unstage">Unstage</button>${files.some(file => file.status === "conflicted") ? '<button type="button" role="menuitem" data-git-action="resolve_ours">Use ours</button><button type="button" role="menuitem" data-git-action="resolve_theirs">Use theirs</button>' : ""}<button class="danger-button" type="button" role="menuitem" data-git-action="discard">Discard unstaged</button><button class="danger-button" type="button" role="menuitem" data-git-action="delete_untracked">Delete untracked</button></div>`;
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
    content.querySelectorAll(".git-file-row").forEach(row => row.addEventListener("contextmenu", event => {
      const selection = row.querySelector("[data-git-path-select]");
      if (!selection) return;
      event.preventDefault();
      if (!selection.checked) {
        content.querySelectorAll("[data-git-path-select]").forEach(input => { input.checked = false; });
        selection.checked = true;
        updateSelectedCount();
      }
      const menu = content.querySelector("#git-file-context-menu");
      menu.hidden = false;
      menu.style.left = `${Math.min(event.clientX, window.innerWidth - menu.offsetWidth - 8)}px`;
      menu.style.top = `${Math.min(event.clientY, window.innerHeight - menu.offsetHeight - 8)}px`;
      menu.querySelector("[role=menuitem]")?.focus({preventScroll: true});
    }));
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
    const actions = content.querySelector("#git-file-actions-toggle");
    if (actions) {
      actions.disabled = selected === 0;
      if (selected === 0) closeFileActionsMenu();
    }
  }

  function closeFileActionsMenu() {
    const menu = content.querySelector("#git-file-actions-menu");
    const toggle = content.querySelector("#git-file-actions-toggle");
    if (!menu || !toggle) return;
    menu.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
  }

  function closeFileContextMenu() {
    const menu = content.querySelector("#git-file-context-menu");
    if (menu) menu.hidden = true;
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
      repository: snapshot.repository,
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
    if (!workspace || actionRunning || messageGenerating) return;
    const viewRepository = repository.value;
    const selectedRepository = payload.repository || viewRepository;
    if (["stage", "unstage", "discard", "delete_untracked", "resolve_ours", "resolve_theirs"].includes(payload.action) && !payload.paths.length) {
      errorBox.textContent = "Select at least one changed file before running this action.";
      errorBox.focus({preventScroll: true});
      return;
    }
    const confirmation = confirmationFor(payload);
    if (confirmation) {
      document.querySelector("#git-confirm-title").textContent = actionLabels[payload.action];
      document.querySelector("#git-confirm-repository").textContent = `${workspace.name} - ${selectedRepository}`;
      document.querySelector("#git-confirm-message").textContent = confirmation + (payload.paths ? `\n${payload.paths.join("\n")}` : "");
      document.querySelector("#git-confirm-accept").textContent = actionLabels[payload.action];
      confirmDialog.returnValue = "cancel";
      const approved = new Promise(resolve => confirmDialog.addEventListener("close", () => resolve(confirmDialog.returnValue === "confirm"), {once: true}));
      confirmDialog.showModal();
      if (!await approved) return;
    }
    if (workspace.id !== window.Workspaces?.active()?.id || viewRepository !== repository.value || actionRunning) return;
    payload.workspace_id = workspace.id;
    payload.repository = selectedRepository;
    payload.confirmed = Boolean(confirmation);
    actionRunning = true;
    saveDrafts();
    errorBox.textContent = "";
    status.textContent = `${actionLabels[payload.action] || "Git operation"} in progress.`;
    content.inert = true;
    content.setAttribute("aria-busy", "true");
    refresh.disabled = true;
    controls.inert = true;
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
      controls.inert = false;
      refresh.disabled = false;
      content.inert = false;
      content.removeAttribute("aria-busy");
    }
  }

  async function generateCommitMessage(button) {
    const workspace = window.Workspaces?.active();
    if (!workspace || actionRunning || messageGenerating || !snapshot) return;
    const contextRequest = requestNumber;
    const message = content.querySelector("#git-commit-message");
    if (!message) return;
    messageGenerating = true;
    button.disabled = true;
    errorBox.textContent = "";
    status.textContent = "Generating a commit message from staged changes.";
    try {
      const response = await fetch("/api/git/commit-message", {
        method: "POST",
        headers: requestHeaders(),
        body: JSON.stringify({workspace_id: workspace.id, repository: repository.value}),
      });
      const result = await readJson(response, "Unable to generate a commit message.");
      if (contextRequest !== requestNumber || workspace.id !== window.Workspaces?.active()?.id) return;
      message.value = result.message;
      message.dispatchEvent(new Event("input", {bubbles: true}));
      status.textContent = "Generated a commit message. Review it before committing.";
      message.focus({preventScroll: true});
    } catch (error) {
      if (contextRequest !== requestNumber || workspace.id !== window.Workspaces?.active()?.id) return;
      errorBox.textContent = error.message;
      errorBox.focus({preventScroll: true});
      status.textContent = "Commit message generation failed. Follow the error message and try again.";
    } finally {
      messageGenerating = false;
      if (contextRequest === requestNumber) button.disabled = false;
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
    const actionsToggle = event.target.closest("#git-file-actions-toggle");
    if (actionsToggle) {
      const menu = content.querySelector("#git-file-actions-menu");
      const opening = menu.hidden;
      menu.hidden = !opening;
      actionsToggle.setAttribute("aria-expanded", String(opening));
      if (opening) menu.querySelector("[role=menuitem]")?.focus({preventScroll: true});
      return;
    }
    if (event.target.closest("#git-file-actions-menu [data-git-action]")) closeFileActionsMenu();
    if (event.target.closest("#git-file-context-menu [data-git-action]")) closeFileContextMenu();
    const generateButton = event.target.closest("[data-git-generate-commit-message]");
    if (generateButton) {
      generateCommitMessage(generateButton);
      return;
    }
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
    if (button.dataset.repository) payload.repository = button.dataset.repository;
    performAction(payload);
  });

  controls.addEventListener("submit", event => { event.preventDefault(); if (!actionRunning) loadSnapshot(); });
  document.addEventListener("pointerdown", event => {
    const menu = content.querySelector("#git-file-actions-menu");
    if (menu && !menu.hidden && !event.target.closest(".git-file-actions-dropdown")) closeFileActionsMenu();
    const contextMenu = content.querySelector("#git-file-context-menu");
    if (contextMenu && !contextMenu.hidden && !event.target.closest(".git-file-context-menu")) closeFileContextMenu();
  });
  document.addEventListener("keydown", event => {
    const menu = content.querySelector("#git-file-actions-menu");
    const contextMenu = content.querySelector("#git-file-context-menu");
    if (event.key !== "Escape") return;
    if (contextMenu && !contextMenu.hidden) {
      closeFileContextMenu();
      return;
    }
    if (menu && !menu.hidden) {
      closeFileActionsMenu();
      content.querySelector("#git-file-actions-toggle")?.focus({preventScroll: true});
    }
  });
  content.addEventListener("keydown", event => {
    if (!event.target.closest("#git-file-actions-menu")) return;
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const items = [...content.querySelectorAll('#git-file-actions-menu [role=menuitem]')];
    const current = items.indexOf(document.activeElement);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
      : (current + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
    items[next]?.focus({preventScroll: true});
  });
  controls.addEventListener("change", event => {
    if (event.target.name !== "git-comparison") return;
    baseField.hidden = comparison() !== "branch";
    loadSnapshot();
  });
  refresh.addEventListener("click", () => {
    if (actionRunning) return;
    repositoryOptions = [];
    if (!snapshot) selectedLogRef = "";
    loadSnapshot();
  });

  repositorySelect.addEventListener("change", () => {
    if (actionRunning) return;
    saveDrafts();
    renderedWorkspace = null;
    repository.value = repositorySelect.value;
    selectedLogRef = "";
    selectedLogRepository = "";
    allLogRepositories = false;
    snapshot = null;
    baseInput.value = "";
    document.querySelector("#git-output").hidden = true;
    loadSnapshot();
  });

  controls.querySelectorAll("[data-git-view]").forEach(button => {
    button.addEventListener("click", () => {
      if (actionRunning || activeView === button.dataset.gitView) return;
      activeView = button.dataset.gitView;
      controls.querySelectorAll("[data-git-view]").forEach(item => {
        item.setAttribute("aria-selected", String(item === button));
        item.tabIndex = item === button ? 0 : -1;
      });
      content.setAttribute("aria-labelledby", button.id);
      controls.querySelector(".git-comparison").hidden = activeView === "log";
      baseField.hidden = activeView === "log" || comparison() !== "branch";
      loadSnapshot();
    });
    button.addEventListener("keydown", event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const tabs = [...controls.querySelectorAll("[data-git-view]")];
      const other = tabs.find(item => item !== button);
      const target = event.key === "Home" ? tabs[0] : event.key === "End" ? tabs.at(-1) : other;
      target.focus();
      target.click();
    });
  });

  function workspaceChanged() {
    commitRequestNumber++;
    commitPatchRequestNumber++;
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
    selectedLogRef = "";
    selectedLogRepository = "";
    allLogRepositories = true;
    for (const key of Object.keys(logFilters)) logFilters[key] = "";
    repositoryOptions = [];
    repositorySelect.innerHTML = "";
    baseInput.value = workspace?.git_base_branch || "";
    branches.innerHTML = "";
    content.innerHTML = '<div class="git-empty"><b>Select a workspace to manage its repository</b></div>';
    if (location.hash === "#git") loadSnapshot();
  }

  function openGitView() {
    if (location.hash === "#git" && !snapshot) loadSnapshot();
  }

  window.GitAgentContext = {get: () => {
    const row = content.querySelector(".git-log-row.selected");
    return {surface: "git", repository: row?.dataset.gitRepository || repository.value,
      commit: row?.dataset.gitCommit || null, ref: selectedLogRef || null,
      comparison: activeView === "changes" ? comparison() : "log",
      selected_paths: selectedPaths().slice(0, 500)};
  }};
  window.addEventListener("workspacechange", workspaceChanged);
  window.addEventListener("gitworktreechange", event => {
    if (event.detail !== window.Workspaces?.active()?.id) return;
    snapshot = null;
    patches.clear();
    if (location.hash === "#git" && !actionRunning) loadSnapshot();
  });
  window.addEventListener("hashchange", openGitView);
  workspaceChanged();
})();
