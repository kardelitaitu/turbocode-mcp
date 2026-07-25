# AGENTS.md — Guide for AI Coding Agents

> This file helps AI agents (Claude, Cursor, ZCode, etc.) understand the TurboCode MCP project
> so they can contribute effectively. Read this first before making changes.

---

## Project Overview

TurboCode MCP is a **globally-installable npm package** that provides a local codebase vector search MCP server.

**Core idea:** `npm install -g turbocode-mcp` → an AI assistant can index and semantically search your codebase, fully local, no cloud.

---

## Tech Stack

| Layer | Technology | Role |
|---|---|---|
| Package manager | npm | Distribution |
| CLI entry | JavaScript (Node.js) | Spawns Python process |
| MCP server | Python (FastMCP) | Tools + Resources |
| Vector index | Turbovec (IdMapIndex) | 4-bit quantized search |
| Embeddings | sentence-transformers (all-MiniLM-L6-v2) | 384-dim local embeddings |

---

## Project Structure

```
turbocode-mcp/
│
├── bin/cli.js                 # Node.js CLI wrapper (entry point when user runs `turbocode-mcp`)
├── scripts/setup.js           # npm postinstall hook — creates .venv, pip installs deps
├── src/server.py              # Python MCP server (main logic lives here)
│
├── docs/
│   ├── index.md               # Documentation homepage
│   ├── getting-started.md     # Install & first workflow
│   ├── usage.md               # Tools & resources API reference
│   ├── architecture.md        # System design & rationale
│   ├── reference.md           # Technical implementation details
│   └── roadmap.md             # Development stages & backlog
│
├── .turbocode/                # Created at runtime (git-ignored)
│   ├── index.tvim             # Serialized turbovec index
│   ├── meta.json              # File tracking (path → id, mtime, last_indexed)
│   └── store.json             # Chunk text content (id → path, content)
│
├── .venv/                     # Python virtual env (created by postinstall, git-ignored)
├── node_modules/              # npm deps (git-ignored)
├── requirements.txt           # Pinned Python deps
├── package.json               # npm definition
├── README.md                  # Project homepage
└── AGENTS.md                  # This file
```

---

## Files You Will Create / Modify

| File | Status | Notes |
|---|---|---|
| `package.json` | ❌ Not created | Define `bin`, `scripts.postinstall`, `keywords` |
| `scripts/setup.js` | ❌ Not created | Python venv bootstrap |
| `bin/cli.js` | ❌ Not created | Node.js wrapper, spawns Python |
| `src/server.py` | ❌ Not created | Main server — full implementation |
| `requirements.txt` | ❌ Not created | Pinned Python dependencies |
| `.gitignore` | ✅ Exists | Already created |

---

## Critical Gotchas (Read Before Writing Code)

### 1. Turbovec `load()` is a classmethod ⚠️

This is the **most important bug** to avoid:

```python
# ✅ CORRECT — returns a working index
index = IdMapIndex.load("index.tvim")

# ❌ WRONG — produces broken index (search returns empty, contains returns False)
index = IdMapIndex(dim=384, bit_width=4)
index.load("index.tvim")
```

### 2. Server has 3 threads

```
Main thread:  FastMCP event loop (handles MCP JSON-RPC over stdio)
Worker thread: Background indexer (daemon, processes 5-file batches)
Watchdog:      Idle shutdown timer (daemon, exits after 30 inactive minutes)
```

### 3. Lazy loading pattern

Model and index are **not loaded at startup**. Load on first use:

```python
model = None          # Set on first ensure_model()
index = None          # Set on first ensure_index()
meta = {}             # Loaded at startup (small JSON)
store = {}            # Loaded at startup (small JSON)
```

- `get_index_stats()` and `turbocode://status`/`turbocode://stats` must **never** trigger a model or index load
- Only `search_codebase()` and `index_directory()` call `ensure_resources()`

### 4. Thread safety — Two independent locks (never nest)

```python
queue_lock = threading.Lock()   # Protects only index_queue deque
index_lock = threading.Lock()   # Protects index, store, meta
```

**Golden rule:** Never hold both locks at once. I/O and `model.encode()` are done without any lock.

```python
# ✅ CORRECT
content = read_file(path)              # No lock
vec = model.encode([content])          # No lock
with index_lock:                       # Only critical section
    index.add_with_ids(vec, ids)
    store[id] = {...}
```

### 5. Idle shutdown

```python
IDLE_TIMEOUT = 30 * 60   # 30 minutes
os._exit(0)              # Hard exit (not sys.exit, which hangs on threads)
```

Every tool and resource handler must call `touch()` to reset the timer.

### 6. Atomic writes

All three persistence files (`index.tvim`, `meta.json`, `store.json`) must be written atomically using temp-file + `os.replace()`. A crash during a direct write corrupts the file.

```python
index.write(INDEX_PATH + ".tmp")
os.replace(INDEX_PATH + ".tmp", INDEX_PATH)  # Atomic
```

### 7. Cold-start consistency check

On startup, `len(meta)` must equal `len(store)`. If they disagree (crash between writes), rebuild `meta` from `store`. Store is the source of truth because it has the path for every vector.

### 8. Stale re-indexing uses random sampling

Do NOT sort all 10,000 files by `last_indexed` on every idle check. Use `random.sample()` on the filtered candidates.

---

## MCP Contract

### Tools

| Function | Signature | Loads Model? |
|---|---|---|
| `index_directory` | `(path: str) → str` | Yes (lazy) |
| `search_codebase` | `(query: str, k: int = 3) → str` | Yes (lazy) |
| `get_index_stats` | `() → str` | **No** |

### Resources

| URI | Returns | Loads Model? |
|---|---|---|
| `turbocode://status` | Human-readable status | **No** |
| `turbocode://stats` | JSON document | **No** |

---

## Design Constraints

1. **`index_directory` must return instantly** — no blocking on embedding. Enqueue to background worker and return status.
2. **Persistence after every batch** — `index.write()`, save `meta.json`, save `store.json` after each background batch.
3. **Incremental indexing** — detect new/changed/removed files by comparing against `meta.json`. Skip unchanged files.
4. **Stale re-indexing** — when queue is empty, find files with `last_indexed > 7 days` and queue them with low priority.
5. **Supported file types** — `.py`, `.rs`, `.md`, `.txt`. Extendable via a constant list.
6. **Content truncation** — files are capped at 2000 characters per chunk (simple v1 strategy).
7. **Node wrapper** — `bin/cli.js` must resolve paths relative to `__dirname`, not `process.cwd()`.

---

## Running & Testing

```bash
# Test the Python server directly (FastMCP inspector)
cd src
fastmcp dev server.py

# Test the full npm pipeline
npm link                          # Simulates global install
turbocode-mcp                     # Should launch the server
```

The server communicates over **stdio** using JSON-RPC 2.0. The FastMCP framework handles all protocol details — you just register tools and resources with decorators.

---

## Porting from Docs

The implementation plan is documented across these files (read them in order):

1. `docs/architecture.md` — System design, decisions, threading model
2. `docs/usage.md` — Exact tool contracts, parameters, return formats
3. `docs/reference.md` — Code patterns, turbovec API, error handling
4. `docs/roadmap.md` — What to build and in what order

The current **priority** is Phase 1 (scaffolding) followed by Phase 2 (server implementation).

---

## Maintain the Journal

After each session of work, log a brief entry in `JOURNAL.md` using the template at the bottom. Cover:

- **What happened** — files created/modified, decisions made
- **Key discoveries** — bugs found, API surprises, performance observations
- **Decisions made** — rationale for design choices
- **Open questions** — anything unresolved for next time

This keeps a permanent record for future agents and human contributors.

---

## Key Project Files

| File | Purpose |
|---|---|
| `README.md` | Project homepage for humans |
| `AGENTS.md` | Orientation for AI agents (this file) |
| `JOURNAL.md` | Session log — update after every work session |
| `CHANGELOG.md` | Version history (unreleased changes go under `[Unreleased]`) |
| `docs/getting-started.md` | Installation guide for humans |
