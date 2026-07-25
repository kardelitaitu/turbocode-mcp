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
- **Lazy loading** — model, index, and embedding model import all load on first use only (instant startup, no cold imports)
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
- **Lazy import of embedding model** — moved from module-level to inside `ensure_model()`, reducing cold startup from ~10s to <0.5s.
- **Integration test transport** — FastMCP 3.x uses newline-delimited JSON, not `Content-Length` headers. Fixed `_send`/`_recv` accordingly.
- **Test performance** — added `mock_model`/`mock_index` fixtures to 7 `index_directory` tests that were loading the real embedding model, cutting unit test time from 54s to 6.8s.
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

## [Unreleased]

### Fixed

- **`handle_index` stat-after-remove orphan bug** — `os.path.getmtime()` was called *after* old entry removal in the re-index path. If `getmtime`/`getsize` raised (file disappeared between read and lock), the old vector was removed from index/store but meta still pointed to it, creating an inconsistency. Moved stat calls before old-entry removal.
- **`handle_index` add failure rollback** — `index.add_with_ids()` failure no longer leaves partial state (orphaned `current_id` increment, inconsistent meta/store). The mutation block is now wrapped in try/except with rollback that resets meta/store/index on failure.
- **Test suite bug fixes** — 7 test bugs fixed: wrong-dimensions mock expectation, tmp file side-effect ordering, recursion in mock atomic_write, stale boundary time drift, mock ordering in corrupt tvim test, hypothesis fixture health check, JS syntax and path-matching issues.
- **`load_and_verify` non-dict meta.json crash** — when `meta.json` contains a JSON array (e.g., from disk corruption), `meta.update()` fails with `AttributeError`. Added `isinstance(loaded, dict)` guard and reset to `{}`.
- **`load_and_verify` non-dict store entry crash** — when store has a string value alongside dict values, `doc.get("path")` fails with `AttributeError`. Added `isinstance(doc, dict)` guard.
- **`handle_index` corrupt meta `id` key crash** — `meta[file_path]["id"]` crashes with `KeyError` when meta entry exists but has no `"id"`. Added `old_entry.get("id")` safe access with `isinstance` guard.
- **`handle_remove` corrupt meta `id` key crash** — same issue in `handle_remove`. Added safe access + early return when `"id"` is missing.

### Added

- **54 new Python unit tests** — 15 new test classes covering: stat-failure-preserves-old-entry (fix validation), add_with_ids rollback, remove failure non-blocking, write-failure no-tmp-leak, stale file boundary conditions, special chars in search results, corrupt tvim recreate, stats consistency, worker sleep behavior, remove-with-none-index, processed count accuracy, index-remove-index cycle, property-based search k-safety.
- **14 new JS tests** — CLI flag precedence, path resolution, platform detection, version format, empty args start, execSync error handling, Python candidates.
- **36 more Python unit tests** — 18 new test classes covering: handle_index rollback inner-remove-failure, meta-already-removed, store-entry-missing, path-is-directory, null-byte-path, BOM-prefixed file, ensure_index path-is-directory, empty-tvim file, get_index_stats missing/statted-path, persist_all no-temp-files-left, tmp-cleanup-on-partial-failure, worker persist-failure-error-counting, persist-failure-last-error, worker priority-remove-only, dequeue remove-before-new reindex-last, search no-results queued-hint, doc-id-not-in-store, store-entry-not-dict, same-file-indexed-twice-overwrites, current-id-increments, worker-status-indexing-during-batch, status-idle-when-no-queue, atomic_write empty-content, temp-file-cleanup-on-failure, worker persist-exception-survival, worker-continues-after-item-error, idle-watchdog-stop-during-sleep, multiline-content-truncation, oserror-during-read, permission-denied-via-mock, non-dict-meta-with-valid-store-rebuild.
- **3 new JS CLI tests** — syntax validity check, --debug + unknown flag, --version + unknown flag.
- **`followlinks=False`** — explicit `os.walk` parameter to prevent following symlinks (security hardening).
- **`OSError` catch in `index_directory`** — generic OSError caught (not just PermissionError), returns descriptive error string.
- **Stale `.tmp` cleanup at startup** — `main()` now cleans up leftover `.tmp` files from previous crashes.
- **Cosmetic `...` suffix fix** — `search_codebase` now only appends `"..."` when content > 500 chars (was always appended).
- **Total: 370 tests all passing** (was 299).

### Known Issues

- First search/index call is ~5s (fastembed model load — unavoidable cold start of the ~30MB model)
- Index is shared across all indexed directories (no multi-project isolation)
- File-level chunking only (2000-char truncation, no semantic splitting)