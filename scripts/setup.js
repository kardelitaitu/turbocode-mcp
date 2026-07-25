#!/usr/bin/env node

/**
 * TurboCode MCP — Postinstall Setup Script
 *
 * Creates an isolated Python virtual environment, installs pinned
 * dependencies, and installs the turbocode skill globally for opencode.
 * Runs automatically after `npm install -g turbocode-mcp`.
 */

const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');

const ROOT_DIR = path.join(__dirname, '..');
const VENV_DIR = path.join(ROOT_DIR, '.venv');
const REQUIREMENTS = path.join(ROOT_DIR, 'requirements.txt');
const IS_WIN = process.platform === 'win32';
const SKILL_SRC = path.join(ROOT_DIR, 'skills', 'turbocode');
const SKILL_DEST = path.join(os.homedir(), '.agents', 'skills', 'turbocode');

function log(msg) {
    console.log(`[turbocode-mcp] ${msg}`);
}

function error(msg) {
    console.error(`[turbocode-mcp] ERROR: ${msg}`);
}

function run(cmd, opts = {}, deps = {}) {
    try {
        const execFn = deps.execSync || execSync;
        execFn(cmd, { stdio: 'inherit', ...opts });
    } catch (err) {
        error(`Command failed: ${cmd}`);
        (deps.exit || process.exit)(1);
    }
}

function findPython(options = {}) {
    const execFn = options.execSync || execSync;
    const isWin = typeof options.isWin === 'boolean' ? options.isWin : IS_WIN;
    const candidates = options.candidates || (isWin
        ? ['python', 'python3', 'py']
        : ['python3', 'python']);
    const exitFn = options.exit || process.exit;

    for (const cmd of candidates) {
        try {
            const output = execFn(`${cmd} --version`, { encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] });
            const match = output.match(/Python (\d+)\.(\d+)/);
            if (match) {
                const major = parseInt(match[1], 10);
                const minor = parseInt(match[2], 10);
                if (major > 3 || (major === 3 && minor >= 9)) {
                    return cmd;
                } else {
                    error(`Found ${cmd} (Python ${major}.${minor}) but need >= 3.9`);
                    exitFn(1);
                }
            }
        } catch {
            continue;
        }
    }
    error('Python not found. Please install Python >= 3.9 and ensure it is on your PATH.');
    exitFn(1);
}

function installSkill(fsImpl, logFn, errorFn, srcDir, destDir) {
    const skillFile = path.join(srcDir, 'SKILL.md');
    const destFile = path.join(destDir, 'SKILL.md');

    if (!fsImpl.existsSync(skillFile)) {
        logFn(`Skill file not found at ${skillFile}, skipping global install`);
        return;
    }

    try {
        fsImpl.mkdirSync(destDir, { recursive: true });
        const content = fsImpl.readFileSync(skillFile, 'utf8');
        const existing = fsImpl.existsSync(destFile) ? fsImpl.readFileSync(destFile, 'utf8') : '';

        if (existing === content) {
            logFn('Skill already installed (up to date)');
            return;
        }

        fsImpl.writeFileSync(destFile, content, 'utf8');
        logFn(`Installed turbocode skill -> ${destFile}`);
    } catch (err) {
        errorFn(`Failed to install skill: ${err.message}`);
    }
}

function main(options = {}) {
    const fsImpl = options.fs || fs;
    const logFn = options.log || log;
    const errorFn = options.error || error;
    const findPythonFn = options.findPython || findPython;
    const runFn = options.run || run;
    const exitFn = options.exit || process.exit;
    const isWin = typeof options.isWin === 'boolean' ? options.isWin : IS_WIN;
    const paths = options.paths || {};
    const venvDir = paths.venvDir || VENV_DIR;
    const requirements = paths.requirements || REQUIREMENTS;
    const pythonBinPath = paths.pythonBin || (
        isWin
            ? path.join(venvDir, 'Scripts', 'python.exe')
            : path.join(venvDir, 'bin', 'python')
    );
    const pipPath = paths.pipPath || (
        isWin
            ? path.join(venvDir, 'Scripts', 'pip.exe')
            : path.join(venvDir, 'bin', 'pip')
    );
    const skillSrc = paths.skillSrc || SKILL_SRC;
    const skillDest = paths.skillDest || SKILL_DEST;

    logFn('Setting up Python environment...');

    const pythonCmd = findPythonFn({ isWin });
    logFn(`Using Python: ${pythonCmd}`);

    // Step 1: Create virtual environment
    if (!fsImpl.existsSync(venvDir)) {
        logFn('Creating virtual environment...');
        runFn(`${pythonCmd} -m venv "${venvDir}"`);
        logFn('Virtual environment created.');
    } else {
        logFn('Virtual environment already exists.');
    }

    // Step 2: Locate pip
    if (!fsImpl.existsSync(pipPath)) {
        errorFn(`pip not found at ${pipPath}`);
        exitFn(1);
    }

    // Step 3: Install dependencies
    if (fsImpl.existsSync(requirements)) {
        logFn('Installing Python dependencies...');
        runFn(`"${pipPath}" install -r "${requirements}"`);
    } else {
        logFn('requirements.txt not found, installing default packages...');
        runFn(`"${pipPath}" install fastmcp turbovec sentence-transformers numpy`);
    }

    // Step 4: Install skill globally
    installSkill(fsImpl, logFn, errorFn, skillSrc, skillDest);

    // Step 5: Verify
    if (!fsImpl.existsSync(pythonBinPath)) {
        errorFn('Python environment not found after setup.');
        exitFn(1);
    }

    logFn('Setup complete!');
}

if (require.main === module) {
    main();
}

module.exports = {
    installSkill,
    main,
    run,
    findPython,
    log,
    error,
    ROOT_DIR,
    VENV_DIR,
    SKILL_SRC,
    SKILL_DEST,
    REQUIREMENTS,
    IS_WIN,
};
