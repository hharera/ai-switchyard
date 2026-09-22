(() => {
  const selectState = new WeakMap();
  const listState = new WeakMap();
  let openSelect = null;

  const controlLabel = control => {
    const label = control.id ? document.querySelector(`label[for="${CSS.escape(control.id)}"]`) : control.closest("label");
    return label?.textContent.trim().replace(/\s+/g, " ") || control.getAttribute("aria-label") || control.name || "this field";
  };

  function closeSelect(state, restoreFocus = false) {
    if (!state) return;
    state.wrapper.classList.remove("open");
    state.button.setAttribute("aria-expanded", "false");
    state.button.removeAttribute("aria-activedescendant");
    if (openSelect === state) openSelect = null;
    if (restoreFocus) state.button.focus();
  }

  function syncSelect(state) {
    if (!state) return;
    const {select, button} = state;
    const selected = select.selectedOptions[0];
    button.querySelector("span").textContent = selected?.textContent || "Choose an option";
    button.disabled = select.disabled;
    button.classList.toggle("placeholder", !select.value);
  }

  function refreshSelect(select) {
    const state = selectState.get(select);
    if (!state) return;
    syncSelect(state);
    if (state.wrapper.classList.contains("open")) renderSelectOptions(state);
  }

  function renderSelectOptions(state) {
    const {select, listbox} = state;
    listbox.replaceChildren();
    state.options = Array.from(select.options).map((option, index) => {
      const item = document.createElement("div");
      item.id = `${state.id}-option-${index}`;
      item.className = "theme-select-option";
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", String(option.selected));
      item.setAttribute("aria-disabled", String(option.disabled));
      item.textContent = option.textContent;
      item.addEventListener("pointerdown", event => event.preventDefault());
      item.addEventListener("click", () => {
        if (option.disabled) return;
        select.value = option.value;
        select.dispatchEvent(new Event("change", {bubbles: true}));
        syncSelect(state);
        closeSelect(state, true);
      });
      listbox.append(item);
      return item;
    });
    state.activeIndex = Math.max(0, Array.from(select.options).findIndex(option => option.selected));
    updateActiveSelectOption(state);
  }

  function updateActiveSelectOption(state) {
    state.options?.forEach((option, index) => option.classList.toggle("active", index === state.activeIndex));
    const active = state.options?.[state.activeIndex];
    if (active) {
      state.button.setAttribute("aria-activedescendant", active.id);
      active.scrollIntoView({block: "nearest"});
    }
  }

  function moveSelectOption(state, direction) {
    const options = Array.from(state.select.options);
    if (!options.length) return;
    let next = state.activeIndex;
    do next = (next + direction + options.length) % options.length;
    while (options[next].disabled && next !== state.activeIndex);
    state.activeIndex = next;
    updateActiveSelectOption(state);
  }

  function openThemeSelect(state) {
    if (state.select.disabled) return;
    if (openSelect && openSelect !== state) closeSelect(openSelect);
    renderSelectOptions(state);
    state.wrapper.classList.add("open");
    state.button.setAttribute("aria-expanded", "true");
    openSelect = state;
  }

  function enhanceSelect(select) {
    if (selectState.has(select) || select.multiple || select.size > 1) return;
    const wrapper = document.createElement("span");
    const button = document.createElement("button");
    const listbox = document.createElement("span");
    const id = `theme-select-${crypto.randomUUID()}`;
    wrapper.className = "theme-select";
    button.type = "button";
    button.className = "theme-select-button";
    button.id = select.id ? `${select.id}-control` : `${id}-control`;
    button.setAttribute("role", "combobox");
    button.setAttribute("aria-haspopup", "listbox");
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-controls", id);
    button.setAttribute("aria-label", controlLabel(select));
    button.innerHTML = '<span></span><i aria-hidden="true"></i>';
    listbox.id = id;
    listbox.className = "theme-select-list";
    listbox.setAttribute("role", "listbox");
    select.before(wrapper);
    wrapper.append(select, button, listbox);
    select.classList.add("theme-native-select");
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");
    const state = {select, wrapper, button, listbox, id, options: [], activeIndex: 0};
    selectState.set(select, state);
    syncSelect(state);

    button.addEventListener("click", () => state.wrapper.classList.contains("open") ? closeSelect(state) : openThemeSelect(state));
    button.addEventListener("keydown", event => {
      if (["ArrowDown", "ArrowUp"].includes(event.key)) {
        event.preventDefault();
        if (!state.wrapper.classList.contains("open")) openThemeSelect(state);
        else moveSelectOption(state, event.key === "ArrowDown" ? 1 : -1);
      } else if (event.key === "Home" && state.wrapper.classList.contains("open")) {
        event.preventDefault(); state.activeIndex = 0; updateActiveSelectOption(state);
      } else if (event.key === "End" && state.wrapper.classList.contains("open")) {
        event.preventDefault(); state.activeIndex = state.options.length - 1; updateActiveSelectOption(state);
      } else if (["Enter", " "].includes(event.key) && state.wrapper.classList.contains("open")) {
        event.preventDefault(); state.options[state.activeIndex]?.click();
      } else if (event.key === "Escape" && state.wrapper.classList.contains("open")) {
        event.preventDefault(); closeSelect(state, true);
      }
    });
    select.addEventListener("change", () => refreshSelect(select));
    new MutationObserver(() => refreshSelect(select)).observe(select, {attributes: true, childList: true, subtree: true});
    if (select.id) {
      document.querySelectorAll(`label[for="${CSS.escape(select.id)}"]`).forEach(label => label.addEventListener("click", event => {
        event.preventDefault(); button.focus();
      }));
    }
  }

  function datalistValues(input) {
    const list = document.getElementById(input.dataset.themeList);
    const query = input.value.trim().toLocaleLowerCase();
    return Array.from(list?.options || [])
      .map(option => option.value)
      .filter(value => value && (!query || value.toLocaleLowerCase().includes(query)))
      .slice(0, 10);
  }

  function closeDatalist(state) {
    state.host.classList.remove("open");
    state.input.setAttribute("aria-expanded", "false");
    state.input.removeAttribute("aria-activedescendant");
    state.activeIndex = -1;
  }

  function updateDatalistActive(state) {
    state.items.forEach((item, index) => item.classList.toggle("active", index === state.activeIndex));
    const active = state.items[state.activeIndex];
    if (active) {
      state.input.setAttribute("aria-activedescendant", active.id);
      active.scrollIntoView({block: "nearest"});
    }
  }

  function renderDatalist(state) {
    const values = datalistValues(state.input);
    state.listbox.replaceChildren();
    state.items = values.map((value, index) => {
      const item = document.createElement("div");
      item.id = `${state.id}-option-${index}`;
      item.className = "theme-combobox-option";
      item.setAttribute("role", "option");
      item.textContent = value;
      item.addEventListener("pointerdown", event => event.preventDefault());
      item.addEventListener("click", () => {
        state.input.value = value;
        state.input.dispatchEvent(new Event("input", {bubbles: true}));
        state.input.dispatchEvent(new Event("change", {bubbles: true}));
        closeDatalist(state);
        state.input.focus();
      });
      state.listbox.append(item);
      return item;
    });
    state.activeIndex = values.length ? 0 : -1;
    state.host.classList.toggle("open", Boolean(values.length));
    state.input.setAttribute("aria-expanded", String(Boolean(values.length)));
    updateDatalistActive(state);
  }

  function enhanceDatalist(input) {
    if (listState.has(input) || !input.hasAttribute("list")) return;
    const id = `theme-combobox-${crypto.randomUUID()}`;
    const listbox = document.createElement("span");
    input.dataset.themeList = input.getAttribute("list");
    input.removeAttribute("list");
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", id);
    listbox.id = id;
    listbox.className = "theme-combobox-list";
    listbox.setAttribute("role", "listbox");
    input.after(listbox);
    const host = input.parentElement;
    host.classList.add("theme-combobox-host");
    const state = {input, host, listbox, id, items: [], activeIndex: -1};
    listState.set(input, state);
    input.addEventListener("focus", () => renderDatalist(state));
    input.addEventListener("input", () => renderDatalist(state));
    input.addEventListener("blur", () => setTimeout(() => closeDatalist(state), 0));
    input.addEventListener("keydown", event => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (!state.host.classList.contains("open")) renderDatalist(state);
        if (!state.items.length) return;
        state.activeIndex = (state.activeIndex + (event.key === "ArrowDown" ? 1 : -1) + state.items.length) % state.items.length;
        updateDatalistActive(state);
      } else if (event.key === "Enter" && state.host.classList.contains("open") && state.activeIndex >= 0) {
        event.preventDefault(); state.items[state.activeIndex].click();
      } else if (event.key === "Escape") {
        closeDatalist(state);
      }
    });
  }

  function enhance(root = document) {
    if (root.matches?.("select")) enhanceSelect(root);
    root.querySelectorAll?.("select").forEach(enhanceSelect);
    if (root.matches?.("input[list]")) enhanceDatalist(root);
    root.querySelectorAll?.("input[list]").forEach(enhanceDatalist);
    if (root.matches?.("form")) root.noValidate = true;
    root.querySelectorAll?.("form").forEach(form => { form.noValidate = true; });
  }

  function clearFieldError(control) {
    const errorId = control.dataset.themeError;
    if (!errorId) return;
    document.getElementById(errorId)?.remove();
    control.removeAttribute("aria-invalid");
    const describedBy = (control.getAttribute("aria-describedby") || "").split(/\s+/).filter(id => id && id !== errorId);
    if (describedBy.length) control.setAttribute("aria-describedby", describedBy.join(" "));
    else control.removeAttribute("aria-describedby");
    delete control.dataset.themeError;
  }

  function showFieldError(control) {
    clearFieldError(control);
    const error = document.createElement("p");
    const errorId = `theme-error-${crypto.randomUUID()}`;
    error.id = errorId;
    error.className = "theme-field-error";
    error.textContent = control.validationMessage || `Check ${controlLabel(control)} and try again.`;
    control.setAttribute("aria-invalid", "true");
    control.dataset.themeError = errorId;
    control.setAttribute("aria-describedby", [control.getAttribute("aria-describedby"), errorId].filter(Boolean).join(" "));
    const field = control.closest(".step-field") || control.closest("label") || control.parentElement;
    field.append(error);
    const select = selectState.get(control);
    (select?.button || control).focus();
  }

  function validateForm(form) {
    Array.from(form.elements).forEach(control => clearFieldError(control));
    const invalid = Array.from(form.elements).find(control => typeof control.checkValidity === "function" && !control.checkValidity());
    if (!invalid) return true;
    showFieldError(invalid);
    return false;
  }

  async function confirmAction({title = "Confirm action", message, confirmLabel = "Confirm", tone = "danger"}) {
    const dialog = document.querySelector("#theme-confirm");
    const trigger = document.activeElement;
    dialog.querySelector("#theme-confirm-title").textContent = title;
    dialog.querySelector("#theme-confirm-message").textContent = message;
    const accept = dialog.querySelector("#theme-confirm-accept");
    accept.textContent = confirmLabel;
    accept.classList.toggle("danger-button", tone === "danger");
    dialog.returnValue = "cancel";
    const result = new Promise(resolve => dialog.addEventListener("close", () => {
      resolve(dialog.returnValue === "confirm");
      if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus();
    }, {once: true}));
    dialog.showModal();
    return result;
  }

  document.addEventListener("click", event => {
    if (openSelect && !openSelect.wrapper.contains(event.target)) closeSelect(openSelect);
  });
  document.addEventListener("submit", event => {
    if (!validateForm(event.target)) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);
  document.addEventListener("input", event => clearFieldError(event.target));
  document.addEventListener("change", event => clearFieldError(event.target));
  document.addEventListener("reset", event => setTimeout(() => event.target.querySelectorAll("select").forEach(select => syncSelect(selectState.get(select))), 0));

  enhance();
  new MutationObserver(mutations => mutations.forEach(mutation => mutation.addedNodes.forEach(node => {
    if (node instanceof HTMLElement) enhance(node);
  }))).observe(document.body, {childList: true, subtree: true});

  window.ThemeControls = {confirm: confirmAction, validateForm, refreshSelect};
})();
