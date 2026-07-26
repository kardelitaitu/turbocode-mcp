# TurboCode MCP — Roadmap

> Development stages with granular tasks, testing checklists, and acceptance criteria.
> Each phase includes implementation tasks followed by verification tests.

---

## Phase 1: Project Scaffolding

> **Goal:** A working npm package that installs and runs the Python server. ✅ **Complete**

### 1.1 — `package.json`

- [x] Define `name`, `version`, `description`, `author`, `license`
- [x] Set `"bin": { "turbocode-mcp": "./bin/cli.js" }`
- [x] Set `"scripts": { "postinstall": "node ./scripts/setup.js" }`
- [x] Add `"engines": { "node": ">=18" }`
- [x] Add `"keywords": ["mcp", "turbovec", "rag", "codebase"]`

**Acceptance:** ✅ `npm install -g .` installs without errors

### 1.2 — `scripts/setup.js`

- [x] Detect platform (Windows vs POSIX) for path resolution
- [x] Locate `python` or `python3` on PATH
- [x] Create `.venv` via `python -m venv`
- [x] Install pip dependencies from `requirements.txt`
- [x] Validate that `.venv/bin/python` (or `Scripts\python.exe`) exists
- [x] Print clear error messages if Python is missing
- [x] Exit with code 1 on failure (npm reports the error)

**Acceptance:** ✅ `npm install -g .` creates `.venv/` with all deps installed

### 1.3 — `requirements.txt`

- [x] Pin `fastmcp>=0.2.0`
- [x] Pin `turbovec>=0.8.0`
- [x] Pin `fastembed>=0.3.0`
- [x] Pin `numpy>=1.24.0`

**Acceptance:** ✅ `pip install -r requirements.txt` succeeds

### 1.4 — `bin/cli.js`

- [x] Resolve paths relative to `__dirname` (not `process.cwd()`)
- [x] Locate `.venv/bin/python` (or `Scripts\python.exe`) relative to package root
- [x] Spawn `src/server.py` as child process with `{ stdio: 'inherit' }`
- [x] On Python executable not found: print error + exit code 1
- [x] Forward child process exit code to parent
- [x] Handle `SIGINT`/`SIGTERM` gracefully (kill Python child)

**Acceptance:** ✅ Running `turbocode-mcp` spawns the Python server

### 1.5 — Phase 1 Tests

- [x] **Clean install:** `npm install -g .` → no errors, `.venv/` created
- [x] **Reinstall idempotent:** Run install again → no duplicate `.venv` errors
- [x] **Python missing:** Temporarily remove Python from PATH → clear error message
- [x] **Command exists:** `which turbocode-mcp` (or `where`) → finds the binary
- [x] **Server starts:** `turbocode-mcp` → Python process starts, waits for stdio
- [x] **Server stops:** Ctrl+C → process exits cleanly

---

## Phase 2: Server Core — Lazy Loading & Persistence

> **Goal:** The server starts, loads state from disk, and exposes the lazy-loading pattern. ✅ **Complete**

### 2.1 — Global State & Constants

- [x] Define `TURBOCODE_DIR = os.path.expanduser("~/.turbocode")`
- [x] Define `INDEX_PATH`, `META_PATH`, `STORE_PATH`
- [x] Declare globals: `model = None`, `index = None`, `meta = {}`, `store = {}`
- [x] Declare globals: `current_id = 0`, `last_activity = time.time()`
- [x] Declare `index_queue = deque()`, `worker_state = {...}`
- [x] Create `queue_lock` and `index_lock` (threading.Lock)

**Acceptance:** ✅ Server boots without loading model or index

### 2.2 — Logging

- [x] Create `log(msg)` function that writes to `sys.stderr`
- [x] All status/warning/error messages use `log()`, never `print()`
- [x] No `print()` calls anywhere in the server code

**Acceptance:** ✅ MCP stdout remains clean (no stray text in JSON-RPC stream)

### 2.3 — Lazy Loading Helpers

- [x] `ensure_model()` — loads `TextEmbedding` (fastembed) on first call
- [x] `ensure_index()` — loads `IdMapIndex.load(INDEX_PATH)` or creates empty
- [x] `ensure_resources()` — calls both helpers
- [x] All three functions are safe to call multiple times (idempotent)

**Acceptance:** ✅ Model not loaded at startup; loads on first `search_codebase` or `index_directory`

### 2.4 — Atomic Persistence

- [x] `atomic_write(path, data)` — writes to `.tmp` then `os.replace()`
- [x] `os.fsync(f.fileno())` after write, before rename
- [x] `persist_all()` — saves `index.tvim`, `meta.json`, `store.json` atomically
- [x] `persist_all()` holds `index_lock` for the entire operation

**Acceptance:** ✅ Kill the process mid-persist → on restart, previous state is intact

### 2.5 — Cold-Start Recovery

- [x] `load_and_verify()` loads `meta.json` and `store.json`
- [x] If `len(meta) != len(store)` → rebuild `meta` from `store` (store is source of truth)
- [x] If `.tvim` is corrupt → log warning, delete, create empty index
- [x] `current_id = max(store.keys(), default=0) + 1`
- [x] If both meta and store are empty → clean start, `current_id = 1`

**Acceptance:** ✅ Delete `meta.json` → server recovers from `store.json` on next boot

### 2.6 — FastMCP Registration

- [x] `mcp = FastMCP("TurboCode MCP")`
- [x] Register all 3 tools and 2 resources
- [x] `mcp.run()` starts the stdio JSON-RPC listener

**Acceptance:** ✅ `fastmcp dev src/server.py` shows all tools and resources in the inspector

### 2.7 — Phase 2 Tests

- [x] **Startup fast:** Server ready in < 200ms (no model load)
- [x] **Lazy load:** `get_index_stats()` returns instantly, model not loaded
- [x] **Lazy load triggers:** Call `search_codebase()` → model loads (~5s)
- [x] **Persistence round-trip:** Index a file, restart server, search → results found
- [x] **Atomic write:** Kill process during `persist_all()` → `.tvim` not corrupt
- [x] **Recovery:** Delete `meta.json` → server rebuilds it from `store.json`
- [x] **Clean start:** Delete all `.turbocode/` → server starts fresh, no errors

---

## Phase 3: Background Indexing Worker

> **Goal:** `index_directory` returns instantly; files are indexed in the background. ✅ **Complete**

### 3.1 — Queue Management

- [x] `enqueue(priority, file_path)` — thread-safe via `queue_lock`
- [x] `dequeue_batch(batch_size=5)` — priority sorted, thread-safe
- [x] `queue_depth()` — thread-safe size check
- [x] Priority order: `remove` (0) > `new` (1) > `changed` (2) > `reindex` (3)

**Acceptance:** ✅ Enqueue and dequeue from different threads without data loss

### 3.2 — File Indexing

- [x] `handle_index(file_path)` — read file, chunk to 2000 chars, `model.encode()`, `index.add_with_ids()`
- [x] I/O (`open().read()`) outside lock
- [x] CPU (`model.encode()`) outside lock
- [x] Only `index_lock` for mutations: `index.add_with_ids()`, `store[id] = ...`, `meta[path] = ...`
- [x] `current_id` read and incremented inside `index_lock`
- [x] If file was previously indexed, `remove()` old ID before adding new
- [x] Skip unreadable files silently (log warning)

**Acceptance:** ✅ File content appears in search results within seconds

### 3.3 — File Removal

- [x] `handle_remove(file_path)` — verify file is in meta, `index.remove()`, clean up store + meta
- [x] Handle case where ID was already removed from index (turbovec silent failure)

**Acceptance:** ✅ Delete a file, re-index, it no longer appears in search results

### 3.4 — Background Worker Loop

- [x] `background_worker()` — daemon thread, infinite loop
- [x] Dequeue batch, process each file, `persist_all()` after batch
- [x] If queue empty, check for stale files → enqueue them
- [x] `BATCH_SIZE = 5`, `BATCH_INTERVAL = 1.0`
- [x] Update `worker_state` counters atomically
- [x] Wrap per-file processing in try/except (never crash the thread)

**Acceptance:** ✅ Index 100 files → tools return instantly, worker processes in background

### 3.5 — Stale Re-indexing

- [x] `find_stale_files(max_age_days=7, max_files=10)` — filter + random sample
- [x] Filter candidates inside `index_lock`
- [x] Enqueue stale files only when main queue is empty
- [x] Random sampling (not full sort) for performance

**Acceptance:** ✅ After initial indexing, worker re-checks old files when idle

### 3.6 — `index_directory` Tool

- [x] `touch()` to reset idle timer
- [x] `ensure_resources()` to load model + index
- [x] Walk directory, collect `.py`, `.rs`, `.md`, `.txt` files
- [x] Compare against `meta` (inside `index_lock`) for new/changed/unchanged
- [x] Detect removed files (in meta but not on disk)
- [x] Enqueue via `queue_lock` (not `index_lock`)
- [x] Return clear summary: "Queued X files (Y new, Z changed, W to remove)"

**Acceptance:** ✅ Calling `index_directory` twice returns "All up to date" on second call

### 3.7 — `search_codebase` Tool

- [x] `touch()`, validate `k` (1–20), check empty index
- [x] `ensure_resources()`, encode query, `index.search()`
- [x] Look up results in `store` (inside `index_lock`)
- [x] Format with file path, score, content snippet (first 500 chars)
- [x] If no results and queue is active, append note about queued files

**Acceptance:** ✅ Search returns results with correct scores and file paths

### 3.8 — `get_index_stats` Tool

- [x] `touch()`, read `len(store)`, `len(meta)`, `queue_depth()`, file size
- [x] Report `model_loaded` status
- [x] Never calls `ensure_model()` or `ensure_index()`

**Acceptance:** ✅ `get_index_stats()` is instant (< 1ms) even with 10K files indexed

### 3.9 — Phase 3 Tests

- [x] **Non-blocking:** `index_directory` on a large dir returns in < 100ms
- [x] **Background progress:** Call `get_index_stats` while indexing → queue_depth decreases
- [x] **Search during indexing:** Results appear as files are processed
- [x] **Idempotent indexing:** Index same dir twice → no duplicates, second call is instant
- [x] **Re-index changed file:** Modify a file, re-index → it's updated in search results
- [x] **Remove deleted file:** Delete a file, re-index → it disappears from results
- [x] **Stale re-index:** Set `max_age_days=0`, wait → stale files get queued
- [x] **Priority order:** Index 100 files, add 1 new file → new file indexed before re-indexing stale ones
- [x] **Worker crash recovery:** Worker hits bad file → logs error, continues with next file
- [x] **Concurrent enqueue:** Call `index_directory` rapidly 3 times → queue handles all items

---

## Phase 4: Resources & Idle Shutdown

> **Goal:** Resources provide auto-context for the AI. Server shuts down after inactivity. ✅ **Complete**

### 4.1 — `turbocode://status` Resource

- [x] `touch()` at start
- [x] Check `model` and `index` state (no load trigger)
- [x] Return: "Ready. N files tracked. (Model loaded on demand)" or "Idle. N files indexed." or "Indexing... N queued."
- [x] Never calls `ensure_model()` or `ensure_index()`

**Acceptance:** ✅ Resource returns in < 1ms regardless of index size

### 4.2 — `turbocode://stats` Resource

- [x] `touch()` at start
- [x] Return JSON: `vectors`, `files_tracked`, `directories`, `disk_size_kb`, `queue_depth`, `state`, `model_loaded`, `model`
- [x] Never calls `ensure_model()` or `ensure_index()`

**Acceptance:** ✅ Resource returns valid JSON with all fields

### 4.3 — Idle Watchdog

- [x] `touch()` — set `last_activity = time.time()`
- [x] Every tool + resource handler calls `touch()`
- [x] `idle_watchdog()` — daemon thread, checks every 60 seconds
- [x] After `IDLE_TIMEOUT = 30 * 60` seconds of inactivity → `persist_all()`, `log()`, `os._exit(0)`
- [x] Shutdown message goes to stderr (not stdout)

**Acceptance:** ✅ Wait 30 minutes → server exits, MCP client auto-restarts on next call

### 4.4 — Phase 4 Tests

- [x] **Status returns:** `turbocode://status` works before any tool call (no model load)
- [x] **Stats returns:** `turbocode://stats` returns valid JSON with correct counts
- [x] **Status updates:** After indexing, status shows "Idle. N files indexed."
- [x] **Touch resets timer:** Calling a tool mid-countdown → timer resets, no shutdown
- [x] **Shutdown fires:** Wait full timeout → `os._exit(0)` called
- [x] **Client restart:** After shutdown, call tool → MCP client restarts server, works
- [x] **No stdout pollution:** All log messages on stderr, stdout is clean JSON-RPC

---

## Phase 5: Local Integration Testing

> **Goal:** Full end-to-end verification of the npm package. ✅ **Complete**

### 5.1 — npm Pipeline

- [x] `npm link` → global install succeeds
- [x] `.venv/` created in the package directory
- [x] `turbocode-mcp` command exists on PATH
- [x] Running `turbocode-mcp` starts the server
- [x] Server shows: "Ready. 0 files tracked. Model/index loaded on demand."

### 5.2 — End-to-End Workflow

- [x] Server starts, `turbocode://status` returns instantly
- [x] `index_directory` on a real project (e.g. this repo) returns within 100ms
- [x] Background worker processes files, status updates show progress
- [x] `search_codebase` on first call takes ~5s (model load)
- [x] `search_codebase` on second call is instant
- [x] Results contain real file paths and content from the project
- [x] `get_index_stats` shows correct counts

### 5.3 — Persistence & Recovery

- [x] Stop server (Ctrl+C), restart → search still works (no re-index needed)
- [x] Delete `.tvim` → server recovers from `store.json` (or starts fresh)
- [x] Delete `meta.json` → server rebuilds it from `store.json`
- [x] Corrupt `.tvim` → server creates empty index, logs warning

### 5.4 — Edge Cases

- [x] **Empty directory:** `index_directory("/empty")` → no errors, "0 files queued"
- [x] **Unsupported files:** Dir with `.jpg`, `.exe`, `.zip` → no errors, only supported files indexed
- [x] **Non-existent directory:** `index_directory("/does/not/exist")` → clear error message
- [x] **Very long query:** `search_codebase("a" * 10000)` → no crash, returns results
- [x] **k out of range:** `search_codebase("test", k=999)` → clamped to 20
- [x] **Concurrent calls:** Rapidly call `index_directory` + `search_codebase` + `get_index_stats` → no deadlocks, all return

---

## Phase 6: Polish

> **Goal:** Production-ready error handling, CLI flags, and documentation. ✅ **Complete**

### 6.1 — CLI Flags

- [x] `--help` — prints usage information
- [x] `--version` — prints package version
- [x] `--debug` — verbose logging to stderr

### 6.2 — Signal Handling

- [x] `SIGINT` (Ctrl+C) → `persist_all()` then exit
- [x] `SIGTERM` → `persist_all()` then exit
- [x] Background worker finishes current file before persist

### 6.3 — Validation

- [x] On startup, verify `.venv/bin/python` exists → clear error if not
- [x] On startup, verify Python version ≥ 3.9
- [x] On startup, verify required packages are importable
- [x] On index, verify `directory_path` is readable

### 6.4 — Documentation

- [x] Update `README.md` shields, install count, screenshots
- [x] Review all `docs/` for accuracy against the final code
- [x] Add MCP client config examples for Claude Desktop, Cursor, ZCode
- [x] Add troubleshooting section to `docs/getting-started.md`

---

## Phase 7: Publish

> **Goal:** Package is live on npm and installable by anyone. ✅ **Complete**

### 7.1 — Pre-Publish

- [x] Bump version in `package.json` (semver)
- [x] Update `CHANGELOG.md` with final release notes
- [x] Update `README.md` shields with published version
- [x] `npm login` — verify credentials
- [x] `npm pack` — dry run, inspect tarball contents

### 7.2 — Publish

- [x] `npm publish --access public`
- [x] Verify package appears on npmjs.com

### 7.3 — Post-Publish

- [x] `npm install -g turbocode-mcp` from a clean machine → works
- [x] Connect to Claude Desktop → tools appear
- [x] Index a real project → search works
- [x] Restart client → persistence works

---

## Backlog

> Features for future versions, not yet scheduled.

### Performance

- [ ] Semantic chunking (function/class boundaries instead of 2000-char truncation)
- [ ] AST-aware indexing (imports, function signatures as metadata)
- [ ] Embedding content-hash cache (skip re-embedding identical content)
- [ ] Configurable batch size and interval via CLI flags

### Features

- [ ] Multi-project / named indexes (separate `.tvim` per project)
- [ ] File-system watch mode (inotify/FSEvents for auto-re-index)
- [ ] `--model` flag to choose embedding model (e.g. `all-mpnet-base-v2`)
- [ ] `--dim` and `--bit-width` flags for turbovec tuning
- [ ] WebSocket transport as alternative to stdio

### Reliability

- [ ] Periodic health check resource (uptime, memory usage, error rate)
- [ ] Index repair tool (`turbocode-mcp --repair`)
- [ ] Automatic backup of `.tvim` before destructive writes
- [ ] Telemetry (opt-in, anonymous, basic stats only)