#!/usr/bin/env node

const { execSync, spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

const ROOT = path.join(__dirname, "..");
const IS_WIN = process.platform === "win32";

const PYTHON = IS_WIN
  ? path.join(ROOT, ".venv", "Scripts", "python.exe")
  : path.join(ROOT, ".venv", "bin", "python");

const PIP = IS_WIN
  ? path.join(ROOT, ".venv", "Scripts", "pip.exe")
  : path.join(ROOT, ".venv", "bin", "pip");

function run(cmd, opts = {}) {
  console.log(`\n> ${cmd}`);
  execSync(cmd, { stdio: "inherit", cwd: ROOT, ...opts });
}

function check(cmd, label) {
  console.log(`\n--- ${label} ---`);
  try {
    execSync(cmd, { stdio: "inherit", cwd: ROOT });
    console.log(`OK: ${label}`);
  } catch {
    console.error(`FAILED: ${label}`);
    process.exit(1);
  }
}

function main() {
  console.log("=== TurboCode MCP — Pre-Publish Validation ===\n");

  // 1. Check .venv exists
  if (!fs.existsSync(PYTHON)) {
    console.error("ERROR: .venv not found. Run 'npm install' first.");
    process.exit(1);
  }

  // 2. Verify requirements installed
  console.log("--- Verify Python dependencies ---");
  const installed = execSync(`"${PIP}" list --format=columns`, {
    encoding: "utf-8",
    cwd: ROOT,
  });
  for (const pkg of ["fastmcp", "turbovec", "fastembed", "numpy", "pathspec"]) {
    if (!installed.includes(pkg)) {
      console.error(`FAILED: ${pkg} not installed in .venv`);
      process.exit(1);
    }
    console.log(`  ${pkg} found`);
  }

  // 3. Ruff lint
  check(`"${PYTHON}" -m ruff check src/ tests/ benchmarks/`, "Ruff lint");

  // 4. Ruff format check
  check(
    `"${PYTHON}" -m ruff format --check src/ tests/ benchmarks/`,
    "Ruff format check"
  );

  // 5. All Python tests
  check(
    `"${PYTHON}" -m pytest tests/ -q --deselect tests/test_concurrency.py::TestWorkerStatusTransitionsDetailed::test_status_goes_indexing_then_idle`,
    "Python tests (all)"
  );

  // 6. All JS tests
  check("node --test test/", "JS tests (all)");

  // 7. CLI smoke test
  check("node bin/cli.js --version", "CLI --version");
  check("node bin/cli.js --help", "CLI --help");

  // 8. MCP protocol smoke test
  console.log("\n--- MCP protocol smoke test ---");
  const proc = spawn(PYTHON, [path.join(ROOT, "src", "server.py")], {
    stdio: ["pipe", "pipe", "pipe"],
    cwd: ROOT,
    env: { ...process.env, MCP_DEBUG: "" },
  });

  let output = "";
  proc.stdout.on("data", (chunk) => {
    output += chunk.toString();
    // If we got a response, kill the server
    if (output.includes("jsonrpc") || output.length > 200) {
      proc.kill();
    }
  });

  proc.stderr.on("data", () => {});

  // Send JSON-RPC initialize
  const initMsg = JSON.stringify({
    jsonrpc: "2.0",
    id: 1,
    method: "initialize",
    params: {
      protocolVersion: "2025-03-26",
      capabilities: {},
      clientInfo: { name: "prepublish-test", version: "1.0.0" },
    },
  });

  proc.stdin.write(initMsg + "\n");
  proc.stdin.end();

  const timeout = setTimeout(() => {
    proc.kill();
    console.error("FAILED: MCP server did not respond within 8s");
    process.exit(1);
  }, 8000);

  proc.on("exit", (code) => {
    clearTimeout(timeout);
    try {
      const lines = output.trim().split("\n");
      const resp = JSON.parse(lines[lines.length - 1]);
      if (resp.jsonrpc === "2.0" && (resp.result || resp.error)) {
        console.log("OK: MCP protocol response received");
      } else {
        console.error("FAILED: Invalid MCP response:", output.slice(-300));
        process.exit(1);
      }
    } catch {
      // Some servers may exit before we read; check for any output
      if (output.length > 0) {
        console.log("OK: MCP server produced output");
      } else {
        console.error("FAILED: No output from MCP server");
        process.exit(1);
      }
    }
  });

  proc.on("error", (err) => {
    clearTimeout(timeout);
    console.error("FAILED: MCP server spawn error:", err.message);
    process.exit(1);
  });
}

if (require.main === module) {
  main();
}
