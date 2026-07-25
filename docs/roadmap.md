# TurboCode MCP — Roadmap

> Development stages with granular tasks, testing checklists, and acceptance criteria.
> Each phase includes implementation tasks followed by verification tests.

---

## Phase 1: Project Scaffolding

> **Goal:** A working npm package that installs and runs the Python server.

### 1.1 — `package.json`

- [ ] Define `name`, `version`, `description`, `author`, `license`
- [ ] Set `"bin": { "turbocode-mcp": "./bin/cli.js" }`
- [ ] Set `"scripts": { "postinstall": "node ./scripts/setup.js" }`
- [ ] Add `"engines": { "node": ">=18" }`
- [ ] Add `"keywords": ["mcp", "turbovec", "rag", "codebase"]`

**Acceptance:** `npm install -g .` installs without errors

### 1.2 — `scripts/setup.js`

- [ ] Detect platform (Windows vs POSIX) for path resolution
- [ ] Locate `python` or `python3` on PATH
- [ ] Create `.venv` via `python -m venv`
- [ ] Install pip dependencies from `requirements.txt`
- [ ] Validate that `.venv/bin/python` (or `Scripts\python.exe`) exists
- [ ] Print clear error messages if Python is missing
- [ ] Exit with code 1 on failure (npm reports the error)

**Acceptance:** `npm install -g .` creates `.venv/` with all deps installed

### 1.3 — `requirements.txt`

- [ ] Pin `fastmcp>=0.2.0`
- [ ] Pin `turbovec>=0.8.0`
- [ ] Pin `fastembed>=0.3.0`
- [ ] Pin `numpy>=1.24.0`

**Acceptance:** `pip install -r requirements.txt` succeeds

### 1.4 — `bin/cli.js`

- [ ] Resolve paths relative to `__dirname` (not `process.cwd()`)
- [ ] Locate `.venv/bin/python` (or `Scripts\python.exe`) relative to package root
- [ ] Spawn `src/server.py` as child process with `{ stdio: 'inherit' }`
- [ ] On Python executable not found: print error + exit code 1
- [ ] Forward child process exit code to parent
- [ ] Handle `SIGINT`/`SIGTERM` gracefully (kill Python child)

**Acceptance:** Running `turbocode-mcp` spawns the Python server

### 1.5 — Phase 1 Tests

- [ ] **Clean install:** `npm install -g .` → no errors, `.venv/` created
- [ ] **Reinstall idempotent:** Run install again → no duplicate `.venv` errors
- [ ] **Python missing:** Temporarily remove Python from PATH → clear error message
- [ ] **Command exists:** `which turbocode-mcp` (or `where`) → finds the binary
- [ ] **Server starts:** `turbocode-mcp` → Python process starts, waits for stdio
- [ ] **Server stops:** Ctrl+C → process exits cleanly

---

## Phase 2: Server Core — Lazy Loading & Persistence

> **Goal:** The server starts, loads state from disk, and exposes the lazy-loading pattern.

### 2.1 — Global State & Constants

- [ ] Define `TURBOCODE_DIR = os.path.expanduser("~/.turbocode")`
- [ ] Define `INDEX_PATH`, `META_PATH`, `STORE_PATH`
- [ ] Declare globals: `model = None`, `index = None`, `meta = {}`, `store = {}`
- [ ] Declare globals: `current_id = 0`, `last_activity = time.time()`
- [ ] Declare `index_queue = deque()`, `worker_state = {...}`
- [ ] Create `queue_lock` and `index_lock` (threading.Lock)

**Acceptance:** Server boots without loading model or index

### 2.2 — Logging

- [ ] Create `log(msg)` function that writes to `sys.stderr`
- [ ] All status/warning/error messages use `log()`, never `print()`
- [ ] No `print()` calls anywhere in the server code

**Acceptance:** MCP stdout remains clean (no stray text in JSON-RPC stream)

### 2.3 — Lazy Loading Helpers

- [ ] `ensure_model()` — loads `TextEmbedding` (fastembed) on first call
- [ ] `ensure_index()` — loads `IdMapIndex.load(INDEX_PATH)` or creates empty
- [ ] `ensure_resources()` — calls both helpers
- [ ] All three functions are safe to call multiple times (idempotent)

**Acceptance:** Model not loaded at startup; loads on first `search_codebase` or `index_directory`

### 2.4 — Atomic Persistence

- [ ] `atomic_write(path, data)` — writes to `.tmp` then `os.replace()`
- [ ] `os.fsync(f.fileno())` after write, before rename
- [ ] `persist_all()` — saves `index.tvim`, `meta.json`, `store.json` atomically
- [ ] `persist_all()` holds `index_lock` for the entire operation

**Acceptance:** Kill the process mid-persist → on restart, previous state is intact

### 2.5 — Cold-Start Recovery

- [ ] `load_and_verify()` loads `meta.json` and `store.json`
- [ ] If `len(meta) != len(store)` → rebuild `meta` from `store` (store is source of truth)
- [ ] If `.tvim` is corrupt → log warning, delete, create empty index
- [ ] `current_id = max(store.keys(), default=0) + 1`
- [ ] If both meta and store are empty → clean start, `current_id = 1`

**Acceptance:** Delete `meta.json` → server recovers from `store.json` on next boot

### 2.6 — FastMCP Registration

- [ ] `mcp = FastMCP("TurboCode MCP")`
- [ ] Register all 3 tools and 2 resources
- [ ] `mcp.run()` starts the stdio JSON-RPC listener

**Acceptance:** `fastmcp dev src/server.py` shows all tools and resources in the inspector

### 2.7 — Phase 2 Tests

- [ ] **Startup fast:** Server ready in < 200ms (no model load)
- [ ] **Lazy load:** `get_index_stats()` returns instantly, model not loaded
- [ ] **Lazy load triggers:** Call `search_codebase()` → model loads (~5s)
- [ ] **Persistence round-trip:** Index a file, restart server, search → results found
- [ ] **Atomic write:** Kill process during `persist_all()` → `.tvim` not corrupt
- [ ] **Recovery:** Delete `meta.json` → server rebuilds it from `store.json`
- [ ] **Clean start:** Delete all `.turbocode/` → server starts fresh, no errors

---

## Phase 3: Background Indexing Worker

> **Goal:** `index_directory` returns instantly; files are indexed in the background.

### 3.1 — Queue Management

- [ ] `enqueue(priority, file_path)` — thread-safe via `queue_lock`
- [ ] `dequeue_batch(batch_size=5)` — priority sorted, thread-safe
- [ ] `queue_depth()` — thread-safe size check
- [ ] Priority order: `remove` (0) > `new` (1) > `changed` (2) > `reindex` (3)

**Acceptance:** Enqueue and dequeue from different threads without data loss

### 3.2 — File Indexing

- [ ] `handle_index(file_path)` — read file, chunk to 2000 chars, `model.encode()`, `index.add_with_ids()`
- [ ] I/O (`open().read()`) outside lock
- [ ] CPU (`model.encode()`) outside lock
- [ ] Only `index_lock` for mutations: `index.add_with_ids()`, `store[id] = ...`, `meta[path] = ...`
- [ ] `current_id` read and incremented inside `index_lock`
- [ ] If file was previously indexed, `remove()` old ID before adding new
- [ ] Skip unreadable files silently (log warning)

**Acceptance:** File content appears in search results within seconds

### 3.3 — File Removal

- [ ] `handle_remove(file_path)` — verify file is in meta, `index.remove()`, clean up store + meta
- [ ] Handle case where ID was already removed from index (turbovec silent failure)

**Acceptance:** Delete a file, re-index, it no longer appears in search results

### 3.4 — Background Worker Loop

- [ ] `background_worker()` — daemon thread, infinite loop
- [ ] Dequeue batch, process each file, `persist_all()` after batch
- [ ] If queue empty, check for stale files → enqueue them
- [ ] `BATCH_SIZE = 5`, `BATCH_INTERVAL = 1.0`
- [ ] Update `worker_state` counters atomically
- [ ] Wrap per-file processing in try/except (never crash the thread)

**Acceptance:** Index 100 files → tools return instantly, worker processes in background

### 3.5 — Stale Re-indexing

- [ ] `find_stale_files(max_age_days=7, max_files=10)` — filter + random sample
- [ ] Filter candidates inside `index_lock`
- [ ] Enqueue stale files only when main queue is empty
- [ ] Random sampling (not full sort) for performance

**Acceptance:** After initial indexing, worker re-checks old files when idle

### 3.6 — `index_directory` Tool

- [ ] `touch()` to reset idle timer
- [ ] `ensure_resources()` to load model + index
- [ ] Walk directory, collect `.py`, `.rs`, `.md`, `.txt` files
- [ ] Compare against `meta` (inside `index_lock`) for new/changed/unchanged
- [ ] Detect removed files (in meta but not on disk)
- [ ] Enqueue via `queue_lock` (not `index_lock`)
- [ ] Return clear summary: "Queued X files (Y new, Z changed, W to remove)"

**Acceptance:** Calling `index_directory` twice returns "All up to date" on second call

### 3.7 — `search_codebase` Tool

- [ ] `touch()`, validate `k` (1–20), check empty index
- [ ] `ensure_resources()`, encode query, `index.search()`
- [ ] Look up results in `store` (inside `index_lock`)
- [ ] Format with file path, score, content snippet (first 500 chars)
- [ ] If no results and queue is active, append note about queued files

**Acceptance:** Search returns results with correct scores and file paths

### 3.8 — `get_index_stats` Tool

- [ ] `touch()`, read `len(store)`, `len(meta)`, `queue_depth()`, file size
- [ ] Report `model_loaded` status
- [ ] Never calls `ensure_model()` or `ensure_index()`

**Acceptance:** `get_index_stats()` is instant (< 1ms) even with 10K files indexed

### 3.9 — Phase 3 Tests

- [ ] **Non-blocking:** `index_directory` on a large dir returns in < 100ms
- [ ] **Background progress:** Call `get_index_stats` while indexing → queue_depth decreases
- [ ] **Search during indexing:** Results appear as files are processed
- [ ] **Idempotent indexing:** Index same dir twice → no duplicates, second call is instant
- [ ] **Re-index changed file:** Modify a file, re-index → it's updated in search results
- [ ] **Remove deleted file:** Delete a file, re-index → it disappears from results
- [ ] **Stale re-index:** Set `max_age_days=0`, wait → stale files get queued
- [ ] **Priority order:** Index 100 files, add 1 new file → new file indexed before re-indexing stale ones
- [ ] **Worker crash recovery:** Worker hits bad file → logs error, continues with next file
- [ ] **Concurrent enqueue:** Call `index_directory` rapidly 3 times → queue handles all items

---

## Phase 4: Resources & Idle Shutdown

> **Goal:** Resources provide auto-context for the AI. Server shuts down after inactivity.

### 4.1 — `turbocode://status` Resource

- [ ] `touch()` at start
- [ ] Check `model` and `index` state (no load trigger)
- [ ] Return: "Ready. N files tracked. (Model loaded on demand)" or "Idle. N files indexed." or "Indexing... N queued."
- [ ] Never calls `ensure_model()` or `ensure_index()`

**Acceptance:** Resource returns in < 1ms regardless of index size

### 4.2 — `turbocode://stats` Resource

- [ ] `touch()` at start
- [ ] Return JSON: `vectors`, `files_tracked`, `directories`, `disk_size_kb`, `queue_depth`, `state`, `model_loaded`, `model`
- [ ] Never calls `ensure_model()` or `ensure_index()`

**Acceptance:** Resource returns valid JSON with all fields

### 4.3 — Idle Watchdog

- [ ] `touch()` — set `last_activity = time.time()`
- [ ] Every tool + resource handler calls `touch()`
- [ ] `idle_watchdog()` — daemon thread, checks every 60 seconds
- [ ] After `IDLE_TIMEOUT = 30 * 60` seconds of inactivity → `persist_all()`, `log()`, `os._exit(0)`
- [ ] Shutdown message goes to stderr (not stdout)

**Acceptance:** Wait 30 minutes → server exits, MCP client auto-restarts on next call

### 4.4 — Phase 4 Tests

- [ ] **Status returns:** `turbocode://status` works before any tool call (no model load)
- [ ] **Stats returns:** `turbocode://stats` returns valid JSON with correct counts
- [ ] **Status updates:** After indexing, status shows "Idle. N files indexed."
- [ ] **Touch resets timer:** Calling a tool mid-countdown → timer resets, no shutdown
- [ ] **Shutdown fires:** Wait full timeout → `os._exit(0)` called
- [ ] **Client restart:** After shutdown, call tool → MCP client restarts server, works
- [ ] **No stdout pollution:** All log messages on stderr, stdout is clean JSON-RPC

---

## Phase 5: Local Integration Testing

> **Goal:** Full end-to-end verification of the npm package.

### 5.1 — npm Pipeline

- [ ] `npm link` → global install succeeds
- [ ] `.venv/` created in the package directory
- [ ] `turbocode-mcp` command exists on PATH
- [ ] Running `turbocode-mcp` starts the server
- [ ] Server shows: "Ready. 0 files tracked. Model/index loaded on demand."

### 5.2 — End-to-End Workflow

- [ ] Server starts, `turbocode://status` returns instantly
- [ ] `index_directory` on a real project (e.g. this repo) returns within 100ms
- [ ] Background worker processes files, status updates show progress
- [ ] `search_codebase` on first call takes ~5s (model load)
- [ ] `search_codebase` on second call is instant
- [ ] Results contain real file paths and content from the project
- [ ] `get_index_stats` shows correct counts

### 5.3 — Persistence & Recovery

- [ ] Stop server (Ctrl+C), restart → search still works (no re-index needed)
- [ ] Delete `.tvim` → server recovers from `store.json` (or starts fresh)
- [ ] Delete `meta.json` → server rebuilds it from `store.json`
- [ ] Corrupt `.tvim` → server creates empty index, logs warning

### 5.4 — Edge Cases

- [ ] **Empty directory:** `index_directory("/empty")` → no errors, "0 files queued"
- [ ] **Unsupported files:** Dir with `.jpg`, `.exe`, `.zip` → no errors, only supported files indexed
- [ ] **Non-existent directory:** `index_directory("/does/not/exist")` → clear error message
- [ ] **Very long query:** `search_codebase("a" * 10000)` → no crash, returns results
- [ ] **k out of range:** `search_codebase("test", k=999)` → clamped to 20
- [ ] **Concurrent calls:** Rapidly call `index_directory` + `search_codebase` + `get_index_stats` → no deadlocks, all return

---

## Phase 6: Polish

> **Goal:** Production-ready error handling, CLI flags, and documentation.

### 6.1 — CLI Flags

- [ ] `--help` — prints usage information
- [ ] `--version` — prints package version
- [ ] `--debug` — verbose logging to stderr

### 6.2 — Signal Handling

- [ ] `SIGINT` (Ctrl+C) → `persist_all()` then exit
- [ ] `SIGTERM` → `persist_all()` then exit
- [ ] Background worker finishes current file before persist

### 6.3 — Validation

- [ ] On startup, verify `.venv/bin/python` exists → clear error if not
- [ ] On startup, verify Python version ≥ 3.9
- [ ] On startup, verify required packages are importable
- [ ] On index, verify `directory_path` is readable

### 6.4 — Documentation

- [ ] Update `README.md` shields, install count, screenshots
- [ ] Review all `docs/` for accuracy against the final code
- [ ] Add MCP client config examples for Claude Desktop, Cursor, ZCode
- [ ] Add troubleshooting section to `docs/getting-started.md`

---

## Phase 7: Publish

> **Goal:** Package is live on npm and installable by anyone.

### 7.1 — Pre-Publish

- [ ] Bump version in `package.json` (semver)
- [ ] Update `CHANGELOG.md` with final release notes
- [ ] Update `README.md` shields with published version
- [ ] `npm login` — verify credentials
- [ ] `npm pack` — dry run, inspect tarball contents

### 7.2 — Publish

- [ ] `npm publish --access public`
- [ ] Verify package appears on npmjs.com

### 7.3 — Post-Publish

- [ ] `npm install -g turbocode-mcp` from a clean machine → works
- [ ] Connect to Claude Desktop → tools appear
- [ ] Index a real project → search works
- [ ] Restart client → persistence works

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