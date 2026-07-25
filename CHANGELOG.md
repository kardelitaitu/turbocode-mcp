# Changelog

> All notable changes to TurboCode MCP are documented here.
> Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
> and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] — 2026-07-25

### Added

- **Project scaffolding:** `package.json` with `bin` and `postinstall`, `scripts/setup.js`, `bin/cli.js`, `requirements.txt`, `.gitignore`
- **Python MCP server** (`src/server.py`) with FastMCP + Turbovec
- **Disk-persistent index** (`~/.turbocode/index.tvim`, `meta.json`, `store.json`) with atomic writes
- **Background indexing worker** — daemon thread, 5-file batches, 1s interval
- **Incremental indexing** — skip unchanged files via mtime comparison
- **Stale file re-indexing** — idle worker refreshes files older than 7 days
- **Lazy loading** — model, index, and sentence_transformers import all load on first use only (instant startup, no cold imports)
- **Idle shutdown watchdog** — exits after 30 minutes of inactivity; client auto-restarts
- **Signal handling** — SIGINT/SIGTERM persist state before exit
- **Three MCP tools:** `index_directory`, `search_codebase`, `get_index_stats`
- **Two MCP resources:** `turbocode://status`, `turbocode://stats`
- **CLI flags:** `--help`, `--version`, `--debug`
- **Startup validation:** Python >= 3.9 check, required package imports
- **Full documentation suite:** getting-started, usage, architecture, reference, roadmap
- `AGENTS.md` — orientation for AI coding agents
- `CHANGELOG.md` — version history
- `JOURNAL.md` — development journal

### Fixed

- **`deque.sort()` bug in `dequeue_batch`** — `collections.deque` has no `.sort()` method. Converted to list for sorting, then rebuilt deque with remaining items.
- **Moved `import random` to module level** — was imported inside `find_stale_files` on every call.
- **Moved `import signal` to module level** — was imported inside `main()` on every invocation.
- **Added None-guard in `persist_all`** — silently returns if index hasn't been loaded yet, preventing `AttributeError`.
- **Wrapped `persist_all` in try/except in background worker** — a disk failure no longer kills the worker thread; error is logged and counter incremented.
- **Worker now tracks `processed` count** — `worker_state["processed"]` was never incremented. Each successfully processed file now increments it.
- **Worker sets `worker_state["status"]`** — switches between `"idle"` and `"indexing"` as the queue drains/fills.
- **Lazy import of sentence_transformers** — moved from module-level to inside `ensure_model()`, reducing cold startup from ~10s to <0.5s.
- **Integration test transport** — FastMCP 3.x uses newline-delimited JSON, not `Content-Length` headers. Fixed `_send`/`_recv` accordingly.
- **Test performance** — added `mock_model`/`mock_index` fixtures to 7 `index_directory` tests that were loading the real SentenceTransformer model, cutting unit test time from 54s to 6.8s.
- **None-guards in `handle_index` and `handle_remove`** — return early if `model` or `index` is None, preventing AttributeError crashes when called without `ensure_resources()`.
- **`os.walk` PermissionError guard** — `index_directory` now catches `PermissionError` and returns a descriptive error instead of crashing.
- **`search_codebase` empty query guard** — returns `"Error: Query cannot be empty."` for empty/whitespace-only input.
- **`index_stats` `default=str`** — added `default=str` to `json.dumps()` to handle non-serializable `last_error` values without `TypeError`.
- **Case-insensitive file extension matching** — `index_directory` now uses `.lower()` on filenames so `.PY`, `.Py`, `.TXT` etc. are detected.
- **TOCTOU race in `removed_files` detection** — moved `os.path.exists` check inside `index_lock` to close the race window.
- **`idle_watchdog` persist failure safety** — wrapped `persist_all()` in try/except so the watchdog still shuts down cleanly even if persist fails.
- **Signal handler deadlock fix** — signal handler now uses `index_lock.acquire(blocking=False)` to avoid deadlock when `background_worker` holds the lock during `persist_all`.
- **`_stop_event` for clean thread shutdown** — added `threading.Event` to signal `background_worker` and `idle_watchdog` loops to exit, enabling test isolation.
- **`clean_globals` fixture now stops background threads** — sets `_stop_event` at the start of every test, preventing thread pollution across tests.
- **Worker tests clear stop event** — all tests that start `background_worker` threads now call `_stop_event.clear()` to let them run.

### Tested

- **145 pytest unit tests** — 40 test classes covering: logging, atomic persistence, queue management, cold-start recovery, stale file detection (including `last_indexed` key missing), file indexing/removal, lazy loading, touch, validation, all 3 MCP tools, both MCP resources, background worker, long-file truncation, binary file resilience, search edge cases (no results/hint/k-clamp/empty/whitespace/special-chars/long-query), empty/unsupported-only directory scanning, symlink/hidden/PermissionError, case-insensitive extensions, None-index/model guards, None-index persistence guard, atomic crash safety, full round-trip consistency, cold-start recovery from crash, idle watchdog condition logic, signal handler registration, main() debug flag parsing, worker resilience under persist/index failures, concurrent enqueue/dequeue thread safety, missing/corrupt/empty/0-byte/non-int-key persistence file edge cases, load-and-verify store cleanup/path-skip, multiple concurrent enqueue threads, test-level cli-option passthrough, stop-event thread shutdown (worker+watchdog), validate_environment failure branches, stale-file re-indexing worker path, non-serializable worker_state handling, atomic_write normal path/cleanup, ensure_index os.remove failure, enqueue None/empty/invalid-priority, dequeue_batch negative size, idle_watchdog persist failure resilience.
- **28 Node.js tests** — 14 CLI wrapper tests (help, version, -h/-v short flags, --debug functional spawn, debug forwarding, spawn, signal forwarding, error paths, exit code forwarding) + 14 setup script tests (path resolution, platform detection, .venv verification, pip dependency audit, Python-not-found exit, old-version exit, missing-pip exit, missing-requirements fallback, post-setup verification, .venv postconditions).
- **11 integration tests** — full MCP protocol handshake, tool listing, get_index_stats call, index_directory not-found/success, resource listing, status/stats resource read, search empty index, search empty query.
- **Total: 183 tests, all passing** — unit tests in ~6.5s, full suite in ~35s.

### Known Issues

- First search/index call is ~5s (sentence-transformers model load — unavoidable cold start of the ~80MB model)
- Index is shared across all indexed directories (no multi-project isolation)
- File-level chunking only (2000-char truncation, no semantic splitting)