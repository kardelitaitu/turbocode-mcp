import sys
import os
import threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collections import deque
from unittest.mock import MagicMock

import numpy as np
import pytest

# Pre-import heavy dependencies once during collection so their import cost
# is not attributed to individual test fixtures.
import sentence_transformers  # noqa: E402
from turbovec import IdMapIndex  # noqa: E402


@pytest.fixture(autouse=True)
def clean_globals():
    import server

    server._stop_event.set()
    server.model = None
    server.index = None
    server.meta = {}
    server.store = {}
    server.current_id = 0
    server.last_activity = 0.0
    server.index_queue = deque()
    server.worker_state = {
        "status": "idle",
        "queue_depth": 0,
        "processed": 0,
        "errors": 0,
        "last_error": None,
    }
    server.DEBUG_MODE = False


@pytest.fixture(autouse=True)
def tmp_paths(tmp_path, monkeypatch):
    import server

    d = tmp_path / ".turbocode"
    d.mkdir()
    monkeypatch.setattr(server, "TURBOCODE_DIR", str(d))
    monkeypatch.setattr(server, "INDEX_PATH", str(d / "index.tvim"))
    monkeypatch.setattr(server, "META_PATH", str(d / "meta.json"))
    monkeypatch.setattr(server, "STORE_PATH", str(d / "store.json"))
    return d


@pytest.fixture
def mock_model(mocker):
    instance = MagicMock()
    instance.encode.return_value = np.random.rand(1, 384).astype(np.float32)
    mocker.patch("sentence_transformers.SentenceTransformer", return_value=instance)
    import server
    server.model = instance
    return instance


@pytest.fixture
def mock_index(mocker):
    instance = MagicMock()
    instance.search.return_value = (
        np.array([[0.95, 0.85, 0.75]]),
        np.array([[1, 2, 3]], dtype=np.uint64),
    )
    instance.contains.return_value = True
    mocker.patch("server.IdMapIndex", return_value=instance)
    import server
    server.index = instance
    return instance


@pytest.fixture
def populated_state():
    import server

    server.meta = {
        "/proj/file1.py": {
            "id": 1, "mtime": 1000.0, "size": 100, "last_indexed": 2000.0
        },
        "/proj/file2.rs": {
            "id": 2, "mtime": 1001.0, "size": 200, "last_indexed": 2001.0
        },
        "/proj/old_file.md": {
            "id": 3, "mtime": 500.0, "size": 50, "last_indexed": 500.0
        },
    }
    server.store = {
        1: {"path": "/proj/file1.py", "content": "print('hello')"},
        2: {"path": "/proj/file2.rs", "content": "fn main() {}"},
        3: {"path": "/proj/old_file.md", "content": "# Old doc"},
    }
    server.current_id = 4
    return server


@pytest.fixture
def sample_dir(tmp_path):
    d = tmp_path / "sample_project"
    d.mkdir()
    (d / "main.py").write_text("def greet(name):\n    return f'Hello {name}'\n")
    (d / "lib.rs").write_text("pub fn add(a: i32, b: i32) -> i32 { a + b }\n")
    (d / "readme.md").write_text("# Sample Project\n\nA test project.\n")
    (d / "notes.txt").write_text("Some random notes\n")
    (d / "ignored.js").write_text("console.log('ignored')\n")
    sub = d / "subdir"
    sub.mkdir()
    (sub / "mod.py").write_text("class Helper:\n    pass\n")
    return d
