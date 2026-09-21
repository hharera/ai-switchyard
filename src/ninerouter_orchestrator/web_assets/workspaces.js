(() => {
  const editor = document.querySelector("#workspace-form");
  const error = document.querySelector("#workspace-error");
  const status = document.querySelector("#workspace-status");
  const save = document.querySelector("#save-workspace");
  const folderDialog = document.querySelector("#folder-browser");
  const folderPath = document.querySelector("#folder-browser-path");
  const folderList = document.querySelector("#folder-browser-list");
  const folderStatus = document.querySelector("#folder-browser-status");
  const folderError = document.querySelector("#folder-browser-error");
  const folderUp = document.querySelector("#folder-browser-up");
  const folderSelect = document.querySelector("#folder-browser-select");
  const folderSearch = document.querySelector("#folder-browser-search");
  const folderClearSearch = document.querySelector("#folder-browser-clear-search");
  const field = name => document.querySelector(`#workspace-${name}`);
  let config = {workspaces: [], default_workspace_id: null};
  let activeId = null;
  let editingId = null;
  let ready = false;
  let saving = false;
  let browsing = false;
  let browseRequest = null;
  let browsedPath = "";
  let parentPath = null;
  let currentFolders = [];
  const localDelivery = {mode: "none", remote: "origin", branch: "orchestrator/{run_id}", base_branch: "main", draft: true};

  const active = () => config.workspaces.find(item => item.id === activeId) || null;
  const message = body => Array.isArray(body.detail) ? body.detail.map(item => item.msg).join("; ") : body.detail;
  const stored = key => { try { return localStorage.getItem(key); } catch { return null; } };

  function renderFolders() {
    const query = folderSearch.value.trim().toLocaleLowerCase();
    const folders = currentFolders.filter(folder => folder.name.toLocaleLowerCase().includes(query));
    folderClearSearch.disabled = !folderSearch.value;
    folderStatus.textContent = query
      ? `${folders.length} of ${currentFolders.length} subfolders match`
      : `${currentFolders.length} subfolder${currentFolders.length === 1 ? "" : "s"}`;
    folderList.replaceChildren();
    folderList.scrollTop = 0;
    if (!folders.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.innerHTML = query
        ? "<b>No matching subfolders</b><span>Try a different name or clear the search.</span>"
        : "<b>No subfolders</b><span>You can use the current folder or move to its parent.</span>";
      folderList.append(empty);
      return;
    }
    folders.forEach(folder => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = folder.name;
      button.dataset.folderPath = folder.path;
      folderList.append(button);
    });
  }

  async function browseFolder(path = "") {
    if (browsing) return;
    browsing = true;
    const controller = new AbortController();
    browseRequest = controller;
    const timeout = setTimeout(() => controller.abort(), 10000);
    browsedPath = "";
    parentPath = null;
    currentFolders = [];
    folderSearch.value = "";
    folderSearch.disabled = true;
    folderPath.value = path;
    folderList.replaceChildren();
    folderError.textContent = "";
    folderPath.removeAttribute("aria-invalid");
    folderStatus.textContent = "Loading folders...";
    folderDialog.querySelectorAll("button:not(#folder-browser-cancel)").forEach(control => { control.disabled = true; });
    try {
      const response = await fetch("/api/repository/folders", {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-Switchyard-Client": "local-ui"},
        body: JSON.stringify({path}),
        signal: controller.signal,
      });
      const body = await response.json();
      if (browseRequest !== controller) return;
      if (!response.ok) throw new Error(message(body) || "Cannot read this folder.");
      browsedPath = body.path;
      parentPath = body.parent;
      folderPath.value = body.path;
      currentFolders = body.folders;
      renderFolders();
    } catch (failure) {
      if (browseRequest !== controller) return;
      folderError.textContent = controller.signal.aborted ? "Loading timed out. Try again or enter a different path." : failure.message;
      folderPath.setAttribute("aria-invalid", "true");
      folderStatus.textContent = "";
      folderPath.focus();
    } finally {
      clearTimeout(timeout);
      if (browseRequest === controller) {
        browsing = false;
        browseRequest = null;
        folderDialog.querySelectorAll("button").forEach(control => { control.disabled = false; });
        folderUp.disabled = !parentPath;
        folderSelect.disabled = !browsedPath;
        folderSearch.disabled = !browsedPath;
        folderClearSearch.disabled = !browsedPath || !folderSearch.value;
        if (folderDialog.open) folderPath.focus();
      }
    }
  }

  function workflowOptions(value) {
    const options = savedWorkflowConfig?.workflows || [];
    return `<option value="">Without a workflow - built-in steps</option>${value && !options.some(item => item.id === value) ? `<option value="${escapeHtml(value)}">${escapeHtml(value)} (unavailable)</option>` : ""}${options.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("")}`;
  }

  function workflowsChanged() {
    const selected = field("workflow").value;
    field("workflow").innerHTML = workflowOptions(selected);
    field("workflow").value = selected;
    save.disabled = !ready || saving || !savedWorkflowConfig;
  }

  function setDispatchWorkflow(id) {
    id = id || "";
    const select = document.querySelector("#dispatch-workflow");
    if (!Array.from(select.options).some(option => option.value === id)) {
      select.add(new Option(`${id} (unavailable)`, id));
    }
    select.value = id;
    updateDispatchWorkflowLabel();
  }

  function selectWorkspace(id) {
    activeId = config.workspaces.some(item => item.id === id) ? id : config.default_workspace_id;
    const workspace = active();
    document.querySelectorAll("[data-workspace-selector]").forEach(select => {
      select.innerHTML = config.workspaces.length
        ? config.workspaces.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("")
        : '<option value="">Add a workspace first</option>';
      select.value = activeId || "";
      select.disabled = !ready || !config.workspaces.length;
    });
    try { localStorage.setItem("switchyard.workspace", activeId || ""); } catch { /* Selection still works without storage. */ }
    repositoryInput.value = workspace?.repository || "";
    document.querySelector("#active-workspace-path").textContent = workspace?.repository || "No workspace selected. Add one in Workspaces.";
    document.querySelector("#dispatch-workspace-settings").textContent = workspace
      ? `${workspace.repository} | ${workspace.forks_per_ticket} attempts per ticket | ${workspace.command_timeout_seconds}s command timeout`
      : "Add a workspace before dispatching.";
    document.querySelector("#allow-host").checked = false;
    if (workspace) {
      setDispatchWorkflow(workspace.workflow_id);
      renderDelivery("dispatch", workspace.delivery);
    } else {
      renderDelivery("dispatch", localDelivery);
    }
    submitButton.disabled = !workspace;
    closeRunDrawer();
    jobs = [];
    document.querySelector("#run-list").innerHTML = '<div class="empty-state"><b>Loading run history</b></div>';
    loadJobs();
    window.dispatchEvent(new CustomEvent("workspacechange", {detail: workspace}));
    renderList();
  }

  function renderList() {
    document.querySelector("#workspace-list").innerHTML = config.workspaces.length
      ? config.workspaces.map(item => `<article class="workspace-card ${item.id === activeId ? "selected" : ""}"><h3>${escapeHtml(item.name)}</h3><p>${escapeHtml(item.repository)}</p><small>${item.forks_per_ticket} attempts / ${item.command_timeout_seconds}s${item.id === config.default_workspace_id ? " / Default" : ""}</small><div><button type="button" class="secondary-button" data-use-workspace="${escapeHtml(item.id)}" ${item.id === activeId ? "disabled" : ""}>${item.id === activeId ? "Active workspace" : "Use workspace"}</button><button type="button" class="secondary-button" data-edit-workspace="${escapeHtml(item.id)}">Edit</button></div></article>`).join("")
      : '<div class="empty-state"><b>No saved workspaces</b><span>Add a repository and its run defaults. Repository files are never moved or deleted.</span></div>';
  }

  function editWorkspace(id = null) {
    editingId = id;
    const workspace = config.workspaces.find(item => item.id === id);
    field("editor-title").textContent = workspace ? `Edit ${workspace.name}` : "Add workspace";
    field("name").value = workspace?.name || "";
    field("repository").value = workspace?.repository || (!config.workspaces.length ? stored("switchyard.repository") || "" : "");
    const workflow = workspace ? workspace.workflow_id || "" : savedWorkflowConfig?.default_workflow_id || "";
    field("workflow").innerHTML = workflowOptions(workflow);
    field("workflow").value = workflow;
    field("forks").value = workspace?.forks_per_ticket || 3;
    field("timeout").value = workspace?.command_timeout_seconds || 1800;
    field("git-base").value = workspace?.git_base_branch || "main";
    field("default").checked = workspace ? config.default_workspace_id === id : !config.workspaces.length;
    renderDelivery("workspace", workspace?.delivery || localDelivery);
    document.querySelector("#remove-workspace").hidden = !workspace;
    error.textContent = "";
    status.textContent = workspace ? "Saved settings loaded. Edits apply only after saving." : "Save this workspace to use it in Dispatch, Git, and Runs.";
  }

  async function persist(candidate, selectedId) {
    saving = true;
    editor.querySelectorAll("button").forEach(button => { button.disabled = true; });
    document.querySelector("#new-workspace").disabled = true;
    error.textContent = "";
    try {
      const response = await fetch("/api/workspaces", {method: "PUT", headers: {"Content-Type": "application/json", "X-Switchyard-Client": "local-ui"}, body: JSON.stringify(candidate)});
      const body = await response.json();
      if (!response.ok) throw new Error(message(body) || "Unable to save workspaces. Retry.");
      config = body;
      selectWorkspace(selectedId);
      editWorkspace(config.workspaces.some(item => item.id === selectedId) ? selectedId : null);
      status.textContent = "Workspace settings saved. Existing runs keep their original configuration.";
    } catch (failure) { error.textContent = failure.message; }
    finally {
      saving = false;
      editor.querySelectorAll("button").forEach(button => { button.disabled = false; });
      document.querySelector("#new-workspace").disabled = false;
      workflowsChanged();
    }
  }

  editor.addEventListener("submit", event => {
    event.preventDefault();
    if (!ready || saving) return;
    const id = editingId || `workspace-${crypto.randomUUID()}`;
    const workspace = {
      id, name: field("name").value.trim(), repository: field("repository").value.trim(),
      workflow_id: field("workflow").value || null,
      forks_per_ticket: Number(field("forks").value), command_timeout_seconds: Number(field("timeout").value),
      git_base_branch: field("git-base").value.trim(), delivery: readDelivery("workspace"),
    };
    const candidate = structuredClone(config);
    const index = candidate.workspaces.findIndex(item => item.id === id);
    if (index < 0) candidate.workspaces.push(workspace); else candidate.workspaces[index] = workspace;
    if (field("default").checked || !candidate.default_workspace_id) candidate.default_workspace_id = id;
    else if (candidate.default_workspace_id === id) candidate.default_workspace_id = candidate.workspaces.find(item => item.id !== id)?.id || id;
    persist(candidate, id);
  });
  editor.addEventListener("input", () => { status.textContent = "Unsaved workspace changes"; });
  document.querySelector("#new-workspace").addEventListener("click", () => { editWorkspace(); field("name").focus(); });
  document.querySelector("#cancel-workspace").addEventListener("click", () => editWorkspace(editingId));
  document.querySelector("#copy-workspace-delivery").addEventListener("click", () => {
    if (!deliveryDefaults) { error.textContent = "Global delivery defaults are unavailable. Reload or configure delivery below."; return; }
    renderDelivery("workspace", deliveryDefaults);
    status.textContent = "Global delivery defaults copied. Save the workspace to apply them.";
  });
  document.querySelector("#workspace-list").addEventListener("click", event => {
    if (saving) return;
    const edit = event.target.closest("[data-edit-workspace]");
    const use = event.target.closest("[data-use-workspace]");
    if (edit) { editWorkspace(edit.dataset.editWorkspace); field("name").focus(); }
    if (use) selectWorkspace(use.dataset.useWorkspace);
  });
  document.querySelectorAll("[data-workspace-selector]").forEach(select => select.addEventListener("change", () => selectWorkspace(select.value)));
  document.querySelector("#remove-workspace").addEventListener("click", async () => {
    if (!editingId || saving || !await ThemeControls.confirm({
      title: "Remove workspace?",
      message: "The saved workspace will be removed. Repository files and run history will be kept.",
      confirmLabel: "Remove workspace",
    })) return;
    const candidate = structuredClone(config);
    candidate.workspaces = candidate.workspaces.filter(item => item.id !== editingId);
    if (candidate.default_workspace_id === editingId) candidate.default_workspace_id = candidate.workspaces[0]?.id || null;
    persist(candidate, activeId === editingId ? candidate.default_workspace_id : activeId);
  });
  document.querySelector("#browse-workspace").addEventListener("click", () => {
    browsedPath = "";
    parentPath = null;
    folderList.replaceChildren();
    folderError.textContent = "";
    folderDialog.showModal();
    browseFolder(field("repository").value.trim());
  });
  document.querySelector("#folder-browser-form").addEventListener("submit", event => {
    event.preventDefault();
    browseFolder(folderPath.value.trim());
  });
  document.querySelector("#folder-browser-home").addEventListener("click", () => browseFolder());
  folderUp.addEventListener("click", () => parentPath && browseFolder(parentPath));
  folderSearch.addEventListener("input", renderFolders);
  folderClearSearch.addEventListener("click", () => {
    folderSearch.value = "";
    renderFolders();
    folderSearch.focus();
  });
  folderList.addEventListener("click", event => {
    const folder = event.target.closest("[data-folder-path]");
    if (folder) browseFolder(folder.dataset.folderPath);
  });
  folderSelect.addEventListener("click", () => {
    if (!browsedPath) return;
    field("repository").value = browsedPath;
    error.textContent = "";
    status.textContent = "Folder selected. Save to keep this workspace.";
    folderDialog.close();
  });
  document.querySelector("#folder-browser-cancel").addEventListener("click", () => folderDialog.close());
  folderDialog.addEventListener("close", () => {
    browseRequest?.abort();
    browseRequest = null;
    browsing = false;
    document.querySelector("#browse-workspace").focus();
  });

  window.Workspaces = {active, workflowsChanged};
  renderDelivery("dispatch", localDelivery);
  editWorkspace();
  submitButton.disabled = true;
  (async () => {
    try {
      const response = await fetch("/api/workspaces");
      const body = await response.json();
      if (!response.ok) throw new Error(message(body) || "Unable to load workspaces. Reload to retry.");
      config = body; ready = true;
      selectWorkspace(stored("switchyard.workspace") || config.default_workspace_id);
      editWorkspace(activeId);
      workflowsChanged();
      const recent = await fetch("/api/jobs").then(response => response.ok ? response.json() : []);
      const paths = [...new Set([stored("switchyard.repository"), ...recent.map(item => item.repository)].filter(Boolean))];
      field("recent-paths").innerHTML = paths.map(path => `<option value="${escapeHtml(path)}"></option>`).join("");
    } catch (failure) { error.textContent = failure.message; document.querySelector("#active-workspace-path").textContent = "Workspaces could not be loaded. Reload to retry."; }
  })();
})();
