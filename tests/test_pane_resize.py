import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from ninerouter_orchestrator.web import app

client = TestClient(app)


def test_resizable_panes_are_accessible_and_served():
    page = client.get("/").text
    script = client.get("/assets/pane-resize.js")

    assert script.status_code == 200
    assert '<script src="/assets/pane-resize.js" defer></script>' in page
    assert 'id="sidebar-resizer" role="separator"' in page
    assert 'aria-controls="primary-sidebar"' in page
    assert 'id="chat-history-resizer" role="separator"' in page
    assert 'aria-controls="chat-history-pane"' in page
    assert page.count('aria-orientation="vertical"') == 2
    assert page.count('tabindex="0" title="Drag to resize.') == 2


def test_pane_width_math_clamps_and_supports_keyboard_control():
    script = Path("src/ninerouter_orchestrator/web_assets/pane-resize.js").read_text(
        encoding="utf-8"
    )
    command = script + """
console.log(JSON.stringify({
  low: clampPaneWidth(100, 190, 360),
  high: clampPaneWidth(500, 190, 360),
  right: keyboardPaneWidth(232, "ArrowRight", 190, 360),
  shifted: keyboardPaneWidth(232, "ArrowLeft", 190, 360, "ltr", true),
  rtl: keyboardPaneWidth(232, "ArrowRight", 190, 360, "rtl"),
  home: keyboardPaneWidth(300, "Home", 190, 360),
  end: keyboardPaneWidth(200, "End", 190, 360)
}));
"""
    result = json.loads(subprocess.check_output(
        ["node", "-e", command], text=True, encoding="utf-8"
    ))

    assert result == {
        "low": 190,
        "high": 360,
        "right": 242,
        "shifted": 192,
        "rtl": 222,
        "home": 190,
        "end": 360,
    }


def test_resizers_have_visible_grips_and_mobile_fallback():
    styles = client.get("/assets/styles.css").text
    chat = client.get("/assets/chat.css").text
    git_view = client.get("/assets/git-view.js").text
    resize_script = client.get("/assets/pane-resize.js").text

    assert "--sidebar-width: 232px" in styles
    assert "--git-file-nav-width: 280px" in styles
    assert ".pane-resizer::after" in styles
    assert ".pane-resizer:focus-visible" in styles
    assert ".pane-resizer { display: none; }" in styles
    assert "grid-template-columns: var(--chat-history-width) 16px minmax(0, 1fr)" in chat
    assert "grid-template-columns: var(--git-file-nav-width) 16px minmax(0, 1fr)" in styles
    assert 'id="git-review-resizer" role="separator"' in git_view
    assert 'aria-controls="git-file-nav git-diff-list"' in git_view
    assert 'storage: "switchyard.git-file-nav-width"' in git_view
    assert "window.PaneResize = {attach: attachPane}" in resize_script


def test_dynamic_pane_drag_keyboard_persistence_and_cleanup():
    script = Path("src/ninerouter_orchestrator/web_assets/pane-resize.js").read_text(
        encoding="utf-8"
    )
    harness = r'''
const assert = require("node:assert/strict");
function target() {
  const listeners = new Map();
  return {
    listeners, attrs: {}, capture: null, classList: new Set(),
    addEventListener(type, callback) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(callback);
    },
    removeEventListener(type, callback) { listeners.get(type)?.delete(callback); },
    emit(type, event = {}) {
      for (const callback of listeners.get(type) || []) callback({preventDefault() {}, ...event});
    },
    setAttribute(name, value) { this.attrs[name] = value; },
    setPointerCapture(id) { this.capture = id; },
    hasPointerCapture(id) { return this.capture === id; },
    releasePointerCapture() { this.capture = null; },
    focus() {},
  };
}
function element() {
  const el = target();
  el.classList.remove = value => el.classList.delete(value);
  return el;
}
const desktop = {matches: true};
const window = {...target(), matchMedia: () => desktop, innerWidth: 1400};
const properties = new Map();
const document = {
  querySelector: () => null,
  documentElement: {style: {setProperty: (name, value) => properties.set(name, value)}},
  body: element(),
};
let direction = "ltr";
const getComputedStyle = () => ({direction});
const stored = new Map([["test.width", "400"]]);
let storageBlocked = false;
const localStorage = {
  getItem(key) { if (storageBlocked) throw Error("blocked"); return stored.get(key); },
  setItem(key, value) { if (storageBlocked) throw Error("blocked"); stored.set(key, value); },
};
const observers = [];
class ResizeObserver {
  constructor(callback) { this.callback = callback; observers.push(this); }
  observe(el) { this.observed = el; }
  disconnect() { this.observed = null; }
}
'''
    checks = r'''
const handle = element();
let maximum = 640;
const options = {
  handle, property: "--test-width", storage: "test.width", minimum: 220,
  maximum: () => maximum, initial: 280, position: event => event.clientX,
  observe: {},
};
let pane = window.PaneResize.attach(options);
const width = () => Number(handle.attrs["aria-valuenow"]);
assert.equal(width(), 400);
handle.emit("keydown", {key: "ArrowRight"});
assert.equal(width(), 410);
handle.emit("keydown", {key: "ArrowLeft", shiftKey: true});
assert.equal(width(), 370);
direction = "rtl";
handle.emit("keydown", {key: "ArrowRight"});
assert.equal(width(), 360);
direction = "ltr";
handle.emit("keydown", {key: "Home"});
assert.equal(width(), 220);
handle.emit("keydown", {key: "End"});
assert.equal(width(), 640);
maximum = 300;
observers[0].callback();
assert.equal(width(), 300);
assert.equal(stored.get("test.width"), "640");
maximum = 640;
window.emit("resize");
assert.equal(width(), 640);
handle.emit("dblclick");
assert.equal(width(), 280);
handle.emit("pointerdown", {button: 0, pointerId: 1, clientX: 288});
handle.emit("pointermove", {pointerId: 2, clientX: 500});
assert.equal(width(), 280);
handle.emit("pointermove", {pointerId: 1, clientX: 408});
assert.equal(width(), 400);
handle.emit("pointerup", {pointerId: 1});
assert.equal(stored.get("test.width"), "400");
assert.equal(handle.capture, null);
desktop.matches = false;
handle.emit("keydown", {key: "End"});
handle.emit("dblclick");
handle.emit("pointerdown", {button: 0, pointerId: 1, clientX: 408});
assert.equal(width(), 400);
assert.equal(handle.capture, null);
desktop.matches = true;
handle.emit("pointerdown", {button: 0, pointerId: 1, clientX: 408});
pane.destroy();
assert.equal(handle.capture, null);
assert.equal(document.body.classList.has("is-resizing-pane"), false);
assert.equal(window.listeners.get("resize").size, 0);
assert.equal(observers[0].observed, null);
for (const callbacks of handle.listeners.values()) assert.equal(callbacks.size, 0);
pane = window.PaneResize.attach(options);
assert.equal(width(), 400);
pane.destroy();
storageBlocked = true;
pane = window.PaneResize.attach(options);
assert.equal(width(), 280);
handle.emit("keydown", {key: "ArrowRight"});
assert.equal(width(), 290);
pane.destroy();
'''
    subprocess.run(["node", "-e", harness + script + checks], check=True)
