(() => {
  const labels = {
    stage: "Stage changes", unstage: "Unstage changes",
    discard: "Discard tracked edits", delete_untracked: "Delete untracked files",
  };

  function eligible(files, action) {
    return files.filter(file => {
      if (action === "stage") return file.unstaged || ["untracked", "conflicted"].includes(file.status);
      if (action === "unstage") return file.staged;
      if (action === "discard") return file.unstaged && !["untracked", "conflicted"].includes(file.status);
      if (action === "delete_untracked") return file.status === "untracked";
      return true;
    });
  }

  function index(files) {
    const scopes = new Map([[".", files]]);
    const ghosts = new Map();
    const add = (path, file) => {
      if (!scopes.has(path)) scopes.set(path, []);
      if (!scopes.get(path).includes(file)) scopes.get(path).push(file);
    };
    files.forEach(file => {
      [file.path, file.old_path].filter(Boolean).forEach(path => {
        const parts = path.split("/");
        for (let length = 1; length <= parts.length; length++) add(parts.slice(0, length).join("/"), file);
        // Deleted and renamed-away paths still need a place in the lazy tree.
        if (file.code.includes("D") || path === file.old_path) {
          parts.forEach((name, position) => {
            const parent = parts.slice(0, position).join("/");
            if (!ghosts.has(parent)) ghosts.set(parent, new Map());
            ghosts.get(parent).set(name, {
              name, path: parts.slice(0, position + 1).join("/"),
              type: position < parts.length - 1 ? "folder" : "file", missing: true,
            });
          });
        }
      });
    });
    return {
      filesFor: path => scopes.get(path) || [],
      entries: (entries, parent = "") => {
        const combined = new Map(ghosts.get(parent) || []);
        entries.forEach(entry => combined.set(entry.name, entry));
        return [...combined.values()].sort((a, b) =>
          Number(a.type !== "folder") - Number(b.type !== "folder") || a.name.localeCompare(b.name));
      },
    };
  }

  function badge(files, folder) {
    if (!files.length) return null;
    const conflict = files.some(file => file.status === "conflicted");
    const staged = files.filter(file => file.staged).length;
    const unstaged = files.filter(file => file.unstaged).length;
    const untracked = files.filter(file => file.status === "untracked").length;
    const state = conflict ? "conflicted" : folder ? "modified" : files[0].status;
    const code = {added: "A", copied: "C", deleted: "D", modified: "M", renamed: "R", "type changed": "T", conflicted: "!", untracked: "?"}[state] || "M";
    return {
      state,
      additions: files.reduce((sum, file) => sum + (file.additions || 0), 0),
      deletions: files.reduce((sum, file) => sum + (file.deletions || 0), 0),
      binary: files.every(file => file.binary),
      incomplete: files.some(file => file.stats_incomplete),
      text: folder ? `${conflict ? "! " : ""}${files.length}` : `${code}${staged ? " +" : ""}`,
      title: `${files.length} changed ${files.length === 1 ? "file" : "files"}; ${staged} staged, ${unstaged} unstaged, ${untracked} untracked${conflict ? "; conflicts need resolution" : ""}`,
    };
  }

  window.FileTreeGit = {labels, eligible, index, badge};
})();
