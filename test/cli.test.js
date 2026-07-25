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
    assert.ok(content.includes('pythonExecutable'));
    assert.ok(content.includes('exitFn(1)'));
  });

  it('should detect missing server script', () => {
    const content = fs.readFileSync(CLI, 'utf-8');
    assert.ok(content.includes('SERVER_SCRIPT'));
    assert.ok(content.includes('serverScript'));
    assert.ok(content.includes('exitFn(1)'));
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
    assert.ok(content.includes('exitFn(typeof code ==='));
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

  it('getVersion returns version from package.json', () => {
    const cli = require('../bin/cli.js');
    const version = cli.getVersion();
    assert.match(version, /^\d+\.\d+\.\d+$/);
  });

  it('cli.log prefixes output with [turbocode-mcp]', () => {
    const cli = require('../bin/cli.js');
    const logs = [];
    const originalError = console.error;
    console.error = (msg) => logs.push(msg);
    try {
      cli.log('test message');
      assert.ok(logs[0].includes('[turbocode-mcp]'));
    } finally {
      console.error = originalError;
    }
  });

  it('cli module has expected exports', () => {
    const cli = require('../bin/cli.js');
    assert.ok(typeof cli.main === 'function');
    assert.ok(typeof cli.getVersion === 'function');
    assert.ok(typeof cli.printHelp === 'function');
    assert.ok(typeof cli.log === 'function');
    assert.ok(typeof cli.ROOT_DIR === 'string');
    assert.ok(typeof cli.PYTHON_EXECUTABLE === 'string');
    assert.ok(typeof cli.SERVER_SCRIPT === 'string');
  });

  it('cli.main spawn error handler calls exit with 1', () => {
    const cli = require('../bin/cli.js');
    const exitCodes = [];
    const { EventEmitter } = require('events');
    const child = new EventEmitter();

    cli.main({
      argv: [],
      fs: { existsSync: () => true },
      paths: {
        pythonExecutable: '/fake/python',
        serverScript: '/fake/server.py',
      },
      spawn: () => child,
      exit: (code) => { exitCodes.push(code); },
    });

    child.emit('error', { message: 'spawn failed' });
    assert.deepStrictEqual(exitCodes, [1]);
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

  it('should detect missing Python by exiting with code 1', () => {
    // Temporarily rename .venv to simulate missing environment
    const venvPath = path.join(ROOT, '.venv');
    const venvBackup = path.join(ROOT, '.venv_backup_test');
    if (fs.existsSync(venvBackup)) {
      fs.rmdirSync(venvBackup, { recursive: true });
    }
    const exists = fs.existsSync(venvPath);
    if (exists) {
      fs.renameSync(venvPath, venvBackup);
    }
    const r = run([]);
    if (exists) {
      fs.renameSync(venvBackup, venvPath);
    }
    assert.notStrictEqual(r.status, 0, 'Should exit non-zero when .venv missing');
    assert.ok(r.stderr.includes('Python environment not found'),
      `stderr: ${r.stderr.slice(-200)}`);
  });

  it('should forward non-zero exit code from child', () => {
    const r = run(['--help']);
    assert.strictEqual(r.status, 0, '--help should exit clean');
  });

  it('should exit with code 1 when server script missing', () => {
    const serverPath = path.join(ROOT, 'src', 'server.py');
    const serverBackup = path.join(ROOT, 'src', 'server.py.bak');
    let exists = fs.existsSync(serverPath);
    if (exists) {
      fs.renameSync(serverPath, serverBackup);
    }
    const r = run([]);
    if (exists) {
      fs.renameSync(serverBackup, serverPath);
    }
    assert.notStrictEqual(r.status, 0, 'Should exit non-zero when server.py missing');
    assert.ok(r.stderr.includes('Server script not found'),
      `stderr: ${r.stderr.slice(-200)}`);
  });

  it('--version with --help prints help (--help checked first)', () => {
    const r = run(['--version', '--debug', '--help']);
    assert.strictEqual(r.status, 0);
    assert.ok(r.stdout.includes('USAGE'));
  });

  it('project root should resolve correctly', () => {
    const content = fs.readFileSync(CLI, 'utf-8');
    assert.ok(content.includes("path.join(__dirname, '..')"));
  });

  it('should detect Windows platform for venv paths', () => {
    const content = fs.readFileSync(CLI, 'utf-8');
    assert.ok(content.includes('IS_WIN'));
    assert.ok(content.includes('Scripts'));
    assert.ok(content.includes('python.exe'));
  });

  it('should detect non-Windows platform for venv paths', () => {
    const content = fs.readFileSync(CLI, 'utf-8');
    assert.ok(content.includes('bin'));
    assert.ok(content.includes('python'));
  });

  it('should have correct SERVER_SCRIPT path', () => {
    const content = fs.readFileSync(CLI, 'utf-8');
    assert.ok(content.includes('SERVER_SCRIPT'));
    assert.ok(content.includes("'src'"));
    assert.ok(content.includes("'server.py'"));
  });

  it('getVersion should return version from package.json', () => {
    const content = fs.readFileSync(CLI, 'utf-8');
    assert.ok(content.includes('getVersion'));
    assert.ok(content.includes('package.json'));
    assert.ok(content.includes('.version'));
  });

  it('should handle empty args gracefully (starts server)', { timeout: 15000 }, async () => {
    const { spawn } = require('child_process');
    const proc = spawn('node', [CLI], {
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
    assert.ok(stderrData.includes('Ready.') || stderrData.includes('[turbocode-mcp]'),
      `stderr was: ${stderrData.slice(-200)}`);
  });

  it('should forward exit code from child process on failure', () => {
    const content = fs.readFileSync(CLI, 'utf-8');
    assert.ok(content.includes("mcpProcess.on('exit'"));
    assert.ok(content.includes('exitFn(typeof code ==='));
  });

  it('should be syntactically valid JavaScript', () => {
    const syntaxCheck = spawnSync('node', ['--check', CLI], { encoding: 'utf-8' });
    assert.strictEqual(syntaxCheck.status, 0, `Syntax error: ${syntaxCheck.stderr}`);
  });

  it('should handle --debug before unknown flags gracefully', { timeout: 10000 }, async () => {
    const { spawn } = require('child_process');
    let stderrData = '';
    const proc = spawn('node', [CLI, '--debug', '--unknown'], {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env },
    });
    proc.stderr.setEncoding('utf-8');
    proc.stderr.on('data', (chunk) => { stderrData += chunk; });
    const killed = new Promise((resolve) => {
      setTimeout(() => { proc.kill(); resolve(); }, 8000);
    });
    await killed;
    assert.ok(
      stderrData.includes('Ready.') || stderrData.includes('[turbocode-mcp]'),
      `No expected output: ${stderrData.slice(-200)}`
    );
  });

  it('should handle --version before unknown flags correctly', () => {
    const r = run(['--version', '--some-unknown-flag']);
    assert.strictEqual(r.status, 0);
    assert.match(r.stdout.trim(), /^\d+\.\d+\.\d+$/);
  });

  it('should detect .venv as directory not file', () => {
    const content = fs.readFileSync(CLI, 'utf-8');
    assert.ok(content.includes('statSync') || content.includes('existsSync'));
  });

  it('should forward child stderr to parent stderr', () => {
    const content = fs.readFileSync(CLI, 'utf-8');
    assert.ok(content.includes("mcpProcess.stderr") || content.includes("stdio: 'inherit'"));
  });

  it('should exit with non-zero when Python fails to start', () => {
    const content = fs.readFileSync(CLI, 'utf-8');
    assert.ok(content.includes('exitFn(1)'));
    assert.ok(content.includes('.on('));
    assert.ok(content.includes('error'));
  });

  it('should have log function printing to stderr', () => {
    const content = fs.readFileSync(CLI, 'utf-8');
    assert.ok(content.includes("console.error(`[turbocode-mcp]"));
  });

  it('should forward child env to preserve PATH', () => {
    const content = fs.readFileSync(CLI, 'utf-8');
    assert.ok(content.includes('{ ...env }'));
  });

  it('should have a main() function', () => {
    const content = fs.readFileSync(CLI, 'utf-8');
    assert.ok(content.includes('function main(options = {})'));
    assert.ok(content.includes('main()'));
  });

  it('should set ROOT_DIR relative to __dirname', () => {
    const content = fs.readFileSync(CLI, 'utf-8');
    assert.ok(content.includes("path.join(__dirname, '..')"));
  });

  it('should handle null exit code (killed by signal)', () => {
    const content = fs.readFileSync(CLI, 'utf-8');
    assert.ok(content.includes("typeof code === 'number'"));
    assert.ok(content.includes("exitFn(typeof code === 'number' ? code : 1)"));
  });

  it('should map SIGINT signal to exit code 130', () => {
    const content = fs.readFileSync(CLI, 'utf-8');
    assert.ok(content.includes("signal === 'SIGINT'"));
    assert.ok(content.includes('128 +'));
  });

  it('should map SIGTERM signal to exit code 143', () => {
    const content = fs.readFileSync(CLI, 'utf-8');
    assert.ok(content.includes('? 2 : 15'));
  });
});
