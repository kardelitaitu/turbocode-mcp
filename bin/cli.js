#!/usr/bin/env node

/**
 * TurboCode MCP — Node.js CLI Wrapper
 *
 * Entry point for the `turbocode-mcp` command.
 * Locates the Python virtual environment, spawns the MCP server,
 * and forwards stdio bidirectionally.
 */

const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const ROOT_DIR = path.join(__dirname, '..');
const IS_WIN = process.platform === 'win32';

const PYTHON_EXECUTABLE = IS_WIN
    ? path.join(ROOT_DIR, '.venv', 'Scripts', 'python.exe')
    : path.join(ROOT_DIR, '.venv', 'bin', 'python');

const SERVER_SCRIPT = path.join(ROOT_DIR, 'src', 'server.py');

function log(msg) {
    // Server communicates over stdout via MCP protocol.
    // All CLI wrapper messages go to stderr.
    console.error(`[turbocode-mcp] ${msg}`);
}

function main() {
    // Check Python environment
    if (!fs.existsSync(PYTHON_EXECUTABLE)) {
        log('Python environment not found.');
        log('Please run: npm install -g turbocode-mcp');
        log('');
        log(`Expected Python at: ${PYTHON_EXECUTABLE}`);
        process.exit(1);
    }

    // Check server script
    if (!fs.existsSync(SERVER_SCRIPT)) {
        log(`Server script not found at: ${SERVER_SCRIPT}`);
        process.exit(1);
    }

    // Spawn the Python MCP server with inherited stdio
    const mcpProcess = spawn(PYTHON_EXECUTABLE, [SERVER_SCRIPT], {
        stdio: 'inherit',
        env: { ...process.env },
    });

    mcpProcess.on('error', (err) => {
        log(`Failed to start MCP server: ${err.message}`);
        process.exit(1);
    });

    mcpProcess.on('exit', (code) => {
        process.exit(code);
    });

    // Forward signals to child process
    process.on('SIGINT', () => {
        mcpProcess.kill('SIGINT');
    });

    process.on('SIGTERM', () => {
        mcpProcess.kill('SIGTERM');
    });
}

main();