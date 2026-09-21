import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { isAbsolute, join } from "node:path";
import { test } from "node:test";

import { findPython, pythonCandidates, pythonInVenv, supportsPython } from "../scripts/python-runtime.js";

test("virtualenv Python paths are native on every supported OS", () => {
  assert.equal(pythonInVenv("root", "win32"), join("root", "Scripts", "python.exe"));
  assert.equal(pythonInVenv("root", "darwin"), join("root", "bin", "python"));
  assert.equal(pythonInVenv("root", "linux"), join("root", "bin", "python"));
});

test("candidate discovery covers Windows launcher and POSIX executables", () => {
  assert.deepEqual(pythonCandidates("win32").slice(0, 2), [
    {command: "py", args: ["-3.14"]}, {command: "py", args: ["-3.13"]},
  ]);
  assert.deepEqual(pythonCandidates("linux").slice(0, 2), [
    {command: "python3.14", args: []}, {command: "python3.13", args: []},
  ]);
});

test("discovery selects the first compatible Python without a shell", () => {
  const calls = [];
  const run = (command, args, options) => {
    calls.push({command, args, options});
    return {status: command === "python3.12" ? 0 : 1};
  };
  assert.deepEqual(findPython({platform: "linux", run}), {command: "python3.12", args: []});
  assert.equal(calls[0].options.windowsHide, true);
  assert.ok(calls.every(call => call.args.includes("-c")));
});

test("an explicit runtime must be absolute, present, and compatible", (t) => {
  assert.throws(() => findPython({override: "python"}), /absolute path/);
  const directory = mkdtempSync(join(tmpdir(), "switchyard-python-"));
  t.after(() => rmSync(directory, {recursive: true, force: true}));
  const executable = join(directory, process.platform === "win32" ? "python.exe" : "python");
  writeFileSync(executable, "fake");
  assert.equal(isAbsolute(executable), true);
  assert.deepEqual(findPython({override: executable, run: () => ({status: 0})}), {
    command: executable, args: [],
  });
  assert.throws(() => findPython({override: executable, run: () => ({status: 1})}), /3.12 or newer/);
});

test("compatibility rejects launch errors", () => {
  assert.equal(supportsPython({command: "python", args: []}, () => ({error: new Error("missing")})), false);
});
