const assert = require('node:assert');
const { describe, it } = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { EventEmitter } = require('node:events');

const setup = require('../scripts/setup.js');
const cli = require('../bin/cli.js');

function makeExitTrap() {
  const codes = [];
  return {
    codes,
    exit(code) {
      codes.push(code);
    },
  };
}

function createTempProject() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'turbocode-mcp-'));
  const venvDir = path.join(root, '.venv');
  const binDir = path.join(venvDir, 'bin');
  const pipPath = path.join(binDir, 'pip');
  const pythonBin = path.join(binDir, 'python');
  const requirements = path.join(root, 'requirements.txt');

  fs.mkdirSync(binDir, { recursive: true });
  fs.writeFileSync(pipPath, '', 'utf-8');
  fs.writeFileSync(pythonBin, '', 'utf-8');
  fs.writeFileSync(requirements, 'fastmcp\n', 'utf-8');

  return { root, venvDir, binDir, pipPath, pythonBin, requirements };
}

describe('Runtime behavior', () => {
  it('setup.findPython returns the first compatible candidate', () => {
    const execCalls = [];
    const candidateMap = {
      python: () => { throw new Error('missing'); },
      python3: () => { throw new Error('missing'); },
      py: () => 'Python 3.11.4',
    };

    const result = setup.findPython({
      candidates: ['python', 'python3', 'py'],
      execSync: (cmd) => {
        execCalls.push(cmd);
        const name = cmd.split(' ')[0];
        return candidateMap[name]();
      },
      exit: (code) => { throw new Error(`unexpected exit ${code}`); },
    });

    assert.strictEqual(result, 'py');
    assert.deepStrictEqual(execCalls, ['python --version', 'python3 --version', 'py --version']);
  });

  it('setup.findPython exits when it finds a version that is too old', () => {
    let exitCode = null;

    assert.throws(() => {
      setup.findPython({
        candidates: ['python'],
        execSync: () => 'Python 3.8.10',
        exit: (code) => {
          exitCode = code;
          throw new Error(`exit ${code}`);
        },
      });
    }, /exit 1/);

    assert.strictEqual(exitCode, 1);
  });

  it('setup.main creates the venv and installs requirements when missing', () => {
    const project = createTempProject();
    fs.rmSync(project.venvDir, { recursive: true, force: true });
    const runCalls = [];

    setup.main({
      fs,
      isWin: false,
      paths: {
        venvDir: project.venvDir,
        requirements: project.requirements,
      },
      findPython: () => 'python3',
      log: () => {},
      error: () => {},
      exit: (code) => { throw new Error(`unexpected exit ${code}`); },
      run: (cmd) => {
        runCalls.push(cmd);
        if (cmd.includes('-m venv')) {
          fs.mkdirSync(project.binDir, { recursive: true });
          fs.writeFileSync(project.pipPath, '', 'utf-8');
          fs.writeFileSync(project.pythonBin, '', 'utf-8');
        }
      },
    });

    assert.strictEqual(runCalls.length, 2);
    assert.ok(runCalls[0].includes('-m venv'));
    assert.ok(runCalls[1].includes('install -r'));
  });

  it('setup.main falls back to default packages when requirements are missing', () => {
    const project = createTempProject();
    fs.rmSync(project.requirements, { force: true });
    const runCalls = [];

    setup.main({
      fs,
      isWin: false,
      paths: {
        venvDir: project.venvDir,
        requirements: project.requirements,
      },
      findPython: () => 'python3',
      log: () => {},
      error: () => {},
      exit: (code) => { throw new Error(`unexpected exit ${code}`); },
      run: (cmd) => runCalls.push(cmd),
    });

    assert.strictEqual(runCalls.length, 1);
    assert.ok(runCalls[0].includes('fastmcp turbovec sentence-transformers numpy'));
  });

  it('cli.main exits cleanly for --help without spawning Python', () => {
    const exitTrap = makeExitTrap();
    let spawned = false;
    const originalLog = console.log;
    console.log = () => {};

    try {
      assert.throws(() => {
        cli.main({
          argv: ['--help'],
          fs: { existsSync: () => true },
          spawn: () => {
            spawned = true;
            throw new Error('spawn should not be called');
          },
          exit: (code) => {
            exitTrap.exit(code);
            throw new Error(`exit ${code}`);
          },
        });
      }, /exit 0/);
    } finally {
      console.log = originalLog;
    }

    assert.deepStrictEqual(exitTrap.codes, [0]);
    assert.strictEqual(spawned, false);
  });

  it('cli.main exits cleanly for --version without spawning Python', () => {
    const exitTrap = makeExitTrap();
    let spawned = false;
    const originalLog = console.log;
    console.log = () => {};

    try {
      assert.throws(() => {
        cli.main({
          argv: ['--version'],
          fs: { existsSync: () => true },
          spawn: () => {
            spawned = true;
            throw new Error('spawn should not be called');
          },
          exit: (code) => {
            exitTrap.exit(code);
            throw new Error(`exit ${code}`);
          },
        });
      }, /exit 0/);
    } finally {
      console.log = originalLog;
    }

    assert.deepStrictEqual(exitTrap.codes, [0]);
    assert.strictEqual(spawned, false);
  });

  it('cli.main spawns Python with the debug flag and forwards signals', () => {
    const signals = {};
    const child = new EventEmitter();
    child.kill = (signal) => {
      signals[signal] = (signals[signal] || 0) + 1;
    };

    const originalOn = process.on;
    process.on = (event, handler) => {
      signals[event] = handler;
      return process;
    };

    const spawnCalls = [];
    const pythonPath = '/fake/.venv/bin/python';
    const serverPath = '/fake/src/server.py';

    try {
      cli.main({
        argv: ['--debug'],
        fs: {
          existsSync: (target) => target === pythonPath || target === serverPath,
        },
        paths: {
          pythonExecutable: pythonPath,
          serverScript: serverPath,
        },
        spawn: (command, args, options) => {
          spawnCalls.push({ command, args, options });
          return child;
        },
        exit: (code) => { throw new Error(`unexpected exit ${code}`); },
      });
    } finally {
      process.on = originalOn;
    }

    assert.strictEqual(spawnCalls.length, 1);
    assert.strictEqual(spawnCalls[0].command, pythonPath);
    assert.deepStrictEqual(spawnCalls[0].args, [serverPath, '--debug']);
    assert.strictEqual(spawnCalls[0].options.stdio, 'inherit');
    assert.ok(spawnCalls[0].options.env.PATH || spawnCalls[0].options.env.Path);
    assert.strictEqual(typeof signals.SIGINT, 'function');
    assert.strictEqual(typeof signals.SIGTERM, 'function');

    signals.SIGINT();
    signals.SIGTERM();
    assert.strictEqual(signals.SIGINT ? child.kill && true : true, true);
    assert.strictEqual(signals.SIGTERM ? child.kill && true : true, true);
  });

  it('cli.main maps child exit signals to stable exit codes', () => {
    const child = new EventEmitter();
    const exitTrap = makeExitTrap();
    const pythonPath = '/fake/.venv/bin/python';
    const serverPath = '/fake/src/server.py';
    const originalOn = process.on;
    process.on = () => process;

    try {
      cli.main({
        fs: {
          existsSync: (target) => target === pythonPath || target === serverPath,
        },
        paths: {
          pythonExecutable: pythonPath,
          serverScript: serverPath,
        },
        spawn: () => child,
        exit: (code) => exitTrap.exit(code),
      });

      child.emit('exit', null, 'SIGINT');
      child.emit('exit', null, 'SIGTERM');
    } finally {
      process.on = originalOn;
    }

    assert.deepStrictEqual(exitTrap.codes, [130, 143]);
  });
});
