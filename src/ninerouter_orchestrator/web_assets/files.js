(() => {
  const panel = document.querySelector("#files-panel");
  if (!panel) return;

  const tree = document.querySelector("#files-tree");
  const filter = document.querySelector("#files-filter");
  const tabsElement = document.querySelector("#files-tabs");
  const editorPanel = document.querySelector("#files-editor-panel");
  const editor = document.querySelector("#files-content");
  const gutter = document.querySelector("#files-gutter");
  const highlighted = document.querySelector("#files-highlight");
  const empty = document.querySelector("#files-empty");
  const breadcrumbs = document.querySelector("#files-breadcrumbs");
  const error = document.querySelector("#files-error");
  const status = document.querySelector("#files-status");
  const position = document.querySelector("#files-position");
  const format = document.querySelector("#files-format");
  const saveButton = document.querySelector("#files-save");
  const refreshButton = document.querySelector("#files-refresh");
  const gitActionsButton = document.querySelector("#files-git-actions");
  const gitSummary = document.querySelector("#files-git-summary");
  const gitMenu = document.querySelector("#files-git-menu");
  const gitDiffDialog = document.querySelector("#files-git-diff");
  const gitDiffPath = document.querySelector("#files-git-diff-path");
  const gitDiffBody = document.querySelector("#files-git-diff-body");
  const entryDialog = document.querySelector("#files-entry-dialog");
  const entryForm = document.querySelector("#files-entry-form");
  const entryName = document.querySelector("#files-entry-name");
  const entryError = document.querySelector("#files-entry-error");
  const project = document.querySelector("#files-project");
  const workbench = document.querySelector("#files-workbench");
  const projectToggle = document.querySelector("#files-toggle-project");
  const findBar = document.querySelector("#files-find-bar");
  const findInput = document.querySelector("#files-find");
  const findResult = document.querySelector("#files-find-result");
  const sessions = new Map();
  let workspace = null;
  let loadedEntries = 0;
  let treeGeneration = 0;
  let openGeneration = 0;
  let selectedTreeItem = null;
  let gitFiles = [];
  let gitIndex = FileTreeGit.index([]);
  let gitAvailable = false;
  let gitActionRunning = false;
  let gitMenuItem = null;
  let gitMenuTrigger = null;
  let diffGeneration = 0;
  let resolveEntryName = null;
  let gitStatusGeneration = 0;
  const sessionKey = item => item ? `${item.id}:${item.repository}` : "";

  const message = body => Array.isArray(body.detail)
    ? body.detail.map(item => item.msg).join("; ")
    : body.detail;
  const session = () => sessions.get(sessionKey(workspace));
  const activeFile = () => session()?.tabs.get(session().activePath) || null;

  function setError(text = "") {
    error.textContent = text;
    error.hidden = !text;
  }

  function setStatus(text) {
    status.textContent = text;
  }

  function fileLabel(name) {
    const special = {"dockerfile": "DK", "makefile": "MK", "license": "TXT"};
    const extension = name.includes(".") ? name.split(".").pop() : "";
    return special[name.toLowerCase()] || extension.slice(0, 3).toUpperCase() || "TXT";
  }

  function folderIcon() {
    const holder = document.createElement("span");
    holder.className = "files-file-icon folder";
    holder.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 6.5h6l2 2h9v9h-17z"/></svg>';
    return holder;
  }

  function fileIcon(name) {
    const holder = document.createElement("span");
    holder.className = "files-file-icon";
    holder.textContent = fileLabel(name);
    return holder;
  }

  async function request(url, options) {
    const response = await fetch(url, {
      ...options,
      signal: AbortSignal.timeout(15000),
      headers: {"X-Switchyard-Client": "local-ui", ...(options?.headers || {})},
    });
    const body = await response.json();
    if (!response.ok) throw new Error(message(body) || "Unable to load workspace files.");
    return body;
  }

  function filesForItem(item = selectedTreeItem) {
    return item ? gitIndex.filesFor(item.path || ".") : [];
  }

  function selectTreeRow(row) {
    document.querySelectorAll(".files-tree-row").forEach(item => item.classList.toggle("is-selected", item === row));
    selectedTreeItem = row ? {
      path: row.dataset.path, type: row.dataset.type, name: row.dataset.label,
      missing: row.dataset.missing === "true",
    } : null;
    gitActionsButton.disabled = !workspace || gitActionRunning;
  }

  function decorateTreeRow(row) {
    row.querySelector(".files-git-badge")?.remove();
    row.querySelector(".files-git-lines")?.remove();
    row.classList.remove("has-git-change");
    [...row.classList].filter(name => name.startsWith("git-status-")).forEach(name => row.classList.remove(name));
    const detail = FileTreeGit.badge(gitIndex.filesFor(row.dataset.path), row.dataset.type === "folder");
    if (!detail) return;
    const badge = document.createElement("span");
    badge.className = `files-git-badge ${detail.state.replaceAll(" ", "-")}`;
    badge.textContent = detail.text;
    badge.title = detail.title;
    badge.setAttribute("aria-label", detail.title);
    row.classList.add("has-git-change", `git-status-${detail.state.replaceAll(" ", "-")}`);
    row.append(badge);
    const lines = document.createElement("span");
    lines.className = "files-git-lines";
    lines.title = "Saved changes vs HEAD; excludes unsaved editor changes";
    if (detail.binary) lines.textContent = "Binary";
    else {
      const added = document.createElement("span");
      added.className = "files-lines-added";
      added.textContent = `+${detail.additions}`;
      const deleted = document.createElement("span");
      deleted.className = "files-lines-deleted";
      deleted.textContent = `-${detail.deletions}`;
      lines.append(added, deleted);
      if (detail.incomplete) lines.append(" (partial)");
    }
    lines.setAttribute("aria-label", detail.binary ? "Binary file" : `${detail.additions} added lines, ${detail.deletions} deleted lines${detail.incomplete ? ', partial counts' : ''}`);
    row.append(lines);
  }

  function updateGitDecorations() {
    document.querySelectorAll(".files-tree-row").forEach(decorateTreeRow);
    gitActionsButton.disabled = !workspace || gitActionRunning;
  }

  async function loadGitStatus(generation = treeGeneration) {
    const statusGeneration = ++gitStatusGeneration;
    gitSummary.textContent = "Reading Git…";
    gitSummary.title = "";
    try {
      const body = await request(`/api/workspace/git-status?${new URLSearchParams({workspace_id: workspace.id})}`);
      if (generation !== treeGeneration || statusGeneration !== gitStatusGeneration) return;
      gitFiles = body.files;
      gitIndex = FileTreeGit.index(gitFiles);
      gitAvailable = body.repositories.length > 0;
      const changed = body.summary.changed;
      const errors = body.repository_errors || [];
      gitSummary.textContent = !gitAvailable ? (errors.length ? "Git unavailable" : "No Git repositories") : changed ? `${changed} changed · +${body.summary.additions} -${body.summary.deletions}${body.summary.stats_incomplete ? " (partial)" : ""}` : "Git clean";
      if (errors.length && gitAvailable) gitSummary.textContent += " · partial status";
      gitSummary.title = `${body.repositories.length} repositories; ${body.summary.staged} staged, ${body.summary.unstaged} unstaged, ${body.summary.untracked} untracked, ${body.summary.conflicted} conflicted. Saved changes vs HEAD.\n${body.repositories.map(repo => `${repo.relative_path}: ${repo.branch || "Detached HEAD"}`).join("\n")}\n${errors.map(item => `${item.repository}: ${item.message}`).join("\n")}`;
    } catch (failure) {
      if (generation !== treeGeneration || statusGeneration !== gitStatusGeneration) return;
      gitFiles = [];
      gitIndex = FileTreeGit.index([]);
      gitAvailable = false;
      gitSummary.textContent = "Git unavailable";
      gitSummary.title = failure.message;
    }
    updateGitDecorations();
  }

  function createTreeRow(item, depth) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "files-tree-row";
    row.style.setProperty("--depth", depth);
    row.dataset.path = item.path;
    row.dataset.name = item.name.toLocaleLowerCase();
    row.dataset.type = item.type;
    row.dataset.label = item.name;
    row.dataset.missing = String(Boolean(item.missing));
    row.title = item.path;
    row.classList.toggle("is-missing", Boolean(item.missing));
    if (selectedTreeItem?.path === item.path) row.classList.add("is-selected");
    if (item.type === "folder") row.setAttribute("aria-expanded", "false");
    const arrow = document.createElement("span");
    arrow.className = "files-tree-arrow";
    arrow.textContent = item.type === "folder" ? "›" : "";
    row.append(arrow, item.type === "folder" ? folderIcon() : fileIcon(item.name));
    const name = document.createElement("span");
    name.className = "files-tree-name";
    name.textContent = item.name;
    row.append(name);
    decorateTreeRow(row);
    row.addEventListener("contextmenu", event => {
      event.preventDefault();
      selectTreeRow(row);
      openGitMenu({anchor: row, x: event.clientX, y: event.clientY});
    });
    row.addEventListener("keydown", event => {
      if (event.key === "ContextMenu" || event.shiftKey && event.key === "F10") {
        event.preventDefault();
        selectTreeRow(row);
        openGitMenu({anchor: row});
      }
    });
    return row;
  }

  function renderEntries(entries, depth) {
    const list = document.createElement("ul");
    entries.forEach(item => {
      loadedEntries += 1;
      const entry = document.createElement("li");
      const row = createTreeRow(item, depth);
      entry.append(row);
      if (item.type === "folder") {
        const children = document.createElement("ul");
        children.hidden = true;
        entry.append(children);
        row.expandFolder = async (opening = true) => {
          const generation = treeGeneration;
          if (opening && !row.dataset.loaded) {
            row.disabled = true;
            row.querySelector(".files-tree-arrow").textContent = "…";
            try {
              const body = item.missing
                ? {entries: []}
                : await request(`/api/workspace/files?${new URLSearchParams({workspace_id: workspace.id, path: item.path})}`);
              if (generation !== treeGeneration) return;
              const rendered = renderEntries(gitIndex.entries(body.entries, item.path), depth + 1);
              children.replaceChildren(...rendered.childNodes);
              row.dataset.loaded = "true";
              document.querySelector("#files-tree-count").textContent = `${loadedEntries} loaded`;
            } catch (failure) {
              if (generation !== treeGeneration) return;
              setError(failure.message);
              row.querySelector(".files-tree-arrow").textContent = "›";
              return;
            } finally {
              row.disabled = false;
            }
          }
          row.setAttribute("aria-expanded", String(opening));
          row.querySelector(".files-tree-arrow").textContent = opening ? "⌄" : "›";
          children.hidden = !opening;
          applyFilter();
        };
        row.addEventListener("click", () => {
          selectTreeRow(row);
          row.expandFolder(row.getAttribute("aria-expanded") !== "true");
        });
      } else {
        row.addEventListener("click", () => {
          selectTreeRow(row);
          if (item.missing) {
            setStatus(`${item.path} is deleted from disk. Use Show changes or a Git action.`);
            return;
          }
          openFile(item.path);
        });
      }
      list.append(entry);
    });
    return list;
  }

  async function loadTree() {
    if (!workspace) return;
    const expanded = [...tree.querySelectorAll('.files-tree-row[aria-expanded="true"]')].map(row => row.dataset.path);
    // Keep the selected item's ancestors visible after creating or renaming a descendant.
    if (selectedTreeItem?.path) {
      let parent = parentPath(selectedTreeItem.path);
      while (parent) { expanded.push(parent); parent = parentPath(parent); }
    }
    const generation = ++treeGeneration;
    tree.setAttribute("aria-busy", "true");
    tree.innerHTML = '<p class="files-tree-message">Indexing the project root…</p>';
    filter.disabled = true;
    refreshButton.disabled = true;
    setError();
    loadedEntries = 0;
    try {
      const [body] = await Promise.all([
        request(`/api/workspace/files?${new URLSearchParams({workspace_id: workspace.id})}`),
        loadGitStatus(generation),
      ]);
      if (generation !== treeGeneration) return;
      const entries = gitIndex.entries(body.entries);
      const list = renderEntries(entries, 0);
      if (!entries.length) {
        tree.innerHTML = '<p class="files-tree-message">This workspace folder is empty.</p>';
      } else {
        tree.replaceChildren(list);
      }
      for (const path of [...new Set(expanded)].sort((a, b) => a.split("/").length - b.split("/").length)) {
        if (generation !== treeGeneration) return;
        const row = [...tree.querySelectorAll(".files-tree-row")].find(item => item.dataset.path === path);
        if (row?.expandFolder) await row.expandFolder();
      }
      if (generation !== treeGeneration) return;
      const selected = [...tree.querySelectorAll(".files-tree-row")].find(row => row.dataset.path === selectedTreeItem?.path);
      if (selected) selectTreeRow(selected);
      else if (selectedTreeItem) selectTreeRow(null);
      document.querySelector("#files-tree-count").textContent = `${loadedEntries} loaded`;
      filter.disabled = false;
      applyFilter();
      updateGitDecorations();
      setStatus("Project tree refreshed.");
    } catch (failure) {
      if (generation !== treeGeneration) return;
      tree.innerHTML = '<p class="files-tree-message">Unable to load the project tree.</p>';
      setError(failure.message);
      setStatus("Project tree unavailable.");
    } finally {
      if (generation === treeGeneration) {
        tree.setAttribute("aria-busy", "false");
        refreshButton.disabled = !workspace;
      }
    }
  }

  function filterList(list, query) {
    let matched = false;
    [...list.children].forEach(item => {
      const row = item.querySelector(":scope > .files-tree-row");
      const children = item.querySelector(":scope > ul");
      const descendant = children ? filterList(children, query) : false;
      const own = !query || row.dataset.name.includes(query);
      item.hidden = !own && !descendant;
      if (children) children.hidden = query ? !descendant : row.getAttribute("aria-expanded") !== "true";
      matched ||= own || descendant;
    });
    return matched;
  }

  function applyFilter() {
    const list = tree.querySelector(":scope > ul");
    if (list) filterList(list, filter.value.trim().toLocaleLowerCase());
  }

  function closeGitMenu(restoreFocus = false) {
    gitMenu.hidden = true;
    gitActionsButton.setAttribute("aria-expanded", "false");
    if (gitMenuTrigger?.hasAttribute?.("aria-haspopup")) gitMenuTrigger.setAttribute("aria-expanded", "false");
    if (restoreFocus) (gitMenuTrigger?.isConnected ? gitMenuTrigger : gitActionsButton).focus();
  }

  function openGitMenu({anchor = gitActionsButton, x, y} = {}) {
    if (!workspace || gitActionRunning) return;
    gitMenuItem = selectedTreeItem ? {...selectedTreeItem} : {
      path: "", type: "folder", name: workspace.name, missing: false,
    };
    gitMenuTrigger = anchor;
    const files = filesForItem(gitMenuItem);
    document.querySelector("#files-git-menu-name").textContent = gitMenuItem.path || workspace.name;
    document.querySelector("#files-git-menu-detail").textContent =
      gitAvailable
        ? `${files.length} changed ${files.length === 1 ? "file" : "files"}${gitMenuItem.type === "folder" ? " · all descendants" : ""}`
        : "File actions · Git unavailable";
    gitMenu.querySelector('[data-files-entry-action="create_file"]').disabled = gitMenuItem.missing;
    gitMenu.querySelector('[data-files-entry-action="create_folder"]').disabled = gitMenuItem.missing;
    gitMenu.querySelector('[data-files-entry-action="rename"]').disabled = !gitMenuItem.path || gitMenuItem.missing;
    gitMenu.querySelector('[data-files-entry-action="delete"]').disabled = !gitMenuItem.path || gitMenuItem.missing;
    gitMenu.querySelector("[data-files-open-in-manager]").disabled = gitMenuItem.missing;
    gitMenu.querySelectorAll("[data-files-git-action]").forEach(button => {
      const action = button.dataset.filesGitAction;
      const count = FileTreeGit.eligible(files, action).length;
      button.disabled = !gitAvailable || !count;
      const label = button.querySelector("small");
      if (label) label.textContent = String(count);
    });
    gitMenu.querySelector('a[href="#git"]').hidden = !gitAvailable;
    gitMenu.hidden = false;
    gitActionsButton.setAttribute("aria-expanded", "true");
    if (anchor.hasAttribute?.("aria-haspopup")) anchor.setAttribute("aria-expanded", "true");
    const bounds = anchor.getBoundingClientRect();
    gitMenu.style.left = `${Math.max(8, Math.min(x ?? bounds.left, innerWidth - gitMenu.offsetWidth - 8))}px`;
    gitMenu.style.top = `${Math.max(8, Math.min(y ?? bounds.bottom, innerHeight - gitMenu.offsetHeight - 8))}px`;
    gitMenu.querySelector('[role="menuitem"]:not(:disabled)')?.focus();
  }

  function parentPath(path) {
    const parts = path.split("/");
    parts.pop();
    return parts.join("/");
  }

  async function openInFileManager(item) {
    if (!workspace || item.missing) return;
    const currentWorkspace = workspace;
    try {
      await request("/api/workspace/open-in-file-manager", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({workspace_id: currentWorkspace.id, path: item.path}),
      });
      if (workspace === currentWorkspace) setStatus(`Requested Files for ${item.path || currentWorkspace.name}.`);
    } catch (failure) {
      if (workspace === currentWorkspace) {
        setError(failure.message);
        setStatus("Unable to open the file manager.");
      }
    }
  }

  function itemContains(item, path) {
    return path === item.path || item.type === "folder" && (!item.path || path.startsWith(`${item.path}/`));
  }

  function askEntryName(action, item) {
    const creating = action.startsWith("create_");
    const folder = action === "create_folder";
    const title = creating ? `Create ${folder ? "folder" : "file"}` : "Rename";
    const location = creating
      ? (item.type === "folder" ? item.path : parentPath(item.path))
      : parentPath(item.path);
    document.querySelector("#files-entry-dialog-title").textContent = title;
    document.querySelector("#files-entry-dialog-location").textContent =
      `${creating ? "Location" : "Current item"}: ${location || workspace.name}`;
    document.querySelector("#files-entry-submit").textContent = title;
    entryName.value = creating ? (folder ? "new-folder" : "untitled.txt") : item.name;
    entryName.removeAttribute("aria-invalid");
    entryError.textContent = "";
    entryDialog.showModal();
    requestAnimationFrame(() => {
      entryName.focus();
      const dot = creating && !folder ? entryName.value.lastIndexOf(".") : -1;
      entryName.setSelectionRange(0, dot > 0 ? dot : entryName.value.length);
    });
    return new Promise(resolve => { resolveEntryName = resolve; });
  }

  function updateSessionPaths(oldPath, newPath, type, currentSession = session()) {
    const updated = new Map();
    currentSession.tabs.forEach((file, path) => {
      const affected = path === oldPath || type === "folder" && path.startsWith(`${oldPath}/`);
      if (!affected) {
        updated.set(path, file);
        return;
      }
      const nextPath = `${newPath}${path.slice(oldPath.length)}`;
      updated.set(nextPath, {...file, path: nextPath, name: nextPath.split("/").pop()});
    });
    currentSession.tabs = updated;
    if (currentSession.activePath === oldPath || type === "folder" && currentSession.activePath?.startsWith(`${oldPath}/`)) {
      currentSession.activePath = `${newPath}${currentSession.activePath.slice(oldPath.length)}`;
    }
  }

  async function performEntryAction(action, item) {
    if (!workspace || gitActionRunning) return;
    const currentWorkspace = workspace;
    const currentSession = session();
    const current = () => workspace === currentWorkspace && session() === currentSession;
    const openInScope = () => [...currentSession.tabs.values()].filter(file => itemContains(item, file.path));
    if (action === "delete" && openInScope().some(file => file.dirty || file.saving)) {
      setError("Save or close unsaved files in this folder before deleting it from disk.");
      return;
    }
    if (action === "rename" && openInScope().some(file => file.saving)) {
      setError("Wait for the open file to finish saving before renaming it.");
      return;
    }
    let name = "";
    if (action !== "delete") {
      name = await askEntryName(action, item);
      if (!name || !current()) return;
      if (action === "rename" && name !== item.name) {
        const destination = [parentPath(item.path), name].filter(Boolean).join("/");
        if ([...currentSession.tabs.keys()].some(path => path === destination || path.startsWith(`${destination}/`))) {
          setError("Close open tabs at the destination path before renaming this item.");
          return;
        }
      }
    }
    let info = null;
    if (action === "delete") {
      try {
        info = await request(`/api/workspace/entry-info?${new URLSearchParams({workspace_id: currentWorkspace.id, path: item.path})}`);
      } catch (failure) {
        if (current()) setError(failure.message);
        return;
      }
      if (!current()) return;
      const contents = info.type === "folder"
        ? `${info.files} ${info.files === 1 ? "file" : "files"} and ${info.folders} ${info.folders === 1 ? "folder" : "folders"}`
        : "this file";
      const gitMetadataWarning = info.contains_git_metadata
        ? " This folder contains Git repositories or worktree metadata. Deleting it removes local history stored here and can leave linked worktree registrations behind. Recovery requires a separate backup or remote copy; unpushed commits and unsaved work may be lost."
        : "";
      const recoveryWarning = info.contains_git_metadata ? "" : " Only content already recorded by Git can be restored with Git. All other content is permanently lost.";
      const message = `${currentWorkspace.name} · ${item.path}\n\nDelete ${contents} from disk?${info.type === "folder" ? " Includes all descendants, hidden and ignored files." : ""}${gitMetadataWarning}${recoveryWarning}`;
      const confirmed = window.ThemeControls?.confirm
        ? await ThemeControls.confirm({title: "Delete from disk?", message, confirmLabel: "Delete from disk"})
        : window.confirm(message);
      if (!confirmed || !current()) return;
    }
    if (gitActionRunning) return;
    if (openInScope().some(file => file.saving || action === "delete" && file.dirty)) {
      setError("The selection has unsaved or saving files. Save or close them before continuing.");
      return;
    }
    openGeneration += 1;
    gitActionRunning = true;
    updateGitDecorations();
    workbench.inert = true;
    setError();
    const label = {create_file: "Creating file", create_folder: "Creating folder", rename: "Renaming", delete: "Deleting"}[action];
    setStatus(`${label} ${item.path || workspace.name}…`);
    try {
      const targetPath = action.startsWith("create_")
        ? (item.type === "folder" ? item.path : parentPath(item.path))
        : item.path;
      const result = await request("/api/workspace/entry-action", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          workspace_id: currentWorkspace.id, action, path: targetPath, name,
          confirmed: action === "delete", revision: info?.revision || null,
        }),
      });
      if (action === "rename") {
        updateSessionPaths(result.old_path, result.path, result.type, currentSession);
        const renamedFile = currentSession.tabs.get(result.path);
        if (renamedFile && result.language) renamedFile.language = result.language;
        if (current()) selectedTreeItem = {...item, path: result.path, name, missing: false};
      } else if (action === "delete") {
        [...currentSession.tabs.keys()].forEach(path => {
          if (itemContains(item, path)) currentSession.tabs.delete(path);
        });
        if (itemContains(item, currentSession.activePath || "")) currentSession.activePath = null;
        if (current()) selectedTreeItem = null;
      } else {
        if (current()) selectedTreeItem = {path: result.path, name, type: result.type, missing: false};
      }
      window.dispatchEvent(new CustomEvent("gitworktreechange", {detail: currentWorkspace.id}));
      if (!current()) return;
      renderEditor();
      await loadTree();
      if (!current()) return;
      if (action === "create_file") await openFile(result.path);
      else setStatus(`${{create_folder: "Folder created", rename: "Item renamed", delete: "Item deleted"}[action]}: ${result.path}.`);
    } catch (failure) {
      if (current()) {
        setError(failure.message);
        setStatus(`${label} failed. Review the error and try again.`);
      }
    } finally {
      gitActionRunning = false;
      workbench.inert = false;
      updateGitDecorations();
      saveButton.disabled = !activeFile()?.dirty || Boolean(activeFile()?.saving);
      if (current()) gitActionsButton.focus();
    }
  }

  async function showGitDiff(item) {
    const files = filesForItem(item);
    if (!files.length) return;
    gitDiffPath.replaceChildren(...files.map(file => {
      const option = document.createElement("option");
      option.value = file.path;
      option.textContent = `${file.path} (${file.status})`;
      return option;
    }));
    gitDiffDialog.showModal();
    loadGitDiff();
  }

  async function loadGitDiff() {
    const generation = ++diffGeneration;
    const key = sessionKey(workspace);
    gitDiffBody.textContent = "Loading changes…";
    try {
      const selected = gitFiles.find(file => file.path === gitDiffPath.value);
      if (!selected) throw new Error("This file is no longer changed. Refresh the tree.");
      const file = await request(`/api/git/diff?${new URLSearchParams({workspace_id: workspace.id, repository: selected.repository, path: selected.repository_path})}`);
      if (generation !== diffGeneration || key !== sessionKey(workspace)) return;
      gitDiffBody.innerHTML = GitDiff.render(file, "unified");
    } catch (failure) {
      if (generation === diffGeneration && key === sessionKey(workspace)) gitDiffBody.textContent = failure.message;
    }
  }

  async function performGitAction(action, item) {
    if (!workspace || gitActionRunning) return;
    const currentWorkspace = workspace;
    const currentSession = session();
    const current = () => workspace === currentWorkspace && session() === currentSession;
    const affects = path => itemContains(item, path);
    const hasUnsaved = () => [...currentSession.tabs.values()].some(file =>
      (affects(file.path) || filesForItem(item).some(change => change.path === file.path || change.old_path === file.path)) && (file.dirty || file.saving));
    if (hasUnsaved()) {
      setError("Save or close unsaved files in this selection before running a Git action.");
      return;
    }
    gitActionRunning = true;
    updateGitDecorations();
    const label = FileTreeGit.labels[action];
    const destructive = ["discard", "delete_untracked"].includes(action);
    let affected = [];
    let attempted = false;
    try {
      await loadGitStatus();
      if (!current() || !gitAvailable) return;
      affected = FileTreeGit.eligible(filesForItem(item), action);
      if (!affected.length) {
        setStatus(`No matching changes remain in ${item.path}.`);
        return;
      }
      if (destructive) {
        const explanation = action === "discard"
          ? "Restores tracked files from the index. Staged changes, conflicts, and untracked files are kept. This cannot be undone."
          : "Permanently deletes untracked files from disk. Tracked and ignored files are kept. This cannot be undone.";
        const names = affected.map(file => file.path);
        const message = `${currentWorkspace.name} · ${item.path}\n${names.length} affected ${names.length === 1 ? "file" : "files"}${item.type === "folder" ? " across all descendants" : ""}.\n\n${explanation}\n\n${names.slice(0, 30).join("\n")}${names.length > 30 ? `\n… and ${names.length - 30} more` : ""}`;
        const confirmed = window.ThemeControls?.confirm
          ? await ThemeControls.confirm({title: `${label}?`, message, confirmLabel: label})
          : window.confirm(message);
        if (!confirmed || !current()) return;
      }
      if (hasUnsaved()) {
        setError("The selection has unsaved edits. Save or close them before running this action.");
        return;
      }
      setError();
      setStatus(`${label} in ${item.path}…`);
      workbench.inert = true;
      openGeneration += 1;
      saveButton.disabled = true;
      refreshButton.disabled = true;
      attempted = true;
      // Freeze destructive targets after confirmation; never include newly discovered children.
      const groups = new Map();
      affected.forEach(file => {
        if (!groups.has(file.repository)) groups.set(file.repository, []);
        groups.get(file.repository).push(file.repository_path);
      });
      for (const [repository, paths] of groups) {
        await request("/api/git/action", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({workspace_id: currentWorkspace.id, repository, action, paths, confirmed: destructive}),
        });
      }
      if (destructive) {
        // Invalidate clean editor tabs whose disk contents were changed by Git.
        affected.forEach(file => {
          currentSession.tabs.delete(file.path);
          if (currentSession.activePath === file.path) currentSession.activePath = null;
        });
        if (!current()) return;
        renderEditor();
        await loadTree();
      } else {
        if (current()) await loadTree();
      }
      if (current()) setStatus(`${label} complete in ${item.path}.`);
    } catch (failure) {
      if (current()) {
        await loadGitStatus();
        if (current()) {
          setError(failure.message);
          setStatus(`${label} failed. Refresh and try again.`);
        }
      }
    } finally {
      gitActionRunning = false;
      workbench.inert = false;
      refreshButton.disabled = !workspace;
      saveButton.disabled = !activeFile()?.dirty || Boolean(activeFile()?.saving);
      updateGitDecorations();
      if (attempted) window.dispatchEvent(new CustomEvent("gitworktreechange", {detail: currentWorkspace.id}));
      if (current()) gitActionsButton.focus();
    }
  }

  function ensureSession(key) {
    if (!sessions.has(key)) sessions.set(key, {tabs: new Map(), activePath: null});
    return sessions.get(key);
  }

  async function fetchFile(path) {
    return request(`/api/workspace/file?${new URLSearchParams({workspace_id: workspace.id, path})}`);
  }

  async function openFile(path, reload = false) {
    const currentSession = session();
    if (!currentSession) return;
    const generation = ++openGeneration;
    setError();
    document.querySelectorAll(".files-tree-row").forEach(row => row.classList.toggle("is-selected", row.dataset.path === path));
    if (currentSession.tabs.has(path) && !reload) {
      currentSession.activePath = path;
      renderEditor();
      return;
    }
    setStatus(`Opening ${path}…`);
    try {
      const body = await fetchFile(path);
      if (generation !== openGeneration || currentSession !== session()) return;
      const normalized = body.content.replace(/\r\n?/g, "\n");
      currentSession.tabs.set(path, {
        ...body,
        content: normalized,
        savedContent: normalized,
        dirty: false,
      });
      currentSession.activePath = path;
      renderEditor();
      setStatus(`${path} opened.`);
    } catch (failure) {
      if (generation !== openGeneration || currentSession !== session()) return;
      setError(failure.message);
      setStatus(`Unable to open ${path}.`);
    }
  }

  function renderTabs() {
    const currentSession = session();
    tabsElement.replaceChildren();
    if (!currentSession) return;
    let tabIndex = 0;
    currentSession.tabs.forEach(file => {
      const wrapper = document.createElement("div");
      wrapper.className = `files-tab${file.path === currentSession.activePath ? " is-active" : ""}`;
      const tab = document.createElement("button");
      tab.type = "button";
      tab.id = `file-tab-${tabIndex}`;
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-selected", String(file.path === currentSession.activePath));
      tab.setAttribute("aria-controls", "files-editor-panel");
      if (file.path === currentSession.activePath) editorPanel.setAttribute("aria-labelledby", tab.id);
      tab.tabIndex = file.path === currentSession.activePath ? 0 : -1;
      tab.dataset.fileTab = file.path;
      tab.title = file.path;
      const icon = fileIcon(file.name);
      const name = document.createElement("span");
      name.className = "files-tab-name";
      name.textContent = file.name;
      tab.append(icon, name);
      if (file.dirty) {
        const dirty = document.createElement("span");
        dirty.className = "files-tab-dirty";
        dirty.textContent = "•";
        dirty.setAttribute("aria-label", "Unsaved changes");
        tab.append(dirty);
      }
      const close = document.createElement("button");
      close.type = "button";
      close.className = "files-tab-close";
      close.dataset.closeFile = file.path;
      close.setAttribute("aria-label", `Close ${file.name}`);
      close.disabled = Boolean(file.saving);
      close.textContent = "×";
      wrapper.append(tab, close);
      tabsElement.append(wrapper);
      tabIndex += 1;
    });
  }

  function renderBreadcrumbs(file) {
    breadcrumbs.replaceChildren();
    const root = document.createElement("b");
    root.textContent = workspace.name;
    breadcrumbs.append(root);
    file.path.split("/").forEach(part => {
      const separator = document.createElement("span");
      separator.textContent = "›";
      const crumb = document.createElement("span");
      crumb.textContent = part;
      breadcrumbs.append(separator, crumb);
    });
  }

  function updateGutter() {
    const lines = editor.value.split("\n").length;
    const visibleLines = Math.min(lines, 100000);
    gutter.textContent = Array.from({length: visibleLines}, (_, index) => index + 1).join("\n");
    if (lines > visibleLines) gutter.textContent += "\n…";
    if (window.FileEditorSyntax) {
      highlighted.innerHTML = FileEditorSyntax.highlight(editor.value, activeFile()?.language || "Plain text") + "\n";
      panel.classList.add("has-syntax");
      highlighted.scrollTop = editor.scrollTop;
      highlighted.scrollLeft = editor.scrollLeft;
    }
    return lines;
  }

  function updatePosition() {
    if (!activeFile()) {
      position.textContent = "";
      return;
    }
    const before = editor.value.slice(0, editor.selectionStart);
    const line = before.split("\n").length;
    const column = before.length - before.lastIndexOf("\n");
    position.textContent = `Ln ${line}, Col ${column}`;
  }

  function updateDirtyCount() {
    const count = session() ? [...session().tabs.values()].filter(file => file.dirty).length : 0;
    const badge = document.querySelector("#files-dirty-count");
    badge.hidden = !count;
    badge.textContent = count;
  }

  function renderEditor() {
    const file = activeFile();
    document.querySelector("#files-find-open").disabled = !file;
    renderTabs();
    updateDirtyCount();
    findBar.hidden = true;
    findResult.textContent = "";
    if (!file) {
      editorPanel.hidden = true;
      empty.hidden = false;
      breadcrumbs.textContent = "No file open";
      saveButton.disabled = true;
      format.textContent = "";
      position.textContent = "";
      return;
    }
    empty.hidden = true;
    editorPanel.hidden = false;
    editor.value = file.content;
    document.querySelector("#files-content-label").textContent = `${file.name} contents`;
    updateGutter();
    renderBreadcrumbs(file);
    format.textContent = `${file.language} · UTF-8 · ${file.line_ending} · ${editor.value.split("\n").length} lines`;
    saveButton.disabled = !file.dirty || Boolean(file.saving);
    document.querySelector("#files-reload").disabled = Boolean(file.saving);
    requestAnimationFrame(() => {
      editor.scrollTop = file.scrollTop || 0;
      editor.scrollLeft = file.scrollLeft || 0;
      gutter.scrollTop = editor.scrollTop;
      editor.setSelectionRange(file.selectionStart || 0, file.selectionEnd || file.selectionStart || 0);
      updatePosition();
    });
  }

  async function confirmDiscard(file, action) {
    if (!file.dirty) return true;
    if (window.ThemeControls?.confirm) {
      return ThemeControls.confirm({
        title: "Discard unsaved edits?",
        message: `${file.path} has changes that are not saved to disk.`,
        confirmLabel: action,
      });
    }
    return window.confirm(`${file.path} has unsaved edits. ${action}?`);
  }

  async function closeFile(path) {
    const currentSession = session();
    const file = currentSession?.tabs.get(path);
    if (!file || file.saving || !await confirmDiscard(file, "Discard edits")) return;
    const paths = [...currentSession.tabs.keys()];
    const index = paths.indexOf(path);
    currentSession.tabs.delete(path);
    if (currentSession.activePath === path) {
      currentSession.activePath = paths[index + 1] || paths[index - 1] || null;
    }
    renderEditor();
  }

  async function saveActive() {
    const file = activeFile();
    if (!file || !file.dirty || file.saving || gitActionRunning) return;
    const currentSession = session();
    const submitted = file.content;
    const serialized = file.line_ending === "CRLF" ? submitted.replace(/\n/g, "\r\n") : submitted;
    file.saving = true;
    saveButton.disabled = true;
    document.querySelector("#files-reload").disabled = true;
    setError();
    setStatus(`Saving ${file.path}…`);
    try {
      const body = await request("/api/workspace/file", {
        method: "PUT",
        headers: {"Content-Type": "application/json", "X-Switchyard-Client": "local-ui"},
        body: JSON.stringify({
          workspace_id: workspace.id,
          path: file.path,
          content: serialized,
          revision: file.revision,
        }),
      });
      file.revision = body.revision;
      file.savedContent = submitted;
      file.dirty = file.content !== submitted;
      file.size = body.size;
      file.line_ending = body.line_ending;
      if (session() === currentSession) {
        setStatus(`${file.path} saved.${file.dirty ? " Newer edits are still unsaved." : ""}`);
        loadGitStatus();
        window.dispatchEvent(new CustomEvent("gitworktreechange", {detail: workspace.id}));
      }
    } catch (failure) {
      if (session() === currentSession) {
        setError(failure.message);
        setStatus(`Unable to save ${file.path}.`);
      }
    } finally {
      file.saving = false;
      if (session() === currentSession) {
        renderTabs();
        updateDirtyCount();
        saveButton.disabled = !activeFile()?.dirty || Boolean(activeFile()?.saving);
        document.querySelector("#files-reload").disabled = Boolean(activeFile()?.saving);
      }
    }
  }

  async function reloadActive() {
    const file = activeFile();
    if (!file || file.saving || !await confirmDiscard(file, "Reload file")) return;
    await openFile(file.path, true);
    editor.focus();
  }

  function applyWorkspace(nextWorkspace) {
    if (sessionKey(nextWorkspace) === sessionKey(workspace) && workspace) return;
    treeGeneration += 1;
    openGeneration += 1;
    closeGitMenu();
    if (gitDiffDialog.open) gitDiffDialog.close();
    if (entryDialog.open) entryDialog.close();
    diffGeneration += 1;
    selectedTreeItem = null;
    gitAvailable = false;
    gitFiles = [];
    gitIndex = FileTreeGit.index([]);
    gitActionsButton.disabled = true;
    gitSummary.textContent = "";
    workspace = nextWorkspace || null;
    tree.replaceChildren();
    gitActionsButton.disabled = !workspace || gitActionRunning;
    document.querySelector("#files-project-name").textContent = workspace?.name || "Project";
    document.querySelector("#files-project-path").textContent = workspace?.repository || "Select a workspace to explore its files.";
    document.querySelector("#files-project-path").title = workspace?.repository || "";
    filter.value = "";
    setError();
    if (!workspace) {
      tree.innerHTML = '<p class="files-tree-message">Add or select a workspace to browse its files.</p>';
      document.querySelector("#files-tree-count").textContent = "";
      refreshButton.disabled = true;
      filter.disabled = true;
      setStatus("Select a workspace to get started.");
      renderEditor();
      return;
    }
    ensureSession(sessionKey(workspace));
    renderEditor();
    loadTree();
  }

  editor.addEventListener("input", () => {
    const file = activeFile();
    if (!file) return;
    const wasDirty = file.dirty;
    file.content = editor.value;
    file.dirty = file.content !== file.savedContent;
    const lines = updateGutter();
    format.textContent = `${file.language} · UTF-8 · ${file.line_ending} · ${lines} lines`;
    saveButton.disabled = !file.dirty || Boolean(file.saving);
    if (file.dirty !== wasDirty) renderTabs();
    updateDirtyCount();
    setStatus(file.dirty ? `${file.path} has unsaved edits.` : `${file.path} matches disk.`);
    updatePosition();
  });
  editor.addEventListener("scroll", () => {
    gutter.scrollTop = editor.scrollTop;
    highlighted.scrollTop = editor.scrollTop;
    highlighted.scrollLeft = editor.scrollLeft;
    const file = activeFile();
    if (file) {
      file.scrollTop = editor.scrollTop;
      file.scrollLeft = editor.scrollLeft;
    }
  });
  ["click", "keyup", "select"].forEach(event => editor.addEventListener(event, () => {
    const file = activeFile();
    if (file) {
      file.selectionStart = editor.selectionStart;
      file.selectionEnd = editor.selectionEnd;
    }
    updatePosition();
  }));
  editor.addEventListener("keydown", event => {
    if (event.key === "Tab") {
      event.preventDefault();
      const start = editor.selectionStart;
      const end = editor.selectionEnd;
      const selected = editor.value.slice(start, end);
      if (event.shiftKey) {
        const lineStart = editor.value.lastIndexOf("\n", start - 1) + 1;
        const block = editor.value.slice(lineStart, end);
        const updated = block.replace(/^ {1,4}/gm, "");
        editor.setRangeText(updated, lineStart, end, "select");
      } else if (selected.includes("\n")) {
        const lineStart = editor.value.lastIndexOf("\n", start - 1) + 1;
        editor.setRangeText(editor.value.slice(lineStart, end).replace(/^/gm, "    "), lineStart, end, "select");
      } else {
        editor.setRangeText("    ", start, end, "end");
      }
      editor.dispatchEvent(new Event("input", {bubbles: true}));
    } else if (event.key === "Escape" && findBar.hidden) {
      tabsElement.querySelector('[role="tab"][aria-selected="true"]')?.focus();
    }
  });

  tabsElement.addEventListener("click", event => {
    const tab = event.target.closest("[data-file-tab]");
    const close = event.target.closest("[data-close-file]");
    if (tab) {
      openGeneration += 1;
      session().activePath = tab.dataset.fileTab;
      renderEditor();
      editor.focus();
    }
    if (close) closeFile(close.dataset.closeFile);
  });
  tabsElement.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const tabs = [...tabsElement.querySelectorAll('[role="tab"]')];
    const index = tabs.indexOf(document.activeElement);
    if (index < 0) return;
    event.preventDefault();
    const target = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
      : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    session().activePath = tabs[target].dataset.fileTab;
    openGeneration += 1;
    renderEditor();
    tabsElement.querySelector('[role="tab"][aria-selected="true"]')?.focus();
  });
  filter.addEventListener("input", applyFilter);
  refreshButton.addEventListener("click", loadTree);
  document.querySelector("#files-root-actions").addEventListener("click", event => {
    selectTreeRow(null);
    openGitMenu({anchor: event.currentTarget});
  });
  tree.addEventListener("contextmenu", event => {
    if (event.target.closest(".files-tree-row")) return;
    event.preventDefault();
    selectTreeRow(null);
    openGitMenu({x: event.clientX, y: event.clientY});
  });
  gitActionsButton.addEventListener("click", () => gitMenu.hidden ? openGitMenu() : closeGitMenu(true));
  gitMenu.addEventListener("click", event => {
    const button = event.target.closest("[data-files-git-action]");
    const entryButton = event.target.closest("[data-files-entry-action]");
    const openButton = event.target.closest("[data-files-open-in-manager]");
    if (openButton && !openButton.disabled) {
      const item = gitMenuItem;
      closeGitMenu(true);
      openInFileManager(item);
    } else if (entryButton && !entryButton.disabled) {
      const item = gitMenuItem;
      closeGitMenu(true);
      performEntryAction(entryButton.dataset.filesEntryAction, item);
    } else if (button && !button.disabled) {
      const item = gitMenuItem;
      closeGitMenu(true);
      const action = button.dataset.filesGitAction;
      if (action === "diff") showGitDiff(item);
      else performGitAction(action, item);
    } else if (event.target.closest("a")) closeGitMenu();
  });
  gitMenu.addEventListener("keydown", event => {
    if (event.key === "Escape" || event.key === "Tab") {
      closeGitMenu(true);
      if (event.key === "Escape") event.preventDefault();
      return;
    }
    if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const items = [...gitMenu.querySelectorAll('[role="menuitem"]:not(:disabled):not([hidden])')];
    const index = items.indexOf(document.activeElement);
    const target = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1
      : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items[target]?.focus();
  });
  document.addEventListener("pointerdown", event => {
    if (!gitMenu.hidden && !gitMenu.contains(event.target) && event.target !== gitActionsButton) closeGitMenu();
  });
  window.addEventListener("resize", () => closeGitMenu());
  window.addEventListener("hashchange", () => {
    closeGitMenu();
    if (gitDiffDialog.open) gitDiffDialog.close();
    if (entryDialog.open) entryDialog.close();
    if (location.hash === "#files" && workspace && !gitActionRunning) loadGitStatus();
  });
  gitDiffPath.addEventListener("change", loadGitDiff);
  gitDiffDialog.addEventListener("close", () => { diffGeneration += 1; });
  entryForm.addEventListener("submit", event => {
    event.preventDefault();
    const value = entryName.value;
    if (!value || value !== value.trim() || /[\\/:<>"|?*]/.test(value) || value === "." || value === ".." || value.toLocaleLowerCase() === ".git" || value.endsWith(".")) {
      entryError.textContent = "Use one name without slashes, reserved characters, outer spaces, or a trailing period.";
      entryName.setAttribute("aria-invalid", "true");
      entryName.focus();
      return;
    }
    entryName.removeAttribute("aria-invalid");
    const resolve = resolveEntryName;
    resolveEntryName = null;
    entryDialog.close();
    resolve?.(value);
  });
  document.querySelector("#files-entry-cancel").addEventListener("click", () => entryDialog.close());
  entryDialog.addEventListener("close", () => {
    const resolve = resolveEntryName;
    resolveEntryName = null;
    resolve?.(null);
  });
  saveButton.addEventListener("click", saveActive);
  document.querySelector("#files-reload").addEventListener("click", reloadActive);
  document.querySelector("#files-find-open").addEventListener("click", () => {
    findBar.hidden = false;
    findInput.focus();
  });
  projectToggle.addEventListener("click", () => {
    const visible = !project.hidden;
    project.hidden = visible;
    workbench.classList.toggle("project-hidden", visible);
    projectToggle.setAttribute("aria-expanded", String(!visible));
    projectToggle.textContent = visible ? "Show project" : "Hide project";
  });
  findBar.addEventListener("submit", event => {
    event.preventDefault();
    const query = findInput.value;
    if (!query) return;
    const source = editor.value.toLocaleLowerCase();
    const needle = query.toLocaleLowerCase();
    let found = source.indexOf(needle, editor.selectionEnd);
    if (found < 0) found = source.indexOf(needle);
    if (found < 0) {
      findResult.textContent = "No matches";
      return;
    }
    editor.focus();
    editor.setSelectionRange(found, found + query.length);
    editor.scrollTop = Math.max(0, (source.slice(0, found).split("\n").length - 3) * parseFloat(getComputedStyle(editor).lineHeight));
    findResult.textContent = `Match at line ${source.slice(0, found).split("\n").length}`;
    updatePosition();
  });
  document.querySelector("#files-find-close").addEventListener("click", () => {
    findBar.hidden = true;
    editor.focus();
  });
  panel.addEventListener("keydown", event => {
    if (event.key === "Escape" && !findBar.hidden) {
      event.preventDefault();
      findBar.hidden = true;
      editor.focus();
    }
  });
  window.addEventListener("keydown", event => {
    if (location.hash !== "#files" || !(event.ctrlKey || event.metaKey)) return;
    if (event.key.toLocaleLowerCase() === "s") {
      event.preventDefault();
      saveActive();
    }
    if (event.key.toLocaleLowerCase() === "f" && activeFile()) {
      event.preventDefault();
      findBar.hidden = false;
      findInput.focus();
      findInput.select();
    }
  });
  window.addEventListener("beforeunload", event => {
    if (![...sessions.values()].some(item => [...item.tabs.values()].some(file => file.dirty))) return;
    event.preventDefault();
    event.returnValue = "";
  });
  window.FilesAgentContext = {
    get: () => ({surface: "files", repository: workspace?.repository,
      file: activeFile()?.path || selectedTreeItem?.path || null,
      unsaved_paths: [...(session()?.tabs.values() || [])].filter(file => file.dirty || file.saving).map(file => file.path)}),
  };
  window.addEventListener("inlineagentopenfile", event => {
    if (workspace?.id === event.detail.workspaceId) openFile(event.detail.path);
  });
  window.addEventListener("inlineagentfinished", async event => {
    const currentSession = sessions.get(sessionKey(event.detail));
    if (!currentSession) return;
    const path = currentSession.activePath;
    for (const [key, file] of currentSession.tabs) {
      if (!file.dirty && !file.saving) currentSession.tabs.delete(key);
    }
    if (workspace?.id !== event.detail.id) return;
    renderEditor();
    await loadTree();
    if (workspace?.id === event.detail.id && path && !currentSession.tabs.has(path)) await openFile(path);
  });
  window.addEventListener("workspacechange", event => applyWorkspace(event.detail));
  applyWorkspace(window.Workspaces?.active?.() || null);
})();
