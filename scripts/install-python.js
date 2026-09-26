import { closeSync, existsSync, openSync, writeSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { findPython, pythonInVenv, supportsPython } from "./python-runtime.js";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const venv = join(packageRoot, ".switchyard-venv");
const venvPython = pythonInVenv(venv);

function run(command, args, hint) {
  const result = spawnSync(command, args, {
    cwd: packageRoot,
    env: process.env,
    stdio: "inherit",
    windowsHide: true,
  });
  if (result.error || result.status !== 0) {
    throw new Error(`${hint}${result.error ? ` (${result.error.message})` : ""}`);
  }
}

function showLaunchCommand() {
  const message = "Switchyard is ready. Run `openswitch ui` to open the web interface.";
  const terminal = process.platform === "win32" ? "\\\\.\\CONOUT$" : "/dev/tty";
  try {
    const descriptor = openSync(terminal, "w");
    writeSync(descriptor, `${message}\n`);
    closeSync(descriptor);
  } catch {
    // npm may capture lifecycle output; this still reaches foreground-script installs and CI logs.
    console.log(message);
  }
}

try {
  if (process.env.SWITCHYARD_PYTHON) {
    const python = findPython({ override: process.env.SWITCHYARD_PYTHON });
    run(python.command, ["-c", "import ninerouter_orchestrator.cli"],
      "The SWITCHYARD_PYTHON environment must already have Switchyard and its dependencies installed.");
    console.log("Using the existing SWITCHYARD_PYTHON environment; no packages were changed.");
  } else {
    if (!existsSync(venvPython)) {
      const python = findPython();
      console.log("Creating Switchyard's isolated Python environment...");
      run(python.command, [...python.args, "-m", "venv", venv],
        "Unable to create the Python environment. On Debian/Ubuntu install the matching python3-venv package. Check that the npm install directory is writable.");
    }
    if (!supportsPython({ command: venvPython, args: [] })) {
      throw new Error("The bundled Python environment is incompatible or broken. Reinstall the npm package with Python 3.12+ available.");
    }
    console.log("Installing Switchyard's Python runtime...");
    run(venvPython, ["-m", "pip", "install", "--disable-pip-version-check", "--no-input", packageRoot],
      "Unable to install Python dependencies. Check network/proxy access to PyPI and retry npm rebuild for the installed package.");
    run(venvPython, ["-c", "import ninerouter_orchestrator.cli"],
      "Switchyard runtime verification failed. Reinstall the package.");
  }
  showLaunchCommand();
} catch (error) {
  console.error(`Switchyard setup failed: ${error.message}`);
  process.exitCode = 1;
}
