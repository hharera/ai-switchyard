(() => {
  const drawer = document.querySelector("#inline-agent");
  if (!drawer) return;
  const title = document.querySelector("#inline-agent-title");
  const surfaceLabel = document.querySelector("#inline-agent-surface");
  const contextTitle = document.querySelector("#inline-agent-context-title");
  const contextDetail = document.querySelector("#inline-agent-context-detail");
  const messagesElement = document.querySelector("#inline-agent-messages");
  const empty = document.querySelector("#inline-agent-empty");
  const form = document.querySelector("#inline-agent-form");
  const input = document.querySelector("#inline-agent-input");
  const tool = document.querySelector("#inline-agent-tool");
  const send = document.querySelector("#inline-agent-send");
  const note = document.querySelector("#inline-agent-note");
  const error = document.querySelector("#inline-agent-error");
  const confirmWrap = document.querySelector("#inline-agent-confirm-wrap");
  const confirm = document.querySelector("#inline-agent-confirm");
  const sessions = new Map();
  let tools = [];
  let mode = "ask";
  let surface = "files";
  let pending = false;
  let trigger = null;
  let capturedWorkspace = null;
  let capturedContext = null;

  const workspace = () => capturedWorkspace;
  const context = () => {
    if (capturedContext) return capturedContext;
    const source = surface === "git" ? window.GitAgentContext : window.FilesAgentContext;
    const value = source?.get?.() || {surface};
    return {...value, surface, selected_paths: value.selected_paths || [], unsaved_paths: value.unsaved_paths || []};
  };
  const key = () => `${workspace()?.id || ""}:${surface}:${context().repository || ""}`;
  const session = () => {
    if (!sessions.has(key())) sessions.set(key(), {messages: [], draft: ""});
    return sessions.get(key());
  };

  function renderContext() {
    const active = workspace();
    const value = context();
    surfaceLabel.textContent = surface.toUpperCase();
    contextTitle.textContent = active ? `${active.name} · ${surface === "git" ? "Git" : "Files"}` : "No workspace selected";
    const parts = [value.file, value.commit ? `commit ${value.commit.slice(0, 12)}` : "", value.ref ? `ref ${value.ref}` : "", value.comparison ? `view ${value.comparison}` : "", value.selected_paths.length ? `${value.selected_paths.length} selected path${value.selected_paths.length === 1 ? "" : "s"}` : ""].filter(Boolean);
    contextDetail.textContent = parts.join("\n") || value.repository || active?.repository || "Select context from the page.";
  }

  function renderMessages() {
    const messages = session().messages;
    messagesElement.replaceChildren(...messages.map(message => {
      const item = document.createElement("li");
      const label = document.createElement("b");
      label.textContent = message.role === "user" ? "YOU" : "AGENT";
      const body = document.createElement("div");
      body.className = "inline-agent-message-body";
      if (message.role === "assistant") {
        body.innerHTML = window.AgentMarkdown.render(message.content, {
          workspace: workspace()?.repository, repository: context().repository,
        });
      } else {
        body.classList.add("is-plain");
        body.textContent = message.content;
      }
      item.append(label, body);
      return item;
    }));
    empty.hidden = messages.length > 0;
    messagesElement.hidden = messages.length === 0;
    messagesElement.scrollTop = messagesElement.scrollHeight;
  }

  function updateMode(nextMode) {
    mode = nextMode;
    drawer.querySelectorAll("[data-inline-agent-mode]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.inlineAgentMode === mode)));
    confirmWrap.hidden = mode !== "edit";
    drawer.querySelectorAll("[data-inline-agent-mode]").forEach(button => { button.disabled = pending; });
    confirm.disabled = pending;
    input.placeholder = mode === "edit" ? "Describe the change to make…" : "Ask about the selected file or commit…";
    if (mode === "edit") tool.value = tools.some(item => item.id === "codex" && item.available) ? "codex" : "";
    tool.disabled = pending || mode === "edit";
    if (!pending) note.textContent = mode === "edit"
      ? context().unsaved_paths.length ? "Save or close unsaved files before running Edit mode." : "Edit mode can modify files in the selected checkout. It will not commit, stage, push, or publish."
      : "Ask mode is read-only. Selected paths and commits are included as context.";
    send.textContent = pending ? "Working…" : mode === "edit" ? "Make changes" : "Send";
  }

  async function loadTools() {
    try {
      const response = await fetch("/api/chat/tools", {headers: {"X-Switchyard-Client": "local-ui"}});
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Unable to load AI tools.");
      tools = body.tools || [];
      tool.replaceChildren(...tools.map(item => {
        const option = new Option(`${item.name}${item.available ? "" : " - unavailable"}`, item.id);
        option.disabled = !item.available;
        return option;
      }));
      const available = tools.find(item => item.available);
      tool.value = available?.id || "";
      updateMode(mode);
    } catch (failure) {
      tool.replaceChildren(new Option("Tools unavailable", ""));
      tool.disabled = true;
      error.textContent = failure.message;
    }
  }

  function open(nextSurface, button) {
    if (pending) {
      drawer.setAttribute("aria-hidden", "false");
      document.body.classList.add("inline-agent-open");
      return;
    }
    if (capturedWorkspace) session().draft = input.value;
    surface = nextSurface;
    capturedWorkspace = window.Workspaces?.active() || null;
    capturedContext = null;
    capturedContext = context();
    confirm.checked = false;
    error.textContent = "";
    trigger = button || document.activeElement;
    input.value = session().draft;
    drawer.setAttribute("aria-hidden", "false");
    document.body.classList.add("inline-agent-open");
    title.textContent = surface === "git" ? "Ask about these changes" : "Ask about this file";
    renderContext();
    renderMessages();
    updateMode(mode);
    title.focus({preventScroll: true});
    if (!tools.length) loadTools();
  }

  function close() {
    session().draft = input.value;
    drawer.setAttribute("aria-hidden", "true");
    document.body.classList.remove("inline-agent-open");
    trigger?.focus?.({preventScroll: true});
  }

  async function sendMessage() {
    const active = workspace();
    const value = context();
    value.unsaved_paths = window.FilesAgentContext?.get()?.unsaved_paths || [];
    const text = input.value.trim();
    error.textContent = "";
    if (!active || active.id !== window.Workspaces?.active()?.id) { error.textContent = "Open the agent again for the active workspace."; return; }
    if (!text) { error.textContent = "Write a message before sending."; input.focus(); return; }
    if (!tools.some(item => item.id === tool.value && item.available)) { error.textContent = mode === "edit" ? "Codex is unavailable. Check System and retry." : "Choose an available AI tool."; return; }
    if (mode === "edit" && value.unsaved_paths.length) { error.textContent = "Save or close unsaved files before running Edit mode."; return; }
    if (mode === "edit" && !confirm.checked) { error.textContent = "Confirm workspace editing before running the agent."; confirm.focus(); return; }
    const state = session();
    state.messages.push({role: "user", content: text});
    state.draft = "";
    input.value = "";
    renderMessages();
    pending = true;
    if (mode === "edit") {
      document.querySelector("#files-panel").inert = true;
      document.querySelector("#git-panel").inert = true;
    }
    updateMode(mode);
    send.disabled = true;
    input.disabled = true;
    note.textContent = mode === "edit" ? "The agent is editing and checking the selected checkout. This can take up to ten minutes." : "The agent is reading the selected context. This can take up to three minutes.";
    let completion = "";
    try {
      const transcript = state.messages.slice(-19).map(message => ({role: message.role, content: message.content.slice(0, 24000)}));
      while (transcript.length > 1 && new TextEncoder().encode(JSON.stringify(transcript)).length > 80000) transcript.splice(0, 2);
      const response = await fetch("/api/inline-agent", {
        method: "POST", headers: {"Content-Type": "application/json", "X-Switchyard-Client": "local-ui"},
        body: JSON.stringify({workspace_id: active.id, tool: tool.value, mode, messages: transcript, context: value, allow_host_execution: mode === "edit"}),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(Array.isArray(body.detail) ? body.detail.map(item => item.msg).join("; ") : body.detail || "Unable to run the agent.");
      state.messages.push({role: "assistant", content: body.message});
      completion = mode === "edit" ? "Changes finished. Review the Files and Git views before committing." : "Answer received.";
    } catch (failure) {
      state.messages.pop();
      state.draft = text;
      input.value = text;
      error.textContent = failure.message;
    } finally {
      pending = false;
      if (mode === "edit") {
        confirm.checked = false;
        document.querySelector("#files-panel").inert = false;
        document.querySelector("#git-panel").inert = false;
        window.dispatchEvent(new CustomEvent("gitworktreechange", {detail: active.id}));
        window.dispatchEvent(new CustomEvent("inlineagentfinished", {detail: active}));
      }
      input.disabled = false;
      send.disabled = false;
      renderMessages();
      renderContext();
      updateMode(mode);
      if (completion) note.textContent = completion;
    }
  }

  document.querySelectorAll("[data-inline-agent]").forEach(button => button.addEventListener("click", () => open(button.dataset.inlineAgent, button)));
  drawer.querySelectorAll("[data-inline-agent-mode]").forEach(button => button.addEventListener("click", () => { if (!pending) updateMode(button.dataset.inlineAgentMode); }));
  document.querySelector("#inline-agent-close").addEventListener("click", close);
  messagesElement.addEventListener("click", event => {
    const link = event.target.closest("[data-agent-file]");
    if (!link) return;
    if (workspace()?.id !== window.Workspaces?.active()?.id) {
      error.textContent = "Open the agent again for the active workspace.";
      return;
    }
    window.dispatchEvent(new CustomEvent("inlineagentopenfile", {detail: {
      workspaceId: workspace().id, path: link.dataset.agentFile,
    }}));
    location.hash = "#files";
    close();
  });
  form.addEventListener("submit", event => { event.preventDefault(); if (!pending) sendMessage(); });
  input.addEventListener("input", () => { session().draft = input.value; error.textContent = ""; });
  input.addEventListener("keydown", event => { if (!event.isComposing && event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); form.requestSubmit(); } });
  window.addEventListener("workspacechange", close);
  window.addEventListener("hashchange", () => { if (!["#files", "#git"].includes(location.hash)) close(); });
  window.addEventListener("keydown", event => { if (event.key === "Escape" && drawer.getAttribute("aria-hidden") === "false") close(); });
  window.InlineAgent = {open, refreshContext: renderContext};
})();
