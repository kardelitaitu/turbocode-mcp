# TurboCode MCP — Development Roadmap

> A local codebase vector search MCP server powered by Turbovec.
> Features: disk-persistent index, background indexing, MCP Resources, incremental updates.

---

## Phase 1: Project Scaffolding

- [ ] Create `package.json` — npm config with `bin` and `postinstall`
- [ ] Create `scripts/setup.js` — postinstall: create .venv, install Python deps
- [ ] Create `bin/cli.js` — Node.js CLI wrapper (spawns Python)
- [ ] Create `requirements.txt` — pinned Python dependencies
- [ ] Create `.gitignore` — node_modules, .venv, .turbocode, __pycache__

## Phase 2: Python MCP Server

- [ ] Create `src/server.py` — FastMCP + Turbovec
  - [ ] Startup: load index from `.turbocode/` or create empty
  - [ ] **Background worker** — daemon thread, 5-file batches, 1s interval
  - [ ] `index_directory` — scan, diff meta.json, enqueue new/changed/removed
  - [ ] `search_codebase` — embed query, search index, format results
  - [ ] `get_index_stats` — vector count, file count, disk usage, queue depth
- [ ] **MCP Resources** — auto-context for the AI
  - [ ] `turbocode://status` — idle/indexing status
  - [ ] `turbocode://stats` — JSON stats
- [ ] **Persistence** — persist index + meta + store after each batch
- [ ] **Stale re-indexing** — idle worker re-checks oldest files (>7 days)

## Phase 3: Local Testing

- [ ] `npm link` to test global installation
- [ ] Verify postinstall creates .venv and installs deps
- [ ] Test `turbocode-mcp` command launches Python server
- [ ] Test persistence: index → restart → search works
- [ ] Test incremental: re-index unchanged files → no work
- [ ] Test background: large dir → tools return instantly
- [ ] Test edge cases: empty dir, unsupported files, corrupt .tvim

## Phase 4: Polish & Robustness

- [ ] Pass `--help` and `--version` CLI flags
- [ ] Graceful shutdown (SIGINT/SIGTERM → persist before exit)
- [ ] Add progress logging for indexing batches
- [ ] Validate Python/.venv on startup with clear error message
- [ ] Handle corrupt `.tvim` gracefully (fallback to empty)
- [ ] Pipeline for re-index on file system changes (watch mode)

## Phase 5: Publish

- [ ] Write comprehensive README.md ✓ (done, will update)
- [ ] Add MCP client configuration examples
- [ ] `npm login` and `npm publish --access public`
- [ ] Verify global install from registry works end-to-end

---

## Backlog

- [ ] Semantic chunking (function/class boundaries instead of file-level)
- [ ] AST-aware indexing (imports, function signatures as metadata)
- [ ] Embedding content-hash cache (skip re-embedding identical content)
- [ ] Multi-project / named indexes
- [ ] Config via CLI flags (`--model`, `--dim`, `--bit-width`, `--batch-size`)
- [ ] File-system watch mode (inotify/FSEvents for auto-re-index)
- [ ] WebSocket transport as alternative to stdio
