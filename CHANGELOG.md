# Changelog

> All notable changes to TurboCode MCP are documented here.
> Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
> and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

- Project scaffolding: `package.json`, `bin/cli.js`, `scripts/setup.js`
- Python MCP server with FastMCP + Turbovec (`src/server.py`)
- Disk-persistent vector index (`.turbocode/index.tvim`)
- Background indexing worker (daemon thread, 5-file batches)
- Incremental indexing (skip unchanged files via `meta.json`)
- Stale file re-indexing (auto-refresh files older than 7 days)
- Lazy loading (model and index load on first use only)
- Idle shutdown watchdog (30-minute timeout, `os._exit(0)`)
- Three MCP tools: `index_directory`, `search_codebase`, `get_index_stats`
- Two MCP resources: `turbocode://status`, `turbocode://stats`
- Full documentation suite: getting-started, usage, architecture, reference, roadmap
- `AGENTS.md` — orientation for AI coding agents
- `JOURNAL.md` — development journal
- Node.js CLI wrapper with automatic Python venv setup via `postinstall`

### Known Issues

- First search/index call is ~5s (sentence-transformers model load)
- Index is shared across all indexed directories (no multi-project isolation)
- File-level chunking only (2000-char truncation, no semantic splitting)
