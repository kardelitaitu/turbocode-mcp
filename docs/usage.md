# Usage Guide

> Complete reference for all MCP tools, resources, and workflows.

---

## Tools

### `index_directory`

Queue a directory for background indexing.

```
index_directory(directory_path: str, respect_gitignore: bool = True) → str
```

**Parameters:**

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `directory_path` | `string` | Yes | — | Absolute path to the directory to index |
| `respect_gitignore` | `boolean` | No | `True` | Whether to skip files matched by `.gitignore` rules |

**Returns:** A human-readable status string describing what was queued.

**Behavior:**
- Scans the directory recursively for supported files (`.py`, `.rs`, `.md`, `.txt`, `.js`, `.ts`, `.go`, `.toml`, `.json`, `.yaml`, `.yml`)
- Respects `.gitignore` files by default (walks up directory tree to find all `.gitignore` files)
- Compares against `meta.json` to detect new, changed, and removed files
- Enqueues files for background processing
- Returns immediately — actual embedding happens in batches
- Idempotent: calling twice on the same directory does not create duplicates

**Examples:**

```
→ index_directory("/home/user/projects/my-app")
  Queued 15 files (12 new, 3 changed) for indexing.

→ index_directory("/home/user/projects/my-app")
  All 45 files up to date.

→ index_directory("/nonexistent/path")
  Error: Directory '/nonexistent/path' not found.
```

---

### `search_codebase`

Search indexed code for semantically similar content.

```
search_codebase(query: str, k: int = 3) → str
```

**Parameters:**

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `query` | `string` | Yes | — | Natural-language search query |
| `k` | `integer` | No | `3` | Number of results to return (1–20) |

**Returns:** Formatted markdown with file paths, similarity scores, and content snippets.

**Behavior:**
- Embeds the query using the same model used for indexing
- Searches the turbovec vector index for the `k` nearest neighbors
- Returns results ranked by cosine similarity (score range: ~0–150, higher = more similar)
- If the index is empty, returns a friendly message
- If fewer results than `k` exist, returns whatever is available
- If indexing is still in progress, appends a note about queued files

**Examples:**

```
→ search_codebase("user authentication flow", k=5)
  **/home/user/projects/my-app/src/auth.py** (score: 0.8921)
  ```python
  def login(username, password):
      user = authenticate(username, password)
      if user:
          return create_session(user)
      raise AuthenticationError("Invalid credentials")
  ```

  ---

  **/home/user/projects/my-app/src/models.py** (score: 0.7345)
  ```python
  class User:
      def __init__(self, username, password_hash):
          self.username = username
          self.password_hash = password_hash
  ```

  ---

→ search_codebase("non-existent feature", k=3)
  No results found for 'non-existent feature'.
  *Note: 12 files still queued for indexing.*
```

---

### `keyword_search`

Exact keyword match across indexed file contents.

```
keyword_search(keyword: str, file_extension_filter: str = "") → str
```

**Parameters:**

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `keyword` | `string` | Yes | — | Keyword to search for (case-insensitive) |
| `file_extension_filter` | `string` | No | `""` | Filter results to a specific extension (e.g., `".py"`) |

**Returns:** Matched lines with file paths and line numbers. Capped at 30 results.

**Behavior:**
- Searches raw stored content (not semantic vectors)
- Case-insensitive matching
- Optional file extension filter
- Shows matching lines with line numbers

---

### `update_file_index`

Re-index a single file immediately. Useful after editing a file.

```
update_file_index(file_path: str) → str
```

**Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_path` | `string` | Yes | Absolute path to the file to re-index |

**Returns:** Confirmation message or error.

**Behavior:**
- Embeds and indexes the file immediately (not queued)
- Persists index to disk after indexing
- Replaces any existing entry for the same file

---

### `get_index_stats`

Return current index health and statistics. Always instant — never loads the model.

```
get_index_stats() → str
```

**Parameters:** None

**Returns:** Formatted markdown with index metadata.

**Behavior:**
- Reads from in-memory counters and disk stats
- **Does not load the embedding model or vector index** — always instant
- Reports whether the model has been loaded yet

**Example:**

```
→ get_index_stats()

  **Index Stats**
  - Vectors: 47
  - Files tracked: 45
  - Directories: 3
  - Disk: 215.0 KB
  - Worker: idle (0 queued, 47 processed, 0 errors)
  - Model loaded: True
```

---

### `drop_index`

Clear the entire index from memory and disk.

```
drop_index() → str
```

**Parameters:** None

**Returns:** `"Index cleared."`

**Behavior:**
- Clears all vectors, metadata, and store
- Resets the turbovec index
- Persists the empty state to disk

---

### `read_file_content`

Read the full content of a file from disk (not from the index).

```
read_file_content(file_path: str) → str
```

**Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_path` | `string` | Yes | Absolute path to the file to read |

**Returns:** Full file content as a string.

**Behavior:**
- Reads directly from disk (not from the indexed store)
- Returns the complete file contents
- Useful for viewing files without leaving the MCP context

---

## Resources

Resources are **automatically provided** to the AI agent by the MCP client. They appear in the AI's context without needing a tool call.

### `turbocode://status`

**MIME type:** `text/plain`

A lightweight status indicator that never loads the model or index.

**Return values:**

| Scenario | Output |
|---|---|
| Server started, no activity yet | `Ready. 47 files tracked. (Model loaded on demand)` |
| Indexing in progress | `Indexing... 12 queued, 35 processed.` |
| Idle, all files indexed | `Idle. 47 files indexed.` |

### `turbocode://stats`

**MIME type:** `application/json`

Detailed statistics as a JSON document. Also lightweight — never loads the model or index.

```json
{
  "vectors": 47,
  "files_tracked": 45,
  "directories": ["/home/user/projects/my-app/src", "/home/user/projects/my-app/docs"],
  "disk_size_kb": 215.0,
  "queue_depth": 0,
  "state": "idle",
  "processed": 47,
  "errors": 0,
  "last_error": null,
  "model_loaded": true,
  "model": "BAAI/bge-small-en-v1.5"
}
```

| Field | Type | Description |
|---|---|---|
| `vectors` | `integer` | Number of vectors in the index |
| `files_tracked` | `integer` | Number of files in metadata |
| `directories` | `array[string]` | Unique root directories indexed |
| `disk_size_kb` | `number` | Size of the `.tvim` file on disk |
| `queue_depth` | `integer` | Files waiting in the background queue |
| `state` | `string` | `"idle"` or `"indexing"` |
| `processed` | `integer` | Total files processed since server start |
| `errors` | `integer` | Total indexing errors since server start |
| `last_error` | `string\|null` | Most recent error message |
| `model_loaded` | `boolean` | Whether the embedding model has been loaded |
| `model` | `string` | The embedding model name |

---

## Workflows

### Standard Workflow

```
1. Server starts (fast, lazy)
2. AI checks turbocode://status → "Ready. 0 files."
3. AI calls index_directory("/project") → "Queued 47 files."
4. AI calls search_codebase("query") → results (first call slow, model loads)
5. AI can check turbocode://status to see indexing progress
6. AI calls search_codebase again → instant (model already loaded)
```

### Re-indexing Workflow

```
1. User modifies files in the indexed directory
2. AI calls index_directory("/project") again
3. Server detects changed mtimes → queues only the changed files
4. Only ~3 files re-indexed instead of 47
```

### Multi-Project Workflow

```
1. AI calls index_directory("/project-alpha")
2. AI calls index_directory("/project-beta")
3. Both are queued; background worker processes them in priority order
4. AI can search across both projects simultaneously
```

### Gitignore-Aware Indexing

```
1. AI calls index_directory("/project", respect_gitignore=True) [default]
2. Server loads .gitignore files from the directory tree upward
3. Files matching gitignore patterns are skipped
4. Use respect_gitignore=False to index everything including gitignored files
```

### Single-File Update

```
1. User edits a file
2. AI calls update_file_index("/path/to/file.py")
3. File is re-indexed immediately (no queue delay)
4. Search results reflect the updated content
```

---

## Expected Latency

| Operation | First call | Subsequent calls |
|---|---|---|
| `turbocode://status` | < 1ms | < 1ms |
| `turbocode://stats` | < 1ms | < 1ms |
| `get_index_stats()` | < 1ms | < 1ms |
| `index_directory` (scan only) | ~50ms | ~50ms |
| `search_codebase` | ~5s (model load) | ~200ms |
| `update_file_index` | ~400ms | ~400ms |
| `keyword_search` | < 10ms | < 10ms |
| `read_file_content` | file size dependent | file size dependent |
| `drop_index` | < 10ms | < 10ms |
| Background worker (per file) | ~400ms | ~400ms |
| Idle shutdown | — | 30 minutes after last activity |

---

## Supported File Types

| Extension | Language |
|---|---|
| `.py` | Python |
| `.rs` | Rust |
| `.md` | Markdown |
| `.txt` | Plain text |
| `.js` | JavaScript |
| `.ts` | TypeScript |
| `.go` | Go |
| `.toml` | TOML config |
| `.json` | JSON |
| `.yaml`, `.yml` | YAML |

---

## File Format (`~/.turbocode/`)

The server creates and manages these files in your home directory (`~/.turbocode/`):

### `index.tvim`

Binary turbovec index file. Contains the compressed vector index and id-map side tables. Not human-readable.

### `meta.json`

```json
{
  "/path/to/file.py": {
    "id": 42,
    "mtime": 1698765432.123,
    "size": 2048,
    "last_indexed": 1698765432.123
  }
}
```

| Field | Description |
|---|---|
| Key | Absolute file path |
| `id` | Internal turbovec vector ID (uint64) |
| `mtime` | File modification timestamp (Unix epoch) |
| `size` | File size in bytes |
| `last_indexed` | Timestamp of last indexing |

### `store.json`

```json
{
  "42": {
    "path": "/path/to/file.py",
    "content": "def login(username, password):\n    ..."
  }
}
```

Keys are stringified turbovec IDs. Each entry maps an ID back to the original file path and content snippet.
