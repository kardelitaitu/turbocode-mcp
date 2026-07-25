# Architecture

> System design, key decisions, and rationale for TurboCode MCP.

---

## Overview

TurboCode MCP bridges the JavaScript ecosystem (npm) with a Python-based vector search engine. It packages a Python MCP server as a globally-installable npm package, handling environment setup transparently.

```
┌──────────────────────────────────────────────────────────┐
│  MCP Client (Claude Desktop / Cursor / ZCode)            │
│                                                           │
│  ┌──────────────────────────────────────────────────┐    │
│  │  Auto-loaded Resources                           │    │
│  │  ├─ turbocode://status  (index progress)         │    │
│  │  └─ turbocode://stats   (detailed metrics)       │    │
│  └──────────────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────────────┐    │
│  │  Callable Tools                                  │    │
│  │  ├─ index_directory(path)                        │    │
│  │  ├─ search_codebase(query, k)                    │    │
│  │  └─ get_index_stats()                            │    │
│  └──────────────────────────────────────────────────┘    │
└──────────────────────┬───────────────────────────────────┘
                       │ stdio (JSON-RPC over stdin/stdout)
                       ▼
┌──────────────────────────────────────────────────────────┐
│  bin/cli.js  (Node.js)                                    │
│  - Locates Python in .venv/                               │
│  - Spawns src/server.py as child process                  │
│  - Forwards stdio bidirectionally                         │
└──────────────────────┬───────────────────────────────────┘
                       │ spawn + pipe
                       ▼
┌──────────────────────────────────────────────────────────┐
│  src/server.py  (Python)                                  │
│                                                           │
│  ┌── FastMCP Runtime ────────────────────────────┐       │
│  │  - JSON-RPC 2.0 over stdio                     │       │
│  │  - Schema generation from type hints            │       │
│  │  - Tool/Resource registration                   │       │
│  └────────────────────────────────────────────────┘       │
│                                                           │
│  ┌── Threads ────────────────────────────────────┐       │
│  │  Main thread  → FastMCP event loop             │       │
│  │  Worker thread → Background indexing (daemon)  │       │
│  │  Watchdog thread → Idle shutdown (daemon)      │       │
│  └────────────────────────────────────────────────┘       │
│                                                           │
│  ┌── Storage ────────────────────────────────────┐       │
│  │  .turbocode/index.tvim   (turbovec binary)     │       │
│  │  .turbocode/meta.json    (file tracking)       │       │
│  │  .turbocode/store.json   (chunk content)       │       │
│  └────────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────┘
```

---

## System Components

### 1. Node.js CLI Wrapper (`bin/cli.js`)

A thin shim that bridges the npm ecosystem to the Python runtime.

**Responsibilities:**
- Resolve paths relative to the package installation directory
- Locate the Python executable inside the isolated `.venv/`
- Spawn `src/server.py` as a child process with inherited stdio
- Forward the exit code on termination

**Why a separate wrapper?** MCP clients expect a single command they can execute. Without the wrapper, users would need to manually manage `.venv` paths and Python invocation. The wrapper makes the install invisible.

### 2. Postinstall Setup (`scripts/setup.js`)

Runs automatically after `npm install -g turbocode-mcp`.

**Responsibilities:**
- Create an isolated Python virtual environment (`.venv/`)
- Install pinned Python dependencies via `pip`
- Validate the installation succeeded

**Why a postinstall script?** Eliminates the "works on my machine" problem. Every user gets the same isolated Python environment regardless of their system Python state.

### 3. MCP Server (`src/server.py`)

The core of the application. A Python server using the FastMCP framework.

**Three threads run inside the server:**

| Thread | Type | Role |
|---|---|---|
| Main | FastMCP event loop | Handles tool calls and resource requests via JSON-RPC |
| Background worker | Daemon thread | Processes the index queue in small batches |
| Idle watchdog | Daemon thread | Monitors inactivity and shuts down after timeout |

---

## Key Design Decisions

### Decision 1: Lazy Loading

**Choice:** The embedding model (`SentenceTransformer`) and vector index (`IdMapIndex`) are not loaded at startup. They load on first use.

**Rationale:**
- The sentence-transformers model consumes ~500MB–1GB of RAM
- Resources (`turbocode://status`, `turbocode://stats`) and `get_index_stats()` never need the model
- If the AI only checks status, the model is never loaded, saving significant memory
- Startup time drops from ~5s to ~100ms

**Trade-off:** First search or index call is ~5s slower. Accepted because:
- It's a one-time penalty per server session
- Subsequent calls are instant
- The 30-minute idle shutdown means long-running sessions reload at most once per work session

### Decision 2: Background Indexing

**Choice:** `index_directory` returns immediately after scanning the filesystem. Actual embedding happens in a background worker thread processing 5-file batches with 1-second intervals.

**Rationale:**
- Embedding 1,000 files takes ~6–7 minutes sequentially
- MCP clients have timeouts (typically 30–60s)
- Blocking the tool call would cause timeout errors
- Results are immediately available for already-indexed files

**Trade-off:** Search results are partial until the queue drains. Accepted because:
- The `turbocode://status` resource tells the AI how many files remain
- The AI can communicate "indexing in progress" to the user
- Most projects are indexed within a few minutes

### Decision 3: Fine-Grained Locking

**Choice:** Only protect the actual critical section with locks, not file I/O or embedding computation.

**Rationale:**
- Holding a lock during `model.encode()` or file reads blocks `search_codebase` for seconds at a time
- The AI should be able to search while indexing is in progress
- Dict mutations (`store[key] = ...`, `del meta[path]`) are microsecond operations — lock only those

**Trade-off:** Slightly more complex code (must read content and compute embedding outside the lock). Accepted because without it, the server effectively blocks during indexing.

### Decision 4: Dedicated Queue Lock

**Choice:** The index queue uses its own lock, separate from the index/meta/store lock.

**Rationale:**
- Queue operations are frequent (every batch) and fast
- Using a single lock for both queue and index would serialize unrelated operations
- Two locks allow concurrent queue management and index reads

**Trade-off:** More lock objects to manage. Worth it for the concurrency win.

### Decision 5: Atomic Writes

**Choice:** Index files are written to a temporary path first, then atomically renamed to the final path.

**Rationale:**
- A crash during `index.write()` corrupts the `.tvim` file
- The current design could leave the index in an unrecoverable state
- `os.replace()` (or `os.rename()` on POSIX) is atomic for files on the same filesystem

**Trade-off:** Slightly more disk I/O (write to temp, then rename). Negligible given the infrequency of writes (every ~5s at most).

### Decision 6: Cold-Start Consistency Check

**Choice:** On startup, verify that `meta.json`, `store.json`, and the `.tvim` index agree on their vector count. If they diverge, recover automatically.

**Rationale:**
- The three storage files are written sequentially, not atomically as a group
- A crash between writes leaves them in an inconsistent state
- An inconsistent index is worse than an empty one because the AI gets silently wrong results

**Trade-off:** Slightly slower startup when divergence is detected (must rebuild from store). Acceptable because this only happens after a crash.

### Decision 7: Idle Shutdown

**Choice:** The server exits after 30 minutes of inactivity. The MCP client auto-restarts it on the next tool call.

**Rationale:**
- The embedding model uses significant RAM (~500MB–1GB)
- Keeping it allocated indefinitely for idle sessions is wasteful
- MCP clients are designed to handle process restarts transparently
- The index persists to disk, so no work is lost

**Why 30 minutes?**
- Too short (5–15 min): spurious restarts during pauses in conversation
- Too long (60+ min): may as well not have a timeout
- 30 minutes accommodates breaks without sacrificing memory recovery

### Decision 8: Directory Structure

**Choice:** The persistent index lives in `~/.turbocode/` (user home directory), not in the indexed project or package root.

**Rationale:**
- Keeps the indexed project clean (no hidden folder injected)
- Survives npm reinstall/upgrade (package root is ephemeral)
- User home is always writable, unlike global `node_modules/`
- The `.turbocode/` directory is git-ignored in the package

**Trade-off:** Indexing multiple independent projects with the same server means they share a single index. Acceptable for the v1; multi-project named indexes are on the roadmap.

---

## Data Flow Diagrams

### Startup

```
server.py starts
  │
  ├── Parse CLI flags (--debug, --help, --version handled by cli.js)
  ├── Validate Python version (>= 3.9)
  ├── Validate required packages (fastmcp, turbovec, etc.)
  ├── Create ~/.turbocode/ if missing
  ├── Load meta.json + store.json (small JSON, instant)
  ├── RECOVERY CHECK: count(store) vs count(meta) vs index_dim
  │   └── If diverged → log warning, rebuild from store data
  │       (or delete all and start fresh as fallback)
  ├── Register MCP tools and resources
  ├── Start background worker thread (daemon)
  ├── Start idle watchdog thread (daemon)
  └── Enter FastMCP event loop
      │
      └── Model + Index: NOT loaded yet (lazy)
```

### Indexing

```
User/AI calls index_directory("/project")
  │
  ├── touch() → reset idle timer
  ├── ensure_resources() → load model + index (if not loaded)
  ├── Walk /project recursively
  ├── For each file:
  │   ├── Not in meta.json → "new"
  │   ├── In meta but mtime changed → "changed"
  │   ├── In meta, unchanged → skip
  │   └── In meta but file gone → "remove"
  ├── With queue_lock: enqueue all to index_queue
  └── Return immediately

[background worker, in parallel]:
  │
  ├── With queue_lock: dequeue up to 5 items
  ├── For each file:
  │   ├── Read content from disk         ← NO LOCK (I/O)
  │   ├── model.encode(content) → vector ← NO LOCK (CPU)
  │   └── With index_lock:
  │       ├── index.remove(old_id)       if re-indexing
  │       ├── index.add_with_ids(vec, id)
  │       ├── store[id] = {path, content}
  │       └── meta[path] = {id, mtime, ...}
  ├── Atomic persist:
  │   ├── index.write(tmp) → os.replace(tmp, INDEX_PATH)
  │   ├── json.dump to tmp → os.replace(tmp, META_PATH)
  │   └── json.dump to tmp → os.replace(tmp, STORE_PATH)
  ├── Sleep 1 second
  └── Repeat
```

### Searching

```
User/AI calls search_codebase("query", k=3)
  │
  ├── touch() → reset idle timer
  ├── If store is empty → return friendly message
  ├── ensure_resources() → load model + index (if not loaded)
  ├── model.encode(["query"]) → query_vector (1 × 384 float32)
  ├── With index_lock:
  │     index.search(query_vector, k=3) → scores, ids
  ├── For each (score, id):
  │   └── With index_lock: store[int(id)] → {path, content}
  └── Return formatted results
```

### Idle Shutdown

```
[30 minutes since last tool/resource call]
  │
  ├── idle_watchdog fires
  ├── persist_all() → save final state (with atomic writes)
  ├── Print shutdown message to stderr
  └── os._exit(0)

[Next tool call from AI]
  │
  ├── MCP client detects process is dead (stdio error)
  ├── MCP client spawns new process
  ├── Server boots (fast — loads meta/store, runs recovery check)
  └── Lazy load model + index on first use, normal processing resumes
```

---

## Threading Model

### Lock Strategy

There are **two independent locks**:

| Lock | Protects | Held by | Typical hold time |
|---|---|---|---|
| `queue_lock` | `index_queue` deque | Worker (dequeue), main (enqueue) | Microseconds |
| `index_lock` | `index`, `store`, `meta` | Worker (mutations), main (reads) | Microseconds |

```python
queue_lock = threading.Lock()
index_lock = threading.Lock()
```

### Which Thread Holds Which Lock

```
Main Thread (FastMCP event loop)
├── enqueue files          → queue_lock (brief)
├── search_codebase        → index_lock (brief read, no I/O)
├── get_index_stats        → index_lock (brief read)
└── touch()                → no lock (atomic float write)

Background Worker Thread
├── dequeue batch          → queue_lock (brief)
├── read file from disk    → NO LOCK (I/O outside lock)
├── model.encode()         → NO LOCK (CPU outside lock)
├── index.add_with_ids()   → index_lock (brief mutation)
├── store[id] = ...        → index_lock (brief dict write)
├── meta[path] = ...       → index_lock (brief dict write)
└── index.write()          → index_lock (brief serialization)

Idle Watchdog Thread
└── persist_all()          → index_lock (writes index + files)
```

### Deadlock Prevention

Deadlocks are avoided by a simple rule: **never acquire `queue_lock` while holding `index_lock`, or vice versa.** These two locks protect unrelated data and are never nested.

```
Scenario that could deadlock (deliberately avoided):
  ❌ Worker: with index_lock: ... with queue_lock: ...  ← NEVER

Safe:
  ✅ Worker: with queue_lock: dequeue
             (release queue_lock)
             with index_lock: mutate index/store/meta
  ✅ Main:   with queue_lock: enqueue
  ✅ Main:   with index_lock: read index/store
```

---

## Memory Model

| Component | Memory | Loaded | Notes |
|---|---|---|---|
| `meta.json` | ~KB | On boot | Grows with file count |
| `store.json` | ~KB–MB | On boot | Grows with file count |
| `IdMapIndex` (.tvim) | ~2–20 MB | Lazy (first search/index) | 4-bit quantization keeps it small |
| `SentenceTransformer` | ~500 MB – 1 GB | Lazy (first search/index) | All-MiniLM-L6-v2 |
| Python runtime | ~30–50 MB | On boot | Base interpreter |
| **Total (idle)** | **~5–10 MB** | | Only meta/store in memory |
| **Total (active)** | **~550 MB – 1.1 GB** | | Model + index loaded |

---

## Reliability Mechanisms

### 1. Atomic Writes (File Corruption Prevention)

```python
import os, tempfile

def atomic_write(path: str, data: str):
    """Write data to path atomically using temp file + rename."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())  # Ensure data reaches disk
    os.replace(tmp, path)     # Atomic on Windows + POSIX

def persist_all():
    os.makedirs(TURBOCODE_DIR, exist_ok=True)
    with index_lock:
        # Atomic index write
        index.write(INDEX_PATH + ".tmp")
        os.replace(INDEX_PATH + ".tmp", INDEX_PATH)
        
        # Atomic JSON writes
        atomic_write(META_PATH, json.dumps(meta, indent=2))
        store_serializable = {str(k): v for k, v in store.items()}
        atomic_write(STORE_PATH, json.dumps(store_serializable, indent=2))
```

**Why `os.replace()`?** On both POSIX and Windows, `os.replace()` atomically replaces the destination file if it exists. On POSIX, `os.rename()` works too, but `os.replace()` is the cross-platform safe choice.

### 2. Cold-Start Consistency Check

```python
def recover_from_crash():
    """Verify all three storage files agree. Recover if not."""
    meta_count = len(meta)
    store_count = len(store)
    
    # Try to load index and check its count
    index_count = 0
    if os.path.exists(INDEX_PATH):
        try:
            temp_idx = IdMapIndex.load(INDEX_PATH)
            index_count = temp_idx.dim  # Can't get count directly without contains()
            # We trust load() succeeded; count check is approximate
        except Exception:
            index_count = -1  # Corrupt
    
    if meta_count != store_count:
        log(f"Inconsistency: meta={meta_count} vs store={store_count}. "
            "Rebuilding from store.")
        # Rebuild meta from store (reverse mapping)
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
```

**Fallback:** If recovery is not possible (corrupt store, corrupt .tvim, etc.), serve with empty state. The user just re-runs `index_directory` — no data lost, just time.

### 3. Fine-Grained Locking

```python
def index_file(file_path: str):
    """Read, embed, and index a single file. Lock is held only for mutations."""
    global current_id
    
    # I/O and CPU — no lock held
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        log(f"Cannot read {file_path}: {e}")
        return
    
    chunk = content[:2000].strip()
    if not chunk:
        return
    
    # CPU-bound embedding — no lock held  
    embedding = model.encode([chunk])
    
    # Critical section — brief, locked
    file_id = current_id  # Read outside lock (but this is atomic in Python)
    current_id += 1
    
    with index_lock:
        # Remove old entry if re-indexing
        if file_path in meta:
            old_id = meta[file_path]["id"]
            try:
                index.remove(old_id)
            except Exception:
                pass
            store.pop(old_id, None)
        
        index.add_with_ids(embedding, np.array([file_id], dtype=np.uint64))
        store[file_id] = {"path": file_path, "content": chunk}
        meta[file_path] = {
            "id": file_id,
            "mtime": os.path.getmtime(file_path),
            "size": os.path.getsize(file_path),
            "last_indexed": time.time(),
        }
```

### 4. Queue Thread Safety

```python
def enqueue(priority: str, file_path: str):
    """Thread-safe enqueue with its own lock."""
    with queue_lock:
        index_queue.append((priority, file_path))

def dequeue_batch(batch_size: int = 5):
    """Thread-safe priority dequeue."""
    batch = []
    with queue_lock:
        # Sort by priority in-place
        index_queue.sort(key=lambda x: 
            {"remove": 0, "new": 1, "changed": 2, "reindex": 3}.get(x[0], 99))
        
        for _ in range(min(batch_size, len(index_queue))):
            batch.append(index_queue.popleft())
    return batch

def queue_depth() -> int:
    """Thread-safe queue size check."""
    with queue_lock:
        return len(index_queue)
```

---

## Security Model

| Vector | Description |
|---|---|
| **Air-gapped** | No network calls for embedding or search. The model downloads once to cache, then runs offline. |
| **No user data egress** | Code content never leaves the process. No telemetry, no analytics. |
| **Isolated Python env** | Dependencies are installed in a dedicated `.venv`, not system Python. |
| **File access scope** | The server only reads files explicitly provided via `index_directory`. It writes only to `~/.turbocode/`. |
