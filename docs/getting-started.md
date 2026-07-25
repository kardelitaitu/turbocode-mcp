# Getting Started

> **Prerequisites:** Node.js ≥ 18, Python ≥ 3.9

---

## Installation

Install the package globally via npm:

```bash
npm install -g turbocode-mcp
```

This command does three things:

1. Downloads the `turbocode-mcp` package
2. Runs the **postinstall script**, which:
   - Creates an isolated Python virtual environment at `.venv/`
   - Installs `fastmcp`, `turbovec`, `fastembed`, and `numpy`
3. Makes the `turbocode-mcp` command available on your PATH

No manual `pip install` or `python -m venv` required.

---

## Verifying the Installation

```bash
# Check that the command exists
which turbocode-mcp          # Linux/Mac
where turbocode-mcp          # Windows

# Check npm global packages
npm list -g turbocode-mcp
```

---

## Connecting to an MCP Client

### Claude Desktop

Edit your `claude_desktop_config.json` (usually at `~/Library/Application Support/Claude/` on Mac, or `%APPDATA%\Claude\` on Windows):

```json
{
  "mcpServers": {
    "turbocode-mcp": {
      "command": "turbocode-mcp"
    }
  }
}
```

Restart Claude Desktop. The server starts automatically — you'll see the tools and resources available in the chat interface.

### Cursor

1. Open Cursor Settings → Features → MCP
2. Add a new MCP server:
   - **Name:** `turbocode-mcp`
   - **Type:** `command`
   - **Command:** `turbocode-mcp`
3. The server connects via stdio. Tools appear in the AI chat panel.

### ZCode / Other MCP Clients

Most MCP clients use the same pattern:

```json
{
  "mcpServers": {
    "turbocode-mcp": {
      "command": "turbocode-mcp"
    }
  }
}
```

If your client needs the full path:

```json
{
  "mcpServers": {
    "turbocode-mcp": {
      "command": "node",
      "args": ["/path/to/turbocode-mcp/bin/cli.js"]
    }
  }
}
```

---

## First Workflow

Once connected, here's the typical flow:

### 1. The AI sees index status automatically

The client loads `turbocode://status` and `turbocode://stats` into the AI's context automatically. You'll see something like:

```
turbocode://status → Ready. 0 files tracked. (Model loaded on demand)
```

### 2. Index a project directory

Ask the AI to index your codebase, or it may do this autonomously:

```
→ index_directory("/path/to/my-project")
  Queued 47 files (42 new, 3 changed, 2 to remove)
```

The server returns immediately. Files are processed in the background in batches of 5.

### 3. Search while indexing

You can search immediately — results are partial until all files are indexed:

```
→ search_codebase("how does login work?")
  (First search takes ~5s — loading the embedding model)
  **/path/to/my-project/src/auth.py** (score: 0.89)
  ...
```

### 4. Check status

```
→ get_index_stats()
  Vectors: 47
  Files tracked: 45
  Worker: idle (0 queued, 47 processed, 0 errors)
  Model loaded: True
```

---

## CLI Reference

```
turbocode-mcp [OPTIONS]
```

| Flag | Description |
|---|---|
| `--help`, `-h` | Print usage information and exit |
| `--version`, `-v` | Print the version number and exit |
| `--debug` | Enable verbose logging to stderr |

---

## What Happens Under the Hood

| Step | What the server does | Time |
|---|---|---|
| Install | Creates `.venv/`, installs Python deps | ~30-60s (one-time) |
| Startup | Loads `meta.json` + `store.json`, starts background threads | ~100ms |
| First tool call | Loads embedding model + vector index | ~5s (one-time) |
| `index_directory` | Scans filesystem, diffs against meta, enqueues changes | ~50ms |
| Background worker | Processes 5 files per batch, persists after each batch | ~1-2s per batch |
| `search_codebase` | Embeds query, searches index, formats results | ~200ms (after model loaded) |
| 30 idle minutes | Watchdog persists and exits; client auto-restarts on next call | Auto |

---

## Troubleshooting

| Problem | Likely Cause | Solution |
|---|---|---|
| `command not found: turbocode-mcp` | npm global bin not on PATH | Run `npm install -g turbocode-mcp` again; check `npm root -g` |
| Server starts but tools error | Python venv failed | Delete `.venv/` and reinstall: `npm install -g turbocode-mcp` |
| First search is slow (~5s) | Model loading | Normal — model is cached for subsequent calls |
| Server exits after idle | Watchdog timeout | Normal — client auto-restarts |
| Index is empty after restart | Persistence directory deleted | Re-run `index_directory` |

---

## Next Steps

- Read the [Usage Guide](usage.md) for a complete reference of all tools and resources
- See the [Architecture](architecture.md) doc for design decisions and rationale
- Check the [Roadmap](roadmap.md) for planned features
