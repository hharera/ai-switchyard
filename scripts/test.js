import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { findPython, pythonInVenv } from "./python-runtime.js";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const localPython = pythonInVenv(join(projectRoot, ".venv"));
const python = existsSync(localPython) ? {command: localPython, args: []} : findPython();

for (const [command, args] of [
  [python.command, [...python.args, "-m", "pytest"]],
  [process.execPath, ["--check", join(projectRoot, "bin", "switchyard.js")]],
  [process.execPath, ["--check", join(projectRoot, "scripts", "install-python.js")]],
  [process.execPath, ["--test", join(projectRoot, "tests", "npm.test.js")]],
  [process.execPath, ["--test", join(projectRoot, "tests", "python-runtime.test.js")]],
]) {
  const result = spawnSync(command, args, { cwd: projectRoot, stdio: "inherit" });
  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}
