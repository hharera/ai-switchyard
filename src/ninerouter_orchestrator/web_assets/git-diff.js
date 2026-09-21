const GitDiff = (() => {
  const escape = value => String(value ?? "").replace(/[&<>'"]/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  })[character]);

  function parse(patch) {
    let oldLine = 0;
    let newLine = 0;
    let inHunk = false;
    const rows = [];
    for (const line of patch.split("\n")) {
      const hunk = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      if (hunk) {
        oldLine = Number(hunk[1]); newLine = Number(hunk[2]); inHunk = true;
        rows.push({kind: "hunk", text: line});
      } else if (inHunk && line.startsWith("\\")) {
        rows.push({kind: "meta", text: line});
      } else if (inHunk && /^[ +\-]/.test(line)) {
        const kind = line[0] === "+" ? "addition" : line[0] === "-" ? "deletion" : "context";
        rows.push({kind, text: line.slice(1), old: kind === "addition" ? "" : oldLine++, new: kind === "deletion" ? "" : newLine++});
      } else {
        inHunk = false;
      }
    }
    return rows;
  }

  function cell(row, side) {
    if (!row) return '<td class="diff-line-number diff-blank"></td><td class="diff-code diff-blank"></td>';
    const sign = row.kind === "addition" ? "+" : row.kind === "deletion" ? "-" : " ";
    return `<td class="diff-line-number diff-${row.kind}">${row[side]}</td><td class="diff-code diff-${row.kind}"><code><span class="diff-sign">${sign}</span>${escape(row.text)}</code></td>`;
  }

  function render(file, layout = "unified") {
    const message = (title, body) => `<div class="git-diff-message"><b>${title}</b><span>${escape(body)}</span></div>`;
    if (file.message) return message("Preview unavailable", file.message);
    if (file.binary) return message("Binary file changed", "A line-by-line preview is not available.");
    const parsed = parse(file.patch || "");
    if (!parsed.length) {
      const detail = file.status === "untracked" ? "This is an empty file."
        : file.status === "renamed" ? `Renamed from ${file.old_path}. No text changes.`
          : "No net text changes against this comparison. File metadata may have changed, or staged and unstaged edits may cancel out.";
      return message("No line changes to show", detail);
    }
    const rows = [];
    for (let index = 0; index < parsed.length; index++) {
      const row = parsed[index];
      if (row.kind === "hunk" || row.kind === "meta") {
        rows.push(`<tr class="diff-${row.kind}"><td colspan="${layout === "split" ? 4 : 3}"><code>${escape(row.text)}</code></td></tr>`);
      } else if (layout === "split") {
        if (row.kind === "context") {
          rows.push(`<tr>${cell(row, "old")}${cell(row, "new")}</tr>`);
        } else {
          const removed = [], added = [];
          while (index < parsed.length && ["addition", "deletion"].includes(parsed[index].kind)) {
            (parsed[index].kind === "deletion" ? removed : added).push(parsed[index++]);
          }
          index--;
          for (let pair = 0; pair < Math.max(removed.length, added.length); pair++) {
            rows.push(`<tr>${cell(removed[pair], "old")}${cell(added[pair], "new")}</tr>`);
          }
        }
      } else {
        const sign = row.kind === "addition" ? "+" : row.kind === "deletion" ? "-" : " ";
        rows.push(`<tr class="diff-${row.kind}"><td class="diff-line-number">${row.old}</td><td class="diff-line-number">${row.new}</td><td class="diff-code"><code><span class="diff-sign">${sign}</span>${escape(row.text)}</code></td></tr>`);
      }
    }
    const warning = file.truncated ? '<p class="git-truncated">Preview limited to 4,000 lines or 256 KB. Inspect the file locally for the complete diff.</p>' : "";
    const columns = layout === "split"
      ? '<col class="diff-number-col"><col><col class="diff-number-col"><col>'
      : '<col class="diff-number-col"><col class="diff-number-col"><col>';
    const headings = layout === "split" ? '<th scope="col">Old line</th><th scope="col">Before</th><th scope="col">New line</th><th scope="col">After</th>'
      : '<th scope="col">Old</th><th scope="col">New</th><th scope="col">Change</th>';
    return `${warning}<div class="git-diff-scroll" tabindex="0" role="region" aria-label="${escape(file.path)} diff"><table class="git-diff-table ${layout}" aria-label="Line-by-line file changes"><colgroup>${columns}</colgroup><thead><tr>${headings}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
  }

  return {parse, render};
})();
