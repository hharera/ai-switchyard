function clampPaneWidth(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, Math.round(Number(value) || minimum)));
}

function keyboardPaneWidth(current, key, minimum, maximum, direction = "ltr", largeStep = false) {
  if (key === "Home") return minimum;
  if (key === "End") return maximum;
  if (!['ArrowLeft', 'ArrowRight'].includes(key)) return current;
  const inlineDirection = key === "ArrowRight" ? 1 : -1;
  const delta = inlineDirection * (direction === "rtl" ? -1 : 1) * (largeStep ? 40 : 10);
  return clampPaneWidth(current + delta, minimum, maximum);
}

(() => {
  if (typeof document === "undefined") return;
  const desktop = window.matchMedia("(min-width: 721px)");
  const direction = () => getComputedStyle(document.documentElement).direction;
  const paneDefinitions = [
    {
      handle: document.querySelector("#sidebar-resizer"),
      property: "--sidebar-width",
      storage: "switchyard.sidebar-width",
      minimum: 190,
      maximum: () => Math.min(360, Math.max(190, window.innerWidth - 520)),
      initial: 232,
      position: event => direction() === "rtl" ? window.innerWidth - event.clientX : event.clientX,
    },
    {
      handle: document.querySelector("#chat-history-resizer"),
      property: "--chat-history-width",
      storage: "switchyard.chat-history-width",
      minimum: 240,
      maximum: () => {
        const shell = document.querySelector(".chat-shell");
        const sidebarWidth = Number.parseFloat(document.documentElement.style.getPropertyValue("--sidebar-width"));
        return Math.min(560, Math.max(240, (shell?.clientWidth || window.innerWidth - sidebarWidth) - 420));
      },
      initial: 320,
      position: event => {
        const bounds = document.querySelector(".chat-shell").getBoundingClientRect();
        return direction() === "rtl" ? bounds.right - event.clientX : event.clientX - bounds.left;
      },
    },
  ];

  function storedWidth(pane) {
    try {
      const value = Number(localStorage.getItem(pane.storage));
      return Number.isFinite(value) && value > 0 ? value : pane.initial;
    }
    catch { return pane.initial; }
  }

  function attachPane(pane) {
    if (!pane.handle) return null;
    pane.preferred = storedWidth(pane);
    pane.width = pane.initial;
    let pointer = null;
    let offset = 0;

    const applyWidth = () => {
      const maximum = pane.maximum();
      const width = clampPaneWidth(pane.preferred, pane.minimum, maximum);
      pane.width = width;
      document.documentElement.style.setProperty(pane.property, `${width}px`);
      pane.handle.setAttribute("aria-valuemax", String(maximum));
      pane.handle.setAttribute("aria-valuenow", String(width));
    };
    const save = () => {
      pane.preferred = pane.width;
      try { localStorage.setItem(pane.storage, String(pane.preferred)); }
      catch { /* Resizing still works when browser storage is unavailable. */ }
    };

    const start = event => {
      if (!desktop.matches || event.button !== 0 || pointer !== null) return;
      pointer = event.pointerId;
      offset = pane.position(event) - pane.width;
      pane.handle.setPointerCapture(event.pointerId);
      pane.handle.focus({preventScroll: true});
      pane.handle.classList.add("is-resizing");
      document.body.classList.add("is-resizing-pane");
      event.preventDefault();
    };
    const move = event => {
      if (pointer !== event.pointerId) return;
      pane.preferred = clampPaneWidth(pane.position(event) - offset, pane.minimum, pane.maximum());
      applyWidth();
    };
    const finish = event => {
      if (pointer !== event.pointerId) return;
      pointer = null;
      if (pane.handle.hasPointerCapture(event.pointerId)) pane.handle.releasePointerCapture(event.pointerId);
      pane.handle.classList.remove("is-resizing");
      document.body.classList.remove("is-resizing-pane");
      save();
    };
    const keydown = event => {
      if (!desktop.matches || !["Home", "End", "ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault();
      pane.preferred = keyboardPaneWidth(
        pane.width, event.key, pane.minimum, pane.maximum(), direction(), event.shiftKey
      );
      applyWidth();
      save();
    };
    const reset = () => {
      if (!desktop.matches) return;
      pane.preferred = pane.initial;
      applyWidth();
      save();
    };

    pane.handle.addEventListener("pointerdown", start);
    pane.handle.addEventListener("pointermove", move);
    pane.handle.addEventListener("pointerup", finish);
    pane.handle.addEventListener("pointercancel", finish);
    pane.handle.addEventListener("lostpointercapture", finish);
    pane.handle.addEventListener("keydown", keydown);
    pane.handle.addEventListener("dblclick", reset);
    window.addEventListener("resize", applyWidth);
    const observer = pane.observe && typeof ResizeObserver !== "undefined"
      ? new ResizeObserver(applyWidth)
      : null;
    if (observer) observer.observe(pane.observe);
    applyWidth();

    return {
      refresh: applyWidth,
      destroy() {
        if (pointer !== null) finish({pointerId: pointer});
        pane.handle.removeEventListener("pointerdown", start);
        pane.handle.removeEventListener("pointermove", move);
        pane.handle.removeEventListener("pointerup", finish);
        pane.handle.removeEventListener("pointercancel", finish);
        pane.handle.removeEventListener("lostpointercapture", finish);
        pane.handle.removeEventListener("keydown", keydown);
        pane.handle.removeEventListener("dblclick", reset);
        window.removeEventListener("resize", applyWidth);
        observer?.disconnect();
      },
    };
  }

  window.PaneResize = {attach: attachPane};
  paneDefinitions.forEach(pane => {
    if (pane.handle?.id === "chat-history-resizer") pane.observe = document.querySelector(".chat-shell");
    attachPane(pane);
  });
})();
