import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync, readFileSync, realpathSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const launcher = join(root, "bin", "switchyard.js");

test("npm tarball contains runtime files, not caches or secrets", () => {
  const result = spawnSync(process.platform === "win32" ? "npm.cmd" : "npm", ["pack", "--dry-run", "--json"], {
    cwd: root, encoding: "utf8", shell: process.platform === "win32",
  });
  assert.equal(result.status, 0, result.stderr);
  const [manifest] = JSON.parse(result.stdout);
  const npmPackage = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
  assert.equal(manifest.name, npmPackage.name);
  const paths = manifest.files.map((file) => file.path);
  for (const required of ["bin/switchyard.js", "scripts/install-python.js", "scripts/python-runtime.js", "pyproject.toml",
    "src/ninerouter_orchestrator/cli.py", "src/ninerouter_orchestrator/adapters/codex.py",
    "src/ninerouter_orchestrator/web_assets/index.html", ".env.example"]) {
    assert.ok(paths.includes(required), `Missing ${required}`);
  }
  assert.ok(paths.every((path) => !/(?:__pycache__|\.pyc$|(^|\/)\.env$|\.venv|node_modules|^tests\/)/.test(path)));
  const pythonMetadata = readFileSync(join(root, "pyproject.toml"), "utf8");
  assert.ok(pythonMetadata.includes(`version = "${npmPackage.version}"`));
});

test("missing Python runtime produces an actionable error", () => {
  const result = spawnSync(process.execPath, [launcher, "--help"], {
    env: { ...process.env, SWITCHYARD_PYTHON: join(root, "missing-python-runtime") },
    encoding: "utf8",
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Python environment is missing/);
});

test("launcher rejects a relative Python override", () => {
  const result = spawnSync(process.execPath, [launcher, "--help"], {
    env: { ...process.env, SWITCHYARD_PYTHON: "python" }, encoding: "utf8",
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /absolute path/);
});

// Fake executables exercise process forwarding without invoking any agents or installing Python.
test("launcher preserves arguments, working directory, environment and exit status", { skip: process.platform === "win32" }, (t) => {
  const directory = mkdtempSync(join(tmpdir(), "switchyard-cli-test-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const python = join(directory, "fake python");
  writeFileSync(python, `#!/usr/bin/env node
console.log(JSON.stringify({ args: process.argv.slice(2), cwd: process.cwd(), value: process.env.SWITCHYARD_TEST_VALUE }));
process.exit(7);
`, { mode: 0o755 });
  const args = ["plan", "--repo", "/a path", "request with spaces; $(echo unsafe)"];
  const result = spawnSync(process.execPath, [launcher, ...args], {
    cwd: directory, encoding: "utf8",
    env: { ...process.env, SWITCHYARD_PYTHON: python, SWITCHYARD_TEST_VALUE: "preserved" },
  });
  assert.equal(result.status, 7, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), {
    args: ["-m", "ninerouter_orchestrator.cli", ...args],
    cwd: realpathSync(directory),
    value: "preserved",
  });
});

test("launcher forwards termination to the Python process", { skip: process.platform === "win32", timeout: 10000 }, async (t) => {
  const directory = mkdtempSync(join(tmpdir(), "switchyard-signal-test-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const python = join(directory, "fake-python");
  writeFileSync(python, `#!/usr/bin/env node
process.on('SIGTERM', () => process.exit(23));
console.log('ready');
setTimeout(() => process.exit(99), 5000);
`, { mode: 0o755 });
  const child = spawn(process.execPath, [launcher], {
    env: { ...process.env, SWITCHYARD_PYTHON: python }, stdio: ["ignore", "pipe", "pipe"],
  });
  t.after(() => child.kill());
  const exit = new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code, signal) => resolve({ code, signal }));
  });
  child.stdout.once("data", () => child.kill("SIGTERM"));
  assert.deepEqual(await exit, { code: 23, signal: null });
});
