(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char]));
  const labels = {running: "Running", stopping: "Stopping", completed: "Completed", failed: "Failed", stopped: "Stopped", interrupted: "Interrupted", timed_out: "Timed out", idle: "Idle"};
  const isActive = status => ["running", "stopping"].includes(status);
  const states = new Map();
  let workspace = null, state = null, generation = 0, refreshing = false, busy = false;
  let detail = null;
  const visible = () => location.hash === "#commands";
  const setText = (id, text) => { if ($(id).textContent !== text) $(id).textContent = text; };
  const stamp = value => new Date(value).toLocaleString([], {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit"});
  const currentTab = () => state?.tabs.find(tab => tab.id === state.tabId);
  function storedTab(id) { try { return localStorage.getItem(`switchyard.cmd.tab.${id}`); } catch { return null; } }
  function saveSelection() { try { localStorage.setItem(`switchyard.cmd.tab.${workspace.id}`, state.tabId || ""); } catch { /* Session selection still works. */ } }
  function tabState() {
    if (!state || !state.tabId) return null;
    if (!state.sessions.has(state.tabId)) state.sessions.set(state.tabId, {draft: "", timeout: "0", runId: null, runs: [], next: null, loadedOlder: false});
    return state.sessions.get(state.tabId);
  }
  function captureDraft() {
    const session = tabState();
    if (session) { session.draft = $("command-input").value; session.timeout = $("command-timeout").value; }
  }
  async function api(path, options = {}, id = workspace?.id) {
    const separator = path.includes("?") ? "&" : "?";
    let response;
    try {
      response = await fetch(`/api/commands${path}${id ? `${separator}workspace_id=${encodeURIComponent(id)}` : ""}`, {
        ...options, headers: {"Content-Type": "application/json", "X-Switchyard-Client": "local-ui"},
      });
    } catch {
      throw new Error("Unable to reach Switchyard. Check that it is running, then refresh.");
    }
    const body = await response.json();
    if (!response.ok) throw new Error(Array.isArray(body.detail) ? body.detail.map(item => item.msg).join("; ") : body.detail || "Command request failed. Refresh and retry.");
    return body;
  }
  function renderTabs() {
    const focusId = $("command-tabs").contains(document.activeElement) ? document.activeElement.id : null;
    const html = state.tabs.map(tab => `<button type="button" role="tab" id="cmd-tab-${tab.id}" class="command-tab" aria-controls="command-tab-content" aria-selected="${tab.id === state.tabId}" tabindex="${tab.id === state.tabId ? 0 : -1}" data-cmd-tab="${tab.id}"><span class="command-tab-status-dot ${esc(tab.latest?.status || "idle")}" aria-hidden="true"></span><span>${esc(tab.name)}</span><small>${esc(labels[tab.latest?.status] || "Idle")}</small></button>`).join("");
    if ($("command-tabs").innerHTML !== html) { $("command-tabs").innerHTML = html; if (focusId) $(focusId)?.focus({preventScroll: true}); }
    const selected = currentTab();
    for (const selector of [".command-toolbar", ".command-stage", ".command-composer"]) document.querySelector(selector).hidden = !selected;
    $("command-tab-content").setAttribute("aria-labelledby", selected ? `cmd-tab-${selected.id}` : "commands-heading");
    $("command-run").disabled = !selected || busy || isActive(selected.latest?.status);
    $("command-remove-tab").disabled = !selected || busy || isActive(selected.latest?.status);
    $("command-rename-tab").disabled = !selected || busy;
    $("command-add-tab").disabled = busy;
    setText("command-tab-status", selected ? `${labels[selected.latest?.status] || "Idle"} / one command at a time` : "");
  }
  function showSelection() {
    detail = null;
    const session = tabState();
    $("command-input").value = session?.draft || "";
    $("command-timeout").value = session?.timeout || "0";
    $("command-tab-name").value = currentTab()?.name || "";
    renderTabs(); renderHistory(); renderOutput();
    setText("command-status", state.loaded && !state.tabs.length ? "Create a tab to run your first command." : "");
    setText("command-error", "");
  }
  function selectTab(id) {
    if (id === state.tabId || busy) return;
    captureDraft(); state.tabId = id; generation++; saveSelection(); showSelection(); refresh();
  }
  function renderHistory() {
    const session = tabState();
    const html = (session?.runs || []).map(run => `<li><button type="button" class="command-history-item" data-cmd-run="${run.id}" aria-current="${session.runId === run.id}"><i class="${esc(run.status)}" aria-hidden="true"></i><span><code>${esc(run.command)}</code><small>${esc(labels[run.status])}${run.exit_code !== null ? ` / exit ${run.exit_code}` : ""} / ${esc(stamp(run.started_at))}</small></span></button></li>`).join("") || '<li class="command-history-empty">Commands you run in this tab appear here.</li>';
    const focused = document.activeElement?.dataset.cmdRun;
    if ($("command-history-list").innerHTML !== html) {
      $("command-history-list").innerHTML = html;
      if (focused) document.querySelector(`[data-cmd-run="${focused}"]`)?.focus({preventScroll: true});
    }
    $("command-history-more").hidden = !session?.next;
  }
  function renderOutput() {
    const output = $("command-output-text");
    const atBottom = output.scrollHeight - output.scrollTop - output.clientHeight < 45;
    setText("command-output-heading", detail?.command || "Ready for a command");
    setText("command-output-status", labels[detail?.status] || "Idle");
    $("command-output-status").className = `command-status ${detail?.status || "idle"}`;
    setText("command-output-meta", detail ? `${stamp(detail.started_at)}${detail.ended_at ? ` - ${stamp(detail.ended_at)}` : ""}${detail.exit_code !== null ? ` / exit ${detail.exit_code}` : ""} / ${detail.repository}` : "Each run starts a fresh command shell in the workspace root.");
    // Process output is plain text: never interpret terminal escapes as HTML or links.
    const text = detail?.output?.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, "").replace(/\x1b\][^\x07]*(?:\x07|\x1b\\)/g, "") || (isActive(detail?.status) ? "Waiting for output..." : "No output yet.");
    if (output.textContent !== text) { output.textContent = text; if (atBottom) output.scrollTop = output.scrollHeight; }
    $("command-stop").hidden = !isActive(detail?.status);
    $("command-stop").disabled = busy || detail?.status === "stopping";
    setText("command-output-note", detail?.truncated ? "Output trimmed: only the most recent 262,144 characters are retained." : "Output and history stay on this machine. Commands may print secrets; review before sharing.");
    const reuse = $("command-reuse");
    reuse.hidden = !detail;
    reuse.disabled = busy;
  }
  async function refresh() {
    if (refreshing || !workspace || !visible()) return;
    refreshing = true;
    const id = workspace.id, revision = generation, target = state;
    try {
      const tabs = await api("/tabs", {}, id);
      if (revision !== generation) return;
      target.loaded = true;
      const hadSelection = !!currentTab();
      target.tabs = tabs;
      if (!tabs.some(tab => tab.id === target.tabId)) {
        target.tabId = tabs[0]?.id || null; saveSelection(); showSelection();
      }
      const selected = currentTab();
      if (!hadSelection && selected) $("command-tab-name").value = selected.name;
      if (tabs.length && $("command-status").textContent === "Create a tab to run your first command.") setText("command-status", "");
      renderTabs();
      if (!target.tabId) { clearRefreshError(); return; }
      const session = tabState(), tabId = target.tabId;
      const history = await api(`/tabs/${tabId}/runs`, {}, id);
      if (revision !== generation || tabId !== target.tabId) return;
      const older = session.runs.filter(run => !history.runs.some(item => item.id === run.id));
      session.runs = [...history.runs, ...older];
      if (!session.loadedOlder) session.next = history.next;
      if (!session.runId || !session.runs.some(run => run.id === session.runId)) session.runId = history.runs[0]?.id || null;
      renderHistory();
      const runId = session.runId;
      if (runId) {
        const run = await api(`/runs/${runId}`, {}, id);
        if (revision !== generation || runId !== session.runId) return;
        const changed = detail?.id === run.id && detail.status !== run.status;
        detail = run; renderOutput();
        if (changed) setText("command-status", `${currentTab().name}: ${labels[run.status]}.`);
      }
      clearRefreshError();
    } catch (error) {
      if (revision === generation) {
        target.refreshError = error.message;
        setText("command-error", error.message);
        if (!target.tabs.length) setText("command-status", "");
      }
    }
    finally { refreshing = false; }
  }
  function clearRefreshError() {
    if (state.refreshError === $("command-error").textContent) setText("command-error", "");
    state.refreshError = null;
  }
  async function action(task) {
    if (busy || !workspace) return;
    busy = true; generation++;
    const revision = generation, id = workspace.id, target = state;
    target.refreshError = null; setText("command-error", ""); renderTabs(); renderOutput();
    try { await task(id, target, () => revision === generation); }
    catch (error) { if (revision === generation) setText("command-error", error.message); }
    finally { busy = false; if (revision === generation) { renderTabs(); renderOutput(); } refresh(); }
  }
  $("command-add-tab").addEventListener("click", () => action(async (id, target, current) => {
    captureDraft();
    const tab = await api("/tabs", {method: "POST", body: JSON.stringify({name: `CMD ${target.tabs.length + 1}`})}, id);
    target.tabs.push(tab); target.tabId = tab.id;
    if (current()) { saveSelection(); showSelection(); $("command-input").focus(); }
  }));
  $("command-tabs").addEventListener("click", event => { const tab = event.target.closest("[data-cmd-tab]"); if (tab) selectTab(tab.dataset.cmdTab); });
  $("command-tabs").addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key) || busy || !state.tabs.length) return;
    event.preventDefault();
    const index = state.tabs.findIndex(tab => tab.id === state.tabId);
    const next = event.key === "Home" ? 0 : event.key === "End" ? state.tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + state.tabs.length) % state.tabs.length;
    selectTab(state.tabs[next].id); $(`cmd-tab-${state.tabId}`).focus();
  });
  $("command-rename-tab").addEventListener("click", () => action(async (id, target, current) => {
    const tabId = target.tabId, name = $("command-tab-name").value.trim();
    if (!name) throw new Error("Enter a tab name before saving.");
    const tab = await api(`/tabs/${tabId}`, {method: "PATCH", body: JSON.stringify({name})}, id);
    Object.assign(target.tabs.find(item => item.id === tabId), tab);
    if (current()) setText("command-status", "Tab name saved.");
  }));
  $("command-remove-tab").addEventListener("click", () => action(async (id, target, current) => {
    const tabId = target.tabId;
    if (!await ThemeControls.confirm({title: "Remove command tab?", message: "This permanently removes the tab and its saved command history. Repository files are kept.", confirmLabel: "Remove tab and history"}) || !current()) return;
    await api(`/tabs/${tabId}`, {method: "DELETE", body: JSON.stringify({confirmed: true})}, id);
    target.tabs = target.tabs.filter(tab => tab.id !== tabId); target.sessions.delete(tabId); target.tabId = target.tabs[0]?.id || null;
    if (current()) { saveSelection(); showSelection(); $("command-add-tab").focus(); }
  }));
  $("command-form").addEventListener("submit", event => {
    event.preventDefault();
    action(async (id, target, current) => {
      const tabId = target.tabId, command = $("command-input").value.trim(), timeout = Number($("command-timeout").value);
      if (!tabId || !command) throw new Error("Create a tab and enter a command first.");
      captureDraft();
      if (!await ThemeControls.confirm({title: "Run command on this machine?", message: `${workspace.name}\n${workspace.repository}\n\n${command}\n\nThis can change local files and access the network. It is not sandboxed.`, confirmLabel: "Run command"}) || !current()) return;
      const run = await api(`/tabs/${tabId}/runs`, {method: "POST", body: JSON.stringify({command, timeout, confirmed: true})}, id);
      target.sessions.get(tabId).runId = run.id;
      target.tabs.find(tab => tab.id === tabId).latest = run;
      if (current()) { detail = run; setText("command-status", `${labels[run.status]}: ${command}`); }
      refreshGlobal();
    });
  });
  $("command-input").addEventListener("input", captureDraft);
  $("command-timeout").addEventListener("change", captureDraft);
  $("command-input").addEventListener("keydown", event => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); if (!$("command-run").disabled) $("command-form").requestSubmit(); }
  });
  $("command-stop").addEventListener("click", () => action(async (id, target, current) => {
    const run = await api(`/runs/${detail.id}/stop`, {method: "POST"}, id);
    if (current()) { detail = run; setText("command-status", "Stopping command and its child processes..."); }
  }));
  $("command-history-list").addEventListener("click", event => {
    const button = event.target.closest("[data-cmd-run]");
    if (!button || busy) return;
    tabState().runId = button.dataset.cmdRun; detail = null; generation++; renderHistory(); renderOutput(); refresh();
  });
  $("command-reuse").addEventListener("click", () => {
    if (!detail) return;
    $("command-input").value = detail.command; captureDraft(); $("command-input").focus();
    setText("command-status", "Command copied to the input. Review it before running.");
  });
  $("command-history-more").addEventListener("click", () => action(async (id, target, current) => {
    const session = tabState();
    if (!session.next) return;
    const history = await api(`/tabs/${target.tabId}/runs?before=${encodeURIComponent(session.next)}`, {}, id);
    session.runs.push(...history.runs.filter(run => !session.runs.some(item => item.id === run.id))); session.next = history.next; session.loadedOlder = true;
    if (current()) renderHistory();
  }));
  $("command-refresh").addEventListener("click", refresh);
  function workspaceChanged(selected) {
    captureDraft(); generation++; workspace = selected; detail = null;
    if (selected && !states.has(selected.id)) states.set(selected.id, {tabs: [], tabId: storedTab(selected.id), sessions: new Map(), loaded: false});
    state = selected ? states.get(selected.id) : null;
    setText("command-workspace-name", selected?.name || "No workspace selected");
    setText("command-workspace-path", selected?.repository || "");
    $("command-empty").hidden = !!selected; $("command-console").hidden = !selected;
    if (selected) { showSelection(); refresh(); }
  }
  let globalBusy = false;
  async function refreshGlobal() {
    if (globalBusy) return;
    globalBusy = true;
    try {
      const statuses = await api("/status", {}, null);
      const count = statuses.reduce((total, item) => total + item.count, 0);
      setText("command-nav-count", String(count)); $("command-nav-count").hidden = !count;
      $("command-nav-count").setAttribute("aria-label", `${count} active commands`);
      const html = statuses.map(row => `<button type="button" class="command-workspace-chip ${esc(row.status)}" data-cmd-workspace="${esc(row.workspace_id)}">${esc(row.name)} <b>${row.count} ${esc(labels[row.status].toLowerCase())}</b></button>`).join("");
      if ($("command-workspace-status").innerHTML !== html) $("command-workspace-status").innerHTML = html;
    } catch { setText("command-nav-count", "?"); $("command-nav-count").hidden = false; $("command-nav-count").setAttribute("aria-label", "Command status unavailable"); }
    finally { globalBusy = false; }
  }
  $("command-workspace-status").addEventListener("click", event => {
    const button = event.target.closest("[data-cmd-workspace]");
    if (!button) return;
    const selector = $("active-workspace");
    selector.value = button.dataset.cmdWorkspace;
    if (selector.value) selector.dispatchEvent(new Event("change", {bubbles: true}));
  });
  window.addEventListener("workspacechange", event => workspaceChanged(event.detail));
  window.addEventListener("hashchange", () => { if (visible()) refresh(); });
  workspaceChanged(window.Workspaces?.active() || null);
  refreshGlobal();
  setInterval(() => { if (!document.hidden) refresh(); }, 1000);
  setInterval(() => { if (!document.hidden) refreshGlobal(); }, 2500);
})();
