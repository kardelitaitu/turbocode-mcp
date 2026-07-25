"""
TurboCode MCP — Local codebase vector search MCP server.

Powered by FastMCP, Turbovec, and fastembed.
Fully local, no cloud, no API keys.
"""

from __future__ import annotations

import os
import sys
import json
import time
import random
import subprocess
import signal as sig_module
import threading
from collections import deque

import numpy as np
import pathspec
from fastmcp import FastMCP
from turbovec import IdMapIndex

# ═══════════════════════════════════════════════════════════════
# Phase 2.1 — Constants & Global State
# ═══════════════════════════════════════════════════════════════

TURBOCODE_DIR = os.path.join(os.path.expanduser("~"), ".turbocode")
INDEX_PATH = os.path.join(TURBOCODE_DIR, "index.tvim")
META_PATH = os.path.join(TURBOCODE_DIR, "meta.json")
STORE_PATH = os.path.join(TURBOCODE_DIR, "store.json")

BATCH_SIZE = 5
BATCH_INTERVAL = 1.0  # seconds
IDLE_TIMEOUT = 30 * 60  # 30 minutes
CHECK_INTERVAL = 60  # seconds between idle checks

# Lazy-loaded globals
MODEL_NAME: str = "BAAI/bge-small-en-v1.5"
model: object | None = None  # _ModelClient instance or None
index: IdMapIndex | None = None
meta: dict[str, dict] = {}
store: dict[int, dict] = {}
current_id: int = 0
last_activity: float = 0.0

# Thread-safe queue
index_queue: deque = deque()
queue_lock = threading.Lock()
index_lock = threading.Lock()
load_lock = threading.Lock()  # Guards ensure_model/ensure_index (lazy loading)
_stop_event = threading.Event()  # Signal to stop background threads (used by tests)

worker_state = {
    "status": "idle",
    "queue_depth": 0,
    "processed": 0,
    "errors": 0,
    "last_error": None,
}

# ═══════════════════════════════════════════════════════════════
# Phase 2.2 — Logging (stderr only; stdout is MCP transport)
# ═══════════════════════════════════════════════════════════════


DEBUG_MODE = False


def log(msg: str) -> None:
    """Log a message to stderr. stdout is reserved for MCP protocol."""
    print(f"[TurboCode MCP] {msg}", file=sys.stderr, flush=True)


def debug(msg: str) -> None:
    """Log a debug message to stderr (only when --debug is enabled)."""
    if DEBUG_MODE:
        print(f"[TurboCode MCP] [DEBUG] {msg}", file=sys.stderr, flush=True)


# ═══════════════════════════════════════════════════════════════
# Phase 2.3 — Lazy Loading Helpers
# ═══════════════════════════════════════════════════════════════


class _ModelClient:
    """Manages a subprocess that runs fastembed, keeping model memory
    isolated from the MCP server. Provides an encode() interface
    compatible with the old direct model API."""

    def __init__(self, model_name: str):
        self._model_name = model_name
        self._lock = threading.Lock()
        self._next_id = 0
        self._proc: subprocess.Popen | None = None
        self._start()

    def _start(self) -> None:
        embed_script = os.path.join(os.path.dirname(__file__), "embed_service.py")
        self._proc = subprocess.Popen(
            [sys.executable, embed_script, self._model_name],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if self._proc.poll() is not None:
            err = self._proc.stderr.read()
            raise RuntimeError(
                f"Embed subprocess exited immediately: {err.strip()}"
            )
        # Forward subprocess stderr to server log
        self._stderr_thread = threading.Thread(
            target=self._forward_stderr, daemon=True
        )
        self._stderr_thread.start()

    def _forward_stderr(self) -> None:
        for line in self._proc.stderr:
            if line.strip():
                log(f"[embed] {line.strip()}")

    def encode(self, texts: list[str], **kwargs) -> np.ndarray:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                log("Restarting embed subprocess...")
                self._start()
            req_id = self._next_id
            self._next_id += 1
            msg = json.dumps({"id": req_id, "texts": texts}, default=str)
            self._proc.stdin.write(msg + "\n")
            self._proc.stdin.flush()
            resp_line = self._proc.stdout.readline()
            if not resp_line:
                raise RuntimeError("Embed subprocess died unexpectedly")
            resp = json.loads(resp_line.strip())
            if "error" in resp:
                raise RuntimeError(f"Embedding error: {resp['error']}")
            return np.array(resp["vectors"], dtype=np.float32)

    def stop(self) -> None:
        with self._lock:
            if self._proc is not None:
                try:
                    self._proc.stdin.write(
                        json.dumps({"type": "shutdown"}) + "\n"
                    )
                    self._proc.stdin.flush()
                except Exception:
                    pass
                try:
                    self._proc.wait(timeout=5)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                self._proc = None


def ensure_model() -> object:
    """Lazy-load the embedding model via subprocess (thread-safe)."""
    global model
    if model is not None:
        return model
    with load_lock:
        if model is not None:
            return model
        try:
            model = _ModelClient(MODEL_NAME)
        except Exception as e:
            log(f"ERROR: Failed to start embed subprocess: {e}")
            raise
    return model


def ensure_index() -> None:
    """Load the turbovec index from disk or create an empty one (thread-safe)."""
    global index
    if index is not None:
        return
    with load_lock:
        if index is not None:
            return
        if os.path.exists(INDEX_PATH):
            try:
                log("Loading index from disk...")
                index = IdMapIndex.load(INDEX_PATH)
            except Exception as e:
                log(f"WARNING: Failed to load index ({e}). Creating empty.")
                try:
                    os.remove(INDEX_PATH)
                except Exception:
                    pass
                index = IdMapIndex(dim=384, bit_width=4)
        else:
            log("Creating new empty index.")
            index = IdMapIndex(dim=384, bit_width=4)


def ensure_resources() -> None:
    """Load both model and index on first use."""
    ensure_model()
    ensure_index()


# ═══════════════════════════════════════════════════════════════
# Phase 6.3 — Startup Validation
# ═══════════════════════════════════════════════════════════════


def validate_python_version() -> None:
    """Verify Python version >= 3.9."""
    if sys.version_info.major < 3 or (sys.version_info.major == 3 and sys.version_info.minor < 9):
        log(f"ERROR: Python >= 3.9 required (found {sys.version_info.major}.{sys.version_info.minor})")
        sys.exit(1)


def validate_imports() -> None:
    """Verify critical lightweight Python packages are importable.
    Heavy packages (fastembed, turbovec) are checked at
    first-use time to keep cold start under 0.5s.
    """
    missing = []
    for mod_name, import_name in [
        ("fastmcp", "fastmcp"),
        ("numpy", "numpy"),
        ("fastembed", "fastembed"),
    ]:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(mod_name)

    if missing:
        log(f"ERROR: Missing required Python packages: {', '.join(missing)}")
        log("Please reinstall: npm install -g turbocode-mcp")
        sys.exit(1)


def validate_environment() -> None:
    """Run all startup validations."""
    validate_python_version()
    validate_imports()
    debug("All startup validations passed.")


# ═══════════════════════════════════════════════════════════════
# Phase 2.4 — Atomic Persistence
# ═══════════════════════════════════════════════════════════════


def atomic_write(path: str, data: str) -> None:
    """Write data to a file atomically using temp file + rename."""
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise


def _persist_locked() -> None:
    """Persist all data. Caller must hold index_lock."""
    if index is None:
        debug("_persist_locked: index not loaded, nothing to persist.")
        return

    # Index (atomic via temp file + replace)
    try:
        index.write(INDEX_PATH + ".tmp")
        os.replace(INDEX_PATH + ".tmp", INDEX_PATH)
    except Exception as e:
        log(f"WARNING: Failed to persist index: {e}")
        # Continue to persist meta/store — any inconsistency is
        # repaired by load_and_verify() on the next restart.

    # Meta
    try:
        atomic_write(META_PATH, json.dumps(meta, indent=2, default=str))
    except Exception as e:
        log(f"WARNING: Failed to persist meta: {e}")

    # Store (convert int keys to strings for JSON)
    try:
        store_serializable = {str(k): v for k, v in store.items()}
        atomic_write(STORE_PATH, json.dumps(store_serializable, indent=2, default=str))
    except Exception as e:
        log(f"WARNING: Failed to persist store: {e}")


def persist_all() -> None:
    """Save index, meta, and store to disk atomically."""
    try:
        os.makedirs(TURBOCODE_DIR, exist_ok=True)
    except Exception as e:
        log(f"WARNING: Cannot create {TURBOCODE_DIR}: {e}")
        return

    with index_lock:
        _persist_locked()


# ═══════════════════════════════════════════════════════════════
# Phase 2.5 — Cold-Start Recovery
# ═══════════════════════════════════════════════════════════════


def load_and_verify() -> None:
    """Load meta/store from disk and check for crash-induced inconsistencies."""
    global meta, store, current_id

    # Load meta
    if os.path.exists(META_PATH):
        try:
            with open(META_PATH, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                meta = loaded
            else:
                log("WARNING: meta.json is not a dict. Starting fresh.")
                meta = {}
        except Exception as e:
            log(f"WARNING: Corrupt meta.json ({e}). Starting fresh.")
            meta = {}

    # Load store
    if os.path.exists(STORE_PATH):
        try:
            with open(STORE_PATH, encoding="utf-8") as f:
                store = {int(k): v for k, v in json.load(f).items()}
        except Exception as e:
            log(f"WARNING: Corrupt store.json ({e}). Starting fresh.")
            store = {}

    meta_count = len(meta)
    store_count = len(store)

    if meta_count == 0 and store_count == 0:
        current_id = 1
        return

    if meta_count != store_count:
        log(f"WARNING: Inconsistency detected (meta={meta_count} vs store={store_count}). "
            f"Rebuilding meta from store.")
        new_meta = {}
        for id_val, doc in store.items():
            if not isinstance(doc, dict):
                continue
            file_path = doc.get("path")
            if not file_path:
                continue
            new_meta[file_path] = {
                "id": id_val,
                "mtime": doc.get("mtime", 0),
                "size": doc.get("size", 0),
                "last_indexed": doc.get("last_indexed", 0),
            }
        meta.clear()
        meta.update(new_meta)
        atomic_write(META_PATH, json.dumps(meta, indent=2, default=str))

    current_id = max(store.keys(), default=0) + 1


# ═══════════════════════════════════════════════════════════════
# Phase 3.1 — Queue Management
# ═══════════════════════════════════════════════════════════════


def enqueue(priority: str, file_path: str) -> None:
    """Thread-safe enqueue with its own lock."""
    if not isinstance(file_path, str) or not file_path:
        return
    with queue_lock:
        index_queue.append((priority, file_path))


def dequeue_batch(batch_size: int = BATCH_SIZE) -> list[tuple[str, str]]:
    """Thread-safe priority dequeue. Returns up to batch_size items."""
    with queue_lock:
        if not index_queue:
            return []

        priority_order = {"remove": 0, "new": 1, "changed": 2, "reindex": 3}
        items = list(index_queue)
        items.sort(key=lambda x: priority_order.get(x[0], 99))
        index_queue.clear()

        try:
            safe_size = max(0, int(batch_size))
        except (ValueError, OverflowError, TypeError):
            safe_size = 0
        batch = items[:safe_size]
        remaining = items[safe_size:]
        index_queue.extend(remaining)
    return batch


def queue_depth() -> int:
    """Thread-safe queue size check."""
    with queue_lock:
        return len(index_queue)


# ═══════════════════════════════════════════════════════════════
# Phase 3.3 — File Removal
# ═══════════════════════════════════════════════════════════════


def handle_remove(file_path: str) -> None:
    """Remove a deleted file from the index. Lock only for mutations."""
    if file_path not in meta:
        return

    if index is None:
        return

    with index_lock:
        if file_path not in meta:
            return
        old_entry = meta[file_path]
        file_id = old_entry.get("id") if isinstance(old_entry, dict) else None
        if file_id is None:
            del meta[file_path]
            return
        try:
            index.remove(file_id)
        except BaseException:
            pass
        store.pop(file_id, None)
        del meta[file_path]


# ═══════════════════════════════════════════════════════════════
# Phase 3.2 — File Indexing
# ═══════════════════════════════════════════════════════════════


def handle_index(file_path: str) -> None:
    """Index a file. I/O and embedding outside lock. Lock only for mutations."""
    global current_id

    if model is None or index is None:
        log(f"WARNING: Model or index not loaded. Skipping {file_path}.")
        return

    # I/O: no lock needed
    try:
        if not os.path.isfile(file_path):
            log(f"WARNING: Not a regular file — skipping {file_path}.")
            return
    except Exception:
        log(f"WARNING: Cannot stat {file_path} for file type check. Skipping.")
        return
    try:
        with open(file_path, "r", encoding="utf-8-sig", errors="replace") as f:
            content = f.read()
    except Exception:
        log(f"WARNING: Cannot read {file_path}. Skipping.")
        return

    chunk = content[:2000].strip()
    if not chunk:
        return

    # CPU: no lock needed
    embedding = model.encode([chunk])
    if embedding is None:
        return
    if hasattr(embedding, 'ndim') and embedding.ndim == 0:
        return
    if hasattr(embedding, '__len__') and len(embedding) == 0:
        log(f"WARNING: Model returned empty embedding for {file_path}. Skipping.")
        return

    # Critical section: lock only for mutations
    with index_lock:
        # Stat first to avoid orphaning old entry on stat failure
        try:
            mtime = os.path.getmtime(file_path)
            size = os.path.getsize(file_path)
        except Exception:
            log(f"WARNING: Cannot stat {file_path} after reading. Skipping.")
            return

        # Capture old entry info BEFORE removing (deferred removal)
        old_entry = meta.get(file_path)
        old_id = old_entry.get("id") if isinstance(old_entry, dict) else None
        old_store_entry = store.pop(old_id, None) if old_id is not None else None

        try:
            file_id = current_id
            current_id += 1
            index.add_with_ids(embedding, np.array([file_id], dtype=np.uint64))
            store[file_id] = {"path": file_path, "content": chunk}
            meta[file_path] = {
                "id": file_id,
                "mtime": mtime,
                "size": size,
                "last_indexed": time.time(),
            }
            # Old vector removal after new add succeeded
            if old_id is not None:
                try:
                    index.remove(old_id)
                except Exception:
                    pass
        except Exception:
            log(f"ERROR: Failed to add {file_path} to index. Rolling back.")
            store.pop(file_id, None)
            try:
                index.remove(file_id)
            except Exception:
                pass
            # Restore old entry to prevent data loss
            if old_id is not None:
                meta[file_path] = old_entry
                if old_store_entry is not None:
                    store[old_id] = old_store_entry
            else:
                meta.pop(file_path, None)
            raise


# ═══════════════════════════════════════════════════════════════
# Phase 3.5 — Stale File Detection
# ═══════════════════════════════════════════════════════════════


def find_stale_files(max_age_days: int = 7, max_files: int = 10) -> list[str]:
    """Pick up to max_files files not re-indexed recently.
    Uses random sampling to avoid sorting all tracked files.
    """
    cutoff = time.time() - (max_age_days * 86400)
    safe_max = max(0, int(max_files))

    with index_lock:
        candidates = [
            path
            for path, info in list(meta.items())
            if isinstance(info, dict) and info.get("last_indexed", 0) < cutoff
        ]

    if not candidates or safe_max == 0:
        return []
    if len(candidates) <= safe_max:
        return candidates
    return random.sample(candidates, safe_max)


# ═══════════════════════════════════════════════════════════════
# Phase 3.4 — Background Worker Loop
# ═══════════════════════════════════════════════════════════════


def background_worker() -> None:
    """Daemon thread: process index queue in small batches."""
    interval = max(BATCH_INTERVAL, 0.1)  # prevent busy-loop

    while not _stop_event.is_set():
        batch = dequeue_batch(BATCH_SIZE)

        # If queue is empty, check for stale files
        if not batch:
            stale = find_stale_files()
            if stale:
                with queue_lock:
                    for f in stale:
                        index_queue.append(("reindex", f))
                batch = dequeue_batch(BATCH_SIZE)

        if not batch:
            worker_state["status"] = "idle"
            time.sleep(interval)
            continue

        worker_state["status"] = "indexing"

        # Process each file — I/O and CPU outside lock
        for priority, file_path in batch:
            try:
                debug(f"Processing [{priority}] {file_path}")
                if priority == "remove":
                    handle_remove(file_path)
                else:
                    handle_index(file_path)
                worker_state["processed"] += 1
            except Exception as e:
                log(f"ERROR: Failed to process {file_path}: {e}")
                worker_state["errors"] += 1
                worker_state["last_error"] = str(e)

        # Persist after each batch
        debug(f"Batch complete. Persisting...")
        try:
            persist_all()
        except Exception as e:
            log(f"ERROR: Failed to persist index: {e}")
            worker_state["errors"] += 1
            worker_state["last_error"] = str(e)

        time.sleep(interval)


# ═══════════════════════════════════════════════════════════════
# Phase 4.3 — Idle Watchdog
# ═══════════════════════════════════════════════════════════════


def touch() -> None:
    """Reset the idle timer. Call on every tool/resource invocation."""
    global last_activity
    last_activity = time.time()


def idle_watchdog() -> None:
    """Daemon thread: exit process if idle too long."""
    while not _stop_event.is_set():
        time.sleep(CHECK_INTERVAL)
        if _stop_event.is_set():
            return
        if time.time() - last_activity > IDLE_TIMEOUT:
            try:
                persist_all()
            except Exception:
                log("WARNING: Failed to persist state during idle shutdown.")
            log(f"Idle for {IDLE_TIMEOUT // 60} minutes. "
                f"Shutting down to free resources. "
                f"Will restart automatically when needed.")
            os._exit(0)


# ═══════════════════════════════════════════════════════════════
# Phase 2.6 — FastMCP Registration
# ═══════════════════════════════════════════════════════════════

mcp = FastMCP("TurboCode MCP")


# ── Helpers ──


def _load_gitignore_specs(root: str) -> list[tuple[str, pathspec.PathSpec]]:
    """Load .gitignore files from root upward. Returns (prefix, spec) pairs."""
    specs: list[tuple[str, pathspec.PathSpec]] = []
    current = os.path.abspath(root)
    while True:
        gi = os.path.join(current, ".gitignore")
        if os.path.isfile(gi):
            try:
                with open(gi, "r") as f:
                    spec = pathspec.PathSpec.from_lines("gitignore", f)
                specs.append((current, spec))
            except Exception:
                pass
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return specs


def _is_gitignored(filepath: str, specs: list[tuple[str, pathspec.PathSpec]]) -> bool:
    """Check if a file path matches any loaded .gitignore spec."""
    for prefix, spec in specs:
        try:
            rel = os.path.relpath(filepath, prefix).replace(os.sep, "/")
            if spec.match_file(rel):
                return True
        except (ValueError, OSError):
            pass
    return False


# ── Tools ──


@mcp.tool
def index_directory(directory_path: str, respect_gitignore: bool = True) -> str:
    """Scan and queue files for background indexing."""
    touch()

    if not isinstance(directory_path, str) or not directory_path.strip():
        return "Error: Directory path cannot be empty."
    if not os.path.exists(directory_path):
        return f"Error: Directory '{directory_path}' not found."
    if not os.path.isdir(directory_path):
        return f"Error: '{directory_path}' is a file, not a directory."

    ensure_resources()

    new_files: list[str] = []
    changed_files: list[str] = []
    removed_files: list[str] = []

    SUPPORTED_EXT = (".py", ".rs", ".md", ".txt")

    # Snapshot meta once to avoid O(n) lock acquisitions during walk
    with index_lock:
        meta_snapshot = dict(meta)

    # Load .gitignore rules from directory upward
    gitignore_specs = _load_gitignore_specs(directory_path) if respect_gitignore else []

    # Walk filesystem
    try:
        for root, _, files in os.walk(directory_path, followlinks=False):
            for f in files:
                if not f.lower().endswith(SUPPORTED_EXT):
                    continue
                fp = os.path.normpath(os.path.join(root, f))
                if respect_gitignore and _is_gitignored(fp, gitignore_specs):
                    debug(f"Skipping gitignored: {fp}")
                    continue

                tracked_info = meta_snapshot.get(fp)
                tracked = tracked_info is not None
                tracked_mtime = (
                    tracked_info.get("mtime", 0)
                    if isinstance(tracked_info, dict)
                    else None
                )

                if not tracked:
                    new_files.append(fp)
                else:
                    try:
                        file_mtime = os.path.getmtime(fp)
                    except OSError:
                        continue
                    if tracked_mtime != file_mtime:
                        changed_files.append(fp)
    except PermissionError:
        return f"Error: Permission denied reading directory '{directory_path}'."
    except OSError as e:
        return f"Error: Cannot read directory '{directory_path}': {e}"

    # Detect removed files
    norm_dir = os.path.normpath(directory_path)
    separator = os.sep
    with index_lock:
        removed_files = [
            p for p in list(meta.keys())
            if os.path.normpath(p).startswith(norm_dir + separator) and not os.path.exists(p)
        ]

    # Enqueue
    for f in removed_files:
        enqueue("remove", f)
    for f in changed_files:
        enqueue("changed", f)
    for f in new_files:
        enqueue("new", f)

    # Build response
    parts = []
    if new_files:
        parts.append(f"{len(new_files)} new")
    if changed_files:
        parts.append(f"{len(changed_files)} changed")
    if removed_files:
        parts.append(f"{len(removed_files)} to remove")

    if not parts:
        tracked_here = sum(1 for p in meta if os.path.normpath(p).startswith(norm_dir + separator))
        return f"All {tracked_here} files up to date."

    total = len(new_files) + len(changed_files) + len(removed_files)
    return f"Queued {total} files ({', '.join(parts)}) for indexing."


@mcp.tool
def search_codebase(query: str, k: int = 3) -> str:
    """Search indexed code for semantically similar content."""
    touch()

    if not isinstance(query, str) or not query.strip():
        return "Error: Query cannot be empty."

    # Clamp k
    if not isinstance(k, int) or k < 1:
        k = 1
    elif k > 20:
        k = 20

    with index_lock:
        if not store:
            return "Index is empty. Use index_directory() to index a codebase first."

    ensure_resources()

    query_vec = model.encode([query])
    if query_vec is None or query_vec.size == 0:
        return f"Error: Failed to embed query '{query}'."

    with index_lock:
        scores, ids = index.search(query_vec, k=k)

    results: list[str] = []
    for score, doc_id in zip(scores[0], ids[0]):
        with index_lock:
            doc = store.get(int(doc_id))
        if doc and isinstance(doc, dict):
            file_path = doc.get("path", "unknown")
            content = doc.get("content", "")
            if not isinstance(content, str):
                content = str(content) if content is not None else ""
            display = content[:500]
            suffix = "..." if len(content) > 500 else ""
            results.append(
                f"**{file_path}** (score: {score:.4f})\n"
                f"```\n{display}{suffix}\n```"
            )

    if not results:
        remaining = queue_depth()
        hint = f"\n*Note: {remaining} files still queued.*" if remaining else ""
        return f"No results found for '{query}'.{hint}"

    return "\n\n---\n\n".join(results)


@mcp.tool
def get_index_stats() -> str:
    """Return index statistics. Never loads model/index."""
    touch()

    try:
        tvim_size = os.path.getsize(INDEX_PATH) if os.path.exists(INDEX_PATH) else 0
    except Exception:
        tvim_size = 0

    with index_lock:
        vcount = len(store)
        fcount = len(meta)

    qdepth = queue_depth()
    dirs = set(os.path.dirname(p) for p in meta)

    return (
        f"**Index Stats**\n"
        f"- Vectors: {vcount}\n"
        f"- Files tracked: {fcount}\n"
        f"- Directories: {len(dirs)}\n"
        f"- Disk: {tvim_size / 1024:.1f} KB\n"
        f"- Worker: {worker_state['status']} "
        f"({qdepth} queued, {worker_state['processed']} processed, "
        f"{worker_state['errors']} errors)\n"
        f"- Model loaded: {model is not None}"
    )


# ── Resources ──


@mcp.resource("turbocode://status")
def index_status() -> str:
    """Current indexer status. Lightweight — no model/index load."""
    touch()

    qdepth = queue_depth()
    tracked = len(meta)

    if model is None and index is None:
        return f"Ready. {tracked} files tracked. (Model loaded on demand)"
    elif qdepth > 0:
        return f"Indexing... {qdepth} queued, {worker_state['processed']} processed."
    else:
        return f"Idle. {worker_state['processed']} files indexed."


@mcp.resource("turbocode://stats")
def index_stats() -> str:
    """Detailed index statistics as JSON. Lightweight — no model/index load."""
    touch()

    try:
        tvim_size = os.path.getsize(INDEX_PATH) if os.path.exists(INDEX_PATH) else 0
    except Exception:
        tvim_size = 0

    with index_lock:
        vcount = len(store)
        fcount = len(meta)

    dirs = list(set(os.path.dirname(p) for p in meta))
    qdepth = queue_depth()

    stats = {
        "vectors": vcount,
        "files_tracked": fcount,
        "directories": dirs,
        "disk_size_kb": round(tvim_size / 1024, 1),
        "queue_depth": qdepth,
        "state": worker_state["status"],
        "processed": worker_state["processed"],
        "errors": worker_state["errors"],
        "last_error": worker_state["last_error"],
        "model_loaded": model is not None,
        "model": MODEL_NAME,
    }
    return json.dumps(stats, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# Startup
# ═══════════════════════════════════════════════════════════════


def main() -> None:
    global meta, store, current_id, DEBUG_MODE

    # Parse CLI flags
    global MODEL_NAME
    if "--debug" in sys.argv:
        DEBUG_MODE = True
    for arg in sys.argv:
        if arg.startswith("--model="):
            MODEL_NAME = arg.split("=", 1)[1]

    # Run startup validations
    validate_environment()

    try:
        os.makedirs(TURBOCODE_DIR, exist_ok=True)
    except Exception as e:
        log(f"WARNING: Cannot create {TURBOCODE_DIR}: {e}")

    # Clean up stale .tmp files from previous crashes
    for stale_tmp in [INDEX_PATH + ".tmp", META_PATH + ".tmp", STORE_PATH + ".tmp"]:
        try:
            if os.path.exists(stale_tmp):
                os.remove(stale_tmp)
                debug(f"Cleaned up stale temp file: {stale_tmp}")
        except Exception:
            pass

    # Load and verify consistency of persisted data
    try:
        load_and_verify()
    except Exception as e:
        log(f"WARNING: Failed to load persisted state ({e}). Starting fresh.")
        meta.clear()
        store.clear()
        current_id = 1

    # Start background threads
    try:
        threading.Thread(target=background_worker, daemon=True).start()
    except Exception:
        log("WARNING: Failed to start background worker. Indexing disabled.")

    try:
        threading.Thread(target=idle_watchdog, daemon=True).start()
    except Exception:
        log("WARNING: Failed to start idle watchdog.")

    debug(f"TURBOCODE_DIR={TURBOCODE_DIR}")
    debug(f"INDEX_PATH={INDEX_PATH}")
    debug(f"meta count={len(meta)}, store count={len(store)}")

    log(f"Ready. {len(meta)} files tracked. "
        f"Model/index loaded on demand. "
        f"Idle timeout: {IDLE_TIMEOUT // 60}m.")

    # Handle graceful shutdown signals
    def handle_signal(signum, frame):
        log(f"Received signal {signum}. Persisting and shutting down...")
        if model is not None and isinstance(model, _ModelClient):
            try:
                model.stop()
            except Exception:
                pass
        if index_lock.acquire(timeout=5):
            try:
                _persist_locked()
            except Exception:
                pass
            finally:
                index_lock.release()
        else:
            log("WARNING: Index lock held by worker for >5s. Shutting down without persist.")
        os._exit(0)

    sig_module.signal(sig_module.SIGINT, handle_signal)
    sig_module.signal(sig_module.SIGTERM, handle_signal)

    mcp.run()


if __name__ == "__main__":
    main()
