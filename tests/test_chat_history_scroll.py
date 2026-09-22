import subprocess
from pathlib import Path


def test_open_history_scrolls_to_last_message():
    script = Path("src/ninerouter_orchestrator/web_assets/chat.js").read_text(encoding="utf-8")
    rendering = script[script.index("  function displayMessages("):script.index("  function formatDate(")]
    opening = script[script.index("  async function openHistory("):script.index("  async function loadTools(")]
    harness = r'''
const assert = require("node:assert/strict");
const elements = new Map();
const document = {
  createElement() { return {append() {}}; },
  querySelector(selector) {
    if (!elements.has(selector)) elements.set(selector, {focus() {}});
    return elements.get(selector);
  },
};
const list = {
  scrollTop: 0, scrollHeight: 0,
  replaceChildren(...items) { this.scrollHeight = items.length * 100; },
};
const empty = {}, input = {value: "Unsent draft"};
const historyDetail = {}, historyDetailStatus = {};
const panel = {classList: {add() {}}};
const sources = [], state = {};
const conversation = () => state;
const activeWorkspace = () => ({id: "workspace"});
const renderHistory = () => {};
const renderStructuredJson = () => "";
let detailRequest = 0, selectedHistory = null;
let messages = Array.from({length: 30}, (_, i) => ({role: "user", content: `Message ${i}`}));
const fetch = async () => ({ok: true, json: async () => ({messages})});
'''
    checks = r'''
(async () => {
  const item = {id: "history", title: "Conversation", source: "codex"};
  await openHistory(item);
  assert.equal(list.scrollTop, 3000);
  assert.equal(list.scrollTop, list.scrollHeight);
  assert.equal(list.hidden, false);
  assert.equal(state.draft, "Unsent draft");

  // Reopening or refreshing starts at the latest message, even after scrolling up.
  list.scrollTop = 100;
  messages.push({role: "assistant", content: "Latest reply"});
  await openHistory(item);
  assert.equal(list.scrollTop, 3100);

  messages = [];
  await openHistory(item);
  assert.equal(list.hidden, true);
  assert.equal(list.scrollTop, 0);
})().catch(error => { console.error(error); process.exit(1); });
'''
    subprocess.run(["node"], input=harness + rendering + opening + checks,
                   text=True, encoding="utf-8", check=True)
