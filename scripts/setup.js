#!/usr/bin/env node

/**
 * TurboCode MCP — Postinstall Setup Script
 *
 * Creates an isolated Python virtual environment and installs pinned
 * dependencies. Runs automatically after `npm install -g turbocode-mcp`.
 */

const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const ROOT_DIR = path.join(__dirname, '..');
const VENV_DIR = path.join(ROOT_DIR, '.venv');
const REQUIREMENTS = path.join(ROOT_DIR, 'requirements.txt');
const IS_WIN = process.platform === 'win32';

function log(msg) {
    console.log(`[turbocode-mcp] ${msg}`);
}

function error(msg) {
    console.error(`[turbocode-mcp] ERROR: ${msg}`);
}

function run(cmd, opts = {}) {
    try {
        execSync(cmd, { stdio: 'inherit', ...opts });
    } catch (err) {
        error(`Command failed: ${cmd}`);
        process.exit(1);
    }
}

function findPython() {
    // Try common Python executable names
    const candidates = IS_WIN
        ? ['python', 'python3', 'py']
        : ['python3', 'python'];

    for (const cmd of candidates) {
        try {
            const output = execSync(`${cmd} --version`, { encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] });
            const match = output.match(/Python (\d+)\.(\d+)/);
            if (match) {
                const major = parseInt(match[1], 10);
                const minor = parseInt(match[2], 10);
                if (major > 3 || (major === 3 && minor >= 9)) {
                    return cmd;
                } else {
                    error(`Found ${cmd} (Python ${major}.${minor}) but need >= 3.9`);
                    process.exit(1);
                }
            }
        } catch {
            continue;
        }
    }
    error('Python not found. Please install Python >= 3.9 and ensure it is on your PATH.');
    process.exit(1);
}

function main() {
    log('Setting up Python environment...');

    const pythonCmd = findPython();
    log(`Using Python: ${pythonCmd}`);

    // Step 1: Create virtual environment
    if (!fs.existsSync(VENV_DIR)) {
        log('Creating virtual environment...');
        run(`${pythonCmd} -m venv "${VENV_DIR}"`);
        log('Virtual environment created.');
    } else {
        log('Virtual environment already exists.');
    }

    // Step 2: Locate pip
    const pipPath = IS_WIN
        ? path.join(VENV_DIR, 'Scripts', 'pip.exe')
        : path.join(VENV_DIR, 'bin', 'pip');

    if (!fs.existsSync(pipPath)) {
        error(`pip not found at ${pipPath}`);
        process.exit(1);
    }

    // Step 3: Install dependencies
    if (fs.existsSync(REQUIREMENTS)) {
        log('Installing Python dependencies...');
        run(`"${pipPath}" install -r "${REQUIREMENTS}"`);
    } else {
        log('requirements.txt not found, installing default packages...');
        run(`"${pipPath}" install fastmcp turbovec sentence-transformers numpy`);
    }

    // Step 4: Verify
    const pythonBin = IS_WIN
        ? path.join(VENV_DIR, 'Scripts', 'python.exe')
        : path.join(VENV_DIR, 'bin', 'python');

    if (!fs.existsSync(pythonBin)) {
        error('Python environment not found after setup.');
        process.exit(1);
    }

    log('Setup complete!');
}

main();