import json
import subprocess
from pathlib import Path

ASSETS = Path("src/ninerouter_orchestrator/web_assets")


def run_node(source):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_syntax_highlighting_escapes_html_and_handles_incomplete_code():
    script = (ASSETS / "files-syntax.js").read_text()
    run_node("import assert from 'node:assert/strict'; const window = {};\n" + script + """
const highlight = window.FileEditorSyntax.highlight;
assert.match(highlight('const answer = 42;', 'JavaScript'), /files-token-keyword/);
assert.match(highlight('const answer = 42;', 'JavaScript'), /files-token-number/);
const escaped = highlight('<img src=x onerror="alert(1)">', 'HTML');
assert.ok(!escaped.includes('<img'));
assert.ok(escaped.includes('&lt;'));
assert.ok(!highlight('<script>', 'Plain text').includes('<script>'));
assert.doesNotThrow(() => highlight('/* unfinished', 'JavaScript'));
assert.match(highlight('# comment', 'Python'), /files-token-comment/);
assert.equal(highlight('x'.repeat(200001), 'Python'), 'x'.repeat(200001));
""")


def test_git_tree_index_includes_recursive_changes_and_deleted_ghosts():
    script = (ASSETS / "files-git.js").read_text()
    run_node("import assert from 'node:assert/strict'; const window = {};\n" + script + """
const files = [
  {path:'src/app.js',old_path:null,status:'modified',code:' M',staged:false,unstaged:true},
  {path:'src/removed.js',old_path:null,status:'deleted',code:' D',staged:false,unstaged:true},
  {path:'docs/new.md',old_path:null,status:'untracked',code:'??',staged:false,unstaged:false},
];
const index = window.FileTreeGit.index(files);
assert.equal(index.filesFor('src').length, 2);
assert.equal(window.FileTreeGit.eligible(index.filesFor('src'), 'stage').length, 2);
assert.equal(window.FileTreeGit.eligible(index.filesFor('src'), 'discard').length, 2);
assert.ok(index.entries([], 'src').some(entry => entry.path === 'src/removed.js' && entry.missing));
assert.equal(window.FileTreeGit.badge(index.filesFor('src'), true).text, '2');
files[0].additions = 4;
files[0].deletions = 2;
files[1].deletions = 3;
files[1].stats_incomplete = true;
const detail = window.FileTreeGit.badge(index.filesFor('src'), true);
assert.equal(detail.additions, 4);
assert.equal(detail.deletions, 5);
assert.equal(detail.incomplete, true);
assert.equal(detail.binary, false);
assert.equal(window.FileTreeGit.badge([{status:'untracked',binary:true}], false).binary, true);
""")


def test_folder_rename_remaps_open_tabs_without_losing_unsaved_buffers():
    script = (ASSETS / "files.js").read_text()
    rename = script[script.index("  function updateSessionPaths("):script.index("  async function performEntryAction(")]
    run_node("import assert from 'node:assert/strict';\n" + """
const draft = {path:'src/deep/file.txt',name:'file.txt',content:'unsaved',dirty:true,revision:'r'};
const untouched = {path:'src-other/file.txt',name:'file.txt',content:'sibling',dirty:true};
const current = {tabs:new Map([[draft.path,draft],[untouched.path,untouched]]),activePath:draft.path};
const session = () => current;
""" + rename + """
updateSessionPaths('src', 'renamed', 'folder');
assert.equal(current.activePath, 'renamed/deep/file.txt');
assert.equal(current.tabs.get(current.activePath).content, 'unsaved');
assert.equal(current.tabs.get(current.activePath).dirty, true);
assert.equal(current.tabs.get(current.activePath).revision, 'r');
assert.equal(current.tabs.get(untouched.path), untouched);
assert.ok(!current.tabs.has('src/deep/file.txt'));
""")


def test_save_captures_submitted_buffer_and_keeps_newer_edits():
    script = (ASSETS / "files.js").read_text()
    save = script[script.index("  async function saveActive()"):script.index("  async function reloadActive()")]
    run_node("import assert from 'node:assert/strict';\n" + """
const file = {path:'app.py', content:'old\\n', dirty:true, revision:'before', line_ending:'CRLF'};
const gitActionRunning = false;
const loadGitStatus = () => {};
const window = {dispatchEvent: () => {}};
const CustomEvent = class {};
const activeFile = () => file;
const workspace = {id:'alpha'};
const currentSession = {};
const session = () => currentSession;
const saveButton = {disabled:false};
const reload = {disabled:false};
const document = {querySelector: () => reload};
let savedBody;
let resolveSave;
let statuses = [];
const setStatus = text => statuses.push(text);
const setError = () => {};
const renderTabs = () => {};
const updateDirtyCount = () => {};
const request = (url, options) => {
  savedBody = JSON.parse(options.body);
  return new Promise(resolve => {resolveSave = resolve;});
};
""" + save + """
const pending = saveActive();
assert.equal(file.saving, true);
assert.equal(saveButton.disabled, true);
assert.equal(savedBody.content, 'old\\r\\n');
file.content = 'newer\\n';
await saveActive(); // Duplicate saves must not replace the in-flight request.
resolveSave({revision:'after',size:5,line_ending:'CRLF'});
await pending;
assert.equal(file.revision, 'after');
assert.equal(file.savedContent, 'old\\n');
assert.equal(file.content, 'newer\\n');
assert.equal(file.dirty, true);
assert.equal(file.saving, false);
assert.equal(saveButton.disabled, false);
assert.match(statuses.at(-1), /Newer edits are still unsaved/);
""")


def test_save_conflict_keeps_original_revision_and_buffer():
    script = (ASSETS / "files.js").read_text()
    save = script[script.index("  async function saveActive()"):script.index("  async function reloadActive()")]
    run_node("import assert from 'node:assert/strict';\n" + """
const file = {path:'app.py', content:'local', savedContent:'old', dirty:true, revision:'before', line_ending:'LF'};
const gitActionRunning = false;
const activeFile = () => file;
const workspace = {id:'alpha'};
const currentSession = {};
const session = () => currentSession;
const saveButton = {disabled:false};
const document = {querySelector: () => ({})};
let failure;
const setStatus = () => {};
const setError = text => {failure = text;};
const renderTabs = () => {};
const updateDirtyCount = () => {};
const request = async () => {throw new Error('Changed on disk');};
""" + save + """
await saveActive();
assert.equal(failure, 'Changed on disk');
assert.equal(file.revision, 'before');
assert.equal(file.content, 'local');
assert.equal(file.savedContent, 'old');
assert.equal(file.dirty, true);
assert.equal(saveButton.disabled, false);
""")


def test_new_editor_javascript_parses():
    for name in ["files.js", "files-syntax.js", "files-git.js", "agent-markdown.js", "inline-agent.js"]:
        result = subprocess.run(
            ["node", "--check", str(ASSETS / name)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, json.dumps(result.stderr)


def test_agent_markdown_formats_content_without_trusting_html_or_links():
    script = (ASSETS / "agent-markdown.js").read_text()
    run_node("import assert from 'node:assert/strict'; const window = {};\n" + script + r'''
const render = window.AgentMarkdown.render;
const html = render('## Details\n\n**Strong** and `code`\n\n- one\n- two', {});
assert.match(html, /<h4>Details<\/h4>/);
assert.match(html, /<strong>Strong<\/strong>/);
assert.match(html, /<code>code<\/code>/);
assert.match(html, /<ul>[\s\S]*<li>one<\/li>/);
const unsafe = render('<img src=x onerror=alert(1)> [bad](javascript:alert(1))', {});
assert.ok(!unsafe.includes('<img'));
assert.ok(!unsafe.includes('href='));
const file = render('[app.py](/workspace/src/app.py:12)', {workspace:'/workspace', repository:'/workspace'});
assert.match(file, /data-agent-file="src\/app.py"/);
assert.match(file, /Open in Files/);
assert.match(render('[config](<./config folder/app.json>)', {workspace:'/workspace', repository:'/workspace/site'}), /data-agent-file="site\/config folder\/app.json"/);
for (const path of ['/workspace-other/secret', '/workspace/../secret', '../../secret', 'file:///etc/passwd', '//evil.test/file']) {
  assert.ok(!render(`[file](${path})`, {workspace:'/workspace', repository:'/workspace'}).includes('data-agent-file='));
}
assert.match(render('[docs](https://example.com/?q="unsafe")'), /href="https:\/\/example.com\/\?q=&quot;unsafe&quot;"/);
assert.match(render('```html\n<script>alert(1)</script>\n```'), /<pre><code>&lt;script&gt;alert\(1\)&lt;\/script&gt;<\/code><\/pre>/);
assert.match(render('```\n**not bold**'), /<pre><code>\*\*not bold\*\*<\/code><\/pre>/);
assert.match(render('1. First\n2. Second'), /<ol>[\s\S]*<li>Second<\/li>[\s\S]*<\/ol>/);
assert.ok(!render('`[fake](https://example.com)`').includes('<a'));
''')
