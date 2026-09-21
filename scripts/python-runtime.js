import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { join, isAbsolute } from "node:path";

export function pythonInVenv(directory, platform = process.platform) {
  return platform === "win32"
    ? join(directory, "Scripts", "python.exe")
    : join(directory, "bin", "python");
}

export function pythonCandidates(platform = process.platform) {
  const versions = ["3.14", "3.13", "3.12"];
  return [
    ...(platform === "win32" ? versions.map(version => ({ command: "py", args: [`-${version}`] })) : []),
    ...versions.map(version => ({ command: `python${version}`, args: [] })),
    ...(platform === "win32" ? [{ command: "py", args: ["-3"] }] : []),
    { command: "python3", args: [] },
    { command: "python", args: [] },
  ];
}

export function supportsPython(candidate, run = spawnSync) {
  const result = run(candidate.command, [...candidate.args, "-c",
    "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)",
  ], { stdio: "ignore", timeout: 10000, windowsHide: true });
  return !result.error && result.status === 0;
}

export function findPython({ platform = process.platform, run = spawnSync, override } = {}) {
  if (override) {
    if (!isAbsolute(override) || !existsSync(override)) {
      throw new Error("SWITCHYARD_PYTHON must be an absolute path to an existing Python executable.");
    }
    const candidate = { command: override, args: [] };
    if (!supportsPython(candidate, run)) throw new Error("SWITCHYARD_PYTHON must run Python 3.12 or newer.");
    return candidate;
  }
  // setup-python and version managers put the selected interpreter first on PATH.
  const selected = { command: platform === "win32" ? "python" : "python3", args: [] };
  if (supportsPython(selected, run)) return selected;
  const candidate = pythonCandidates(platform).find(item => supportsPython(item, run));
  if (!candidate) {
    throw new Error("Python 3.12+ was not found. Install Python from python.org (Windows: include the py launcher; macOS: python.org or Homebrew; Linux: your package manager), then retry.");
  }
  return candidate;
}
