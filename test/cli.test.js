const assert = require('node:assert');
const { describe, it } = require('node:test');
const { spawnSync, execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const ROOT = path.join(__dirname, '..');
const CLI = path.join(ROOT, 'bin', 'cli.js');
const SERVER_SCRIPT = path.join(ROOT, 'src', 'server.py');

function run(args) {
  return spawnSync('node', [CLI, ...args], { encoding: 'utf-8' });
}

describe('CLI Wrapper', () => {
  it('server.py should exist', () => {
    assert.ok(fs.existsSync(SERVER_SCRIPT));
  });

  it('package.json should have valid semver version', () => {
    const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf-8'));
    assert.match(pkg.version, /^\d+\.\d+\.\d+$/);
  });

  it('.venv should exist (postinstall ran)', () => {
    const pythonBin = process.platform === 'win32'
      ? path.join(ROOT, '.venv', 'Scripts', 'python.exe')
      : path.join(ROOT, '.venv', 'bin', 'python');
    assert.ok(fs.existsSync(pythonBin));
  });

  it('--help flag should print help and exit', () => {
    const r = run(['--help']);
    assert.strictEqual(r.status, 0);
    assert.ok(r.stdout.includes('TurboCode MCP'));
    assert.ok(r.stdout.includes('--help'));
    assert.ok(r.stdout.includes('--version'));
    assert.ok(r.stdout.includes('--debug'));
    assert.ok(r.stdout.includes('USAGE'));
  });

  it('--version flag should print version and exit', () => {
    const r = run(['--version']);
    assert.strictEqual(r.status, 0);
    assert.match(r.stdout.trim(), /^\d+\.\d+\.\d+$/);
  });

  it('-h short flag should print help and exit', () => {
    const r = run(['-h']);
    assert.strictEqual(r.status, 0);
    assert.ok(r.stdout.includes('TurboCode MCP'));
  });

  it('-v short flag should print version and exit', () => {
    const r = run(['-v']);
    assert.strictEqual(r.status, 0);
    assert.match(r.stdout.trim(), /^\d+\.\d+\.\d+$/);
  });

  it('--debug flag spawns Python server', { timeout: 15000 }, async () => {
    const { spawn } = require('child_process');
    const proc = spawn('node', [CLI, '--debug'], {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env },
    });
    proc.stderr.setEncoding('utf-8');
    let stderrData = '';
    proc.stderr.on('data', (chunk) => { stderrData += chunk; });
    const killed = new Promise((resolve) => {
      setTimeout(() => { proc.kill(); resolve(); }, 10000);
    });
    await killed;
    assert.ok(stderrData.includes('Ready.'), `stderr was: ${stderrData.slice(-200)}`);
  });

  it('should detect missing Python environment', () => {
    const content = fs.readFileSync(CLI, 'utf-8');
    assert.ok(content.includes('PYTHON_EXECUTABLE'));
    assert.ok(content.includes('fs.existsSync(PYTHON_EXECUTABLE)'));
    assert.ok(content.includes('process.exit(1)'));
  });

  it('should detect missing server script', () => {
    const content = fs.readFileSync(CLI, 'utf-8');
    assert.ok(content.includes('SERVER_SCRIPT'));
    assert.ok(content.includes('fs.existsSync(SERVER_SCRIPT)'));
    assert.ok(content.includes('process.exit(1)'));
  });

  it('should forward SIGINT/SIGTERM to child process', () => {
    const content = fs.readFileSync(CLI, 'utf-8');
    assert.ok(content.includes('SIGINT'));
    assert.ok(content.includes('SIGTERM'));
    assert.ok(content.includes('mcpProcess.kill'));
  });

  it('should spawn Python with inherit stdio', () => {
    const content = fs.readFileSync(CLI, 'utf-8');
    assert.ok(content.includes("stdio: 'inherit'"));
    assert.ok(content.includes('spawn'));
  });

  it('should catch spawn errors', () => {
    const content = fs.readFileSync(CLI, 'utf-8');
    assert.ok(content.includes("mcpProcess.on('error'"));
    assert.ok(content.includes('Failed to start MCP server'));
  });

  it('should forward exit code from child', () => {
    const content = fs.readFileSync(CLI, 'utf-8');
    assert.ok(content.includes("mcpProcess.on('exit'"));
    assert.ok(content.includes('process.exit(code)'));
  });

  it('unknown flag does not crash', { timeout: 8000 }, async () => {
    const { spawn } = require('child_process');
    let stderrData = '';
    const proc = spawn('node', [CLI, '--unknown-flag'], {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env },
    });
    proc.stderr.setEncoding('utf-8');
    proc.stderr.on('data', (chunk) => { stderrData += chunk; });
    const killed = new Promise((resolve) => {
      setTimeout(() => { proc.kill(); resolve(); }, 6000);
    });
    await killed;
    assert.ok(
      stderrData.includes('Ready.') || stderrData.includes('[turbocode-mcp]'),
      `No expected output in stderr: ${stderrData.slice(-200)}`
    );
  });

  it('--debug prints Python path and server path', { timeout: 8000 }, async () => {
    const { spawn } = require('child_process');
    let stderrData = '';
    const proc = spawn('node', [CLI, '--debug'], {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env },
    });
    proc.stderr.setEncoding('utf-8');
    proc.stderr.on('data', (chunk) => { stderrData += chunk; });
    const killed = new Promise((resolve) => {
      setTimeout(() => { proc.kill(); resolve(); }, 6000);
    });
    await killed;
    assert.ok(stderrData.includes('ready.') || stderrData.includes('Debug mode enabled'),
      `Debug output missing: ${stderrData.slice(-300)}`);
  });

  it('--help overrides --debug when both provided', () => {
    const r = run(['--debug', '--help']);
    assert.strictEqual(r.status, 0);
    assert.ok(r.stdout.includes('TurboCode MCP'));
  });

  it('--version overrides --debug when both provided', () => {
    const r = run(['--debug', '--version']);
    assert.strictEqual(r.status, 0);
    assert.match(r.stdout.trim(), /^\d+\.\d+\.\d+$/);
  });
});
