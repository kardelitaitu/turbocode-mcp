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
    --help              Print this help message and exit
    --version           Print the version number and exit
    --debug             Enable verbose logging to stderr
    --model=<name>      Override the embedding model (default: BAAI/bge-small-en-v1.5)
    --workspace=<path>  Directory to auto-index on startup (default: auto-detect)

EXAMPLES:
    turbocode-mcp
        Start the MCP server (connects via stdio to your MCP client).

    turbocode-mcp --debug
        Start the server with verbose debug logging.

    CONFIGURATION:
    Add to Claude Desktop (claude_desktop_config.json):
    {
        "mcpServers": {
            "turbocode-mcp": {
                "command": "turbocode-mcp"
            }
        }
    }

DOCUMENTATION:
    https://github.com/kardelitaitu/turbocode-mcp
`.trim());
}

function main(options = {}) {
    const argv = Array.isArray(options.argv) ? options.argv : process.argv.slice(2);
    const fsImpl = options.fs || fs;
    const spawnImpl = options.spawn || spawn;
    const logFn = options.log || log;
    const exitFn = options.exit || process.exit;
    const env = options.env || process.env;
    const paths = options.paths || {};
    const pythonExecutable = paths.pythonExecutable || PYTHON_EXECUTABLE;
    const serverScript = paths.serverScript || SERVER_SCRIPT;

    // Parse CLI flags
    const flags = new Set(argv);

    if (flags.has('--help') || flags.has('-h')) {
        printHelp();
        exitFn(0);
    }

    if (flags.has('--version') || flags.has('-v')) {
        console.log(getVersion());
        exitFn(0);
    }

    const debug = flags.has('--debug');

    // Extract --model=<name> and --workspace=<path> if provided
    let modelArg = null;
    let workspaceArg = null;
    for (const arg of argv) {
        if (arg.startsWith('--model=')) {
            modelArg = arg;
        } else if (arg.startsWith('--workspace=')) {
            workspaceArg = arg;
        }
    }

    // Check Python environment
    if (!fsImpl.existsSync(pythonExecutable)) {
        logFn('Python environment not found.');
        logFn('Please run: npm install -g turbocode-mcp');
        logFn('');
        logFn(`Expected Python at: ${pythonExecutable}`);
        exitFn(1);
    }

    // Check server script
    if (!fsImpl.existsSync(serverScript)) {
        logFn(`Server script not found at: ${serverScript}`);
        exitFn(1);
    }

    if (debug) {
        logFn(`Debug mode enabled`);
        logFn(`Python: ${pythonExecutable}`);
        logFn(`Server: ${serverScript}`);
    }

    // Always run in stdio mode (MCP protocol over stdin/stdout)
    const serverArgs = [serverScript, '--stdio'];
    if (debug) serverArgs.push('--debug');
    if (modelArg) serverArgs.push(modelArg);
    if (workspaceArg) serverArgs.push(workspaceArg);

    if (debug) {
        logFn(`Spawning: ${pythonExecutable} ${serverArgs.join(' ')}`);
    }

    // Use 'inherit' so the Python process communicates directly with
    // the MCP client (opencode) over stdin/stdout without Node.js
    // buffering or transforming the data.
    const mcpProcess = spawnImpl(pythonExecutable, serverArgs, {
        stdio: 'inherit',
        env: { ...env },
    });

    mcpProcess.on('error', (err) => {
        logFn(`Failed to start MCP server: ${err.message}`);
        exitFn(1);
    });

    mcpProcess.on('exit', (code, signal) => {
        if (signal) {
            if (debug) logFn(`Exited with signal ${signal}`);
            exitFn(128 + (signal === 'SIGINT' ? 2 : 15));
            return;
        }
        if (debug) logFn(`Exited with code ${code}`);
        exitFn(typeof code === 'number' ? code : 1);
    });

    // Forward signals to child process
    process.on('SIGINT', () => {
        mcpProcess.kill('SIGINT');
    });

    process.on('SIGTERM', () => {
        mcpProcess.kill('SIGTERM');
    });
}

if (require.main === module) {
    main();
}

module.exports = {
    main,
    getVersion,
    printHelp,
    log,
    ROOT_DIR,
    PYTHON_EXECUTABLE,
    SERVER_SCRIPT,
};
