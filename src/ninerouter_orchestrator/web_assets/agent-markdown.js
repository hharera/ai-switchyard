(() => {
  const escape = value => String(value).replace(/[&<>"']/g, character => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[character]));

  function filePath(target, workspace, repository) {
    if (!workspace || /[\\\u0000-\u001f]/.test(target) || target.startsWith("//")) return null;
    const root = workspace.replace(/\/$/, "");
    const path = target.replace(/(?::\d+(?::\d+)?|#L\d+(?:C\d+)?)$/, "");
    if (/^[a-z][a-z\d+.-]*:/i.test(path)) return null;
    const absolute = path.startsWith("/") ? path : `${repository || root}/${path}`;
    const parts = [];
    for (const part of absolute.split("/")) {
      if (part === "..") parts.pop();
      else if (part && part !== ".") parts.push(part);
    }
    const normalized = `/${parts.join("/")}`;
    return normalized.startsWith(`${root}/`) ? normalized.slice(root.length + 1) : null;
  }

  // Raw HTML is always escaped; only these generated tags and validated links are emitted.
  function inline(text, options, depth = 0) {
    if (depth > 8) return escape(text);
    const tokens = /(`+)([^`]*?)\1|\[([^\]\n]+)\]\((<[^>\n]+>|[^\s)]+)\)|\*\*([^*\n]+)\*\*|__([^_\n]+)__|\*([^*\n]+)\*|_([^_\n]+)_|\\([\\`*_[\]{}()#+.!>-])/g;
    let result = "", offset = 0;
    for (const match of text.matchAll(tokens)) {
      result += escape(text.slice(offset, match.index));
      if (match[1]) result += `<code>${escape(match[2])}</code>`;
      else if (match[3]) {
        const target = match[4].replace(/^<|>$/g, "");
        const label = inline(match[3], {}, depth + 1);
        const path = filePath(target, options.workspace, options.repository);
        if (path) result += `<button type="button" class="agent-file-link" data-agent-file="${escape(path)}" title="Open in Files">${label}</button>`;
        else if (/^https?:\/\//i.test(target) && !/[\u0000-\u0020\\]/.test(target)) result += `<a href="${escape(target)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
        else result += label;
      } else if (match[5] || match[6]) result += `<strong>${inline(match[5] || match[6], options, depth + 1)}</strong>`;
      else if (match[7] || match[8]) result += `<em>${inline(match[7] || match[8], options, depth + 1)}</em>`;
      else result += escape(match[9]);
      offset = match.index + match[0].length;
    }
    return result + escape(text.slice(offset));
  }

  function render(value, options = {}) {
    const lines = String(value || "").replace(/\r\n?/g, "\n").split("\n");
    const blocks = [];
    let paragraph = [], list = null, fence = null, code = [];
    const flushParagraph = () => {
      if (paragraph.length) blocks.push(`<p>${inline(paragraph.join("\n"), options)}</p>`);
      paragraph = [];
    };
    const closeList = () => { if (list) blocks.push(`</${list}>`); list = null; };
    for (const line of lines) {
      if (fence) {
        if (new RegExp(`^\\s{0,3}${fence[0]}{${fence.length},}\\s*$`).test(line)) {
          blocks.push(`<pre><code>${escape(code.join("\n"))}</code></pre>`);
          fence = null; code = [];
        } else code.push(line);
        continue;
      }
      const startFence = line.match(/^\s{0,3}(`{3,}|~{3,})/);
      const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+)$/);
      const item = line.match(/^\s*(?:([-+*])|\d+[.)])\s+(.+)$/);
      if (startFence) { flushParagraph(); closeList(); fence = startFence[1]; }
      else if (!line.trim()) { flushParagraph(); closeList(); }
      else if (heading) {
        flushParagraph(); closeList();
        const level = Math.min(heading[1].length + 2, 6);
        blocks.push(`<h${level}>${inline(heading[2], options)}</h${level}>`);
      } else if (item) {
        flushParagraph();
        const type = item[1] ? "ul" : "ol";
        if (list !== type) { closeList(); list = type; blocks.push(`<${type}>`); }
        blocks.push(`<li>${inline(item[2], options)}</li>`);
      } else { closeList(); paragraph.push(line); }
    }
    flushParagraph(); closeList();
    if (fence) blocks.push(`<pre><code>${escape(code.join("\n"))}</code></pre>`);
    return blocks.join("\n");
  }

  window.AgentMarkdown = {render};
})();
