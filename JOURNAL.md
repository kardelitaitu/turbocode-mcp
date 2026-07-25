# Development Journal

> A running log of decisions, discoveries, and progress on TurboCode MCP.

---

## 2026-07-25 — Project Init & Documentation Restructure

### What happened

The project started with two planning documents (`plan.md` and `reference.md`) based on a naive v1 design: a Python MCP server with `fastmcp` + `turbovec`, no persistence, blocking indexing, and a simple npm wrapper.

We analyzed the viability, identified the core problems, and restructured everything.

### Key discoveries

- **Turbovec has built-in persistence** (`write()` / `load()`), but `load()` is a **classmethod** — the instance method produces a broken index. This was verified experimentally.
- **5 vectors × 384 dims** with 4-bit quantization = ~4KB on disk. 10,000 vectors = ~2MB. Storage is not a concern.
- **`sentence-transformers` (`all-MiniLM-L6-v2`)** uses ~500MB–1GB RAM. This is the memory bottleneck, not the index.

### Decisions made

| Decision | Rationale |
|---|---|
| **Lazy loading** | Model and index load on first use, not startup. Server starts in ~100ms, resources never trigger load. |
| **Background indexing** | 5-file batches with 1s interval via daemon thread. Tools return instantly. |
| **Disk persistence** | `.turbocode/index.tvim` + `meta.json` + `store.json`. Persist after every batch. |
| **Incremental indexing** | Compare mtimes against `meta.json`. Only embed new/changed files. |
| **Stale re-indexing** | Idle worker re-embeds files older than 7 days. Keeps index fresh. |
| **Idle shutdown** | 30-minute timeout. `os._exit(0)` — MCP client auto-restarts. |
| **MCP Resources** | `turbocode://status` + `turbocode://stats`. Never load model/index. |
| **Docs restructuring** | Split from 2 planning docs into 6 professional docs + AGENTS.md. |

### Open questions

- Should we support file-system watch mode (inotify/FSEvents) for auto-re-index?
- What's the right chunking strategy beyond simple truncation?
- Should the `.turbocode/` directory be configurable?

---

## Template for future entries

```markdown
## YYYY-MM-DD — [Title]

### What happened

### Key discoveries

### Decisions made

### Open questions
```
