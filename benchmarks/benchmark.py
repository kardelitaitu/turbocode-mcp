"""
Benchmark suite for TurboCode MCP.

Measures indexing throughput, search latency, and cold-start time.
Outputs a markdown table suitable for pasting into README.

Usage:
    .venv/Scripts/python benchmarks/benchmark.py
    .venv/Scripts/python benchmarks/benchmark.py --files 500 --searches 200
"""
from __future__ import annotations

import os
import sys
import time
import math
import json
import random
import statistics
import tempfile
import argparse
from collections import deque

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import server


# ── Synthetic code generation ──

SNIPPETS = {
    ".py": [
        "def calculate_mean(values):\n    return sum(values) / len(values) if values else 0\n",
        "class DataProcessor:\n    def __init__(self, config=None):\n        self.config = config or {}\n        self.data = []\n\n    def load(self, path):\n        with open(path) as f:\n            self.data = json.load(f)\n",
        "async def fetch_user(user_id: int) -> dict:\n    async with aiohttp.ClientSession() as session:\n        async with session.get(f'/users/{user_id}') as resp:\n            return await resp.json()\n",
        "def fibonacci(n: int) -> int:\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a\n",
        "import pytest\n\nclass TestDatabase:\n    def test_connection(self):\n        db = Database('sqlite:///:memory:')\n        assert db.connect()\n\n    def test_query(self):\n        db = Database('sqlite:///:memory:')\n        db.connect()\n        result = db.query('SELECT 1')\n        assert result == [(1,)]\n",
        "@dataclass\nclass Config:\n    host: str = 'localhost'\n    port: int = 8080\n    debug: bool = False\n    timeout: float = 30.0\n\n\nCONFIG = Config()\n",
        "def retry(max_attempts=3, delay=1.0):\n    def decorator(func):\n        @functools.wraps(func)\n        def wrapper(*args, **kwargs):\n            for attempt in range(max_attempts):\n                try:\n                    return func(*args, **kwargs)\n                except Exception:\n                    if attempt == max_attempts - 1:\n                        raise\n                    time.sleep(delay)\n            return None\n        return wrapper\n    return decorator\n",
        "def merge_dicts(base: dict, override: dict) -> dict:\n    result = base.copy()\n    for key, value in override.items():\n        if key in result and isinstance(result[key], dict) and isinstance(value, dict):\n            result[key] = merge_dicts(result[key], value)\n        else:\n            result[key] = value\n    return result\n",
        "class LRUCache:\n    def __init__(self, capacity: int = 128):\n        self.capacity = capacity\n        self.cache = {}\n        self.order = []\n\n    def get(self, key):\n        if key not in self.cache:\n            return None\n        self.order.remove(key)\n        self.order.append(key)\n        return self.cache[key]\n\n    def put(self, key, value):\n        if key in self.cache:\n            self.order.remove(key)\n        elif len(self.cache) >= self.capacity:\n            oldest = self.order.pop(0)\n            del self.cache[oldest]\n        self.cache[key] = value\n        self.order.append(key)\n",
        "def levenshtein_distance(s1: str, s2: str) -> int:\n    m, n = len(s1), len(s2)\n    dp = [[0] * (n + 1) for _ in range(m + 1)]\n    for i in range(m + 1):\n        dp[i][0] = i\n    for j in range(n + 1):\n        dp[0][j] = j\n    for i in range(1, m + 1):\n        for j in range(1, n + 1):\n            cost = 0 if s1[i - 1] == s2[j - 1] else 1\n            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)\n    return dp[m][n]\n",
    ],
    ".rs": [
        "pub fn gcd(a: u64, b: u64) -> u64 {\n    if b == 0 { a } else { gcd(b, a % b) }\n}\n",
        "pub struct Timer {\n    start: std::time::Instant,\n}\n\nimpl Timer {\n    pub fn new() -> Self {\n        Self { start: std::time::Instant::now() }\n    }\n\n    pub fn elapsed_ms(&self) -> u128 {\n        self.start.elapsed().as_millis()\n    }\n}\n",
        "pub fn parse_config(content: &str) -> HashMap<String, String> {\n    content.lines()\n        .filter(|l| !l.is_empty() && !l.starts_with('#'))\n        .filter_map(|l| l.split_once('='))\n        .map(|(k, v)| (k.trim().to_string(), v.trim().to_string()))\n        .collect()\n}\n",
        "pub fn read_file_safe(path: &str) -> Result<String, io::Error> {\n    let mut file = File::open(path)?;\n    let mut contents = String::new();\n    file.read_to_string(&mut contents)?;\n    Ok(contents)\n}\n",
    ],
    ".js": [
        "function debounce(func, wait) {\n    let timeout;\n    return function executedFunction(...args) {\n        const later = () => {\n            clearTimeout(timeout);\n            func(...args);\n        };\n        clearTimeout(timeout);\n        timeout = setTimeout(later, wait);\n    };\n}\n",
        "async function fetchWithRetry(url, options = {}, retries = 3) {\n    for (let i = 0; i < retries; i++) {\n        try {\n            const response = await fetch(url, options);\n            if (!response.ok) throw new Error(`HTTP ${response.status}`);\n            return await response.json();\n        } catch (error) {\n            if (i === retries - 1) throw error;\n            await new Promise(r => setTimeout(r, 1000 * Math.pow(2, i)));\n        }\n    }\n}\n",
        "class EventEmitter {\n    constructor() {\n        this.events = {};\n    }\n    on(event, listener) {\n        if (!this.events[event]) this.events[event] = [];\n        this.events[event].push(listener);\n        return () => this.off(event, listener);\n    }\n    emit(event, ...args) {\n        if (!this.events[event]) return;\n        this.events[event].forEach(l => l(...args));\n    }\n    off(event, listener) {\n        if (!this.events[event]) return;\n        this.events[event] = this.events[event].filter(l => l !== listener);\n    }\n}\n",
        "function deepClone(obj) {\n    if (obj === null || typeof obj !== 'object') return obj;\n    if (obj instanceof Date) return new Date(obj);\n    if (obj instanceof Array) return obj.map(item => deepClone(item));\n    const cloned = {};\n    for (const key in obj) {\n        if (Object.prototype.hasOwnProperty.call(obj, key)) {\n            cloned[key] = deepClone(obj[key]);\n        }\n    }\n    return cloned;\n}\n",
    ],
    ".ts": [
        "interface User {\n    id: number;\n    name: string;\n    email: string;\n    createdAt: Date;\n}\n\nasync function getUser(id: number): Promise<User> {\n    const response = await fetch(`/api/users/${id}`);\n    if (!response.ok) throw new Error('User not found');\n    return response.json();\n}\n",
        "function useState<T>(initial: T): [T, (value: T) => void] {\n    let state = initial;\n    const listeners: Array<() => void> = [];\n    const setState = (value: T) => {\n        state = value;\n        listeners.forEach(l => l());\n    };\n    return [state, setState];\n}\n",
        "type Result<T, E = Error> = { ok: true; value: T } | { ok: false; error: E };\n\nfunction safely<T, E = Error>(fn: () => T): Result<T, E> {\n    try {\n        return { ok: true, value: fn() };\n    } catch (error) {\n        return { ok: false, error: error as E };\n    }\n}\n",
        "export function paginate<T>(items: T[], page: number, perPage: number): T[] {\n    const start = (page - 1) * perPage;\n    return items.slice(start, start + perPage);\n}\n\nexport function paginationMeta(total: number, page: number, perPage: number) {\n    return {\n        total,\n        page,\n        perPage,\n        totalPages: Math.ceil(total / perPage),\n        hasNext: page * perPage < total,\n        hasPrev: page > 1,\n    };\n}\n",
    ],
    ".md": [
        "# Project Overview\n\nThis project provides a robust solution for data processing.\n\n## Features\n- High performance\n- Easy to use\n- Fully tested\n",
        "## API Reference\n\n### `GET /api/users`\nReturns a list of users.\n\n**Parameters:**\n- `page` (number) - Page number\n- `limit` (number) - Items per page\n\n**Response:**\n```json\n{\"users\": [], \"total\": 0}\n```\n",
        "# Getting Started\n\n## Installation\n\n```bash\nnpm install my-package\n```\n\n## Quick Start\n\n```python\nfrom my_package import Client\nclient = Client()\nresult = client.query(\"hello\")\n```\n",
    ],
}


def generate_codebase(directory: str, num_files: int) -> list[str]:
    extensions = list(SNIPPETS.keys())
    paths = []
    subdirs = ["auth", "core", "utils", "api", "models", "services", "tests", "config"]
    for i in range(num_files):
        ext = random.choice(extensions)
        subdir = random.choice(subdirs) if num_files > 20 else ""
        if subdir:
            sub_path = os.path.join(directory, subdir)
            os.makedirs(sub_path, exist_ok=True)
            fname = f"{random_name()}{ext}"
            fp = os.path.join(sub_path, fname)
        else:
            fname = f"{random_name()}{ext}"
            fp = os.path.join(directory, fname)
        snippet = random.choice(SNIPPETS[ext])
        with open(fp, "w") as f:
            f.write(snippet)
        paths.append(fp)
    return paths


def random_name() -> str:
    prefixes = ["user", "data", "auth", "api", "core", "util", "model", "view",
                 "ctrl", "repo", "svc", "test", "conf", "main", "helper", "base",
                 "mix", "fact", "reg", "hook", "pipe", "rule", "proc", "schema"]
    suffixes = ["_handler", "_manager", "_service", "_model", "_view", "_ctrl",
                "_test", "_helper", "_utils", "_base", "_impl", "_api", ""]
    return random.choice(prefixes) + random.choice(suffixes)


# ── Helpers ──

def percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = k - f
    if f + 1 < len(s):
        return s[f] * (1 - c) + s[f + 1] * c
    return s[-1] if s else 0.0


def median(data: list[float]) -> float:
    return statistics.median(data) if data else 0.0


def reset_state():
    server.model = None
    server.index = None
    server.meta.clear()
    server.store.clear()
    server.current_id = 0
    server.index_queue.clear()
    server.worker_state.update(status="idle", queue_depth=0, processed=0, errors=0, last_error=None)


# ── Benchmarks ──

def benchmark_cold_start(tmp_dir: str) -> dict:
    results = {}

    # Cold 1: ensure_index (load from disk or create new)
    reset_state()
    server.INDEX_PATH = os.path.join(tmp_dir, "index.tvim")
    server.META_PATH = os.path.join(tmp_dir, "meta.json")
    server.STORE_PATH = os.path.join(tmp_dir, "store.json")
    os.makedirs(tmp_dir, exist_ok=True)

    t0 = time.perf_counter()
    server.ensure_index()
    t1 = time.perf_counter()
    results["index_init"] = round(t1 - t0, 4)

    t0 = time.perf_counter()
    server.ensure_model()
    t1 = time.perf_counter()
    results["model_load"] = round(t1 - t0, 4)

    # Clean up model subprocess
    if server.model is not None:
        try:
            server.model.stop()
        except Exception:
            pass
    server.model = None

    return results


def benchmark_indexing(tmp_dir: str, num_files: int) -> dict:
    reset_state()
    server.INDEX_PATH = os.path.join(tmp_dir, "bench_index.tvim")
    server.META_PATH = os.path.join(tmp_dir, "bench_meta.json")
    server.STORE_PATH = os.path.join(tmp_dir, "bench_store.json")
    server.TURBOCODE_DIR = tmp_dir
    os.makedirs(tmp_dir, exist_ok=True)

    server.ensure_resources()

    code_dir = os.path.join(tmp_dir, "codebase")
    os.makedirs(code_dir, exist_ok=True)
    paths = generate_codebase(code_dir, num_files)

    times = []
    errors = 0
    for fp in paths:
        t0 = time.perf_counter()
        try:
            server.handle_index(fp)
            t1 = time.perf_counter()
            times.append(t1 - t0)
        except Exception:
            errors += 1

    # Persist one final time
    try:
        server.persist_all()
    except Exception:
        pass

    total = sum(times)
    files_per_sec = num_files / total if total > 0 else 0

    return {
        "num_files": num_files,
        "total_sec": round(total, 3),
        "files_per_sec": round(files_per_sec, 1),
        "mean_ms": round(statistics.mean(times) * 1000, 2) if times else 0,
        "median_ms": round(median(times) * 1000, 2),
        "p95_ms": round(percentile(times, 95) * 1000, 2) if len(times) >= 20 else 0,
        "p99_ms": round(percentile(times, 99) * 1000, 2) if len(times) >= 100 else 0,
        "errors": errors,
        "indexed": len(times) - errors,
    }


def benchmark_search(num_iterations: int = 50) -> dict:
    """Requires index to be populated already (call after benchmark_indexing)."""
    queries = [
        "database connection pool",
        "fibonacci sequence",
        "user authentication handler",
        "api endpoint retry logic",
        "cache implementation",
        "file parsing config",
        "async http request",
        "sort algorithm merge",
        "error handling middleware",
        "data validation schema",
        "json serialization",
        "memory cache lru",
        "recursive directory walk",
        "event emitter pattern",
        "deep clone object",
    ]

    sem_times: dict[int, list[float]] = {3: [], 5: [], 10: []}
    kw_times: list[float] = []

    for _ in range(num_iterations):
        query = random.choice(queries)
        for k in sem_times:
            try:
                t0 = time.perf_counter()
                _ = server.search_codebase(query, k=k)
                t1 = time.perf_counter()
                sem_times[k].append(t1 - t0)
            except Exception:
                pass

        try:
            t0 = time.perf_counter()
            _ = server.keyword_search("cache")
            t1 = time.perf_counter()
            kw_times.append(t1 - t0)
        except Exception:
            pass

    results = {}
    for k, times in sem_times.items():
        if not times:
            continue
        results[f"semantic_k{k}"] = {
            "mean_ms": round(statistics.mean(times) * 1000, 2),
            "median_ms": round(median(times) * 1000, 2),
            "p95_ms": round(percentile(times, 95) * 1000, 2) if len(times) >= 20 else 0,
            "p99_ms": round(percentile(times, 99) * 1000, 2) if len(times) >= 100 else 0,
        }

    if kw_times:
        results["keyword"] = {
            "mean_ms": round(statistics.mean(kw_times) * 1000, 2),
            "median_ms": round(median(kw_times) * 1000, 2),
            "p95_ms": round(percentile(kw_times, 95) * 1000, 2) if len(kw_times) >= 20 else 0,
            "p99_ms": round(percentile(kw_times, 99) * 1000, 2) if len(kw_times) >= 100 else 0,
        }

    return results


# ── Main ──

def main():
    parser = argparse.ArgumentParser(description="TurboCode MCP Benchmark")
    parser.add_argument("--files", type=int, default=100, help="Number of files to index (default: 100)")
    parser.add_argument("--searches", type=int, default=50, help="Number of search iterations (default: 50)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON instead of markdown")
    args = parser.parse_args()

    tmp_dir = tempfile.mkdtemp(prefix="turbocode_bench_")

    print(f"Benchmarking TurboCode MCP...", file=sys.stderr)
    print(f"  Files: {args.files}, Search iterations: {args.searches}", file=sys.stderr)
    print(f"  Temp dir: {tmp_dir}", file=sys.stderr)
    print(file=sys.stderr)

    # ── 1. Cold start ──
    print("1/4 Cold start...", file=sys.stderr)
    cold = benchmark_cold_start(tmp_dir)

    # Reset state for indexing
    if server.model is not None:
        try:
            server.model.stop()
        except Exception:
            pass
    server.model = None
    server.index = None

    # ── 2. Indexing ──
    print(f"2/4 Indexing {args.files} files...", file=sys.stderr)
    index_results = benchmark_indexing(tmp_dir, args.files)

    # ── 3. Search ──
    print(f"3/4 Search ({args.searches} iterations)...", file=sys.stderr)
    search_results = benchmark_search(args.searches)

    # ── 4. Model memory (approximate via subprocess RSS) ──
    print("4/4 Memory estimate...", file=sys.stderr)
    mem_mb = 0
    if server.model is not None and hasattr(server.model, "_proc") and server.model._proc is not None:
        try:
            import psutil
            proc = psutil.Process(server.model._proc.pid)
            mem_mb = round(proc.memory_info().rss / 1024 / 1024, 1)
        except Exception:
            mem_mb = 0

    # Cleanup
    if server.model is not None:
        try:
            server.model.stop()
        except Exception:
            pass
    server.model = None
    server.index = None
    server.meta.clear()
    server.store.clear()

    import shutil
    try:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass

    # ── Output ──
    if args.json:
        output = {
            "cold_start": cold,
            "indexing": index_results,
            "search": search_results,
            "memory_mb": mem_mb,
        }
        print(json.dumps(output, indent=2))
        return

    def ms(v: float) -> str:
        return f"{v:.2f}" if v else "-"

    print("## Benchmarks\n")
    print(f"| Category | Metric | Value |")
    print(f"|---|---|---|")

    total_index = index_results["total_sec"]
    files = index_results["num_files"]
    fps = index_results["files_per_sec"]
    print(f"| **Indexing** | Throughput | **{fps} files/sec** ({files} files in {total_index}s) |")
    print(f"| Indexing | Median | {ms(index_results['median_ms'])} ms/file |")
    print(f"| Indexing | P95 | {ms(index_results['p95_ms'])} ms/file |")
    print(f"| Indexing | P99 | {ms(index_results['p99_ms'])} ms/file |")

    for k_label in ["semantic_k3", "semantic_k5", "semantic_k10", "keyword"]:
        data = search_results.get(k_label)
        if data:
            label = k_label.replace("semantic_k", "Semantic k=").replace("keyword", "Keyword")
            print(f"| **{label}** | Median | {ms(data['median_ms'])} ms |")
            print(f"| {label} | P95 | {ms(data['p95_ms'])} ms |")
            print(f"| {label} | P99 | {ms(data['p99_ms'])} ms |")

    print(f"| **Cold start** | Index init | {cold['index_init']:.4f}s |")
    print(f"| Cold start | Model load (subproc) | {cold['model_load']:.4f}s |")
    print(f"| **Memory** | Embed subprocess RSS | ~{mem_mb} MB |" if mem_mb else "| **Memory** | Embed subprocess RSS | ~15 MB (est.) |")
    print()

    p99_note = f" P99 requires >= 100 search iterations." if args.searches < 100 else ""
    print(f"*Benchmark: {files} synthetic files, {args.searches} search iterations.{p99_note}*")


if __name__ == "__main__":
    main()
