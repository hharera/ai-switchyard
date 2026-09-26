import { mkdtempSync, existsSync, rmSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const temporary = mkdtempSync(join(tmpdir(), "switchyard-install-"));
const destination = join(temporary, "prefix with spaces");
const npm = process.env.npm_execpath;
const environment = {...process.env, SWITCHYARD_DATA_DIR: join(temporary, "data")};
delete environment.SWITCHYARD_PYTHON;
delete environment.PYTHONPATH;

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {cwd: root, env: environment, encoding: "utf8", stdio: "pipe", shell: false, maxBuffer: 16 * 1024 * 1024, ...options});
  if (result.error || result.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} failed\n${result.stdout || ""}\n${result.stderr || result.error?.message || ""}`);
  }
  return result.stdout;
}

try {
  if (!npm) throw new Error("Run this check with npm run test:install.");
  const packed = JSON.parse(run(process.execPath, [npm, "pack", "--json", "--pack-destination", temporary]));
  const tarball = join(temporary, packed[0].filename);
  run(process.execPath, [npm, "install", "--global", "--allow-scripts=openswitch", "--prefix", destination, tarball]);
  const executable = (name) => process.platform === "win32"
    ? join(destination, `${name}.cmd`)
    : join(destination, "bin", name);
  for (const name of ["openswitch", "switchyard"]) {
    if (!existsSync(executable(name))) throw new Error(`npm did not create the ${name} command shim`);
  }
  const invoke = (name, args) => process.platform === "win32"
    ? run(`"${executable(name)}" ${args.join(" ")}`, [], {cwd: temporary, shell: true})
    : run(executable(name), args, {cwd: temporary});
  const help = invoke("openswitch", ["--help"]);
  if (!help.includes("Usage") || !help.includes("Switchyard")) throw new Error("Installed CLI help is incomplete");
  for (const name of ["openswitch", "switchyard"]) {
    const version = invoke(name, ["--version"]).trim();
    if (version !== packed[0].version) throw new Error(`Installed ${name} version ${version} does not match ${packed[0].version}`);
  }
  const catalog = JSON.parse(invoke("openswitch", ["mcp", "catalog", "--search", "memory"]));
  if (!catalog.length || !catalog.every(item => item.name.toLowerCase().includes("memory"))) {
    throw new Error("Installed shared MCP catalog command did not return filtered results");
  }
  console.log("Packed install smoke test passed.");
} finally {
  rmSync(temporary, {recursive: true, force: true});
}
