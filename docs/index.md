# TurboCode MCP — Documentation

> **Version:** 1.0.0  
> **Package:** `turbocode-mcp`  
> **License:** MIT

TurboCode MCP is a **fully local** codebase vector search server for the [Model Context Protocol (MCP)](https://modelcontextprotocol.io). It connects your AI coding assistant to your local codebase via semantic search — no cloud, no API keys, no data leaves your machine.

---

## Quick Links

| If you want to… | Go here |
|---|---|
| Install and run it | [Getting Started](getting-started.md) |
| See available tools and resources | [Usage Guide](usage.md) |
| Understand how it works under the hood | [Architecture](architecture.md) |
| Dive into the implementation details | [Technical Reference](reference.md) |
| See what's planned next | [Roadmap](roadmap.md) |

---

## Features

| Feature | Description |
|---|---|
| **🔒 100% Local** | Embeddings, indexing, and search all happen on your machine |
| **💾 Disk-Persistent** | Index saved to `.turbocode/`, survives restarts |
| **⏳ Background Indexing** | Tools return instantly; files processed in batches by a background worker |
| **🪶 Lazy Loading** | Server starts in ~100ms; the heavy ML model loads only on first search |
| **💤 Auto-Shutdown** | Exits after 30 idle minutes to free RAM; client auto-restarts on next call |
| **🔄 Self-Maintaining** | Idle worker re-indexes stale files automatically |
| **📦 One-Command Install** | `npm install -g turbocode-mcp` sets up everything |

---

## Architecture at a Glance

```
MCP Client ──stdio──► bin/cli.js ──spawns──► src/server.py
                                                 │
                                          ┌──────┴──────┐
                                          │   Server     │
                                          │  ─────────   │
                                          │  Tools       │
                                          │  Resources   │
                                          │  Background  │
                                          │  Worker      │
                                          │  Idle        │
                                          │  Watchdog    │
                                          └──────┬──────┘
                                                 │
                                          .turbocode/
                                          ├ index.tvim
                                          ├ meta.json
                                          └ store.json
```

---

## MCP Contract Summary

**3 Tools** (AI-invoked actions):

| Tool | Description | Blocks? |
|---|---|---|
| `index_directory(path)` | Scan and queue files for background indexing | No |
| `search_codebase(query, k=3)` | Semantic search against indexed code | No |
| `get_index_stats()` | Index health and statistics | No |

**2 Resources** (auto-context for the AI):

| Resource | Returns |
|---|---|
| `turbocode://status` | Human-readable status string |
| `turbocode://stats` | JSON statistics document |

---

## Quick Start

```bash
# Install globally
npm install -g turbocode-mcp

# Add to your MCP client (Claude Desktop example)
# claude_desktop_config.json:
{
  "mcpServers": {
    "turbocode-mcp": {
      "command": "turbocode-mcp"
    }
  }
}
```

See the [Getting Started](getting-started.md) guide for detailed instructions.

---

## Project Structure

```
turbocode-mcp/
├── bin/cli.js                    # Node.js CLI wrapper
├── scripts/setup.js              # Postinstall venv bootstrap
├── src/server.py                 # Python MCP server
├── docs/
│   ├── index.md                  # This file
│   ├── getting-started.md        # Installation & quick start
│   ├── usage.md                  # Tools & resources reference
│   ├── architecture.md           # System design & decisions
│   ├── reference.md              # Technical deep-dive
│   └── roadmap.md                # Development roadmap
├── .turbocode/                   # Created on first index
│   ├── index.tvim                # Serialized vector index
│   ├── meta.json                 # File tracking metadata
│   └── store.json                # Chunk text content
├── requirements.txt              # Python dependencies
├── package.json                  # npm package definition
└── README.md                     # Project homepage
```
