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
const PACKAGE_JSON = path.join(ROOT_DIR, 'package.json');

const PYTHON_EXECUTABLE = IS_WIN
    ? path.join(ROOT_DIR, '.venv', 'Scripts', 'python.exe')
    : path.join(ROOT_DIR, '.venv', 'bin', 'python');

const SERVER_SCRIPT = path.join(ROOT_DIR, 'src', 'server.py');

function log(msg) {
    console.error(`[turbocode-mcp] ${msg}`);
}

function getVersion() {
    try {
        return JSON.parse(fs.readFileSync(PACKAGE_JSON, 'utf-8')).version || 'unknown';
    } catch {
        return 'unknown';
    }
}

function printHelp() {
    const version = getVersion();
    console.log(`
TurboCode MCP v${version}

A fully local codebase vector search MCP server powered by Turbovec.
Zero cloud dependencies — everything runs on your machine.

USAGE:
    turbocode-mcp [OPTIONS]

OPTIONS:
    --help          Print this help message and exit
    --version       Print the version number and exit
    --debug         Enable verbose logging to stderr

EXAMPLES:
    turbocode-mcp
        Start the MCP server (connects via stdio to your MCP client).

    turbocode-mcp --debug
        Start the server with verbose debug logging.

CONFIGURATION:
    Add to Claude Desktop (claude_desktop_config.json):
    {
        "mcpServers": {
            "turbovec-search": {
                "command": "turbocode-mcp"
            }
        }
    }

DOCUMENTATION:
    https://github.com/kardelitaitu/turbocode-mcp
`.trim());
}

function main() {
    // Parse CLI flags
    const args = process.argv.slice(2);
    const flags = new Set(args);

    if (flags.has('--help') || flags.has('-h')) {
        printHelp();
        process.exit(0);
    }

    if (flags.has('--version') || flags.has('-v')) {
        console.log(getVersion());
        process.exit(0);
    }

    const debug = flags.has('--debug');

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

    if (debug) {
        log(`Debug mode enabled`);
        log(`Python: ${PYTHON_EXECUTABLE}`);
        log(`Server: ${SERVER_SCRIPT}`);
    }

    // Spawn the Python MCP server with inherited stdio
    const serverArgs = debug ? [SERVER_SCRIPT, '--debug'] : [SERVER_SCRIPT];
    const mcpProcess = spawn(PYTHON_EXECUTABLE, serverArgs, {
        stdio: 'inherit',
        env: { ...process.env },
    });

    mcpProcess.on('error', (err) => {
        log(`Failed to start MCP server: ${err.message}`);
        process.exit(1);
    });

    mcpProcess.on('exit', (code, signal) => {
        if (signal) {
            process.exit(128 + (signal === 'SIGINT' ? 2 : 15));
        }
        process.exit(typeof code === 'number' ? code : 1);
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