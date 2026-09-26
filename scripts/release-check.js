import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
const pyproject = readFileSync(join(root, "pyproject.toml"), "utf8");
const problems = [];
const pythonVersion = pyproject.match(/^version\s*=\s*"([^"]+)"/m)?.[1];
if (pkg.version !== pythonVersion) problems.push("Keep package.json and pyproject.toml versions equal before publishing.");
if (pkg.name === "switchyard") problems.push("Choose an npm package name/scope you own; unscoped switchyard is already taken.");
if (!pkg.license || pkg.license === "UNLICENSED" || !existsSync(join(root, "LICENSE"))) {
  problems.push("Choose the distribution license, set package.json license, and add the matching LICENSE file.");
}
if (!pkg.repository?.url) problems.push("Set repository.url to the real public source repository.");
if (pkg.publishConfig?.access !== "public") problems.push("Set publishConfig.access to public.");
if (pkg.private) problems.push("Remove private:true before publishing.");
if (problems.length) {
  console.error(`Release not ready:\n${problems.map(message => `- ${message}`).join("\n")}`);
  process.exitCode = 1;
} else {
  console.log("Release metadata checked. Verify ownership, license rights, and green cross-platform CI before publishing.");
}
