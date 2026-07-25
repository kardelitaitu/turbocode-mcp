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
- **Lazy loading** — model and index load on first use only (instant startup)
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

### Known Issues

- First search/index call is ~5s (sentence-transformers model load)
- Index is shared across all indexed directories (no multi-project isolation)
- File-level chunking only (2000-char truncation, no semantic splitting)