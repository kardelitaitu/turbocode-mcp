---
name: turbocode
description: local semantic codebase search — index, query, and explore your project with vector embeddings, fully offline
trigger: /turbocode
---

# /turbocode

Local semantic codebase search powered by vector embeddings (BAAI/bge-small-en-v1.5). Index your project once, then search by meaning — not just keywords. Fully offline, no cloud.

## AI: When to auto-call the MCP tools

The MCP server (`turbocode-mcp`) exposes tools that the AI **should use automatically** without waiting for the user to type `/turbocode`:

- **`search_codebase(query, k)`** — Call this anytime the user asks a code question that needs semantic understanding. Examples:
  - "how do we connect to the database?" → searches for connection patterns
  - "find the retry logic" → finds retry/backoff implementations
  - "where is the login handler?" → locates auth-related code
  - "show me similar code to X" → semantic code search

- **`index_directory(path)`** — Call this when the user says to index, re-index, or when searching returns no results (the project may not be indexed yet). Returns instantly; indexing happens in background.

- **`get_index_stats()`** — Check how many files are indexed, workspace size, last index time.

AI should **always** call `search_codebase` when the user asks a codebase question. Do not wait for a `/turbocode` command.

## Slash Commands

| Command | Description |
|---|---|
| `/turbocode search <query>` | Semantic search with default k=5 |
| `/turbocode search <query> k=10` | Search with custom result count |
| `/turbocode index` | Index/re-index the entire workspace |
| `/turbocode index <path>` | Index a specific directory |
| `/turbocode stats` | Show index statistics |
| `/turbocode status` | Show server health status |

## Examples

```
/turbocode search how does authentication work
/turbocode search error handling patterns k=8
/turbocode index src/components
/turbocode stats
```

## How It Works

1. **Indexing** — `index_directory()` scans files, chunks content (max 2000 chars per chunk), embeds with BGE-small-en-v1.5, stores in a turbovec 4-bit quantized index
2. **Search** — `search_codebase()` embeds the query, finds nearest neighbors in vector space, returns ranked results with path, score, and matching snippet
3. **Storage** — Index lives in `~/.turbocode/` (index.tvim, meta.json, store.json), persisted atomically

## Supported File Types

`.py`, `.rs`, `.md`, `.txt`, `.js`, `.ts`, `.jsx`, `.tsx`, `.go`, `.java`, `.rb`, `.c`, `.cpp`, `.h`, `.hpp`, `.toml`, `.yaml`, `.yml`, `.json`, `.css`, `.scss`, `.html`
