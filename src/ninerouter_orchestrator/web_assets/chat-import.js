(() => {
  const form = document.querySelector("#chat-import-form");
  const source = document.querySelector("#chat-import-source");
  const file = document.querySelector("#chat-import-file");
  const preview = document.querySelector("#chat-import-preview");
  const save = document.querySelector("#chat-import-save");
  const selection = document.querySelector("#chat-import-selection");
  const items = document.querySelector("#chat-import-items");
  const status = document.querySelector("#chat-import-status");
  const error = document.querySelector("#chat-import-error");
  let reviewed = null;
  let generation = 0;
  let busy = false;

  function active() { return window.Workspaces?.active() || null; }
  function reset() {
    generation++;
    reviewed = null;
    selection.hidden = true;
    items.replaceChildren();
    status.textContent = "";
    error.textContent = "";
    file.removeAttribute("aria-invalid");
    document.querySelector("#chat-import-workspace").textContent = active()
      ? `Import destination: ${active().repository}` : "Select a workspace before importing chats.";
  }
  function setBusy(value) {
    busy = value;
    for (const control of [file, source, preview, save]) control.disabled = value;
    form.setAttribute("aria-busy", String(value));
  }
  async function request(payload) {
    const serialized = JSON.stringify(payload);
    if (new TextEncoder().encode(serialized).length > 8 * 1024 * 1024) {
      throw new Error("Choose a JSON export smaller than 8 MB.");
    }
    const response = await fetch("/api/chat/history/import", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Switchyard-Client": "local-ui"},
      body: serialized,
    });
    const body = await response.json();
    if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Check the export and retry.");
    return body;
  }
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy) return;
    reset();
    const workspace = active();
    if (!workspace) { error.textContent = "Select a workspace before importing chats."; return; }
    const current = generation;
    setBusy(true);
    try {
      const chosen = file.files[0];
      if (!chosen || chosen.size > 8 * 1024 * 1024) throw new Error("Choose a JSON export smaller than 8 MB.");
      const payload = {workspace_id: workspace.id, source_name: source.value.trim(), export: JSON.parse(await chosen.text())};
      const body = await request(payload);
      if (current !== generation) return;
      reviewed = {payload, repository: workspace.repository};
      items.replaceChildren(...body.conversations.map(conversation => {
        const label = document.createElement("label");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.value = String(conversation.index);
        const text = document.createElement("span");
        text.textContent = `${conversation.title} (${conversation.messages} messages)`;
        label.append(checkbox, text);
        return label;
      }));
      selection.hidden = false;
      status.textContent = `Review these conversations before importing into ${workspace.name}. Nothing has been saved yet.`;
      items.querySelector("input")?.focus();
    } catch (failure) {
      if (current !== generation) return;
      error.textContent = failure instanceof SyntaxError ? "Choose a valid JSON export. This file could not be parsed." : failure.message;
      file.setAttribute("aria-invalid", "true");
    } finally {
      setBusy(false);
      if (error.textContent) file.focus();
    }
  });
  save.addEventListener("click", async () => {
    if (busy || !reviewed) return;
    const workspace = active();
    if (!workspace || workspace.id !== reviewed.payload.workspace_id || workspace.repository !== reviewed.repository) {
      reset();
      error.textContent = "The workspace changed. Review the export again before importing.";
      return;
    }
    const selected = [...items.querySelectorAll("input:checked")].map(input => Number(input.value));
    if (!selected.length) {
      error.textContent = "Select at least one conversation that belongs to this workspace.";
      items.querySelector("input")?.focus();
      return;
    }
    const current = generation;
    setBusy(true);
    error.textContent = "";
    try {
      const body = await request({...reviewed.payload, repository: reviewed.repository, confirmed: true, selected});
      if (current !== generation) return;
      reset();
      status.textContent = `${body.imported} chats imported into ${workspace.name}. Previously imported chats are not duplicated.`;
      window.dispatchEvent(new Event("chathistoryimported"));
    } catch (failure) {
      if (current === generation) error.textContent = failure.message;
    } finally {
      setBusy(false);
      if (current <= generation && !reviewed && !error.textContent) preview.focus();
    }
  });
  source.addEventListener("input", reset);
  file.addEventListener("change", reset);
  window.addEventListener("workspacechange", reset);
  reset();
})();
