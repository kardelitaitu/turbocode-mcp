#!/usr/bin/env node
const { execSync } = require("child_process");
const path = require("path");
const ROOT = path.join(__dirname, "..");
const PYTHON = process.platform === "win32"
  ? path.join(ROOT, ".venv", "Scripts", "python.exe")
  : path.join(ROOT, ".venv", "bin", "python");
const args = process.argv.slice(2).join(" ");
try {
  execSync(`"${PYTHON}" ${args}`, { stdio: "inherit", cwd: ROOT });
} catch {
  process.exit(1);
}
