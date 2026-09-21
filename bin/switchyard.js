#!/usr/bin/env node

import { accessSync, constants, existsSync } from "node:fs";
import { spawn } from "node:child_process";
import { dirname, join, resolve, isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";
import { pythonInVenv } from "../scripts/python-runtime.js";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const bundledPython = pythonInVenv(join(packageRoot, ".switchyard-venv"));
const python = process.env.SWITCHYARD_PYTHON || bundledPython;

if (!isAbsolute(python)) {
  console.error("SWITCHYARD_PYTHON must be an absolute path to a Python executable.");
  process.exit(1);
}

if (!existsSync(python)) {
  console.error(
    "Switchyard's Python environment is missing. Enable npm lifecycle scripts and run `npm rebuild -g <installed-package-name>` or reinstall. If SWITCHYARD_PYTHON is set, check that path.",
  );
  process.exit(1);
}

try {
  accessSync(python, constants.X_OK);
} catch {
  console.error(`Switchyard cannot execute its Python runtime at ${python}.`);
  process.exit(1);
}

const child = spawn(
  python,
  ["-m", "ninerouter_orchestrator.cli", ...process.argv.slice(2)],
  { cwd: process.cwd(), env: process.env, stdio: "inherit" },
);

// Forward signals even when only the npm launcher receives them (e.g. a service stop).
const handlers = new Map();
for (const signal of ["SIGINT", "SIGTERM"]) {
  const handler = () => child.kill(signal);
  handlers.set(signal, handler);
  process.on(signal, handler);
}

child.on("error", (error) => {
  console.error(`Failed to start Switchyard: ${error.message}`);
  process.exitCode = 1;
});

child.on("close", (code, signal) => {
  for (const [name, handler] of handlers) process.removeListener(name, handler);
  if (signal && process.platform !== "win32") process.kill(process.pid, signal);
  process.exitCode = code ?? 1;
});
