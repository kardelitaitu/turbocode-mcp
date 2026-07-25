# Technical Reference

> Implementation details, APIs, and edge cases for TurboCode MCP.
> This document is intended for developers contributing to or extending the server.

---

## Dependencies

```
fastmcp>=0.2.0              # MCP protocol layer (Python)
turbovec>=0.8.0             # Vector index with 4-bit quantization
sentence-transformers>=3.0.0  # Local embedding model
numpy>=1.24.0               # Array operations
```

---

## Logging Convention

**MCP communicates over stdout using JSON-RPC.** Any stray `print()` on stdout corrupts the protocol. All logging must go to stderr.

```python
import sys

def log(msg: str):
    """Log a message to stderr (stdout is reserved for MCP protocol)."""
    print(f"[TurboCode MCP] {msg}", file=sys.stderr, flush=True)
```

Use `log()` everywhere instead of `print()` for status messages, warnings, and errors.

---

## Turbovec Persistence API

### The Critical Finding

`IdMapIndex.load()` is a **classmethod**. The instance method signature exists but produces a broken index.

```python
import numpy as np
from turbovec import IdMapIndex

# CREATE
index = IdMapIndex(dim=384, bit_width=4)
vectors = np.random.rand(5, 384).astype(np.float32)
ids = np.array([10, 20, 30, 40, 50], dtype=np.uint64)
index.add_with_ids(vectors, ids)

# SAVE (instance method)
index.write("index.tvim")

# LOAD (✅ CLASSMETHOD)
index = IdMapIndex.load("index.tvim")

# LOAD (❌ WRONG — broken index)
index = IdMapIndex(dim=384, bit_width=4)
index.load("index.tvim")   # BUG: search empty, contains False

# SEARCH
scores, ids = index.search(queries, k=3)

# CONTAINS / REMOVE
index.contains(id)     # Returns bool
index.remove(id)       # True if removed, False if not found
```

### Storage Efficiency

| Vectors | File Size | Notes |
|---|---|---|
| 5 | ~4 KB | Minimum overhead |
| 1,000 | ~200 KB | Typical small project |
| 10,000 | ~2 MB | Medium project |
| 100,000 | ~20 MB | Large monorepo |

### API Reference

| Method | Signature | Description |
|---|---|---|
| `add_with_ids` | `(vectors, ids: NDArray[uint64])` | Add vectors with external IDs. Raises `ValueError` on duplicate. |
| `search` | `(queries, k, *, allowlist=None)` → `(scores, ids)` | Top-K nearest neighbors |
| `contains` | `(id: uint64)` → `bool` | Check if ID exists in index |
| `remove` | `(id: uint64)` → `bool` | Remove a vector by external ID |
| `write` | `(path: str)` | Serialize to `.tvim` file |
| `load` | `(path: str)` → `IdMapIndex` | **Classmethod.** Deserialize `.tvim` |
| `prepare` | `()` | Internal optimization hint |

---

## Lazy Loading System

```python
model: SentenceTransformer | None = None
index: IdMapIndex | None = None

def ensure_model():
    global model
    if model is not None:
        return
    model = SentenceTransformer('all-MiniLM-L6-v2')

def ensure_index():
    global index
    if index is not None:
        return
    if os.path.exists(INDEX_PATH):
        index = IdMapIndex.load(INDEX_PATH)
    else:
        index = IdMapIndex(dim=384, bit_width=4)

def ensure_resources():
    ensure_model()
    ensure_index()
```

### Load Triggers

| Operation | Loads Model | Loads Index | Latency |
|---|---|---|---|
| `turbocode://status` | No | No | < 1ms |
| `turbocode://stats` | No | No | < 1ms |
| `get_index_stats()` | No | No | < 1ms |
| `index_directory()` | Yes (first call) | Yes (first call) | +~5s first time |
| `search_codebase()` | Yes (first call) | Yes (first call) | +~5s first time |

---

## Threading & Locking

### Two-Lock Strategy

```python
import threading
from collections import deque

# ── Independent locks (never nested) ──
queue_lock = threading.Lock()   # Protects only index_queue
index_lock = threading.Lock()   # Protects index, store, meta

# ── Protected globals ──
index_queue: deque = deque()
index: IdMapIndex | None = None
store: dict = {}
meta: dict = {}
```

**Golden rule:** Never acquire both locks at once. If you hold `index_lock`, don't touch the queue. If you hold `queue_lock`, don't touch index/meta/store.

### Lock Hold Times

| Operation | Lock | Hold Time | Inside Lock |
|---|---|---|---|
| `dict[key] = val` | `index_lock` | ~1 µs | ✅ Brief |
| `dict.pop(key)` | `index_lock` | ~1 µs | ✅ Brief |
| `index.add_with_ids()` | `index_lock` | ~10 µs | ✅ Brief |
| `index.search()` | `index_lock` | ~10 µs | ✅ Brief |
| `index.write()` | `index_lock` | ~5-50 ms | Acceptable (persist) |
| `index_queue.append()` | `queue_lock` | ~1 µs | ✅ Brief |
| `index_queue.popleft()` | `queue_lock` | ~1 µs | ✅ Brief |

**What is NOT inside a lock:** File reads (`open().read()`), embedding computation (`model.encode()`), filesystem walking (`os.walk()`).

---

## Background Worker

```python
BATCH_SIZE = 5
BATCH_INTERVAL = 1.0  # seconds

def background_worker():
    """Process index queue in small batches. No lock held during I/O."""
    while True:
        # 1. Dequeue batch (queue_lock only)
        batch = dequeue_batch(BATCH_SIZE)
        
        # 2. If queue was empty, check stale files
        if not batch:
            stale = find_stale_files()
            if stale:
                with queue_lock:
                    for f in stale:
                        index_queue.append(("reindex", f))
                batch = dequeue_batch(BATCH_SIZE)
        
        if not batch:
            time.sleep(BATCH_INTERVAL)
            continue
        
        # 3. Process each file — I/O and CPU outside lock
        for priority, file_path in batch:
            try:
                if priority == "remove":
                    handle_remove(file_path)
                else:
                    handle_index(file_path)
            except Exception as e:
                log(f"Failed to index {file_path}: {e}")
        
        # 4. Persist atomically
        persist_all()
        
        time.sleep(BATCH_INTERVAL)

def dequeue_batch(batch_size: int = 5):
    """Thread-safe priority dequeue. queue_lock only."""
    with queue_lock:
        if not index_queue:
            return []
        
        # Sort by priority
        priority_order = {"remove": 0, "new": 1, "changed": 2, "reindex": 3}
        index_queue.sort(key=lambda x: priority_order.get(x[0], 99))
        
        batch = []
        for _ in range(min(batch_size, len(index_queue))):
            batch.append(index_queue.popleft())
    return batch

def queue_depth() -> int:
    with queue_lock:
        return len(index_queue)

def enqueue(priority: str, file_path: str):
    with queue_lock:
        index_queue.append((priority, file_path))
```

---

## File Indexing (Lock-Friendly)

```python
def handle_remove(file_path: str):
    """Remove a deleted file. Lock only for mutations."""
    # Quick check outside lock (race-safe: worst case we do nothing)
    if file_path not in meta:
        return
    
    with index_lock:
        if file_path not in meta:  # Double-check inside lock
            return
        file_id = meta[file_path]["id"]
        try:
            index.remove(file_id)
        except Exception:
            pass
        store.pop(file_id, None)
        del meta[file_path]


def handle_index(file_path: str):
    """Index a file. I/O and embedding outside lock."""
    global current_id
    
    # ── I/O: no lock needed ──
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception:
        return  # Skip unreadable files silently
    
    chunk = content[:2000].strip()
    if not chunk:
        return
    
    # ── CPU: no lock needed ──
    embedding = model.encode([chunk])
    
    # ── Brief critical section ──
    with index_lock:
        # Remove old entry if re-indexing
        if file_path in meta:
            old_id = meta[file_path]["id"]
            try:
                index.remove(old_id)
            except Exception:
                pass
            store.pop(old_id, None)
        
        file_id = current_id
        current_id += 1
        index.add_with_ids(embedding, np.array([file_id], dtype=np.uint64))
        store[file_id] = {"path": file_path, "content": chunk}
        meta[file_path] = {
            "id": file_id,
            "mtime": os.path.getmtime(file_path),
            "size": os.path.getsize(file_path),
            "last_indexed": time.time(),
        }
```

---

## Stale File Detection (Efficient)

```python
import random

def find_stale_files(max_age_days=7, max_files=10):
    """Pick up to max_files files not re-indexed recently.
    
    Uses random sampling instead of sorting all tracked files
    to avoid O(n log n) on every idle check.
    """
    cutoff = time.time() - (max_age_days * 86400)
    
    with index_lock:
        candidates = [
            path for path, info in meta.items()
            if info.get("last_indexed", 0) < cutoff
        ]
    
    if not candidates:
        return []
    
    if len(candidates) <= max_files:
        return candidates
    
    return random.sample(candidates, max_files)
```

**Why random sampling?** The stale check runs every time the queue goes idle. Sorting 10,000 files by `last_indexed` is wasteful. Random sampling is O(n) in the filter step and O(1) for the sample, and "any stale file" is good enough — precision doesn't matter for background maintenance.

---

## Idle Shutdown

```python
IDLE_TIMEOUT = 30 * 60       # 30 minutes
CHECK_INTERVAL = 60          # Check every 60 seconds
last_activity: float = 0.0

def touch():
    """Reset the idle timer. Call on every tool/resource invocation."""
    global last_activity
    last_activity = time.time()

def idle_watchdog():
    """Daemon thread: exit process if idle too long."""
    while True:
        time.sleep(CHECK_INTERVAL)
        if time.time() - last_activity > IDLE_TIMEOUT:
            persist_all()
            log(f"Idle for {IDLE_TIMEOUT // 60} minutes. "
                "Shutting down to free resources. "
                "Will restart automatically when needed.")
            os._exit(0)
```

**Why `os._exit(0)`?** `sys.exit()` raises `SystemExit` and runs cleanup handlers, which can hang on daemon threads. `os._exit(0)` terminates immediately.

**Why 30 minutes?** Too short (5–15 min) causes spurious restarts during conversation pauses. Too long (60+ min) wastes RAM. 30 minutes accommodates short breaks.

---

## Atomic Persistence

```python
import os, json, tempfile

def atomic_write(path: str, data: str):
    """Write a string to a file atomically using temp + replace."""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())  # Force data to disk
    os.replace(tmp_path, path)  # Atomic on both POSIX and Windows

def persist_all():
    """Save index, meta, and store to disk atomically."""
    os.makedirs(TURBOCODE_DIR, exist_ok=True)
    
    with index_lock:
        # Write index to tmp then rename
        index.write(INDEX_PATH + ".tmp")
        os.replace(INDEX_PATH + ".tmp", INDEX_PATH)
        
        # Write JSON files atomically
        atomic_write(META_PATH, json.dumps(meta, indent=2, default=str))
        
        # Convert integer keys to strings for JSON compatibility
        store_serializable = {str(k): v for k, v in store.items()}
        atomic_write(STORE_PATH, json.dumps(store_serializable, indent=2, default=str))
```

### Why Atomic Writes?

A crash during `index.write()` or `json.dump()` leaves a half-written file. On next startup:

- **Without atomic:** corrupt `.tvim` → `IdMapIndex.load()` fails → index lost
- **With atomic:** either the full file exists, or the previous version is intact

---

## Cold-Start Consistency Check

```python
def load_and_verify():
    """Load meta/store from disk and check for crash-induced inconsistencies."""
    global meta, store, current_id
    
    # Load meta
    if os.path.exists(META_PATH):
        with open(META_PATH) as f:
            meta = json.load(f)
    
    # Load store
    if os.path.exists(STORE_PATH):
        with open(STORE_PATH) as f:
            store = {int(k): v for k, v in json.load(f).items()}
    
    # Verify consistency
    meta_count = len(meta)
    store_count = len(store)
    
    if meta_count == 0 and store_count == 0:
        current_id = 1
        return  # Clean state
    
    if meta_count != store_count:
        log(f"Inconsistency detected: meta={meta_count} files vs "
            f"store={store_count} vectors. Rebuilding meta from store.")
        
        # Rebuild meta from store (store is the source of truth)
        new_meta = {}
        for id_val, doc in store.items():
            new_meta[doc["path"]] = {
                "id": id_val,
                "mtime": doc.get("mtime", 0),
                "size": doc.get("size", 0),
                "last_indexed": doc.get("last_indexed", 0),
            }
        meta.clear()
        meta.update(new_meta)
        # Persist the repaired meta
        atomic_write(META_PATH, json.dumps(meta, indent=2))
    
    current_id = max(store.keys(), default=0) + 1
```

### Recovery Priority

1. If `meta` and `store` disagree → rebuild `meta` from `store` (store has the path for every vector)
2. If `.tvim` is corrupt → delete and rebuild from `store` (requires re-embedding on first search)
3. If all three disagree completely → start fresh, log warning

---

## Tools Implementation

```python
@mcp.tool
def index_directory(directory_path: str) -> str:
    """Scan and queue a directory for background indexing."""
    touch()
    
    if not os.path.exists(directory_path):
        return f"Error: Directory '{directory_path}' not found."
    
    ensure_resources()
    
    # Scan filesystem
    new_files, changed_files, removed_files = [], [], []
    current_files = set()
    
    for root, _, files in os.walk(directory_path):
        for f in files:
            if not f.endswith(('.py', '.rs', '.md', '.txt')):
                continue
            fp = os.path.join(root, f)
            current_files.add(fp)
            
            with index_lock:
                exists = fp in meta
                if not exists:
                    new_files.append(fp)
                else:
                    file_mtime = os.path.getmtime(fp)
                    if meta[fp]["mtime"] != file_mtime:
                        changed_files.append(fp)
    
    with index_lock:
        for tracked_path in list(meta.keys()):
            if tracked_path.startswith(directory_path) and not os.path.exists(tracked_path):
                removed_files.append(tracked_path)
    
    # Enqueue with queue_lock only
    for f in removed_files: enqueue("remove", f)
    for f in changed_files: enqueue("changed", f)
    for f in new_files:     enqueue("new", f)
    
    # Build response
    parts = []
    if new_files:     parts.append(f"{len(new_files)} new")
    if changed_files: parts.append(f"{len(changed_files)} changed")
    if removed_files: parts.append(f"{len(removed_files)} to remove")
    
    if not parts:
        tracked_here = sum(1 for p in meta if p.startswith(directory_path))
        return f"✓ All {tracked_here} files up to date."
    
    total = len(new_files) + len(changed_files) + len(removed_files)
    return f"⏳ Queued {total} files ({', '.join(parts)}) for indexing."


@mcp.tool
def search_codebase(query: str, k: int = 3) -> str:
    """Search indexed code for semantically similar content."""
    touch()
    
    k = max(1, min(k, 20))
    
    with index_lock:
        if not store:
            return "Index is empty. Use index_directory() first."
    
    ensure_resources()
    
    query_vec = model.encode([query])
    
    with index_lock:
        scores, ids = index.search(query_vec, k=k)
    
    results = []
    for score, doc_id in zip(scores[0], ids[0]):
        with index_lock:
            doc = store.get(int(doc_id))
        if doc:
            results.append(
                f"**{doc['path']}** (score: {score:.4f})\n"
                f"```\n{doc['content'][:500]}...\n```"
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
    
    tvim_size = os.path.getsize(INDEX_PATH) if os.path.exists(INDEX_PATH) else 0
    
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
```

---

## Resources

Resources never trigger model or index loading.

```python
@mcp.resource("turbocode://status")
def index_status() -> str:
    """Current indexer status. Lightweight — no model/index load."""
    touch()
    
    qdepth = queue_depth()
    tracked = len(meta)
    
    if model is None and index is None:
        return f"✅ Ready. {tracked} files tracked. (Model loaded on demand)"
    elif qdepth > 0:
        return f"⏳ Indexing... {qdepth} queued."
    else:
        with index_lock:
            return f"✅ Idle. {worker_state['processed']} files indexed."


@mcp.resource("turbocode://stats")
def index_stats() -> str:
    """Detailed index statistics. Lightweight — no model/index load."""
    touch()
    
    tvim_size = os.path.getsize(INDEX_PATH) if os.path.exists(INDEX_PATH) else 0
    
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
        "model": "all-MiniLM-L6-v2",
    }
    return json.dumps(stats, indent=2)
```

---

## Startup Sequence

```python
def main():
    global meta, store, current_id
    
    os.makedirs(TURBOCODE_DIR, exist_ok=True)
    
    # Load and verify consistency of persisted data
    load_and_verify()
    
    # Start background threads
    threading.Thread(target=background_worker, daemon=True).start()
    threading.Thread(target=idle_watchdog, daemon=True).start()
    
    log(f"Ready. {len(meta)} files tracked. "
        f"Model/index loaded on demand. "
        f"Idle timeout: {IDLE_TIMEOUT // 60}m.")
    
    mcp.run()


if __name__ == "__main__":
    main()
```

---

## Error Handling Matrix

| Scenario | Detection | Response |
|---|---|---|
| `.tvim` corrupt | `IdMapIndex.load()` raises | Log warning, delete corrupt file, create empty index |
| Store/meta mismatch on boot | `len(store) != len(meta)` | Rebuild meta from store (store has path for every vector) |
| File unreadable during index | `open()` raises `IOError` | Log error, skip file, continue batch |
| Search on empty index | `len(store) == 0` | Return: "Index is empty. Use index_directory() first." |
| Duplicate ID in `add_with_ids` | turbovec raises `ValueError` | Remove old ID first (tracked in meta) |
| Queue item processed twice | Protected by `dequeue_batch()` (removes from deque) | Impossible by design |
| Background worker exception | try/except in `background_worker()` | Log error, increment error counter, continue next file |
| Idle shutdown during indexing | Timer reads `time.time()` | `persist_all()` saves whatever has been processed |
| MCP client disconnects | stdio EOF | Main thread exits, daemon threads die |
| Enqueue while worker dequeueing | `queue_lock` | Protected — two independent lock acquisitions |
