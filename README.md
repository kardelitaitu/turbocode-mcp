<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://img.shields.io/badge/TurboCode-MCP-FF6B35?style=for-the-badge&logo=python&logoColor=white">
  <img alt="TurboCode MCP" src="https://img.shields.io/badge/TurboCode-MCP-FF6B35?style=for-the-badge&logo=python&logoColor=white">
</picture>

# TurboCode MCP

A **fully local** codebase vector search MCP server powered by [Turbovec](https://github.com/andrewm4894/turbovec).  
Index your projects and search them semantically — no cloud, no data leaves your machine.

```bash
npm install -g turbocode-mcp
```

[![npm version](https://img.shields.io/npm/v/turbocode-mcp.svg)](https://www.npmjs.com/package/turbocode-mcp)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)
[![Node >=18](https://img.shields.io/badge/node->=18-green.svg)](https://nodejs.org)
[![Python >=3.9](https://img.shields.io/badge/python->=3.9-blue.svg)](https://python.org)
[![Tests: 619 passing](https://img.shields.io/badge/tests-619%20passing-brightgreen.svg)](https://github.com/anomalyco/turbocode-mcp)

---

## Quick Start

```bash
npm install -g turbocode-mcp
```

Add to your MCP client (Claude Desktop example):

```json
{
  "mcpServers": {
    "turbovec-search": {
      "command": "turbocode-mcp"
    }
  }
}
```

Your AI assistant can now index and search your local codebase.

---

## Features

| Feature | What it means |
|---|---|
| **🔒 100% Local** | All embeddings and search run on your machine. No API keys. No cloud calls. |
| **💾 Persistent** | Index saved to disk. Survives restarts. |
| **⏳ Background Indexing** | Tools return instantly; files processed in batches. |
| **🪶 Lazy Loading** | Server starts in ~100ms. The heavy ML model loads only on first search. |
| **✅ 619 Tests** | 575 Python + 44 JS, all passing. Regression-gated. |
| **💤 Auto-Shutdown** | Exits after 30 idle minutes. Client auto-restarts on next call. |
| **🔄 Self-Maintaining** | Idle worker re-indexes stale files automatically. |
| **📦 One-Command Install** | Python venv and dependencies set up automatically. |
| **🧩 MCP Native** | Tools + Resources. Works with any MCP client. |

---

## Documentation

| Document | Description |
|---|---|
| **[Getting Started](docs/getting-started.md)** | Installation, configuration, and first workflow |
| **[Usage Guide](docs/usage.md)** | Complete tools & resources reference |
| **[Architecture](docs/architecture.md)** | System design, decisions, and data flows |
| **[Technical Reference](docs/reference.md)** | Implementation details for contributors |
| **[Roadmap](docs/roadmap.md)** | Planned features and development status |

---

## Quick Reference

**Tools:**

| Tool | Description |
|---|---|
| `index_directory(path)` | Queue a directory for background indexing |
| `search_codebase(query, k=3)` | Semantic search against indexed code |
| `get_index_stats()` | Index health and statistics (instant, no model load) |

**Resources (auto-context for the AI):**

| Resource | What it shows |
|---|---|
| `turbocode://status` | `Idle. 47 files indexed.` |
| `turbocode://stats` | JSON with vector count, disk usage, queue depth |

---

## Requirements

- **Node.js** ≥ 18
- **Python** ≥ 3.9 (with `python` or `python3` on PATH)

---

## License

MIT
