(() => {
  const form = document.querySelector("#chat-form");
  const input = document.querySelector("#chat-input");
  const select = document.querySelector("#chat-tool");
  const send = document.querySelector("#chat-send");
  const status = document.querySelector("#chat-status");
  const error = document.querySelector("#chat-error");
  const list = document.querySelector("#chat-messages");
  const empty = document.querySelector("#chat-empty");
  const newChat = document.querySelector("#chat-new");
  const conversations = new Map();
  let tools = [];
  let pending = false;
  let loadingTools = false;
  let currentKey = "";
  const refresh = document.querySelector("#chat-refresh-tools");
  const sessionDetails = document.querySelector(".chat-session-details");
  const historyList = document.querySelector("#chat-history-list");
  const historyStatus = document.querySelector("#chat-history-status");
  const historyError = document.querySelector("#chat-history-error");
  const historyRefresh = document.querySelector("#chat-history-refresh");
  const historySearch = document.querySelector("#chat-history-search");
  const historySource = document.querySelector("#chat-history-source");
  const historyDetail = document.querySelector("#chat-history-detail");
  const historyDetailStatus = document.querySelector("#chat-history-detail-status");
  const panel = document.querySelector("#chat-panel");
  let history = [];
  let sources = [];
  let selectedHistory = null;
  let historyRequest = 0;
  let detailRequest = 0;
  let historyLoading = false;

  function freshConversation() {
    return {messages: [], draft: "", status: "", error: "", sessionId: crypto.randomUUID()};
  }

  function resizeInput() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 240)}px`;
  }

  function activeWorkspace() { return window.Workspaces?.active() || null; }

  function conversation() {
    if (!conversations.has(currentKey)) conversations.set(currentKey, freshConversation());
    return conversations.get(currentKey);
  }

  function updateWorkspace(workspace = activeWorkspace()) {
    document.querySelector("#chat-workspace-name").textContent = workspace?.name || "No workspace selected";
    document.querySelector("#chat-workspace-path").textContent = workspace?.repository || "Add or select a workspace to start chatting.";
    send.disabled = pending || loadingTools || !workspace || !tools.some(tool => tool.id === select.value && tool.available);
    input.disabled = pending || !workspace;
    status.textContent = workspace ? conversation().status : "Select a workspace before sending a message.";
    error.textContent = conversation().error;
    resizeInput();
  }

  function switchWorkspace() {
    conversation().draft = input.value;
    const workspace = activeWorkspace();
    currentKey = workspace?.repository || "";
    selectedHistory = null;
    detailRequest++;
    panel.classList.remove("reading-history", "history-mobile-detail");
    historyDetail.hidden = true;
    historySearch.value = "";
    historySource.value = "";
    input.value = conversation().draft;
    renderMessages();
    updateWorkspace();
    loadHistory();
  }

  function toolName(id) { return tools.find(tool => tool.id === id)?.name || id; }

  function renderMessages() {
    if (selectedHistory) return;
    const {messages} = conversation();
    const scrollTop = list.scrollTop;
    const following = list.scrollHeight - scrollTop - list.clientHeight < 80;
    displayMessages(messages);
    if (pending && !following) list.scrollTop = scrollTop;
  }

  function displayMessages(messages) {
    list.replaceChildren(...messages.map((message) => {
      const item = document.createElement("li");
      item.className = `chat-message ${message.role}`;
      const meta = document.createElement("span");
      meta.textContent = `${message.role === "user" ? "You" : message.label || "Assistant"}${message.timestamp ? ` / ${formatDate(message.timestamp)}` : ""}`;
      const content = document.createElement("div");
      const structured = message.role === "assistant" ? renderStructuredJson(message.content) : "";
      if (message.role === "assistant" && (message.streaming || message.activity?.length)) {
        content.className = "chat-message-body";
        const progress = document.createElement("details");
        progress.className = "chat-progress";
        progress.open = message.stepsOpen ?? Boolean(message.streaming);
        progress.addEventListener("toggle", () => { message.stepsOpen = progress.open; });
        const heading = document.createElement("summary");
        const pulse = document.createElement("span");
        pulse.className = "chat-progress-pulse";
        pulse.setAttribute("aria-hidden", "true");
        const headingText = document.createElement("span");
        headingText.textContent = "Thinking & steps";
        heading.append(pulse, headingText);
        const steps = document.createElement("ol");
        const activity = message.activity?.length ? message.activity : [{
          kind: "status", label: "Starting", detail: `${message.label || "Assistant"} is reading the workspace.`
        }];
        steps.replaceChildren(...activity.map((entry) => {
          const step = document.createElement("li");
          step.dataset.kind = entry.kind;
          const label = document.createElement("strong");
          label.textContent = entry.label;
          const detail = document.createElement("span");
          detail.textContent = entry.detail;
          step.append(label, detail);
          return step;
        }));
        progress.append(heading, steps);
        const answer = document.createElement("div");
        answer.className = `chat-answer${message.streaming && !message.content ? " pending" : ""}`;
        if (structured) {
          answer.className += " structured-message";
          answer.innerHTML = structured;
        } else {
          answer.textContent = message.content || "Preparing a response...";
        }
        content.append(progress, answer);
      } else if (structured) {
        content.className = "structured-message";
        content.innerHTML = structured;
      } else {
        content.textContent = message.content;
      }
      item.append(meta, content);
      return item;
    }));
    empty.hidden = messages.length > 0;
    list.hidden = messages.length === 0;
    list.scrollTop = list.scrollHeight;
  }

  function formatDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "Date unavailable" : date.toLocaleString([], {dateStyle: "medium", timeStyle: "short"});
  }

  function renderHistory() {
    const query = historySearch.value.trim().toLocaleLowerCase();
    const visible = history.filter(item => (!historySource.value || item.source === historySource.value)
      && `${item.title} ${item.model}`.toLocaleLowerCase().includes(query));
    historyList.replaceChildren(...visible.map(item => {
      const row = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "chat-history-item";
      button.setAttribute("aria-current", selectedHistory?.id === item.id ? "true" : "false");
      const title = document.createElement("strong");
      title.textContent = item.title;
      const source = document.createElement("span");
      source.textContent = `${sources.find(s => s.id === item.source)?.name || item.source}${item.model ? ` / ${item.model}` : ""}${item.archived ? " / Archived" : ""}`;
      const date = document.createElement("time");
      date.dateTime = item.updated_at;
      date.textContent = formatDate(item.updated_at);
      button.append(source, title, date);
      button.addEventListener("click", () => openHistory(item));
      row.append(button);
      return row;
    }));
    historyStatus.textContent = historyLoading ? "Reading local history..." : !activeWorkspace()
      ? "Select a workspace to see its history." : visible.length
        ? `${visible.length} of ${history.length} conversations` : history.length
          ? "No conversations match these filters." : "No local conversations found for this path. Start a chat or check History sources.";
    document.querySelector("#chat-history-clear").hidden = !query && !historySource.value;
  }

  async function loadHistory() {
    const workspace = activeWorkspace();
    const request = ++historyRequest;
    historyLoading = true;
    historyRefresh.disabled = true;
    historyError.textContent = "";
    history = [];
    document.querySelector("#chat-history-path").textContent = workspace?.repository || "No workspace selected";
    renderHistory();
    try {
      if (!workspace) return;
      const response = await fetch(`/api/chat/history?${new URLSearchParams({workspace_id: workspace.id})}`, {
        headers: {"X-Switchyard-Client": "local-ui"}
      });
      const body = await response.json();
      if (request !== historyRequest) return;
      if (!response.ok) throw new Error(body.detail || "Unable to read local history.");
      history = body.conversations;
      sources = body.sources;
      const previous = historySource.value;
      historySource.replaceChildren(new Option("All tools", ""), ...sources.filter(source => source.count).map(source => new Option(`${source.name} (${source.count})`, source.id)));
      historySource.value = sources.some(s => s.id === previous && s.count) ? previous : "";
      document.querySelector("#chat-history-sources").replaceChildren(...sources.map(source => {
        const item = document.createElement("li");
        const name = document.createElement("b");
        name.textContent = source.name;
        const detail = document.createElement("span");
        detail.textContent = source.count ? `${source.count} conversations. ${source.detail}` : source.detail;
        item.append(name, detail);
        return item;
      }));
    } catch (failure) {
      if (request === historyRequest) historyError.textContent = `${failure.message} Try Refresh.`;
    } finally {
      if (request === historyRequest) {
        historyLoading = false;
        historyRefresh.disabled = !workspace;
        renderHistory();
      }
    }
  }

  async function openHistory(item) {
    const workspace = activeWorkspace();
    if (!workspace) return;
    const request = ++detailRequest;
    conversation().draft = input.value;
    selectedHistory = item;
    panel.classList.add("reading-history", "history-mobile-detail");
    historyDetail.hidden = false;
    document.querySelector("#chat-history-title").textContent = item.title;
    document.querySelector("#chat-history-meta").textContent = `${sources.find(s => s.id === item.source)?.name || item.source} / ${item.model || "Local conversation"} / Read-only`;
    historyDetailStatus.textContent = "Loading conversation...";
    displayMessages([]);
    empty.hidden = true;
    renderHistory();
    document.querySelector("#chat-history-title").focus({preventScroll: true});
    try {
      const response = await fetch(`/api/chat/history/${encodeURIComponent(item.id)}?${new URLSearchParams({workspace_id: workspace.id})}`, {
        headers: {"X-Switchyard-Client": "local-ui"}
      });
      const body = await response.json();
      if (request !== detailRequest) return;
      if (!response.ok) throw new Error(body.detail || "Unable to load this conversation.");
      displayMessages(body.messages);
      empty.hidden = true;
      historyDetailStatus.textContent = body.truncated ? "This transcript is shortened for display. Open the original tool for the full conversation."
        : body.messages.length ? `${body.messages.length} messages. Viewing history does not send it to an AI provider.`
          : "No readable messages are available in this local conversation.";
    } catch (failure) {
      if (request === detailRequest) historyDetailStatus.textContent = failure.message;
    }
  }

  async function loadTools() {
    if (loadingTools || pending) return;
    loadingTools = true;
    refresh.disabled = true;
    const previous = select.value;
    select.disabled = true;
    updateWorkspace();
    try {
      const response = await fetch("/api/chat/tools");
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Unable to load AI tools.");
      tools = body.tools || [];
      const available = tools.filter(tool => tool.available);
      select.innerHTML = tools.length
        ? tools.map(tool => `<option value="${escapeHtml(tool.id)}" ${tool.available ? "" : "disabled"}>${escapeHtml(tool.name)}${tool.available ? "" : " - unavailable"}</option>`).join("")
        : '<option value="">No AI tools available</option>';
      if (previous && !tools.some(tool => tool.id === previous)) {
        select.add(new Option(`${previous} - unavailable`, previous));
        select.lastElementChild.disabled = true;
      }
      select.value = tools.some(tool => tool.id === previous && tool.available)
        ? previous : available[0]?.id || "";
      select.disabled = !available.length;
      document.querySelector("#chat-tool-note").textContent = body.warning
        ? `${body.warning} Showing the last known routes.`
        : `${available.length} AI ${available.length === 1 ? "tool" : "tools"} available. Each reply uses the current selection.`;
    } catch (failure) {
      select.innerHTML = '<option value="">Tools unavailable</option>';
      select.disabled = true;
      conversation().error = `${failure.message} Check System, then refresh tools.`;
    }
    loadingTools = false;
    refresh.disabled = false;
    updateWorkspace();
  }

  async function sendMessage(content) {
    if (selectedHistory) return;
    const workspace = activeWorkspace();
    if (!workspace) {
      error.textContent = "Select an active workspace before sending a message.";
      document.querySelector(".chat-repository").focus();
      return;
    }
    if (!tools.some(tool => tool.id === select.value && tool.available)) {
      error.textContent = "Choose an available AI tool before sending a message.";
      select.focus();
      return;
    }
    if (!content.trim()) {
      error.textContent = "Write a message before sending.";
      input.setAttribute("aria-invalid", "true");
      input.focus();
      return;
    }
    input.removeAttribute("aria-invalid");
    const state = conversation();
    const {messages} = state;
    const selectedTool = select.value;
    const label = toolName(selectedTool);
    const userMessage = {role: "user", content: content.trim()};
    messages.push(userMessage);
    renderMessages();
    // Keep complete recent exchanges inside the backend's transcript budget.
    const transcript = messages.slice(-19)
      .map(({role, content: text}) => ({role, content: text.slice(0, 24000)}));
    while (transcript.length > 1 && new TextEncoder().encode(JSON.stringify(transcript)).length > 95000) transcript.splice(0, 2);
    input.value = "";
    state.draft = "";
    pending = true;
    state.error = "";
    const assistantMessage = {
      role: "assistant", content: "", tool: selectedTool, label, activity: [], streaming: true
    };
    messages.push(assistantMessage);
    renderMessages();
    state.status = `${label} is streaming its work.`;
    updateWorkspace();
    send.disabled = true;
    input.disabled = true;
    select.disabled = true;
    newChat.disabled = true;
    refresh.disabled = true;
    send.querySelector("span").textContent = "Receiving reply";
    send.title = "Receiving reply";
    try {
      const response = await fetch("/api/chat/stream", {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-Switchyard-Client": "local-ui"},
        body: JSON.stringify({
          tool: selectedTool, workspace_id: workspace.id, messages: transcript, session_id: state.sessionId
        })
      });
      if (!response.ok) {
        const body = await response.json();
        const detail = Array.isArray(body.detail) ? body.detail.map(item => item.msg).join("; ") : body.detail;
        throw new Error(detail || "Unable to get a reply. Try again.");
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffered = "";
      let completed = false;
      while (true) {
        const {value, done} = await reader.read();
        buffered += decoder.decode(value || new Uint8Array(), {stream: !done});
        const lines = buffered.split("\n");
        buffered = lines.pop();
        for (const line of lines) {
          if (!line.trim()) continue;
          const event = JSON.parse(line);
          if (event.type === "thinking") {
            assistantMessage.activity.push({kind: "thinking", label: "Thinking", detail: event.content});
          } else if (event.type === "step") {
            assistantMessage.activity.push({kind: "step", label: event.label, detail: event.detail});
          } else if (event.type === "delta") {
            assistantMessage.content += `${assistantMessage.content ? "\n" : ""}${event.content}`;
          } else if (event.type === "done") {
            assistantMessage.content = event.message;
            assistantMessage.streaming = false;
            state.error = event.warning || "";
            completed = true;
          } else if (event.type === "error") {
            throw new Error(event.message);
          }
          if (state === conversation()) renderMessages();
        }
        if (done) break;
      }
      if (!completed) throw new Error("The reply stream ended before completion. Try again.");
      state.status = `Reply received from ${label}.`;
      if (workspace.repository === activeWorkspace()?.repository) loadHistory();
    } catch (failure) {
      messages.pop();
      messages.pop();
      state.draft = userMessage.content;
      state.error = failure.message;
      state.status = "";
    } finally {
      pending = false;
      input.disabled = false;
      select.disabled = !tools.some(tool => tool.available);
      newChat.disabled = false;
      refresh.disabled = false;
      send.querySelector("span").textContent = "Send message";
      send.title = "Send message";
      if (state === conversation()) {
        input.value = state.draft;
        renderMessages();
      }
      updateWorkspace();
      if (state === conversation() && location.hash === "#chat" && document.activeElement === document.body) {
        input.focus({preventScroll: true});
      }
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!pending && !loadingTools) sendMessage(input.value);
  });
  input.addEventListener("keydown", (event) => {
    if (!event.isComposing && event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  input.addEventListener("input", () => {
    conversation().draft = input.value;
    conversation().error = "";
    error.textContent = "";
    input.removeAttribute("aria-invalid");
    resizeInput();
  });
  refresh.addEventListener("click", () => { conversation().error = ""; loadTools(); });
  select.addEventListener("change", () => updateWorkspace());
  sessionDetails.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && sessionDetails.open) {
      sessionDetails.open = false;
      sessionDetails.querySelector("summary").focus();
    }
  });
  newChat.addEventListener("click", () => {
    selectedHistory = null;
    detailRequest++;
    historyDetail.hidden = true;
    panel.classList.remove("reading-history");
    panel.classList.add("history-mobile-detail");
    conversations.set(currentKey, freshConversation());
    input.value = "";
    renderMessages();
    updateWorkspace();
    input.focus();
    renderHistory();
  });
  document.querySelectorAll("[data-chat-suggestion]").forEach(button => {
    button.addEventListener("click", () => {
      input.value = button.dataset.chatSuggestion;
      conversation().draft = input.value;
      resizeInput();
      input.focus();
    });
  });
  window.addEventListener("workspacechange", switchWorkspace);
  window.addEventListener("chathistoryimported", loadHistory);
  historyRefresh.addEventListener("click", () => { loadHistory(); if (selectedHistory) openHistory(selectedHistory); });
  historySearch.addEventListener("input", renderHistory);
  historySource.addEventListener("change", renderHistory);
  document.querySelector("#chat-history-clear").addEventListener("click", () => {
    historySearch.value = ""; historySource.value = ""; renderHistory(); historySearch.focus();
  });
  document.querySelector("#chat-history-back").addEventListener("click", () => {
    panel.classList.remove("history-mobile-detail");
    historySearch.focus();
  });
  window.addEventListener("hashchange", () => { if (location.hash === "#chat") loadHistory(); });

  switchWorkspace();
  loadTools();
})();
