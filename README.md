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
[![Tests: 667 passing](https://img.shields.io/badge/tests-667%20passing-brightgreen.svg)](https://github.com/anomalyco/turbocode-mcp)

---

## Quick Start

### CPU (default)

```bash
npm install -g turbocode-mcp
```

### GPU (CUDA / DirectML / CoreML)

```bash
npm install -g turbocode-mcp
# Install GPU-enabled ONNX Runtime inside the auto-created venv
~/.turbocode/.venv/Scripts/pip install onnxruntime-gpu
```

### Configure

TurboCode MCP works with any MCP-compatible agent. Below are setup instructions for popular clients.

| Client | Type | Config Method |
|---|---|---|
| **Claude Desktop** | Desktop app | `claude_desktop_config.json` → `mcpServers` |
| **Cursor** | IDE | Settings → Features → MCP, add server |
| **Windsurf** | IDE | `~/.codeium/windsurf/mcp_config.json` → `mcpServers` |
| **Continue** | VS Code / JetBrains | `~/.continue/config.json` → `experimental.mcpServers` |
| **Cline / Roo Code** | VS Code extension | `cline_mcp_settings.json` or `roo_mcp_settings.json` |
| **Copilot** | VS Code / JetBrains | `~/.github/copilot-mcp.json` → `servers` |
| **Aider** | CLI | `aider --mcp-servers turbocode-mcp` or `aider-mcp-chat` mode |
| **Genkit / Goose** | CLI | `goose add mcp turbocode-mcp` or JSON config |

**Claude Desktop** (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "turbocode-mcp": {
      "command": "turbocode-mcp"
    }
  }
}
```

**Windsurf** (`~/.codeium/windsurf/mcp_config.json`):
```json
{
  "mcpServers": {
    "turbocode-mcp": {
      "command": "turbocode-mcp"
    }
  }
}
```

**Continue** (`~/.continue/config.json`):
```json
{
  "experimental": {
    "mcpServers": {
      "turbocode-mcp": {
        "command": "turbocode-mcp"
      }
    }
  }
}
```

**Cline / Roo Code** (`cline_mcp_settings.json` or `roo_mcp_settings.json`):
```json
{
  "mcpServers": {
    "turbocode-mcp": {
      "command": "turbocode-mcp"
    }
  }
}
```

**Copilot** (`~/.github/copilot-mcp.json`):
```json
{
  "servers": {
    "turbocode-mcp": {
      "command": "turbocode-mcp"
    }
  }
}
```

**Cursor:** Settings → Features → MCP → Add Server → Name: `turbocode-mcp`, Type: `command`, Command: `turbocode-mcp`

**Aider:** `aider --mcp-servers turbocode-mcp` (or run `aider-mcp-chat` for an MCP-native session)

**Genkit / Goose:** `goose add mcp turbocode-mcp`

Your AI assistant can now index and search your local codebase.

---

## Features

| Feature | What it means |
|---|---|
| **100% Local** | All embeddings and search run on your machine. No API keys. No cloud calls. |
| **Persistent** | Index saved to disk. Survives restarts. |
| **Background Indexing** | Tools return instantly; files processed in batches. |
| **Lazy Loading** | Server starts in ~100ms. The heavy ML model loads only on first search. |
| **Process Isolation** | Embedding model runs in a subprocess — main server stays at ~15MB. |
| **Model Choice** | `--model=<name>` flag to swap embedding models (default: `BAAI/bge-small-en-v1.5`). |
| **GPU Auto-Detect** | Uses CUDA/DirectML/CoreML automatically when `onnxruntime-gpu` is installed. |
| **.gitignore Aware** | `index_directory` skips gitignored files by default. Opt out with `respect_gitignore=False`. |
| **667 Tests** | 7 focused test files, all passing. Regression-gated. |
| **Auto-Shutdown** | Exits after 30 idle minutes. Client auto-restarts on next call. |
| **Self-Maintaining** | Idle worker re-indexes stale files automatically. |
| **One-Command Install** | Python venv and dependencies set up automatically. |
| **MCP Native** | Tools + Resources. Works with any MCP client. |

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
| `index_directory(path, respect_gitignore=True)` | Queue a directory for background indexing (`.py`, `.rs`, `.md`, `.txt`) |
| `index_workspace(path)` | Queue a workspace for background indexing (`.py`, `.rs`, `.js`, `.ts`, `.md`) |
| `update_file_index(path)` | Immediately re-index a single file after modification |
| `search_codebase(query, k=3)` | Semantic search against indexed code |
| `semantic_search(query, top_k=5)` | Semantic search alias with `top_k` parameter |
| `keyword_search(keyword, file_extension_filter="")` | Case-insensitive exact match across indexed content — returns file paths and line numbers |
| `read_file_content(path)` | Read a file's full unabridged content from disk |
| `get_index_stats()` | Index health and statistics (instant, no model load) |
| `get_index_status()` | Lightweight status — file count, vector count, directories tracked |
| `drop_index()` | Clear the entire index from memory and disk |

**Resources (auto-context for the AI):**

| Resource | What it shows |
|---|---|
| `turbocode://status` | `Idle. 47 files indexed.` |
| `turbocode://stats` | JSON with vector count, disk usage, queue depth |

---

## Benchmarks

### Python (current)

Synthetic benchmark: 100 generated code files, 100 search iterations, CPU embeddings.

| Category | Metric | Value |
|---|---|---|
| **Indexing** | Throughput | **~55 files/sec** |
| Indexing | Median | ~10 ms/file |
| Indexing | P95 | ~17 ms/file |
| Indexing | P99 | ~28 ms/file |
| **Semantic search (k=3)** | Median | ~3.0 ms |
| Semantic search (k=3) | P95 | ~3.9 ms |
| Semantic search (k=3) | P99 | ~4.4 ms |
| **Semantic search (k=5)** | Median | ~3.0 ms |
| Semantic search (k=5) | P95 | ~3.7 ms |
| Semantic search (k=5) | P99 | ~4.1 ms |
| **Semantic search (k=10)** | Median | ~3.0 ms |
| Semantic search (k=10) | P95 | ~3.7 ms |
| Semantic search (k=10) | P99 | ~3.8 ms |
| **Keyword search** | Median | ~0.08 ms |
| Keyword search | P95 | ~0.11 ms |
| Keyword search | P99 | ~0.12 ms |
| **Cold start (model load)** | — | ~5 ms |
| **Process memory** | — | ~20 MB (Python + embed subprocess) |

Run benchmarks yourself:
```bash
.venv/Scripts/python benchmarks/benchmark.py          # 100 files, 50 searches
.venv/Scripts/python benchmarks/benchmark.py --files 500 --searches 200
.venv/Scripts/python benchmarks/benchmark.py --json    # raw JSON output
```

### Rust (WIP — estimated)

A native Rust port would replace the Python server + embed subprocess with a single binary. The embedding model (ONNX Runtime) stays the same — inference speed is unchanged. The gains come from removing Python overhead, IPC serialization, and the GIL.

| Category | Python (current) | Rust (estimate) | Why |
|---|---|---|---|
| **Indexing throughput** | ~55 files/sec | **~65–70 files/sec** | Eliminate JSON-RPC to embed subprocess; embed ONNX inline |
| **Semantic search (k=5)** | ~3.0 ms | **~0.3–0.5 ms** | No GIL, no Python dict lookups, native `turbovec-rs` |
| **Keyword search** | ~0.08 ms | **~0.01 ms** | `memchr` over contiguous memory instead of Python string ops |
| **Cold start** | ~5 ms | **~5 ms** | ONNX Runtime model load is the bottleneck (unchanged) |
| **Process memory** | ~20 MB | **~5–8 MB** | Single binary, no Python interpreter, no subprocess |
| **Dependency footprint** | Node.js + Python + .venv | **Single binary** | `cargo install turbocode-mcp`, no runtime deps |

The real win of a Rust port is not raw speed — it's **consistency** (no GC pauses, no GIL contention under concurrent search), **simplicity** (one static binary), and **eliminating the Node.js + Python runtime dependency**.

---

## Requirements

- **Node.js** ≥ 18
- **Python** ≥ 3.9 (with `python` or `python3` on PATH)

---

## License

MIT
