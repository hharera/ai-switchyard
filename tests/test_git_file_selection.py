import subprocess
from pathlib import Path


def test_bulk_selection_respects_filter_and_updates_checkbox_state():
    source = Path("src/ninerouter_orchestrator/web_assets/git-view.js").read_text(
        encoding="utf-8"
    )
    # Run the production event bindings and selection helpers without loading Git.
    functions = source[
        source.index("  function bindSnapshotEvents()"):
        source.index("  function statusLetter(")
    ]
    harness = r'''
const assert = require("node:assert/strict");
function element(properties = {}) {
  return {
    checked: false, hidden: false, disabled: false, listeners: {}, ...properties,
    addEventListener(type, callback) { this.listeners[type] = callback; },
    emit(type) { this.listeners[type]({target: this}); },
  };
}
const rows = ["src/app.js", "src/git.js", "tests/git.py"].map(path =>
  element({dataset: {path}}));
const inputs = rows.map(row => element({value: row.dataset.path, closest: () => row}));
const filter = element({value: ""});
const all = element();
const count = element();
const empty = element();
const content = {
  querySelector(selector) {
    return {"#git-file-filter": filter, "#git-select-all": all,
      "#git-selected-count": count, "#git-filter-empty": empty}[selector] || null;
  },
  querySelectorAll(selector) {
    if (selector === ".git-file-row") return rows;
    if (selector === "[data-git-path-select]") return inputs;
    if (selector === "[data-git-path-select]:checked") return inputs.filter(input => input.checked);
    return [];
  },
};
function search(value) { filter.value = value; filter.emit("input"); }
function selectAll(checked) { all.checked = checked; all.emit("change"); }
'''
    checks = r'''
bindSnapshotEvents();
search("SRC/");
selectAll(true);
assert.deepEqual(selectedPaths(), ["src/app.js", "src/git.js"]);
assert.equal(count.textContent, "2 selected");
assert.equal(all.checked, true);
assert.equal(all.indeterminate, false);
assert.equal(inputs[2].checked, false);

search("");
assert.equal(all.checked, false);
assert.equal(all.indeterminate, true);
selectAll(true);
assert.equal(selectedPaths().length, 3);

search("src/");
selectAll(false);
assert.deepEqual(selectedPaths(), ["tests/git.py"]);
assert.equal(count.textContent, "1 selected");
assert.equal(all.checked, false);
assert.equal(all.indeterminate, false);
inputs[0].checked = true;
inputs[0].emit("change");
assert.equal(all.indeterminate, true);

search("missing");
assert.equal(empty.hidden, false);
assert.equal(all.disabled, true);
assert.equal(all.checked, false);
assert.equal(all.indeterminate, false);
selectAll(true);
assert.deepEqual(selectedPaths(), ["src/app.js", "tests/git.py"]);

search("");
assert.equal(empty.hidden, true);
assert.equal(all.disabled, false);
assert.equal(all.indeterminate, true);
selectAll(false);
assert.deepEqual(selectedPaths(), []);
assert.equal(count.textContent, "0 selected");
assert.equal(all.indeterminate, false);
'''
    subprocess.run(["node", "-e", harness + functions + checks], check=True)
