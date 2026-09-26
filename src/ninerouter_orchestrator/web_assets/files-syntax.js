(() => {
  const escape = value => value.replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
  const keywords = new Set(("abstract as async await assert break case catch class const continue "
    + "def default del do elif else enum except export extends final finally for from function "
    + "if implements import in instanceof interface is lambda let match new not of or package "
    + "pass private protected public raise return static struct super switch this throw throws "
    + "try type typeof var void while with yield and True False None true false null undefined").split(" "));

  // A small lexical colorizer, not a parser: editing never depends on valid syntax.
  function highlight(source, language) {
    if (source.length > 200000 || ["Plain text", "Markdown"].includes(language)) return escape(source);
    const hashComments = ["Python", "Shell", "YAML", "TOML", "Dockerfile", "Makefile"].includes(language);
    const tokens = /<!--[^]*?(?:-->|$)|\/\*[^]*?(?:\*\/|$)|\/\/[^\n]*|#[^\n]*|"""[^]*?(?:"""|$)|'''[^]*?(?:'''|$)|"(?:\\[^]|[^"\\])*"|'(?:\\[^]|[^'\\])*'|`(?:\\[^]|[^`\\])*`|\b\d+(?:\.\d+)?\b|\b[A-Za-z_$][\w$]*\b/g;
    let result = "";
    let offset = 0;
    for (const match of source.matchAll(tokens)) {
      const token = match[0];
      result += escape(source.slice(offset, match.index));
      let kind = "";
      if (/^(\/\/|\/\*|<!--)/.test(token) || (hashComments && token.startsWith("#"))) kind = "comment";
      else if (/^["'`]/.test(token)) kind = "string";
      else if (/^\d/.test(token)) kind = "number";
      else if (keywords.has(token)) kind = "keyword";
      result += kind ? `<span class="files-token-${kind}">${escape(token)}</span>` : escape(token);
      offset = match.index + token.length;
    }
    return result + escape(source.slice(offset));
  }
  window.FileEditorSyntax = {highlight};
})();
