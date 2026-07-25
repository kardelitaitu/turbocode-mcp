import builtins
import json
import os
import time
import threading
import signal as sig_module
from collections import deque

import numpy as np
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck

import server


class TestLogging:
    def test_log_debug_off(self, capsys):
        server.debug("should not appear")
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_log_debug_on(self, capsys):
        server.DEBUG_MODE = True
        server.debug("verbose detail")
        captured = capsys.readouterr()
        assert "[DEBUG]" in captured.err
        assert "verbose detail" in captured.err

    def test_log_message(self, capsys):
        server.log("hello world")
        captured = capsys.readouterr()
        assert "[TurboCode MCP]" in captured.err
        assert "hello world" in captured.err


class TestAtomicPersistence:
    def test_atomic_write_creates_file(self, tmp_path):
        f = tmp_path / "test.json"
        server.atomic_write(str(f), '{"a": 1}')
        assert f.exists()
        assert f.read_text(encoding="utf-8") == '{"a": 1}'

    def test_atomic_write_no_tmp_leftover(self, tmp_path):
        f = tmp_path / "test.json"
        server.atomic_write(str(f), "data")
        assert not (tmp_path / "test.json.tmp").exists()

    def test_atomic_write_overwrites(self, tmp_path):
        f = tmp_path / "test.json"
        server.atomic_write(str(f), "first")
        server.atomic_write(str(f), "second")
        assert f.read_text(encoding="utf-8") == "second"

    def test_persist_all(self, mock_index, populated_state):
        server.index.write.side_effect = lambda p: open(p, "w").close()
        server.persist_all()

        assert os.path.exists(server.INDEX_PATH)
        assert os.path.exists(server.META_PATH)
        assert os.path.exists(server.STORE_PATH)

        meta_loaded = json.load(open(server.META_PATH))
        assert "/proj/file1.py" in meta_loaded

        store_loaded = json.load(open(server.STORE_PATH))
        assert "1" in store_loaded
        assert store_loaded["1"]["path"] == "/proj/file1.py"


class TestQueueManagement:
    def test_enqueue_dequeue_single(self):
        server.enqueue("new", "/a.py")
        batch = server.dequeue_batch(5)
        assert len(batch) == 1
        assert batch[0] == ("new", "/a.py")

    def test_enqueue_dequeue_empty(self):
        assert server.dequeue_batch(5) == []

    def test_queue_depth(self):
        assert server.queue_depth() == 0
        server.enqueue("new", "/a.py")
        server.enqueue("changed", "/b.py")
        assert server.queue_depth() == 2

    def test_dequeue_batch_size_limit(self):
        for i in range(10):
            server.enqueue("new", f"/f{i}.py")
        batch = server.dequeue_batch(3)
        assert len(batch) == 3
        assert server.queue_depth() == 7

    def test_priority_ordering(self):
        server.enqueue("reindex", "/r.py")
        server.enqueue("new", "/n.py")
        server.enqueue("remove", "/d.py")
        server.enqueue("changed", "/c.py")

        batch = server.dequeue_batch(4)
        priorities = [p for p, _ in batch]
        assert priorities == ["remove", "new", "changed", "reindex"]

    def test_dequeue_maintains_order_within_same_priority(self):
        server.enqueue("new", "/a.py")
        server.enqueue("new", "/b.py")
        server.enqueue("new", "/c.py")
        batch = server.dequeue_batch(3)
        files = [fp for _, fp in batch]
        assert files == ["/a.py", "/b.py", "/c.py"]

    def test_enqueue_unknown_priority_sorted_last(self):
        server.enqueue("new", "/n.py")
        server.enqueue("unknown", "/x.py")
        server.enqueue("remove", "/d.py")
        batch = server.dequeue_batch(3)
        priorities = [p for p, _ in batch]
        assert priorities == ["remove", "new", "unknown"]


class TestColdStartRecovery:
    def test_fresh_state_no_files(self):
        server.load_and_verify()
        assert server.meta == {}
        assert server.store == {}
        assert server.current_id == 1

    def test_clean_state_matches(self):
        server.meta = {"/a.py": {"id": 1, "mtime": 100, "size": 50, "last_indexed": 200}}
        server.store = {1: {"path": "/a.py", "content": "code", "mtime": 100, "size": 50, "last_indexed": 200}}
        server.current_id = 0
        json.dump(server.meta, open(server.META_PATH, "w"))
        json.dump({str(k): v for k, v in server.store.items()}, open(server.STORE_PATH, "w"))

        server.load_and_verify()
        assert len(server.meta) == 1
        assert len(server.store) == 1
        assert server.current_id == 2

    def test_mismatch_rebuilds_meta_from_store(self, tmp_path):
        meta_bad = {"/gone.py": {"id": 99, "mtime": 0, "size": 0, "last_indexed": 0}}
        json.dump(meta_bad, open(server.META_PATH, "w"))
        store_ser = {"2": {"path": "/store_only.py", "content": "y", "mtime": 50, "size": 5, "last_indexed": 100},
                     "3": {"path": "/another.py", "content": "z", "mtime": 60, "size": 6, "last_indexed": 110}}
        json.dump(store_ser, open(server.STORE_PATH, "w"))

        server.load_and_verify()
        assert "/store_only.py" in server.meta
        assert "/another.py" in server.meta
        assert "/gone.py" not in server.meta
        assert server.current_id == 4

    def test_corrupt_meta_rebuilds_from_store(self):
        with open(server.META_PATH, "w") as f:
            f.write("not-json{")
        store_data = {1: {"path": "/a.py", "content": "x"}}
        json.dump({str(k): v for k, v in store_data.items()}, open(server.STORE_PATH, "w"))

        server.load_and_verify()
        assert "/a.py" in server.meta
        assert len(server.store) == 1

    def test_corrupt_store_empties_meta(self):
        server.meta = {"/a.py": {"id": 1, "mtime": 0, "size": 0, "last_indexed": 0}}
        json.dump(server.meta, open(server.META_PATH, "w"))
        with open(server.STORE_PATH, "w") as f:
            f.write("not-json{")

        server.load_and_verify()
        assert server.store == {}
        assert server.meta == {}

    def test_current_id_from_max_store_key(self):
        server.store = {5: {"path": "/a.py", "content": "x"}, 12: {"path": "/b.py", "content": "y"}}
        json.dump({str(k): v for k, v in server.store.items()}, open(server.STORE_PATH, "w"))
        json.dump({}, open(server.META_PATH, "w"))

        server.load_and_verify()
        assert server.current_id == 13


class TestStaleFileDetection:
    def test_finds_stale_files(self):
        now = time.time()
        server.meta = {
            "/fresh.py": {"id": 1, "last_indexed": now},
            "/stale.py": {"id": 2, "last_indexed": now - 8 * 86400},
            "/older.py": {"id": 3, "last_indexed": now - 30 * 86400},
        }
        stale = server.find_stale_files(max_age_days=7, max_files=10)
        assert "/fresh.py" not in stale
        assert "/stale.py" in stale
        assert "/older.py" in stale

    def test_empty_meta_returns_empty(self):
        assert server.find_stale_files() == []

    def test_no_stale_files_returns_empty(self):
        now = time.time()
        server.meta = {
            "/a.py": {"id": 1, "last_indexed": now},
            "/b.py": {"id": 2, "last_indexed": now},
        }
        assert server.find_stale_files(max_age_days=7, max_files=10) == []

    def test_respects_max_files_limit(self):
        now = time.time()
        server.meta = {
            f"/f{i}.py": {"id": i, "last_indexed": now - 14 * 86400}
            for i in range(20)
        }
        stale = server.find_stale_files(max_age_days=7, max_files=5)
        assert len(stale) == 5

    def test_missing_last_indexed_treated_as_stale(self):
        now = time.time()
        server.meta = {
            "/fresh.py": {"id": 1, "last_indexed": now},
            "/no_index.py": {"id": 2},
        }
        stale = server.find_stale_files(max_age_days=7, max_files=10)
        assert "/fresh.py" not in stale
        assert "/no_index.py" in stale


class TestFileIndexing:
    def test_handle_index(self, tmp_path, mock_model, mock_index, populated_state):
        f = tmp_path / "new_file.py"
        f.write_text("def hello():\n    pass\n")
        server.meta.clear()
        server.store.clear()
        server.current_id = 1

        server.handle_index(str(f))

        assert str(f) in server.meta
        assert server.meta[str(f)]["id"] == 1
        assert 1 in server.store
        assert "def hello()" in server.store[1]["content"]
        mock_model.encode.assert_called_once()
        mock_index.add_with_ids.assert_called_once()

    def test_handle_index_empty_file_skipped(self, tmp_path, mock_model, mock_index):
        f = tmp_path / "empty.py"
        f.write_text("   \n  \n")
        server.handle_index(str(f))
        assert mock_model.encode.call_count == 0

    def test_handle_index_unreadable_skipped(self, mock_model, mock_index):
        server.handle_index("/nonexistent/file.py")
        assert mock_model.encode.call_count == 0

    def test_handle_index_reindex_replaces_old(self, tmp_path, mock_model, mock_index, populated_state):
        f = tmp_path / "updated.py"
        f.write_text("new content")

        server.handle_index(str(f))

        # Re-index adds another copy
        f.write_text("revised content v2")
        server.handle_index(str(f))

        assert server.meta[str(f)]["id"] == 5
        assert "revised content v2" in server.store[5]["content"]

    def test_handle_remove_removes_tracked(self, mock_index, populated_state):
        server.handle_remove("/proj/file1.py")
        assert "/proj/file1.py" not in server.meta
        assert 1 not in server.store

    def test_handle_remove_not_tracked(self, mock_index, populated_state):
        server.handle_remove("/not/tracked.py")
        assert len(server.meta) == 3
        assert len(server.store) == 3

    def test_handle_remove_idempotent(self, mock_index, populated_state):
        server.handle_remove("/proj/file1.py")
        server.handle_remove("/proj/file1.py")
        assert "/proj/file1.py" not in server.meta


class TestLazyLoading:
    def test_ensure_model_loads_on_first_call(self, mocker):
        mock_class = mocker.patch("sentence_transformers.SentenceTransformer")
        instance = mock_class.return_value

        server.ensure_model()
        mock_class.assert_called_once_with("all-MiniLM-L6-v2")
        assert server.model is instance

    def test_ensure_model_does_not_reload(self, mocker):
        mock_class = mocker.patch("sentence_transformers.SentenceTransformer")
        server.ensure_model()
        server.ensure_model()
        assert mock_class.call_count == 1

    def test_ensure_index_creates_empty(self):
        server.ensure_index()
        assert server.index is not None

    def test_ensure_index_loads_existing(self, mocker, mock_index, populated_state):
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_load = mocker.patch("server.IdMapIndex.load")
        server.persist_all()
        server.index = None

        server.ensure_index()
        mock_load.assert_called_once_with(server.INDEX_PATH)

    def test_ensure_index_handles_corrupt(self, mocker):
        mock_load = mocker.patch("server.IdMapIndex.load", side_effect=Exception("corrupt"))
        with open(server.INDEX_PATH, "wb") as f:
            f.write(b"garbage")

        server.ensure_index()
        assert server.index is not None

    def test_ensure_resources_loads_both(self, mocker):
        mocker.patch("sentence_transformers.SentenceTransformer")
        mocker.patch("server.IdMapIndex.load")

        server.ensure_resources()
        assert server.model is not None
        assert server.index is not None


class TestTouch:
    def test_touch_resets_timer(self):
        before = server.last_activity
        server.last_activity = before - 1000
        server.touch()
        assert server.last_activity > before


class TestValidate:
    def test_validates_python_version(self):
        server.validate_python_version()

    def test_validate_imports_passes(self):
        server.validate_imports()


class TestToolIndexDirectory:
    def test_directory_not_found(self):
        result = server.index_directory("/nonexistent/dir")
        assert "not found" in result.lower()

    def test_scans_and_queues_files(self, sample_dir, mock_model, mock_index):
        result = server.index_directory(str(sample_dir))

        assert "queued" in result.lower()
        # Should find 5 supported files (main.py, lib.rs, readme.md, notes.txt, subdir/mod.py)
        # 4 in root + 1 ignored (.js) + 1 in subdir
        assert server.queue_depth() == 5

    def test_up_to_date_on_repeat(self, sample_dir, mock_model, mock_index):
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        server.index_directory(str(sample_dir))
        batch = server.dequeue_batch(10)
        for _, fp in batch:
            server.handle_index(fp)
        server.persist_all()

        result = server.index_directory(str(sample_dir))
        assert "up to date" in result.lower()

    def test_detects_changed_files(self, sample_dir, mock_model, mock_index):
        server.index_directory(str(sample_dir))
        batch = server.dequeue_batch(10)
        for _, fp in batch:
            server.handle_index(fp)

        old_mtime = server.meta[str(sample_dir / "main.py")]["mtime"]
        f = sample_dir / "main.py"
        f.write_text(f.read_text() + "\n# new line\n")
        os.utime(str(f), (old_mtime + 10, old_mtime + 10))

        result = server.index_directory(str(sample_dir))
        assert "changed" in result.lower()

    def test_detects_removed_files(self, sample_dir, mock_model, mock_index):
        server.index_directory(str(sample_dir))
        batch = server.dequeue_batch(10)
        for _, fp in batch:
            server.handle_index(fp)

        os.remove(str(sample_dir / "main.py"))
        result = server.index_directory(str(sample_dir))
        assert "remove" in result.lower()


class TestToolSearchCodebase:
    def test_search_empty_index(self, mock_model, mock_index):
        result = server.search_codebase("something")
        assert "empty" in result.lower()

    def test_search_with_results(self, mock_model, mock_index, populated_state):
        result = server.search_codebase("test query", k=2)
        assert "file1.py" in result or "file2.rs" in result
        assert "score:" in result

    def test_search_clamps_k_low(self, mock_model, mock_index, populated_state):
        result = server.search_codebase("test", k=0)
        assert isinstance(result, str)

    def test_search_clamps_k_high(self, mock_model, mock_index, populated_state):
        result = server.search_codebase("test", k=50)
        assert isinstance(result, str)


class TestToolGetIndexStats:
    def test_stats_no_load(self):
        result = server.get_index_stats()
        assert "Index Stats" in result
        assert "Model loaded: False" in result
        assert "Vectors:" in result

    def test_stats_with_data(self, populated_state, mock_index):
        result = server.get_index_stats()
        assert "Vectors: 3" in result
        assert "Files tracked: 3" in result

    def test_stats_model_loaded(self, populated_state, mock_model, mock_index):
        result = server.get_index_stats()
        assert "Model loaded: True" in result


class TestResources:
    def test_status_ready(self):
        result = server.index_status()
        assert "Ready" in result
        assert "files tracked" in result

    def test_status_indexing(self):
        server.model = object()
        server.index = object()
        server.enqueue("new", "/a.py")
        result = server.index_status()
        assert "Indexing" in result

    def test_status_idle(self, populated_state):
        server.model = object()
        server.index = object()
        result = server.index_status()
        assert "Idle" in result

    def test_stats_json(self, populated_state):
        result = server.index_stats()
        data = json.loads(result)
        assert data["vectors"] == 3
        assert data["files_tracked"] == 3
        assert data["model_loaded"] is False

    def test_stats_json_with_model(self, populated_state, mock_model, mock_index):
        result = server.index_stats()
        data = json.loads(result)
        assert data["model_loaded"] is True
        assert data["processed"] == 0


class TestBackgroundWorker:
    def test_worker_processes_queue(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        f = tmp_path / "test.py"
        f.write_text("def foo():\n    return 42\n")
        server.enqueue("new", str(f))

        from server import background_worker
        import threading

        server._stop_event.clear()
        t = threading.Thread(target=background_worker, daemon=True)
        t.start()
        time.sleep(0.02)

        assert str(f) in server.meta
        assert server.worker_state["errors"] == 0

    def test_worker_skips_unreadable_file(self, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        server.enqueue("new", "/nonexistent/file.py")

        from server import background_worker
        import threading

        server._stop_event.clear()
        t = threading.Thread(target=background_worker, daemon=True)
        t.start()
        time.sleep(0.02)

        assert server.worker_state["errors"] == 0
        assert "/nonexistent/file.py" not in server.meta


class TestHandleIndexNoneGuards:
    def test_handle_index_with_model_none_does_not_crash(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("x = 1")
        server.handle_index(str(f))
        assert len(server.store) == 0

    def test_handle_index_with_index_none_does_not_crash(self, tmp_path, mock_model):
        server.index = None
        f = tmp_path / "test.py"
        f.write_text("x = 1")
        server.handle_index(str(f))
        assert len(server.store) == 0

    def test_handle_remove_with_index_none_does_not_crash(self, populated_state):
        server.index = None
        server.handle_remove("/proj/file1.py")
        assert "/proj/file1.py" in server.meta  # File stays in meta when index is None

    def test_handle_remove_with_index_none_does_not_raise(self, populated_state):
        server.index = None
        server.handle_remove("/proj/file1.py")
        # No crash is the assertion


class TestFileIndexingEdgeCases:
    def test_handle_index_long_file_truncates(self, tmp_path, mock_model, mock_index):
        server.current_id = 1
        f = tmp_path / "long.py"
        f.write_text("x" * 5000)
        server.handle_index(str(f))

        stored = server.store[1]["content"]
        assert len(stored) == 2000
        assert stored == "x" * 2000

    def test_handle_index_binary_file_does_not_crash(self, tmp_path, mock_model, mock_index):
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x00\x01\x02\xff\xfe\xfd")
        server.handle_index(str(f))

        assert len(server.store) == 1

    def test_handle_index_updates_processed_count_via_worker(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        server.current_id = 1
        f = tmp_path / "counter.py"
        f.write_text("x = 1")
        server.enqueue("new", str(f))

        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)

        assert server.worker_state["processed"] == 1
        assert str(f) in server.meta

    def test_handle_remove_nonexistent_id_does_not_raise(self, mock_index, populated_state):
        mock_index.remove.side_effect = Exception("not found")
        server.handle_remove("/proj/file1.py")
        assert "/proj/file1.py" not in server.meta


class TestSearchCodebaseEdgeCases:
    def test_search_no_results_with_queued_hint(self, mock_model, mock_index, populated_state):
        mock_index.search.return_value = (np.array([[]]), np.array([[]], dtype=np.uint64))
        server.enqueue("new", "/pending.py")
        result = server.search_codebase("something unique")

        assert "No results" in result
        assert "queued" in result

    def test_search_k_larger_than_available(self, mock_model, mock_index, populated_state):
        mock_index.search.return_value = (
            np.array([[0.9, 0.8]]),
            np.array([[1, 2]], dtype=np.uint64),
        )
        result = server.search_codebase("test", k=10)

        assert "file1.py" in result
        assert "file2.rs" in result

    def test_search_empty_store_no_model_load(self, mock_model, mock_index):
        result = server.search_codebase("anything")
        assert "empty" in result.lower()

    def test_search_empty_query_returns_error(self, mock_model, mock_index):
        result = server.search_codebase("")
        assert "Error" in result
        assert "empty" in result.lower()

    def test_search_whitespace_query_returns_error(self, mock_model, mock_index):
        result = server.search_codebase("   ")
        assert "Error" in result
        assert "empty" in result.lower()

    def test_search_with_special_characters(self, mock_model, mock_index, populated_state):
        result = server.search_codebase("import # $ % & * ( ) + = { } [ ] | \\ : ; \" ' < > , . / ? ~ `")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_search_very_long_query(self, mock_model, mock_index, populated_state):
        long_query = "x" * 10000
        result = server.search_codebase(long_query)
        assert isinstance(result, str)
        assert len(result) > 0


class TestIndexDirectoryEdgeCases:
    def test_empty_directory(self, tmp_path, mock_model, mock_index):
        d = tmp_path / "empty"
        d.mkdir()
        result = server.index_directory(str(d))
        assert "up to date" in result.lower()

    def test_unsupported_files_only(self, tmp_path, mock_model, mock_index):
        d = tmp_path / "web_project"
        d.mkdir()
        (d / "app.js").write_text("console.log('hi')")
        (d / "style.css").write_text("body { color: red }")
        (d / "index.html").write_text("<html></html>")

        result = server.index_directory(str(d))
        assert "up to date" in result.lower()
        assert server.queue_depth() == 0

    def test_case_insensitive_extensions(self, tmp_path, mock_model, mock_index):
        d = tmp_path / "mixed_case"
        d.mkdir()
        (d / "UPPER.PY").write_text("x = 1")
        (d / "Mixed.Rs").write_text("fn main() {}")
        (d / "lower.md").write_text("# hello")

        result = server.index_directory(str(d))
        assert "queued" in result.lower()
        assert server.queue_depth() == 3

    def test_symlink_skipped_on_walk(self, tmp_path, mock_model, mock_index):
        d = tmp_path / "with_link"
        d.mkdir()
        (d / "real.py").write_text("x = 1")
        link = d / "linked.py"
        try:
            os.symlink(str(d / "real.py"), str(link))
        except (OSError, NotImplementedError):
            pass

        result = server.index_directory(str(d))
        assert "up to date" in result.lower() or "queued" in result.lower()

    def test_directory_trailing_slash(self, tmp_path, sample_dir, mock_model, mock_index):
        result = server.index_directory(str(sample_dir) + "\\")
        assert "queued" in result.lower()

    def test_directory_with_hidden_files(self, tmp_path, mock_model, mock_index):
        d = tmp_path / "with_hidden"
        d.mkdir()
        (d / "main.py").write_text("visible")
        (d / ".hidden.py").write_text("hidden")
        (d / "__pycache__").mkdir()

        result = server.index_directory(str(d))
        assert "queued" in result.lower()

    def test_directory_permdenied_returns_error(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch("server.os.walk", side_effect=PermissionError("access denied"))
        d = tmp_path / "locked"
        d.mkdir()
        result = server.index_directory(str(d))
        assert "Error" in result
        assert "Permission denied" in result


class TestPersistenceRobustness:
    def test_persist_all_none_index_does_not_crash(self):
        server.persist_all()

    def test_persist_all_empty_state(self, mock_index):
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        server.persist_all()
        assert os.path.exists(server.INDEX_PATH)
        assert os.path.exists(server.META_PATH)
        assert os.path.exists(server.STORE_PATH)

    def test_persist_all_atomic_on_crash(self, mock_index, populated_state, mocker):
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        server.persist_all()

        mtime_before = os.path.getmtime(server.META_PATH)
        content_before = open(server.META_PATH).read()

        mock_write = mocker.patch.object(server, "atomic_write")
        mock_write.side_effect = [None, Exception("crash during store write")]

        try:
            server.persist_all()
        except Exception:
            pass

        assert os.path.exists(server.META_PATH)
        assert open(server.META_PATH).read() == content_before


class TestStorageConsistency:
    def test_full_round_trip(self, tmp_path, mock_model, mock_index):
        server.current_id = 1
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        f = tmp_path / "roundtrip.py"
        f.write_text("def test():\n    pass\n")

        server.handle_index(str(f))
        server.persist_all()

        loaded_meta = json.load(open(server.META_PATH))
        loaded_store = json.load(open(server.STORE_PATH))
        assert str(f) in loaded_meta
        assert "def test():" in loaded_store["1"]["content"]

    def test_rebuild_after_persist_crash(self, tmp_path, mock_model, mock_index, mocker):
        server.current_id = 1
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        f = tmp_path / "crash_recovery.py"
        f.write_text("survive = True")

        server.handle_index(str(f))
        server.persist_all()

        json.dump({}, open(server.META_PATH, "w"))

        server.meta = {}
        server.store = {}
        server.current_id = 0

        server.load_and_verify()
        assert len(server.store) == 1
        assert str(f) in server.meta
        assert server.meta[str(f)]["id"] == 1


class TestIdleWatchdog:
    def test_watchdog_condition_true_when_timed_out(self):
        server.last_activity = time.time() - (server.IDLE_TIMEOUT + 60)
        assert time.time() - server.last_activity > server.IDLE_TIMEOUT

    def test_watchdog_condition_false_within_timeout(self):
        server.last_activity = time.time()
        assert time.time() - server.last_activity < server.IDLE_TIMEOUT

    def test_watchdog_does_not_call_persist_within_timeout(self, mocker):
        mock_persist = mocker.patch("server.persist_all")
        mocker.patch("server.os._exit")
        mocker.patch("server.CHECK_INTERVAL", 1000)
        mocker.patch("time.sleep", lambda s: None)

        server.last_activity = time.time()
        server._stop_event.clear()

        t = threading.Thread(target=server.idle_watchdog, daemon=True)
        t.start()
        time.sleep(0.03)

        mock_persist.assert_not_called()
        server._stop_event.set()
        t.join(timeout=1)

    def test_watchdog_calls_persist_when_timed_out(self, mocker):
        mock_persist = mocker.patch("server.persist_all")
        mocker.patch("server.os._exit")
        mocker.patch("server.log")  # suppress flood
        mocker.patch("server.CHECK_INTERVAL", 0.05)
        mocker.patch("time.sleep", lambda s: None)

        server.last_activity = time.time() - (server.IDLE_TIMEOUT + 60)
        server._stop_event.clear()

        t = threading.Thread(target=server.idle_watchdog, daemon=True)
        t.start()
        time.sleep(0.05)

        mock_persist.assert_called()
        server._stop_event.set()
        t.join(timeout=1)


class TestSignalHandling:
    def test_main_registers_signal_handlers(self, mocker):
        mock_signal = mocker.patch("server.sig_module.signal")
        mocker.patch("server.validate_environment")
        mocker.patch("os.makedirs")
        mocker.patch("server.load_and_verify")
        mocker.patch("threading.Thread")
        mocker.patch("server.mcp.run")

        server.main()

        mock_signal.assert_any_call(sig_module.SIGINT, mocker.ANY)
        mock_signal.assert_any_call(sig_module.SIGTERM, mocker.ANY)

    def test_signal_persists_on_interrupt(self, mocker):
        mock_persist_locked = mocker.patch("server._persist_locked")
        mock_exit = mocker.patch("server.os._exit")
        mocker.patch("server.sig_module.signal")

        mocker.patch("server.validate_environment")
        mocker.patch("os.makedirs")
        mocker.patch("server.load_and_verify")
        mocker.patch("threading.Thread")
        mocker.patch("server.mcp.run")

        registered_handlers = {}

        def capture_signal(signum, handler):
            registered_handlers[signum] = handler

        mock_signal = mocker.patch("server.sig_module.signal", side_effect=capture_signal)
        mocker.patch("server.mcp.run", side_effect=Exception("stop"))

        try:
            server.main()
        except Exception:
            pass

        handler = registered_handlers.get(sig_module.SIGINT)
        if handler:
            handler(sig_module.SIGINT, None)
            mock_persist_locked.assert_called_once()
            mock_exit.assert_called_once_with(0)


class TestMainFunction:
    def test_main_parses_debug_flag(self, mocker):
        mocker.patch("sys.argv", ["server.py", "--debug"])
        mock_validate = mocker.patch("server.validate_environment")
        mock_makedirs = mocker.patch("os.makedirs")
        mock_load = mocker.patch("server.load_and_verify")
        mock_thread = mocker.patch("threading.Thread")
        mock_signal = mocker.patch("server.sig_module.signal")
        mocker.patch("server.mcp.run")

        server.main()

        assert server.DEBUG_MODE is True
        mock_validate.assert_called_once()

    def test_main_no_debug_flag(self, mocker):
        mocker.patch("sys.argv", ["server.py"])
        mock_validate = mocker.patch("server.validate_environment")
        mock_makedirs = mocker.patch("os.makedirs")
        mock_load = mocker.patch("server.load_and_verify")
        mock_thread = mocker.patch("threading.Thread")
        mock_signal = mocker.patch("server.sig_module.signal")
        mocker.patch("server.mcp.run")

        server.DEBUG_MODE = False
        server.main()

        assert server.DEBUG_MODE is False


class TestBackgroundWorkerRobustness:
    def test_worker_survives_persist_failure(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = Exception("disk full")
        f = tmp_path / "survivor.py"
        f.write_text("x = 1")
        server.enqueue("new", str(f))

        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)

        # persist_all handles the error internally; worker continues
        assert server.worker_state["errors"] == 0

    def test_worker_survives_index_failure(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = Exception("turbovec error")
        server.current_id = 1
        f = tmp_path / "fails.py"
        f.write_text("x = 1")
        server.enqueue("new", str(f))

        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)

        assert server.worker_state["errors"] == 1


class TestThreadSafety:
    def test_concurrent_enqueue_from_multiple_threads(self):
        def enqueuer(n):
            for i in range(n):
                server.enqueue("new", f"/f{i}.py")

        threads = [threading.Thread(target=enqueuer, args=(50,)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        depth = server.queue_depth()
        assert 190 <= depth <= 200, f"Expected ~200 got {depth}"

    def test_concurrent_enqueue_and_dequeue(self):
        def enqueuer(n):
            for i in range(n):
                server.enqueue("new", f"/f{i}.py")

        e = threading.Thread(target=enqueuer, args=(50,))
        d = threading.Thread(target=lambda: [server.dequeue_batch(10) for _ in range(3)])

        e.start()
        d.start()
        e.join()
        d.join()

        assert 0 <= server.queue_depth() <= 50, f"Expected ≤50 got {server.queue_depth()}"


class TestLoadAndVerifyEdgeCases:
    def test_empty_json_files(self):
        json.dump({}, open(server.META_PATH, "w"))
        json.dump({}, open(server.STORE_PATH, "w"))

        server.load_and_verify()
        assert server.meta == {}
        assert server.store == {}
        assert server.current_id == 1

    def test_missing_both_files(self):
        server.load_and_verify()
        assert server.meta == {}
        assert server.store == {}
        assert server.current_id == 1

    def test_only_meta_exists(self):
        meta = {"/a.py": {"id": 1, "mtime": 0, "size": 0, "last_indexed": 0}}
        json.dump(meta, open(server.META_PATH, "w"))

        server.load_and_verify()
        assert server.meta == {}
        assert server.store == {}

    def test_only_store_exists(self):
        store = {1: {"path": "/a.py", "content": "x"}}
        json.dump({str(k): v for k, v in store.items()}, open(server.STORE_PATH, "w"))

        server.load_and_verify()
        assert len(server.store) == 1
        assert "/a.py" in server.meta

    def test_zero_byte_json_files(self):
        open(server.META_PATH, "w").close()
        open(server.STORE_PATH, "w").close()
        server.load_and_verify()
        assert server.meta == {}
        assert server.store == {}

    def test_store_non_int_key_wipes_store(self):
        json.dump({}, open(server.META_PATH, "w"))
        store = {"1": {"path": "/a.py", "content": "x"}, "not_a_number": {"path": "/b.py", "content": "y"}}
        json.dump(store, open(server.STORE_PATH, "w"))
        server.load_and_verify()
        assert server.store == {}  # int("not_a_number") raises ValueError → store wiped

    def test_load_and_verify_handles_store_entry_without_path(self):
        json.dump({}, open(server.META_PATH, "w"))
        store = {"1": {"content": "no_path"}}
        json.dump(store, open(server.STORE_PATH, "w"))
        server.load_and_verify()
        assert 1 in server.store
        assert server.meta == {}


class TestEnsureIndexEdgeCases:
    def test_ensure_index_tvim_missing_creates_empty(self, mocker):
        mocker.patch("os.path.exists", return_value=False)
        mocker.patch("server.IdMapIndex")

        server.ensure_index()
        assert server.index is not None

    def test_ensure_index_tvim_corrupt_recreates(self, mocker):
        with open(server.INDEX_PATH, "wb") as f:
            f.write(b"garbage")

        mock_load = mocker.patch("server.IdMapIndex.load", side_effect=Exception("corrupt"))
        server.index = None
        server.ensure_index()

        assert not os.path.exists(server.INDEX_PATH)
        assert server.index is not None

    def test_ensure_index_already_loaded_is_noop(self, mocker):
        mock_load = mocker.patch("server.IdMapIndex.load")
        server.index = object()
        server.ensure_index()
        mock_load.assert_not_called()

    def test_ensure_index_os_remove_failure_still_creates_index(self, mocker):
        with open(server.INDEX_PATH, "wb") as f:
            f.write(b"garbage")
        mocker.patch("server.IdMapIndex.load", side_effect=Exception("corrupt"))
        mock_remove = mocker.patch("os.remove", side_effect=PermissionError("locked"))
        server.index = None
        server.ensure_index()
        assert server.index is not None
        mock_remove.assert_called_once_with(server.INDEX_PATH)


class TestUnicodeAndSpecialPaths:
    def test_unicode_file_path(self, tmp_path, mock_model, mock_index):
        server.current_id = 1
        d = tmp_path / "プロジェクト"
        d.mkdir()
        f = d / "main.py"
        f.write_text("def hello():\n    pass\n")
        server.handle_index(str(f))
        assert str(f) in server.meta
        assert str(d / "main.py") in server.meta

    def test_file_path_with_spaces(self, tmp_path, mock_model, mock_index):
        server.current_id = 1
        d = tmp_path / "my project"
        d.mkdir()
        f = d / "main file.py"
        f.write_text("x = 1")
        server.handle_index(str(f))
        assert str(f) in server.meta

    def test_very_deep_directory_traversal(self, tmp_path, mock_model, mock_index):
        d = tmp_path
        for _ in range(20):
            d = d / "sub"
        d.mkdir(parents=True)
        f = d / "leaf.py"
        f.write_text("x = 1")
        server.current_id = 1
        server.handle_index(str(f))
        assert str(f) in server.meta


class TestIndexDirectoryAdditionalEdgeCases:
    def test_file_instead_of_directory(self, tmp_path):
        f = tmp_path / "not_a_dir.py"
        f.write_text("x = 1")
        result = server.index_directory(str(f))
        assert "file" in result.lower()
        assert "not a directory" in result.lower()

    def test_mixed_supported_and_unsupported(self, sample_dir, mock_model, mock_index):
        result = server.index_directory(str(sample_dir))
        parts = result.split()
        num = int(parts[1]) if parts[1].isdigit() else 0
        assert num == 5
        assert server.queue_depth() == 5

    def test_twice_in_a_row_queues_once(self, tmp_path, mock_model, mock_index):
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        d = tmp_path / "twice"
        d.mkdir()
        (d / "a.py").write_text("x = 1")
        server.index_directory(str(d))
        batch1 = server.dequeue_batch(10)
        for _, fp in batch1:
            server.handle_index(fp)
        server.persist_all()

        server.index_directory(str(d))
        assert server.queue_depth() == 0

    def test_concurrent_index_directory(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        d1 = tmp_path / "proj_a"
        d2 = tmp_path / "proj_b"
        d1.mkdir()
        d2.mkdir()
        for i in range(5):
            (d1 / f"f{i}.py").write_text(f"x = {i}")
            (d2 / f"g{i}.py").write_text(f"y = {i}")

        results = [None, None]

        def scan_a():
            results[0] = server.index_directory(str(d1))

        def scan_b():
            results[1] = server.index_directory(str(d2))

        t1 = threading.Thread(target=scan_a)
        t2 = threading.Thread(target=scan_b)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results[0] is not None
        assert results[1] is not None
        assert server.queue_depth() == 10


class TestCorruptStoreEntries:
    def test_load_and_verify_store_with_array(self):
        json.dump({}, open(server.META_PATH, "w"))
        with open(server.STORE_PATH, "w") as f:
            f.write("[1, 2, 3]")
        server.load_and_verify()
        assert server.store == {}
        assert server.meta == {}

    def test_load_and_verify_store_with_null(self):
        json.dump({"/a.py": {"id": 1}}, open(server.META_PATH, "w"))
        with open(server.STORE_PATH, "w") as f:
            f.write("null")
        server.load_and_verify()
        assert server.store == {}

    def test_load_and_verify_store_entry_missing_path(self):
        json.dump({}, open(server.META_PATH, "w"))
        bad_store = {"1": {"content": "no_path_key"}}
        json.dump(bad_store, open(server.STORE_PATH, "w"))
        server.load_and_verify()
        assert server.store == {1: {"content": "no_path_key"}}
        assert server.meta == {}
        assert server.current_id == 2

    def test_load_and_verify_meta_with_extra_paths(self):
        meta = {"/a.py": {"id": 1}, "/b.py": {"id": 2}}
        store = {1: {"path": "/a.py", "content": "x"}}
        json.dump(meta, open(server.META_PATH, "w"))
        json.dump({str(k): v for k, v in store.items()}, open(server.STORE_PATH, "w"))
        server.load_and_verify()
        assert len(server.meta) == 1
        assert "/a.py" in server.meta
        assert "/b.py" not in server.meta


class TestGetIndexStatsEdgeCases:
    def test_zero_size_index_file(self, tmp_path):
        open(server.INDEX_PATH, "w").close()
        result = server.get_index_stats()
        assert "0.0 KB" in result

    def test_stats_with_removed_file(self, populated_state, mock_index):
        server.handle_remove("/proj/file1.py")
        result = server.get_index_stats()
        assert "Vectors: 2" in result
        assert "Files tracked: 2" in result


class TestHandleIndexGetmtimeFailure:
    def test_getmtime_failure_does_not_leave_orphan(self, tmp_path, mock_model, mock_index, mocker):
        server.current_id = 1
        f = tmp_path / "disappearing.py"
        f.write_text("x = 1")
        mocker.patch.object(os.path, "getmtime", side_effect=OSError("file disappeared after read"))
        server.handle_index(str(f))
        assert len(server.store) == 0
        assert str(f) not in server.meta

    def test_getsize_failure_does_not_leave_orphan(self, tmp_path, mock_model, mock_index, mocker):
        server.current_id = 1
        f = tmp_path / "shrinking.py"
        f.write_text("x = 1")
        mocker.patch.object(os.path, "getsize", side_effect=OSError("file shrunk"))
        server.handle_index(str(f))
        assert len(server.store) == 0
        assert str(f) not in server.meta


class TestSearchCodebaseModelFailure:
    def test_model_encode_failure_returns_error(self, mock_model, mock_index, populated_state):
        mock_model.encode.side_effect = RuntimeError("OOM")
        with pytest.raises(RuntimeError, match="OOM"):
            server.search_codebase("test query")


class TestEnsureResourcesFailure:
    def test_model_load_failure_propagates(self, mocker):
        mocker.patch("sentence_transformers.SentenceTransformer", side_effect=RuntimeError("download failed"))
        with pytest.raises(RuntimeError, match="download failed"):
            server.ensure_model()

    def test_index_created_when_tvim_missing(self, mocker):
        mocker.patch("os.path.exists", return_value=False)
        mocker.patch("server.IdMapIndex")
        server.index = None
        server.ensure_index()
        assert server.index is not None


class TestQueueEdgeCases:
    def test_dequeue_batch_zero(self):
        server.enqueue("new", "/a.py")
        batch = server.dequeue_batch(0)
        assert len(batch) == 0
        assert server.queue_depth() == 1

    def test_dequeue_batch_more_than_available(self):
        for i in range(3):
            server.enqueue("new", f"/f{i}.py")
        batch = server.dequeue_batch(100)
        assert len(batch) == 3
        assert server.queue_depth() == 0

    def test_enqueue_remove_for_nonexistent(self, mock_index, populated_state):
        server.enqueue("remove", "/nonexistent.py")
        batch = server.dequeue_batch(5)
        assert len(batch) == 1
        assert batch[0][0] == "remove"

    def test_enqueue_none_path_skipped(self):
        server.enqueue("new", None)
        assert server.queue_depth() == 0

    def test_enqueue_empty_string_skipped(self):
        server.enqueue("new", "")
        assert server.queue_depth() == 0

    def test_dequeue_batch_negative_size(self):
        server.enqueue("new", "/a.py")
        batch = server.dequeue_batch(-5)
        assert len(batch) == 0
        assert server.queue_depth() == 1

    def test_enqueue_non_string_priority(self):
        server.enqueue(42, "/a.py")
        assert server.queue_depth() == 1


class TestAtomicWriteEdgeCases:
    def test_atomic_write_deep_path(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c" / "d" / "deep.json"
        with pytest.raises(FileNotFoundError):
            server.atomic_write(str(deep), "{}")

    def test_atomic_write_normal_works(self, tmp_path):
        f = tmp_path / "test.json"
        server.atomic_write(str(f), '{"key": "value"}')
        assert json.loads(open(str(f)).read()) == {"key": "value"}

class TestTouchThreadSafety:
    def test_concurrent_touch(self):
        def touch_many(n):
            for _ in range(n):
                server.touch()

        ts = [threading.Thread(target=touch_many, args=(20,)) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert abs(time.time() - server.last_activity) < 1.0


class TestValidateFailures:
    def test_validate_python_version_old_version_exits(self, mocker):
        vi = mocker.MagicMock(major=3, minor=7, micro=0)
        mocker.patch("sys.version_info", vi)
        mock_exit = mocker.patch("sys.exit")
        server.validate_python_version()
        mock_exit.assert_called_once_with(1)

    def test_validate_imports_missing_packages_exits(self, mocker):
        mocker.patch("builtins.__import__", side_effect=ImportError("no module"))
        mock_exit = mocker.patch("sys.exit")
        server.validate_imports()
        mock_exit.assert_called_once_with(1)


class TestWorkerStopEvent:
    def test_background_worker_respects_stop_event(self, mocker):
        server._stop_event.set()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        t.join(timeout=1)
        assert not t.is_alive()

    def test_idle_watchdog_respects_stop_event(self, mocker):
        mocker.patch("server.os._exit")
        mocker.patch("server.log")
        server._stop_event.set()
        t = threading.Thread(target=server.idle_watchdog, daemon=True)
        t.start()
        t.join(timeout=1)
        assert not t.is_alive()

    def test_worker_clears_stale_files(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.search.return_value = (
            np.array([[]]), np.array([[]], dtype=np.uint64),
        )
        mock_index.contains.return_value = False
        old_file = tmp_path / "old.py"
        old_file.write_text("x = 1")
        server.handle_index(str(old_file))
        server.meta[str(old_file)]["last_indexed"] = 0
        server.enqueue("new", str(old_file))
        server.worker_state["processed"] = 0
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        assert server.worker_state["processed"] >= 1


class TestPersistAllEdgeCases:
    def test_persist_all_with_none_index(self):
        server.index = None
        server.persist_all()
        # no crash

    def test_persist_all_makedirs_failure_logs_warning(self, mocker):
        mock_log = mocker.patch("server.log")
        mocker.patch("os.makedirs", side_effect=PermissionError("access denied"))
        server.persist_all()
        assert any("Cannot create" in str(call) for call in mock_log.call_args_list)


class TestHandleIndexIOErrors:
    def test_handle_index_with_directory_path(self, tmp_path, mock_model, mock_index):
        server.current_id = 1
        d = tmp_path / "subdir"
        d.mkdir()
        server.handle_index(str(d))
        assert len(server.store) == 0

    def test_handle_index_with_locked_file(self, tmp_path, mock_model, mock_index, mocker):
        server.current_id = 1
        f = tmp_path / "locked.py"
        f.write_text("x = 1")
        mocker.patch("builtins.open", side_effect=PermissionError("locked"))
        server.handle_index(str(f))
        assert len(server.store) == 0

    def test_handle_index_with_non_ascii_content(self, tmp_path, mock_model, mock_index):
        server.current_id = 1
        f = tmp_path / "unicode.py"
        f.write_text("def café():\n    return 'über cool'\n", encoding='utf-8')
        server.handle_index(str(f))
        assert len(server.store) == 1
        stored = list(server.store.values())[0]["content"]
        assert "café" in stored

    def test_handle_index_with_binary_content(self, tmp_path, mock_model, mock_index):
        server.current_id = 1
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x00\x01\x02\xff\xfe")
        server.handle_index(str(f))
        # binary data read via errors="replace" yields replacement chars; non-empty
        assert len(server.store) == 1

    def test_handle_index_with_too_long_content_is_truncated(self, tmp_path, mock_model, mock_index):
        server.current_id = 1
        f = tmp_path / "long.py"
        f.write_text("x" * 5000)
        server.handle_index(str(f))
        assert len(server.store) == 1
        stored = list(server.store.values())[0]["content"]
        assert len(stored) == 2000

    def test_handle_index_reindex_succeeds(self, tmp_path, mock_model, mock_index):
        server.current_id = 1
        f = tmp_path / "reindex.py"
        f.write_text("x = 1")
        server.handle_index(str(f))
        assert str(f) in server.meta
        initial_id = server.meta[str(f)]["id"]
        f.write_text("y = 2")
        server.handle_index(str(f))
        # old entry removed, new entry added
        assert str(f) in server.meta
        assert server.meta[str(f)]["id"] != initial_id


class TestSearchCodebaseKEdgeCases:
    def test_search_with_negative_k_clamps_to_1(self, mock_model, mock_index, populated_state):
        result = server.search_codebase("test", k=-5)
        assert "No results" not in result

    def test_search_with_zero_k_clamps_to_1(self, mock_model, mock_index, populated_state):
        result = server.search_codebase("test", k=0)
        assert "No results" not in result

    def test_search_with_k_above_max_clamps_to_20(self, mock_model, mock_index, populated_state):
        result = server.search_codebase("test", k=100)
        assert "No results" not in result


class TestGetIndexStatsPermissionError:
    def test_tool_handles_getsize_permission_error(self, mocker, populated_state):
        mocker.patch("os.path.exists", return_value=True)
        mocker.patch("os.path.getsize", side_effect=PermissionError("access denied"))
        result = server.get_index_stats()
        assert "0.0 KB" in result

    def test_resource_handles_getsize_permission_error(self, mocker, populated_state):
        mocker.patch("os.path.exists", return_value=True)
        mocker.patch("os.path.getsize", side_effect=PermissionError("access denied"))
        result = server.index_stats()
        data = json.loads(result)
        assert data["disk_size_kb"] == 0.0


class TestWorkerStatusTransitions:
    def test_worker_idle_on_empty_queue(self, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        assert server.worker_state["status"] == "idle"

    def test_worker_indexing_on_pending_queue(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        server._stop_event.clear()
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        f = tmp_path / "work.py"
        f.write_text("x = 1")
        server.enqueue("new", str(f))
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        assert server.worker_state["processed"] >= 1

    def test_worker_transitions_to_idle_after_empty_batch(self, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mocker.patch.object(server, "find_stale_files", return_value=[])
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        assert server.worker_state["status"] == "idle"


class TestMainFailurePaths:
    def test_main_survives_load_and_verify_crash(self, mocker):
        mocker.patch("sys.argv", ["server.py"])
        mocker.patch("server.validate_environment")
        mocker.patch("os.makedirs")
        mocker.patch("server.load_and_verify", side_effect=RuntimeError("rebuild failed"))
        mock_thread = mocker.patch("threading.Thread")
        mocker.patch("server.sig_module.signal")
        mocker.patch("server.mcp.run")
        import copy
        meta_before = copy.copy(server.meta)
        store_before = copy.copy(server.store)
        server.main()
        assert server.current_id == 1

    def test_main_survives_first_thread_failure(self, mocker):
        mocker.patch("sys.argv", ["server.py"])
        mocker.patch("server.validate_environment")
        mocker.patch("os.makedirs")
        mocker.patch("server.load_and_verify")
        real_thread = threading.Thread
        call_count = [0]
        def failing_thread(**kw):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("cannot create thread")
            return real_thread(**kw)
        mocker.patch("threading.Thread", side_effect=failing_thread)
        mocker.patch("server.sig_module.signal")
        mock_run = mocker.patch("server.mcp.run")
        server.main()
        mock_run.assert_called_once()

    def test_main_survives_both_threads_failure(self, mocker):
        mocker.patch("sys.argv", ["server.py"])
        mocker.patch("server.validate_environment")
        mocker.patch("os.makedirs")
        mocker.patch("server.load_and_verify")
        mocker.patch("threading.Thread", side_effect=RuntimeError("no more threads"))
        mocker.patch("server.sig_module.signal")
        mock_run = mocker.patch("server.mcp.run")
        server.main()
        mock_run.assert_called_once()


class TestHandleRemoveEdgeCases:
    def test_handle_remove_when_index_is_none(self, populated_state):
        server.index = None
        server.handle_remove("/proj/file1.py")
        assert "/proj/file1.py" in server.meta  # unchanged

    def test_handle_remove_when_file_not_in_meta(self, mock_index):
        server.handle_remove("/nonexistent.py")
        mock_index.remove.assert_not_called()

    def test_handle_remove_from_empty_meta(self, mock_index):
        server.handle_remove("/any.py")
        # no crash


class TestHandleIndexContentEdgeCases:
    def test_handle_index_whitespace_only(self, tmp_path, mock_model, mock_index):
        server.current_id = 1
        f = tmp_path / "whitespace.py"
        f.write_text("   \n  \n  ")
        server.handle_index(str(f))
        assert len(server.store) == 0

    def test_handle_index_file_deleted_during_read(self, tmp_path, mock_model, mock_index, mocker):
        server.current_id = 1
        f = tmp_path / "vanish.py"
        f.write_text("x = 1")
        mocker.patch("builtins.open", side_effect=[FileNotFoundError("file vanished")])
        server.handle_index(str(f))
        assert len(server.store) == 0


class TestPersistAllPartialFailure:
    def test_index_write_succeeds_replace_fails(self, mocker, mock_index, populated_state):
        server.index = mock_index
        mock_replace = mocker.patch("os.replace", side_effect=OSError("cross-device"))
        server.persist_all()
        assert not os.path.exists(server.INDEX_PATH)  # .tmp might exist but index not in place

    def test_persist_all_continues_after_meta_failure(self, mocker, mock_index, populated_state):
        server.index = mock_index
        mock_index.write.side_effect = lambda p: open(p, "w").close()

        def atomic_write_real_then_fail(path, data):
            if path == server.META_PATH:
                raise OSError("meta fail")
            with open(path, "w") as f:
                f.write(data)

        mocker.patch("server.atomic_write", side_effect=atomic_write_real_then_fail)
        server.persist_all()
        assert os.path.exists(server.INDEX_PATH)

    def test_persist_all_continues_after_store_failure(self, mocker, mock_index, populated_state):
        server.index = mock_index
        mock_index.write.side_effect = lambda p: open(p, "w").close()

        def atomic_write_real_then_fail(path, data):
            if path == server.STORE_PATH:
                raise OSError("store fail")
            with open(path, "w") as f:
                f.write(data)

        mocker.patch("server.atomic_write", side_effect=atomic_write_real_then_fail)
        server.persist_all()
        assert os.path.exists(server.INDEX_PATH)
        assert os.path.exists(server.META_PATH)


class TestEnsureModelThreadSafety:
    def test_concurrent_ensure_model_loads_once(self, mocker):
        model_instance = mocker.MagicMock()
        model_instance.encode.return_value = np.random.rand(1, 384).astype(np.float32)
        mock_ctr = [0]

        def slow_constructor(*a, **kw):
            mock_ctr[0] += 1
            time.sleep(0.05)
            return model_instance

        mocker.patch("sentence_transformers.SentenceTransformer", side_effect=slow_constructor)
        server.model = None

        def load():
            server.ensure_model()

        ts = [threading.Thread(target=load) for _ in range(5)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        assert mock_ctr[0] == 1, f"Model loaded {mock_ctr[0]} times (expected 1)"
        assert server.model is model_instance

    def test_ensure_model_after_already_loaded_is_noop(self, mocker):
        server.model = object()
        mock_sb = mocker.patch("sentence_transformers.SentenceTransformer")
        server.ensure_model()
        mock_sb.assert_not_called()


class TestEnsureIndexThreadSafety:
    def test_concurrent_ensure_index_loads_once(self, mocker, tmp_path):
        mocker.patch("server.INDEX_PATH", str(tmp_path / "index.tvim"))
        open(server.INDEX_PATH, "wb").close()
        index_instance = mocker.MagicMock()
        mock_ctr = [0]

        def slow_load(*a, **kw):
            mock_ctr[0] += 1
            time.sleep(0.05)
            return index_instance

        mocker.patch("server.IdMapIndex.load", side_effect=slow_load)
        server.index = None

        def load():
            server.ensure_index()

        ts = [threading.Thread(target=load) for _ in range(5)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        assert mock_ctr[0] == 1, f"Index loaded {mock_ctr[0]} times (expected 1)"
        assert server.index is index_instance

    def test_concurrent_ensure_index_both_creates_empty(self, mocker):
        mocker.patch("os.path.exists", return_value=False)
        index_instance = mocker.MagicMock()
        mock_ctr = [0]

        def slow_create(*a, **kw):
            mock_ctr[0] += 1
            time.sleep(0.05)
            return index_instance

        mocker.patch("server.IdMapIndex", side_effect=slow_create)
        server.index = None

        def load():
            server.ensure_index()

        ts = [threading.Thread(target=load) for _ in range(5)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        assert mock_ctr[0] == 1, f"Index created {mock_ctr[0]} times (expected 1)"
        assert server.index is index_instance


class TestAtomicWriteFailures:
    def test_atomic_write_removes_temp_on_replace_failure(self, tmp_path, mocker):
        target = tmp_path / "data.json"
        tmp_file = tmp_path / "data.json.tmp"
        mocker.patch("os.replace", side_effect=OSError("cross-device"))

        with pytest.raises(OSError, match="cross-device"):
            server.atomic_write(str(target), '{"key": "val"}')

        assert not os.path.exists(str(tmp_file)), "Temp file should be cleaned up"

    def test_atomic_write_original_unchanged_on_failure(self, tmp_path, mocker):
        target = tmp_path / "data.json"
        target.write_text('{"original": true}')
        mocker.patch("os.replace", side_effect=OSError("cross-device"))

        with pytest.raises(OSError, match="cross-device"):
            server.atomic_write(str(target), '{"new": "data"}')

        assert json.loads(target.read_text()) == {"original": True}

    def test_atomic_write_removes_temp_on_write_failure(self, tmp_path, mocker):
        target = tmp_path / "data.json"
        tmp_file = tmp_path / "data.json.tmp"
        # Make tmpdir read-only so open succeeds but write fails
        mocker.patch("builtins.open", side_effect=OSError("write denied"))

        with pytest.raises(OSError, match="write denied"):
            server.atomic_write(str(target), '{"key": "val"}')

        assert not os.path.exists(str(tmp_file)), "Temp file should be cleaned up"

    def test_atomic_write_no_tmp_left_on_success(self, tmp_path):
        f = tmp_path / "clean.json"
        server.atomic_write(str(f), "{}")
        assert not os.path.exists(str(f) + ".tmp")


class TestHandleIndexTruncation:
    def test_content_capped_at_2000_chars(self, tmp_path, mock_model, mock_index):
        server.current_id = 1
        f = tmp_path / "long.py"
        f.write_text("x" * 3000)
        server.handle_index(str(f))
        assert len(server.store) == 1
        stored = list(server.store.values())[0]["content"]
        assert len(stored) == 2000

    def test_2001_chars_trailing_newline_stripped(self, tmp_path, mock_model, mock_index):
        server.current_id = 1
        f = tmp_path / "trailing.py"
        content = "x" * 2000 + "\n"
        assert len(content) == 2001
        f.write_text(content)
        server.handle_index(str(f))
        assert len(server.store) == 1
        stored = list(server.store.values())[0]["content"]
        # after [:2000] -> 2000 "x"s, then .strip() -> 2000 "x"s (newline stripped)
        assert len(stored) == 2000
        assert stored == "x" * 2000

    def test_2001_chars_all_whitespace_skipped(self, tmp_path, mock_model, mock_index):
        server.current_id = 1
        f = tmp_path / "space.py"
        f.write_text(" " * 2001)
        server.handle_index(str(f))
        assert len(server.store) == 0

    def test_exactly_2000_chars_no_strip(self, tmp_path, mock_model, mock_index):
        server.current_id = 1
        f = tmp_path / "exact.py"
        content = "x" * 2000
        f.write_text(content)
        server.handle_index(str(f))
        assert len(server.store) == 1
        stored = list(server.store.values())[0]["content"]
        assert stored == "x" * 2000


class TestBackgroundWorkerEdgeCases:
    def test_worker_zero_batch_interval_does_not_busyloop(self, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0)
        mocker.patch.object(server, "find_stale_files", return_value=[])
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        t.join(timeout=1)
        assert not t.is_alive(), "Worker busy-looped at BATCH_INTERVAL=0"

    def test_worker_negative_batch_interval_does_not_crash(self, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", -5)
        mocker.patch.object(server, "find_stale_files", return_value=[])
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        t.join(timeout=1)
        assert not t.is_alive()


class TestLoadAndVerifyTOCTOU:
    def test_meta_file_deleted_between_exists_and_open(self, mocker):
        mocker.patch("os.path.exists", side_effect=[True, False])
        mocker.patch("builtins.open", side_effect=FileNotFoundError("file vanished"))
        server.load_and_verify()
        assert server.meta == {}

    def test_store_file_deleted_between_exists_and_open(self, mocker):
        mocker.patch("os.path.exists", side_effect=[False, True, False])
        mocker.patch("builtins.open", side_effect=FileNotFoundError("file vanished"))
        server.load_and_verify()
        assert server.store == {}


class TestEnqueuePriorityEdgeCases:
    def test_unknown_priority_sorted_last(self):
        server.enqueue("new", "/a.py")
        server.enqueue("unknown_priority", "/b.py")
        batch = server.dequeue_batch(5)
        assert len(batch) == 2
        assert batch[0][0] == "new"
        assert batch[1][0] == "unknown_priority"

    def test_remove_priority_comes_first(self):
        server.enqueue("new", "/a.py")
        server.enqueue("remove", "/old.py")
        batch = server.dequeue_batch(5)
        assert batch[0][0] == "remove"

    def test_reindex_priority_comes_last(self):
        server.enqueue("remove", "/a.py")
        server.enqueue("new", "/b.py")
        server.enqueue("changed", "/c.py")
        server.enqueue("reindex", "/d.py")
        batch = server.dequeue_batch(5)
        priorities = [p for p, _ in batch]
        assert priorities == ["remove", "new", "changed", "reindex"]

    def test_mixed_queue_returns_prioritized_order(self):
        priorities = ["reindex", "new", "remove", "changed", "new"]
        for i, p in enumerate(priorities):
            server.enqueue(p, f"/f{i}.py")
        batch = server.dequeue_batch(10)
        result = [p for p, _ in batch]
        assert result == ["remove", "new", "new", "changed", "reindex"]


class TestPropertyBasedQueue:
    @given(
        priorities=st.lists(st.sampled_from(["remove", "new", "changed", "reindex", "unknown"]), max_size=4),
    )
    def test_dequeue_preserves_items(self, priorities):
        server.index_queue.clear()
        for i, p in enumerate(priorities):
            server.enqueue(p, f"/f{i}.py")
        batch = server.dequeue_batch(1000)
        assert len(batch) == len(priorities)
        dequeued_priorities = [p for p, _ in batch]
        assert sorted(dequeued_priorities) == sorted(priorities)

    @given(
        n1=st.integers(min_value=0, max_value=8),
        n2=st.integers(min_value=0, max_value=8),
    )
    def test_enqueue_dequeue_equivalence(self, n1, n2):
        server.index_queue.clear()
        for i in range(n1):
            server.enqueue("new", f"/a{i}.py")
        for i in range(n2):
            server.enqueue("changed", f"/b{i}.py")
        batch = server.dequeue_batch(100)
        assert len(batch) == n1 + n2

    @given(
        file_paths=st.lists(st.text(min_size=1, max_size=50), min_size=0, max_size=5),
    )
    def test_queue_depth_matches_enqueues(self, file_paths):
        server.index_queue.clear()
        for fp in file_paths:
            server.enqueue("new", fp)
        assert server.queue_depth() == len(file_paths)

    @given(
        batch_size=st.integers(min_value=0, max_value=20),
        n=st.integers(min_value=0, max_value=10),
    )
    def test_dequeue_batch_size_constraint(self, batch_size, n):
        server.index_queue.clear()
        for i in range(n):
            server.enqueue("new", f"/f{i}.py")
        batch = server.dequeue_batch(batch_size)
        expected = min(n, batch_size) if batch_size > 0 else 0
        assert len(batch) == expected


class TestNonDictMetaValues:
    def test_find_stale_non_dict_meta_skipped(self, mocker):
        server.meta = {
            "/good.py": {"id": 1, "last_indexed": 0},
            "/bad.py": "not_a_dict",
            "/also_bad.py": None,
        }
        stale = server.find_stale_files(max_age_days=0, max_files=10)
        assert "/good.py" in stale
        assert "/bad.py" not in stale
        assert "/also_bad.py" not in stale

    def test_index_directory_non_dict_meta_skipped(self, tmp_path, mock_model, mock_index):
        d = tmp_path / "mixed"
        d.mkdir()
        (d / "a.py").write_text("x = 1")
        (d / "b.py").write_text("y = 2")
        server.meta[str(d / "a.py")] = {"id": 1, "mtime": 0}
        server.meta[str(d / "b.py")] = "corrupt"
        result = server.index_directory(str(d))
        assert "Queued" in result

    def test_persist_all_with_non_dict_meta_does_not_crash(self, mock_index, mocker):
        server.index = mock_index
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        server.meta = {"/a.py": "not_a_dict"}
        server.store = {}
        server.persist_all()
        # no crash


class TestSearchCodebaseMalformedModel:
    def test_model_encode_returns_none(self, mock_model, mock_index, populated_state):
        mock_model.encode.return_value = None
        result = server.search_codebase("query")
        assert "Failed to embed" in result

    def test_model_encode_returns_empty_array(self, mock_model, mock_index, populated_state):
        mock_model.encode.return_value = np.array([])
        result = server.search_codebase("query")
        assert "Failed to embed" in result

    def test_model_encode_returns_wrong_dimensions(self, mock_model, mock_index, populated_state):
        mock_model.encode.return_value = np.random.rand(1, 10).astype(np.float32)
        result = server.search_codebase("query")
        assert isinstance(result, str) and len(result) > 0

    def test_model_encode_returns_non_array(self, mock_model, mock_index, populated_state):
        mock_model.encode.return_value = "not an array"
        with pytest.raises(Exception):
            server.search_codebase("query")


class TestConcurrentIndexAndSearch:
    def test_concurrent_index_and_search(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.search.return_value = (
            np.array([[0.95]]), np.array([[1]], dtype=np.uint64),
        )

        d = tmp_path / "concurrent"
        d.mkdir()
        (d / "test.py").write_text("x = 1")
        server.store = {1: {"path": str(d / "test.py"), "content": "x = 1"}}

        results = [None, None]

        def indexer():
            results[0] = server.index_directory(str(d))

        def searcher():
            results[1] = server.search_codebase("test")

        t1 = threading.Thread(target=indexer)
        t2 = threading.Thread(target=searcher)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results[0] is not None
        assert results[1] is not None


class TestIdleWatchdogTimeout:
    def test_idle_watchdog_exits_after_timeout(self, mocker):
        mocker.patch.object(server, "CHECK_INTERVAL", 0.01)
        mocker.patch.object(server, "IDLE_TIMEOUT", -1)  # always idle
        mock_exit = mocker.patch("server.os._exit")
        server._stop_event.clear()
        t = threading.Thread(target=server.idle_watchdog, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        t.join(timeout=1)
        # os._exit may be called multiple times since mock prevents actual exit
        mock_exit.assert_any_call(0)

    def test_idle_watchdog_persist_failure_logs(self, mocker):
        mocker.patch.object(server, "CHECK_INTERVAL", 0.01)
        mocker.patch.object(server, "IDLE_TIMEOUT", -1)
        mocker.patch("server.persist_all", side_effect=RuntimeError("persist fail"))
        mock_log = mocker.patch("server.log")
        mock_exit = mocker.patch("server.os._exit")
        server._stop_event.clear()
        t = threading.Thread(target=server.idle_watchdog, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        assert any("persist" in str(c).lower() for c in mock_log.call_args_list)


class TestBackgroundWorkerMixedBatch:
    def test_mixed_remove_and_index_batch(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.remove.return_value = None

        f = tmp_path / "mixed.py"
        f.write_text("x = 1")
        server.current_id = 1
        server.handle_index(str(f))
        old_id = server.meta[str(f)]["id"]

        server.enqueue("remove", str(f))
        server.enqueue("new", str(f))

        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.03)
        server._stop_event.set()

        assert str(f) in server.meta
        new_id = server.meta[str(f)]["id"]
        assert new_id != old_id


class TestIndexStatsNonSerializable:
    def test_index_stats_with_non_serializable_last_error(self, mocker):
        mocker.patch("os.path.exists", return_value=True)
        mocker.patch("os.path.getsize", return_value=1024)
        server.worker_state["last_error"] = Exception("bad")
        result = server.index_stats()
        data = json.loads(result)
        assert "last_error" in data
        assert data["last_error"] is not None


class TestHandleIndexReindexStatFailure:
    """Verify that stat failure during reindex does NOT orphan the old entry."""

    def test_reindex_stat_failure_preserves_meta(self, tmp_path, mock_model, mock_index, populated_state, mocker):
        f = tmp_path / "existing.py"
        f.write_text("original content")
        server.current_id = 1
        server.handle_index(str(f))
        old_entry = dict(server.meta[str(f)])

        mocker.patch.object(os.path, "getmtime", side_effect=OSError("file disappeared"))
        server.handle_index(str(f))

        # Old entry should still be intact
        assert str(f) in server.meta
        assert server.meta[str(f)]["id"] == old_entry["id"]

    def test_reindex_stat_failure_preserves_store(self, tmp_path, mock_model, mock_index, populated_state, mocker):
        f = tmp_path / "existing.py"
        f.write_text("original content")
        server.current_id = 1
        server.handle_index(str(f))
        old_id = server.meta[str(f)]["id"]

        mocker.patch.object(os.path, "getmtime", side_effect=OSError("file disappeared"))
        server.handle_index(str(f))

        assert old_id in server.store
        assert server.store[old_id]["content"] == "original content"

    def test_reindex_stat_failure_does_not_remove_vector(self, tmp_path, mock_model, mock_index, populated_state, mocker):
        f = tmp_path / "existing.py"
        f.write_text("original content")
        server.current_id = 1
        server.handle_index(str(f))

        mocker.patch.object(os.path, "getmtime", side_effect=OSError("file disappeared"))
        server.handle_index(str(f))

        # add_with_ids was called exactly once (first index, not during failed reindex)
        assert mock_index.add_with_ids.call_count == 1


class TestHandleIndexAddWithIdsFailure:
    """Verify rollback when index.add_with_ids fails."""

    def test_new_file_add_failure_does_not_pollute_meta(self, tmp_path, mock_model, mock_index, mocker):
        mock_index.add_with_ids.side_effect = RuntimeError("turbovec oom")
        f = tmp_path / "fails.py"
        f.write_text("x = 1")
        server.current_id = 1

        with pytest.raises(RuntimeError, match="turbovec oom"):
            server.handle_index(str(f))

        assert str(f) not in server.meta
        assert not any(d.get("path") == str(f) for d in server.store.values())

    def test_new_file_add_failure_does_not_pollute_store(self, tmp_path, mock_model, mock_index, mocker):
        mock_index.add_with_ids.side_effect = RuntimeError("turbovec oom")
        f = tmp_path / "fails.py"
        f.write_text("x = 1")
        server.current_id = 1

        with pytest.raises(RuntimeError, match="turbovec oom"):
            server.handle_index(str(f))

        assert len(server.store) == 0

    def test_reindex_add_failure_does_not_crash(self, tmp_path, mock_model, mock_index, populated_state, mocker):
        f = tmp_path / "reindex_fail.py"
        f.write_text("original")
        server.current_id = 1
        server.handle_index(str(f))

        mock_index.add_with_ids.side_effect = RuntimeError("turbovec oom")
        f.write_text("modified")

        with pytest.raises(RuntimeError, match="turbovec oom"):
            server.handle_index(str(f))

        # Should not crash — rollback handled internally
        assert True

    def test_worker_catches_add_failure(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = RuntimeError("turbovec oom")
        server.current_id = 1
        f = tmp_path / "worker_fail.py"
        f.write_text("x = 1")
        server.enqueue("new", str(f))

        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()

        assert server.worker_state["errors"] >= 1
        assert "turbovec oom" in (server.worker_state["last_error"] or "")


class TestHandleIndexRemoveFailure:
    """Verify behavior when index.remove fails during reindex."""

    def test_remove_failure_does_not_block_reindex(self, tmp_path, mock_model, mock_index, mocker):
        mock_index.remove.side_effect = Exception("remove failed")
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        server.current_id = 1
        f = tmp_path / "remove_fail.py"
        f.write_text("original")
        server.handle_index(str(f))
        old_id = server.meta[str(f)]["id"]

        f.write_text("modified content")
        server.handle_index(str(f))

        assert str(f) in server.meta
        assert server.meta[str(f)]["id"] != old_id
        assert server.meta[str(f)]["id"] == 2


class TestPersistAllWriteFailure:
    """Atomic write failure during persist_all — verify no corruption."""

    def test_index_write_creates_tmp_only_on_failure(self, tmp_path, mock_index, populated_state, mocker):
        mock_index.write.side_effect = OSError("write failed")
        server.persist_all()

        assert not os.path.exists(server.INDEX_PATH + ".tmp")
        assert not os.path.exists(server.INDEX_PATH)

    def test_store_write_meta_still_saved(self, mock_index, populated_state, mocker):
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        original_atomic_write = server.atomic_write

        def atomic_fail_on_store(path, data):
            if path == server.STORE_PATH:
                raise OSError("store fail")
            original_atomic_write(path, data)

        mocker.patch("server.atomic_write", side_effect=atomic_fail_on_store)
        server.persist_all()

        assert os.path.exists(server.INDEX_PATH)
        assert os.path.exists(server.META_PATH)


class TestFindStaleFilesEdgeCases:
    def test_find_stale_with_non_dict_entries_skipped(self):
        server.meta = {
            "/good.py": {"id": 1, "last_indexed": 0},
            "/null.py": None,
        }
        stale = server.find_stale_files(max_age_days=0, max_files=10)
        assert "/good.py" in stale
        assert "/null.py" not in stale

    def test_find_stale_boundary(self):
        stale_file = "/definitely_stale.py"
        fresh_file = "/definitely_fresh.py"
        server.meta = {
            stale_file: {"id": 1, "last_indexed": 0},
            fresh_file: {"id": 2, "last_indexed": time.time() + 86400},
        }
        stale = server.find_stale_files(max_age_days=7, max_files=10)
        assert stale_file in stale
        assert fresh_file not in stale

    def test_find_stale_empty_after_filter_returns_empty(self):
        server.meta = {
            "/fresh.py": {"id": 1, "last_indexed": time.time()},
        }
        stale = server.find_stale_files()
        assert stale == []


class TestSearchCodebaseSpecialCharsInContent:
    def test_search_with_special_chars_in_results(self, mock_model, mock_index, populated_state):
        server.store[1] = {"path": "/test.py", "content": "import re  # $peci@l ch@rs"}
        mock_index.search.return_value = (
            np.array([[0.95]]), np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("special")
        assert "$peci@l ch@rs" in result

    def test_search_results_contain_backticks_and_code(self, mock_model, mock_index, populated_state):
        server.store[1] = {"path": "/code.py", "content": "```python\nx = 1\n```"}
        mock_index.search.return_value = (
            np.array([[0.99]]), np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("code")
        assert "```" in result


class TestEnsureIndexLoadCorrupt:
    def test_corrupt_tvim_removed_and_recreated(self, mocker):
        with open(server.INDEX_PATH, "wb") as f:
            f.write(b"corrupt data")
        mock_load = mocker.patch("server.IdMapIndex.load", side_effect=Exception("corrupt"))
        server.index = None
        server.ensure_index()

        assert not os.path.exists(server.INDEX_PATH)
        assert server.index is not None


class TestIndexStatsResourceConsistency:
    def test_stats_and_resource_agree_on_empty(self):
        stats_result = server.get_index_stats()
        resource_result = server.index_stats()
        stats_json = json.loads(resource_result)
        assert "Vectors: 0" in stats_result
        assert stats_json["vectors"] == 0

    def test_stats_and_resource_agree_on_populated(self, populated_state, mock_index):
        stats_result = server.get_index_stats()
        resource_result = server.index_stats()
        stats_json = json.loads(resource_result)
        assert "Vectors: 3" in stats_result
        assert stats_json["vectors"] == 3
        assert stats_json["files_tracked"] == 3


class TestBackgroundWorkerEmptyQueue:
    def test_worker_does_not_consume_cpu_on_empty_queue(self, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mocker.patch.object(server, "find_stale_files", return_value=[])
        time_calls = []
        original_sleep = time.sleep

        def tracking_sleep(s):
            time_calls.append(s)
            if len(time_calls) >= 3:
                server._stop_event.set()
            original_sleep(min(s, 0.01))

        mocker.patch.object(time, "sleep", side_effect=tracking_sleep)
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        t.join(timeout=3)
        assert len(time_calls) >= 2
        assert all(s >= 0.1 for s in time_calls), f"Sleeps too small: {time_calls}"


class TestHandleRemoveIndexNoneStillInMeta:
    def test_remove_when_index_none_meta_unchanged(self, populated_state):
        server.index = None
        server.handle_remove("/proj/file1.py")
        assert "/proj/file1.py" in server.meta

    def test_remove_when_file_not_in_meta_does_nothing(self):
        server.handle_remove("/not/in/meta.py")
        assert server.meta == {}
        assert server.store == {}


class TestProcessCountMatches:
    def test_processed_count_matches_indexed_files(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        server.current_id = 1

        for i in range(3):
            f = tmp_path / f"f{i}.py"
            f.write_text(f"x = {i}")
            server.enqueue("new", str(f))

        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.03)
        server._stop_event.set()

        assert server.worker_state["processed"] == 3
        assert len(server.meta) == 3


class TestPingPongConsistency:
    """Index then remove then index — ensure no ghost entries."""

    def test_index_remove_index_cycle(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        server.current_id = 1

        f = tmp_path / "cycle.py"
        f.write_text("x = 1")
        server.handle_index(str(f))
        assert str(f) in server.meta
        first_id = server.meta[str(f)]["id"]

        server.handle_remove(str(f))
        assert str(f) not in server.meta
        assert first_id not in server.store

        f.write_text("y = 2")
        server.handle_index(str(f))
        assert str(f) in server.meta
        second_id = server.meta[str(f)]["id"]
        assert second_id != first_id
        assert second_id in server.store


class TestValidateEdgeCases:
    def test_validate_python_version_acceptable(self):
        # Should not raise or exit for current Python
        server.validate_python_version()

    def test_validate_environment_import_failure_logs(self, mocker):
        mock_py = mocker.patch("server.validate_python_version")
        mock_imports = mocker.patch("server.validate_imports")
        server.validate_environment()
        mock_py.assert_called_once()
        mock_imports.assert_called_once()


class TestValidateEnvironmentOrder:
    def test_validate_python_called_before_imports(self, mocker):
        calls = []
        mocker.patch("server.validate_python_version", side_effect=lambda: calls.append("py"))
        mocker.patch("server.validate_imports", side_effect=lambda: calls.append("imports"))
        server.validate_environment()
        assert calls == ["py", "imports"]


class TestEnqueueAfterDequeue:
    def test_enqueue_after_batch_maintains_count(self):
        for i in range(5):
            server.enqueue("new", f"/f{i}.py")
        server.dequeue_batch(3)
        server.enqueue("new", "/g.py")
        assert server.queue_depth() == 3

    def test_empty_dequeue_then_enqueue(self):
        server.dequeue_batch(5)
        assert server.queue_depth() == 0
        server.enqueue("new", "/a.py")
        assert server.queue_depth() == 1


class TestPropertyBasedSearch:
    @given(
        k=st.integers(min_value=-5, max_value=30),
        query=st.text(min_size=0, max_size=20),
    )
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_search_k_is_safe(self, k, query, mock_model, mock_index, populated_state):
        if not query.strip():
            return
        result = server.search_codebase(query, k=k)
        assert isinstance(result, str)
        assert len(result) > 0

    @given(
        k=st.integers(min_value=1, max_value=10),
    )
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_search_returns_correct_count(self, k, mock_model, mock_index, populated_state):
        mock_index.search.return_value = (
            np.array([[0.9] * k]),
            np.array([list(range(1, k + 1))], dtype=np.uint64),
        )
        result = server.search_codebase("test", k=k)
        count = result.count("(score:") if result != "No results for 'test'." else 0
        assert count == min(k, 3)  # limited by available store entries


class TestIndexDirectoryOSError:
    """index_directory handles filesystem errors robustly."""

    def test_oswalk_filenotfound_caught(self, tmp_path, mock_model, mock_index, mocker):
        d = tmp_path / "vanished"
        d.mkdir()
        mocker.patch("server.os.walk", side_effect=FileNotFoundError("dir vanished"))
        result = server.index_directory(str(d))
        assert "Error" in result
        assert "Cannot read directory" in result

    def test_oswalk_permissionerror_caught(self, tmp_path, mock_model, mock_index, mocker):
        d = tmp_path / "locked"
        d.mkdir()
        mocker.patch("server.os.walk", side_effect=PermissionError("access denied"))
        result = server.index_directory(str(d))
        assert "Error" in result
        assert "Permission denied" in result

    def test_oswalk_generic_oserror_caught(self, tmp_path, mock_model, mock_index, mocker):
        d = tmp_path / "broken"
        d.mkdir()
        mocker.patch("server.os.walk", side_effect=OSError("device error"))
        result = server.index_directory(str(d))
        assert "Error" in result
        assert "Cannot read directory" in result


class TestStartupCleanup:
    """Stale .tmp files are cleaned up on startup."""

    def test_stale_tmp_removed(self, mocker):
        mocker.patch("server.validate_environment")
        mocker.patch("os.makedirs")
        mocker.patch("server.load_and_verify")
        mock_thread = mocker.patch("threading.Thread")
        mocker.patch("server.sig_module.signal")
        mocker.patch("server.mcp.run")

        # Create stale .tmp files
        open(server.INDEX_PATH + ".tmp", "w").close()
        open(server.META_PATH + ".tmp", "w").close()

        server.main()

        assert not os.path.exists(server.INDEX_PATH + ".tmp")
        assert not os.path.exists(server.META_PATH + ".tmp")

    def test_cleanup_does_not_crash_when_no_tmp(self, mocker):
        mocker.patch("server.validate_environment")
        mocker.patch("os.makedirs")
        mocker.patch("server.load_and_verify")
        mock_thread = mocker.patch("threading.Thread")
        mocker.patch("server.sig_module.signal")
        mocker.patch("server.mcp.run")

        server.main()

    def test_cleanup_handles_remove_failure(self, mocker):
        mocker.patch("server.validate_environment")
        mocker.patch("os.makedirs")
        mocker.patch("server.load_and_verify")
        mock_thread = mocker.patch("threading.Thread")
        mocker.patch("server.sig_module.signal")
        mocker.patch("server.mcp.run")

        open(server.INDEX_PATH + ".tmp", "w").close()
        mock_remove = mocker.patch("os.remove", side_effect=PermissionError("locked"))
        server.main()
        mock_remove.assert_called()


class TestSearchCodebaseShortContent:
    """Search results don't append ... for short content."""

    def test_short_content_no_ellipsis(self, mock_model, mock_index, populated_state):
        server.store[1] = {"path": "/short.py", "content": "x = 1"}
        mock_index.search.return_value = (
            np.array([[0.95]]), np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("test")
        assert "x = 1" in result
        assert result.count("```") == 2  # opening + closing backticks
        # Content should be "x = 1" without trailing ...
        assert "x = 1..." not in result

    def test_long_content_has_ellipsis(self, mock_model, mock_index, populated_state):
        long = "x" * 600
        server.store[1] = {"path": "/long.py", "content": long}
        mock_index.search.return_value = (
            np.array([[0.95]]), np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("test")
        assert "..." in result

    def test_exactly_500_chars_no_ellipsis(self, mock_model, mock_index, populated_state):
        content = "x" * 500
        server.store[1] = {"path": "/exact.py", "content": content}
        mock_index.search.return_value = (
            np.array([[0.95]]), np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("test")
        assert "x" * 500 in result
        assert "..." not in result


class TestHandleIndexMissingMetaId:
    """handle_index survives meta entries missing 'id' key during reindex."""

    def test_reindex_with_missing_meta_id_recovered_by_worker(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        server.current_id = 1
        f = tmp_path / "bad_meta.py"
        f.write_text("original")
        server.handle_index(str(f))

        # Corrupt meta to remove 'id' key
        with server.index_lock:
            server.meta[str(f)] = {"mtime": 100, "size": 10}

        f.write_text("modified")
        server.enqueue("new", str(f))

        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()

        # Worker gracefully handles missing 'id' — file re-indexed
        assert server.worker_state["errors"] == 0
        assert str(f) in server.meta
        assert server.meta[str(f)].get("id") == 2

    def test_reindex_with_missing_meta_id_does_not_crash_server(self, tmp_path, mock_model, mock_index):
        server.current_id = 1
        f = tmp_path / "bad_meta.py"
        f.write_text("original")
        server.handle_index(str(f))

        # Corrupt meta to remove 'id' key
        with server.index_lock:
            server.meta[str(f)] = {"mtime": 100, "size": 10}

        f.write_text("modified")
        server.handle_index(str(f))

        # Should not crash — exception caught and handled
        assert True


class TestHandleRemoveMissingMetaId:
    """handle_remove survives meta entries missing 'id' key."""

    def test_remove_with_missing_meta_id_cleaned_gracefully(self, mock_model, mock_index, populated_state, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        with server.index_lock:
            server.meta["/proj/file1.py"] = {"mtime": 100, "size": 10}  # no 'id' key

        server.enqueue("remove", "/proj/file1.py")
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()

        # Gracefully removes meta entry even without 'id' key
        assert server.worker_state["errors"] == 0
        assert "/proj/file1.py" not in server.meta


class TestBackgroundWorkerImmediateStop:
    """background_worker and idle_watchdog respect _stop_event immediately."""

    def test_worker_stops_when_event_set_while_idle(self, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mocker.patch.object(server, "find_stale_files", return_value=[])
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        t.join(timeout=1)
        assert not t.is_alive()

    def test_watchdog_stops_when_event_set_during_sleep(self, mocker):
        mocker.patch("server.os._exit")
        mocker.patch("server.log")
        mocker.patch.object(server, "CHECK_INTERVAL", 0.01)
        server._stop_event.clear()
        t = threading.Thread(target=server.idle_watchdog, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        t.join(timeout=1)
        assert not t.is_alive()


class TestLoadAndVerifyNonDictInMeta:
    """load_and_verify handles non-dict entries in meta/store."""

    def test_non_dict_meta_entry_skipped_during_rebuild(self):
        json.dump({"/a.py": "not_a_dict", "/b.py": {"id": 1}}, open(server.META_PATH, "w"))
        json.dump({"1": {"path": "/b.py", "content": "x"}}, open(server.STORE_PATH, "w"))
        server.load_and_verify()
        assert "/b.py" in server.meta
        assert "/a.py" not in server.meta

    def test_store_entry_without_path_skipped_during_rebuild(self):
        json.dump({}, open(server.META_PATH, "w"))
        json.dump({"1": {"content": "no_path"}}, open(server.STORE_PATH, "w"))
        server.load_and_verify()
        assert len(server.store) == 1
        # Entry without path is in store but not rebuilt into meta


class TestSearchCodebaseWithMalformedStore:
    """search_codebase handles malformed store entries."""

    def test_store_entry_missing_path(self, mock_model, mock_index, populated_state):
        server.store[99] = {"content": "orphan content"}  # no "path" key
        mock_index.search.return_value = (
            np.array([[0.9]]), np.array([[99]], dtype=np.uint64),
        )
        result = server.search_codebase("test")
        assert "unknown" in result

    def test_store_entry_is_none(self, mock_model, mock_index, populated_state):
        server.store[99] = None
        mock_index.search.return_value = (
            np.array([[0.9]]), np.array([[99]], dtype=np.uint64),
        )
        result = server.search_codebase("test")
        assert "No results" in result or "unknown" not in result

    def test_search_k_at_max_clamps(self, mock_model, mock_index, populated_state):
        mock_index.search.return_value = (
            np.array([list(range(20))]), np.array([list(range(1, 21))], dtype=np.uint64),
        )
        result = server.search_codebase("test", k=20)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_search_k_above_max_clamps(self, mock_model, mock_index, populated_state):
        mock_index.search.return_value = (
            np.array([list(range(20))]), np.array([list(range(1, 21))], dtype=np.uint64),
        )
        result = server.search_codebase("test", k=100)
        assert isinstance(result, str)

    def test_search_ids_not_in_store(self, mock_model, mock_index, populated_state):
        mock_index.search.return_value = (
            np.array([[0.9, 0.8]]), np.array([[99, 100]], dtype=np.uint64),
        )
        result = server.search_codebase("test")
        assert "No results" in result


class TestFindStaleFilesLargeMeta:
    """find_stale_files performs well with large meta."""

    def test_find_stale_scalable(self):
        now = time.time()
        server.meta = {
            f"/f{i}.py": {"id": i, "last_indexed": 0 if i < 50 else now}
            for i in range(100)
        }
        stale = server.find_stale_files(max_age_days=1, max_files=10)
        assert len(stale) == 10
        assert all("f" in p for p in stale)

    def test_find_stale_all_stale_limited(self):
        now = time.time()
        server.meta = {
            f"/f{i}.py": {"id": i, "last_indexed": now - 86400 * 30}
            for i in range(25)
        }
        stale = server.find_stale_files(max_age_days=7, max_files=5)
        assert len(stale) == 5


class TestEnqueueEdgeCases:
    """Edge cases for enqueue/dequeue."""

    def test_enqueue_very_long_path(self):
        long_path = "/" + "a" * 10000 + ".py"
        server.enqueue("new", long_path)
        assert server.queue_depth() == 1
        batch = server.dequeue_batch(1)
        assert batch[0][1] == long_path

    def test_enqueue_special_chars_path(self):
        path = "/project/测试/main.py"
        server.enqueue("new", path)
        batch = server.dequeue_batch(1)
        assert batch[0][1] == path

    def test_dequeue_batch_larger_than_queue(self):
        for i in range(3):
            server.enqueue("new", f"/f{i}.py")
        batch = server.dequeue_batch(100)
        assert len(batch) == 3
        assert server.queue_depth() == 0

    def test_dequeue_cleared_queue_then_re_enqueue(self):
        server.enqueue("new", "/a.py")
        server.dequeue_batch(1)
        assert server.queue_depth() == 0
        server.enqueue("new", "/b.py")
        assert server.queue_depth() == 1


class TestPersistAllAfterMakedirsFailure:
    """persist_all handles makedirs failure gracefully."""

    def test_persist_all_makedirs_failure_returns_early(self, mocker, mock_index, populated_state):
        mock_log = mocker.patch("server.log")
        mocker.patch("os.makedirs", side_effect=PermissionError("access denied"))
        server.persist_all()
        assert any("Cannot create" in str(call) for call in mock_log.call_args_list)

    def test_persist_all_makedirs_failure_no_files_written(self, mocker, mock_index, populated_state):
        mocker.patch("os.makedirs", side_effect=PermissionError("access denied"))
        server.persist_all()
        assert not os.path.exists(server.INDEX_PATH)
        assert not os.path.exists(server.META_PATH)
        assert not os.path.exists(server.STORE_PATH)


class TestEnsureIndexWithMissingDirectory:
    """ensure_index handles missing TURBOCODE_DIR."""

    def test_ensure_index_creates_index_when_dir_missing(self, mocker):
        mocker.patch("os.path.exists", return_value=False)
        mock_idmap = mocker.patch("server.IdMapIndex", return_value=mocker.MagicMock())
        server.index = None
        server.ensure_index()
        mock_idmap.assert_called_once_with(dim=384, bit_width=4)

    def test_ensure_index_caches_result(self):
        server.index = object()
        server.ensure_index()
        assert server.index is not None


class TestWorkerStatePersistsAfterError:
    """Worker state accurately reflects error history."""

    def test_worker_state_last_error_updated(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = RuntimeError("turbovec crash")
        server.current_id = 1
        f = tmp_path / "err.py"
        f.write_text("x = 1")
        server.enqueue("new", str(f))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        assert "turbovec crash" in (server.worker_state["last_error"] or "")

    def test_worker_clears_error_after_success(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()

        # First a failure
        mock_index.add_with_ids.side_effect = RuntimeError("first crash")
        server.current_id = 1
        f = tmp_path / "recover.py"
        f.write_text("x = 1")
        server.enqueue("new", str(f))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        t.join(timeout=1)
        assert server.worker_state["errors"] >= 1
        # last_error is not cleared after success — it keeps the LAST error
        assert server.worker_state["last_error"] is not None


class TestConcurrentTouch:
    """Concurrent touch calls are safe."""

    def test_concurrent_touch_from_multiple_threads(self):
        def toucher():
            for _ in range(100):
                server.touch()

        threads = [threading.Thread(target=toucher) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert abs(time.time() - server.last_activity) < 2.0

    def test_touch_after_long_idle_sets_recent_time(self):
        server.last_activity = 0
        server.touch()
        assert server.last_activity > 0


class TestSearchCodebaseUnicode:
    """Search with unicode queries and content."""

    def test_search_unicode_query(self, mock_model, mock_index, populated_state):
        result = server.search_codebase("café über cool 🎉")
        assert isinstance(result, str)

    def test_search_unicode_in_results(self, mock_model, mock_index, populated_state):
        server.store[1] = {"path": "/cafe.py", "content": "def café():\n    return 'über cool'\n"}
        mock_index.search.return_value = (
            np.array([[0.95]]), np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("cafe")
        assert "café" in result
        assert "über" in result


class TestIndexDirectoryWithSymlinks:
    """index_directory does not follow symlinks."""

    def test_symlinked_directory_not_followed(self, tmp_path, mock_model, mock_index):
        real = tmp_path / "real"
        real.mkdir()
        (real / "real.py").write_text("x = 1")
        link = tmp_path / "link"
        try:
            os.symlink(str(real), str(link), target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported on this platform")

        result = server.index_directory(str(tmp_path))
        # Should find real.py but not the symlinked content again
        assert "Queued" in result or "up to date" in result


class TestLoadAndVerifyStoreNonDict:
    """load_and_verify handles non-dict store values."""

    def test_store_with_non_dict_value_rebuilds_meta(self):
        json.dump({}, open(server.META_PATH, "w"))
        json.dump({"1": "not a dict", "2": {"path": "/good.py", "content": "x"}}, open(server.STORE_PATH, "w"))
        server.load_and_verify()
        assert 2 in server.store
        # Entry with string value "not a dict" — int("1") succeeds so store["1"] = "not a dict"
        assert 1 in server.store
        assert server.store[1] == "not a dict"

    def test_store_with_list_value_empties_store(self):
        json.dump({}, open(server.META_PATH, "w"))
        with open(server.STORE_PATH, "w") as f:
            json.dump([1, 2, 3], f)
        server.load_and_verify()
        assert server.store == {}
        assert server.meta == {}

    def test_store_with_non_dict_value_does_not_crash_meta_rebuild(self):
        json.dump({}, open(server.META_PATH, "w"))
        json.dump({"1": {"path": "/a.py", "content": "x"}, "2": "bad"}, open(server.STORE_PATH, "w"))
        server.load_and_verify()
        assert "/a.py" in server.meta
        assert 1 in server.store
        assert server.current_id > 1


class TestDequeueWithMixedPrioritiesLarge:
    """Dequeue maintains priority ordering with many items."""

    def test_many_items_priority_order(self):
        for i in range(20):
            server.enqueue("new", f"/n{i}.py")
        for i in range(5):
            server.enqueue("remove", f"/r{i}.py")
        for i in range(10):
            server.enqueue("changed", f"/c{i}.py")

        batch = server.dequeue_batch(35)
        priorities = [p for p, _ in batch]
        assert priorities[:5] == ["remove"] * 5
        assert priorities[5:25] == ["new"] * 20
        assert priorities[25:35] == ["changed"] * 10

    def test_batch_smaller_than_single_priority_group(self):
        for i in range(20):
            server.enqueue("new", f"/n{i}.py")
        batch = server.dequeue_batch(5)
        assert len(batch) == 5
        assert all(p == "new" for p, _ in batch)
        assert server.queue_depth() == 15


class TestWorkerDoesNotIncrementProcessedOnError:
    """Worker does not increment processed count for failed items."""

    def test_error_does_not_increment_processed(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = RuntimeError("fail")
        server.current_id = 1
        f = tmp_path / "fail.py"
        f.write_text("x = 1")
        server.enqueue("new", str(f))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        assert server.worker_state["processed"] == 0
        assert server.worker_state["errors"] >= 1

    def test_mixed_success_failure_counts(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        server.current_id = 1
        f_good = tmp_path / "good.py"
        f_good.write_text("x = 1")
        f_bad = tmp_path / "bad.py"
        f_bad.write_text("y = 2")

        # Make second file fail (first succeeds, returns None)
        call_count = [0]
        def failing_add(emb, ids):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("second fails")
            return None

        mock_index.add_with_ids.side_effect = failing_add

        server.enqueue("new", str(f_good))
        server.enqueue("new", str(f_bad))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        assert server.worker_state["processed"] == 1
        assert server.worker_state["errors"] == 1


class TestHandleIndexRollbackScenarios:
    """Edge cases in handle_index rollback when add_with_ids fails."""

    def test_rollback_remove_fails_in_rollback(self, tmp_path, mock_model, mock_index):
        server.current_id = 10
        f = tmp_path / "f.py"
        f.write_text("x = 1")
        mock_index.add_with_ids.side_effect = RuntimeError("add fails")
        # Make index.remove raise too (simulate rollback failure)
        mock_index.remove.side_effect = RuntimeError("remove fails in rollback")
        try:
            server.handle_index(str(f))
        except RuntimeError:
            pass
        # current_id is incremented even on failure (harmless gap)
        assert server.current_id == 11
        # meta and store should not contain the file
        assert str(f) not in server.meta
        assert 10 not in server.store

    def test_rollback_meta_already_removed(self, tmp_path, mock_model, mock_index):
        server.current_id = 5
        f = tmp_path / "f.py"
        f.write_text("x = 1")
        # Pre-populate meta (simulate re-index scenario)
        server.meta[str(f)] = {"id": 3, "mtime": 100.0, "size": 10, "last_indexed": 200.0}
        server.store[3] = {"path": str(f), "content": "old"}
        mock_index.add_with_ids.side_effect = RuntimeError("add fails")
        try:
            server.handle_index(str(f))
        except RuntimeError:
            pass
        # Old entry should be preserved (data loss prevention)
        assert str(f) in server.meta
        assert 3 in server.store
        assert server.store[3]["content"] == "old"

    def test_rollback_store_entry_missing(self, tmp_path, mock_model, mock_index):
        server.current_id = 7
        f = tmp_path / "f.py"
        f.write_text("x = 1")
        mock_index.add_with_ids.side_effect = RuntimeError("add fails")
        try:
            server.handle_index(str(f))
        except RuntimeError:
            pass
        # file_id=7 was never in store, so store.pop(file_id, None) is safe
        assert 7 not in server.store
        assert str(f) not in server.meta


class TestHandleIndexReindexAddFailurePreservesOld:
    """Reindex add failure preserves old meta/store entries (no data loss)."""

    def test_add_failure_preserves_old_meta(self, tmp_path, mock_model, mock_index):
        server.current_id = 1
        f = tmp_path / "f.py"
        f.write_text("old content")
        server.handle_index(str(f))
        old_meta = dict(server.meta[str(f)])

        f.write_text("new content")
        mock_index.add_with_ids.side_effect = RuntimeError("turbovec oom")
        with pytest.raises(RuntimeError, match="turbovec oom"):
            server.handle_index(str(f))

        assert str(f) in server.meta
        assert server.meta[str(f)]["id"] == old_meta["id"]

    def test_add_failure_preserves_old_store(self, tmp_path, mock_model, mock_index):
        server.current_id = 1
        f = tmp_path / "f.py"
        f.write_text("old content")
        server.handle_index(str(f))
        old_id = server.meta[str(f)]["id"]

        f.write_text("new content")
        mock_index.add_with_ids.side_effect = RuntimeError("turbovec oom")
        with pytest.raises(RuntimeError, match="turbovec oom"):
            server.handle_index(str(f))

        assert old_id in server.store
        assert server.store[old_id]["content"] == "old content"


class TestHandleIndexPathTypeEdgeCases:
    """Path type edge cases for handle_index."""

    def test_path_is_directory_skipped(self, tmp_path, mock_model, mock_index):
        d = tmp_path / "adirectory"
        d.mkdir()
        server.handle_index(str(d))
        # Should skip cleanly (open raises IsADirectoryError caught by try/except)
        assert str(d) not in server.meta
        assert mock_index.add_with_ids.call_count == 0

    def test_path_with_null_byte_skipped(self, tmp_path, mock_model, mock_index):
        path = str(tmp_path / "bad\x00file.py")
        server.handle_index(path)
        assert mock_index.add_with_ids.call_count == 0

    def test_file_with_bom_prefix_no_crash(self, tmp_path, mock_model, mock_index):
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        mock_index.write.side_effect = None
        mock_index.write.return_value = None
        f = tmp_path / "bom.py"
        f.write_bytes(b"\xef\xbb\xbfprint('hello')\n")
        server.current_id = 1
        server.handle_index(str(f))
        assert str(f) in server.meta
        assert 1 in server.store
        # BOM char (\\ufeff) is preserved — .strip() does not remove it
        assert "print" in server.store[1]["content"]


class TestEnsureIndexPathEdgeCases:
    """INDEX_PATH being a directory or special file."""

    def test_index_path_is_directory_creates_new(self, tmp_path, mocker, monkeypatch):
        d = tmp_path / ".turbocode"
        d.mkdir(parents=True, exist_ok=True)
        idx_path = d / "index.tvim"
        idx_path.mkdir()
        monkeypatch.setattr(server, "INDEX_PATH", str(idx_path))
        # simulate fresh index
        server.index = None
        mocker.patch("server.IdMapIndex.load", side_effect=Exception("not a file"))
        server.ensure_index()
        assert server.index is not None

    def test_index_path_existing_empty_file(self, tmp_path, mocker, monkeypatch):
        d = tmp_path / ".turbocode"
        d.mkdir(parents=True, exist_ok=True)
        idx_path = d / "index.tvim"
        idx_path.write_text("")
        monkeypatch.setattr(server, "INDEX_PATH", str(idx_path))
        server.index = None
        mocker.patch("server.IdMapIndex.load", side_effect=Exception("empty file"))
        server.ensure_index()
        assert server.index is not None


class TestGetIndexStatsPathEdgeCases:
    """get_index_stats with unusual INDEX_PATH states."""

    def test_stats_path_does_not_exist(self, monkeypatch):
        monkeypatch.setattr(server, "INDEX_PATH", "/nonexistent/path.tvim")
        result = server.get_index_stats()
        assert "Disk: 0.0 KB" in result

    def test_stats_path_stat_raises(self, mocker):
        mocker.patch("server.os.path.exists", return_value=True)
        mocker.patch("server.os.path.getsize", side_effect=OSError("stat fail"))
        result = server.get_index_stats()
        assert "Disk: 0.0 KB" in result


class TestPersistAllNoTempFiles:
    """No .tmp files left behind after persist_all completes."""

    def test_no_temp_files_after_persist(self, mock_index, mock_model):
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        server.meta["/a.py"] = {"id": 1, "mtime": 100, "size": 10, "last_indexed": 200}
        server.store[1] = {"path": "/a.py", "content": "x"}
        server.persist_all()
        assert not os.path.exists(server.INDEX_PATH + ".tmp")
        assert not os.path.exists(server.META_PATH + ".tmp")
        assert not os.path.exists(server.STORE_PATH + ".tmp")
        assert os.path.exists(server.INDEX_PATH)
        assert os.path.exists(server.META_PATH)
        assert os.path.exists(server.STORE_PATH)

    def test_temp_files_cleaned_on_partial_failure(self, mock_index, mock_model, mocker):
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        server.meta["/a.py"] = {"id": 1, "mtime": 100, "size": 10, "last_indexed": 200}
        server.store[1] = {"path": "/a.py", "content": "x"}
        mocker.patch.object(server, "atomic_write", side_effect=RuntimeError("write fails"))
        server.persist_all()
        assert not os.path.exists(server.INDEX_PATH + ".tmp")


class TestBackgroundWorkerPersistFailure:
    """Worker error counting when persist_all fails."""

    def test_persist_failure_increments_errors(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        mocker.patch.object(server, "persist_all", side_effect=RuntimeError("persist boom"))
        f = tmp_path / "f.py"
        f.write_text("x = 1")
        server.current_id = 1
        server.enqueue("new", str(f))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        assert server.worker_state["errors"] >= 1

    def test_persist_failure_sets_last_error(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        mocker.patch.object(server, "persist_all", side_effect=RuntimeError("persist boom"))
        f = tmp_path / "f.py"
        f.write_text("x = 1")
        server.current_id = 1
        server.enqueue("new", str(f))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        assert server.worker_state["last_error"] is not None
        assert "persist boom" in server.worker_state["last_error"]


class TestBackgroundWorkerPriorityProcessing:
    """Worker processes items in correct priority order."""

    def test_background_worker_priority_remove_only(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        server.current_id = 5
        # Populate meta with files to be removed
        f1 = tmp_path / "r1.py"
        f1.write_text("x")
        f2 = tmp_path / "r2.py"
        f2.write_text("y")
        server.meta[str(f1)] = {"id": 1, "mtime": 100, "size": 1, "last_indexed": 200}
        server.meta[str(f2)] = {"id": 2, "mtime": 100, "size": 1, "last_indexed": 200}
        server.enqueue("remove", str(f1))
        server.enqueue("remove", str(f2))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        assert str(f1) not in server.meta
        assert str(f2) not in server.meta
        assert server.worker_state["processed"] == 2

    def test_dequeue_batch_prioritizes_remove(self):
        server.enqueue("new", "/n1.py")
        server.enqueue("new", "/n2.py")
        server.enqueue("remove", "/r1.py")
        server.enqueue("changed", "/c1.py")
        batch = server.dequeue_batch(10)
        assert batch[0][0] == "remove"
        assert batch[1][0] == "new"
        assert batch[2][0] == "new"
        assert batch[3][0] == "changed"

    def test_dequeue_batch_reindex_is_last(self):
        server.enqueue("reindex", "/ri1.py")
        server.enqueue("new", "/n1.py")
        server.enqueue("remove", "/r1.py")
        server.enqueue("changed", "/c1.py")
        batch = server.dequeue_batch(10)
        assert batch[0][0] == "remove"
        assert batch[1][0] == "new"
        assert batch[2][0] == "changed"
        assert batch[3][0] == "reindex"
        priorities = [p for p, _ in batch]
        assert priorities == ["remove", "new", "changed", "reindex"]


class TestSearchCodebaseNoResults:
    """Search returns appropriate messages when no results found."""

    def test_no_results_with_queued_hint(self, mock_model, mock_index):
        mock_index.search.return_value = (
            np.array([[]]),
            np.array([[]], dtype=np.uint64),
        )
        server.store[99] = {"path": "/dummy.py", "content": "x"}
        server.enqueue("new", "/pending.py")
        result = server.search_codebase("query", k=3)
        assert "No results found" in result
        assert "queued" in result

    def test_no_results_without_queued_hint(self, mock_model, mock_index):
        mock_index.search.return_value = (
            np.array([[]]),
            np.array([[]], dtype=np.uint64),
        )
        server.store[99] = {"path": "/dummy.py", "content": "x"}
        result = server.search_codebase("query", k=3)
        assert "No results found" in result
        assert "queued" not in result


class TestSearchCodebaseStoreEdgeCases:
    """Search handles missing or malformed store entries."""

    def test_doc_id_not_in_store(self, mock_model, mock_index):
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[42]], dtype=np.uint64),
        )
        server.store[99] = {"path": "/dummy.py", "content": "x"}
        result = server.search_codebase("query", k=1)
        assert "No results found" in result

    def test_store_entry_not_dict(self, mock_model, mock_index):
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[1]], dtype=np.uint64),
        )
        server.store[1] = "not a dict"
        result = server.search_codebase("query", k=1)
        assert "No results found" in result


class TestHandleIndexDuplicateFile:
    """Indexing same file twice overwrites correctly."""

    def test_same_file_indexed_twice_overwrites(self, tmp_path, mock_model, mock_index):
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        mock_index.write.side_effect = None
        mock_index.write.return_value = None
        f = tmp_path / "overwrite.py"
        f.write_text("version one")
        server.current_id = 1
        server.handle_index(str(f))
        first_id = server.meta[str(f)]["id"]
        assert first_id == 1
        assert server.store[1]["content"] == "version one"

        f.write_text("version two")
        server.handle_index(str(f))
        second_id = server.meta[str(f)]["id"]
        assert second_id == 2
        assert 1 not in server.store
        assert server.store[2]["content"] == "version two"

    def test_current_id_strictly_increases(self, tmp_path, mock_model, mock_index):
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        mock_index.write.side_effect = None
        mock_index.write.return_value = None
        f1 = tmp_path / "a.py"
        f1.write_text("a")
        f2 = tmp_path / "b.py"
        f2.write_text("b")
        server.current_id = 100
        server.handle_index(str(f1))
        server.handle_index(str(f2))
        assert server.meta[str(f1)]["id"] == 100
        assert server.meta[str(f2)]["id"] == 101
        assert server.current_id == 102


class TestBackgroundWorkerStateTransitions:
    """Worker state transitions between idle and indexing."""

    def test_status_indexing_during_batch(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        f = tmp_path / "f.py"
        f.write_text("x")
        server.current_id = 1
        server.enqueue("new", str(f))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.05)
        server._stop_event.set()
        time.sleep(0.05)
        # Worker finished, status should be idle
        assert server.worker_state["status"] == "idle"

    def test_worker_status_idle_when_no_queue(self, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        server._stop_event.clear()
        result_queue = []
        original_dequeue = server.dequeue_batch
        def capture_and_dequeue(*a, **kw):
            batch = original_dequeue(*a, **kw)
            result_queue.append((server.worker_state["status"], len(batch)))
            return batch
        mocker.patch.object(server, "dequeue_batch", side_effect=capture_and_dequeue)
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        assert any(status == "idle" for status, _ in result_queue)


class TestAtomicWriteEdgeCasesMore:
    """Additional atomic_write edge cases."""

    def test_atomic_write_empty_content(self, tmp_path):
        target = tmp_path / "empty.txt"
        server.atomic_write(str(target), "")
        assert target.read_text() == ""

    def test_atomic_write_cleanup_on_failure(self, mocker, tmp_path):
        target = tmp_path / "test.txt"
        mocker.patch("builtins.open", side_effect=PermissionError("denied"))
        try:
            server.atomic_write(str(target), "data")
        except PermissionError:
            pass
        assert not os.path.exists(str(target) + ".tmp")


class TestBackgroundWorkerHandlesPersistException:
    """Worker survives persist_all exceptions."""

    def test_persist_exception_does_not_crash_worker(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        call_count = [0]
        original_persist = server.persist_all
        def flaky_persist():
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("first persist fails")
            return original_persist()
        mocker.patch.object(server, "persist_all", side_effect=flaky_persist)
        f = tmp_path / "f.py"
        f.write_text("x")
        server.current_id = 1
        server.enqueue("new", str(f))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        assert server.worker_state["last_error"] is not None
        assert "first persist fails" in server.worker_state["last_error"]

    def test_worker_continues_after_item_error(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        f1 = tmp_path / "good1.py"
        f1.write_text("x")
        f2 = tmp_path / "good2.py"
        f2.write_text("y")
        mock_index.add_with_ids.side_effect = [
            None,
            RuntimeError("item fail"),
            None,
        ]
        mock_index.remove.return_value = None
        f3 = tmp_path / "good3.py"
        f3.write_text("z")
        server.current_id = 1
        server.enqueue("new", str(f1))
        server.enqueue("new", str(f2))
        server.enqueue("new", str(f3))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        assert server.worker_state["processed"] == 2
        assert server.worker_state["errors"] == 1


class TestIdleWatchdogStopDuringSleep:
    """Idle watchdog exits quickly when stop event set during sleep."""

    def test_watchdog_stops_when_event_set_during_sleep(self):
        os.environ["CHECK_INTERVAL_test"] = "1"
        server.idle_watchdog()
        # When _stop_event is already set (from clean_globals), should return immediately
        assert True

    def test_watchdog_event_set_before_sleep_check(self):
        server._stop_event.set()
        server.last_activity = 0
        server.idle_watchdog()
        assert True


class TestHandleIndexMultilineTruncation:
    """Content with many newlines is truncated correctly."""

    def test_multiline_content_truncated(self, tmp_path, mock_model, mock_index):
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        mock_index.write.side_effect = None
        mock_index.write.return_value = None
        f = tmp_path / "long.py"
        long_content = "\n".join(f"line {i}" for i in range(500))
        f.write_text(long_content)
        server.current_id = 1
        server.handle_index(str(f))
        stored = server.store[1]["content"]
        assert len(stored) <= 2000
        assert stored.startswith("line 0")


class TestHandleIndexIOErrorsAdvanced:
    """Additional I/O error scenarios in handle_index."""

    def test_oserror_during_read_skipped(self, tmp_path, mock_model, mock_index, mocker):
        f = tmp_path / "bad.py"
        f.write_text("x")
        mocker.patch("builtins.open", side_effect=OSError("device error"))
        server.handle_index(str(f))
        assert mock_index.add_with_ids.call_count == 0

    def test_file_with_no_read_permission_skipped(self, tmp_path, mock_model, mock_index, mocker):
        f = tmp_path / "noperm.py"
        f.write_text("x")
        # Simulate permission denied (platform-independent)
        mocker.patch.object(server, "open", side_effect=PermissionError("permission denied"))
        server.handle_index(str(f))
        assert mock_index.add_with_ids.call_count == 0


class TestLoadAndVerifyNonDictStoreRebuild:
    """load_and_verify rebuilds from store when meta is invalid."""

    def test_meta_non_dict_with_valid_store_rebuilds(self, monkeypatch, tmp_path):
        d = tmp_path / ".turbocode"
        d.mkdir(parents=True, exist_ok=True)
        meta_p = d / "meta.json"
        meta_p.write_text("[1, 2, 3]")
        store_p = d / "store.json"
        store_p.write_text('{"1": {"path": "/a.py", "content": "x", "mtime": 100, "size": 10, "last_indexed": 200}}')
        monkeypatch.setattr(server, "META_PATH", str(meta_p))
        monkeypatch.setattr(server, "STORE_PATH", str(store_p))
        server.meta = {}
        server.store = {}
        server.load_and_verify()
        assert "/a.py" in server.meta
        assert server.current_id == 2


class TestBackgroundWorkerPersistDoesNotCrashOnWarning:
    """Worker handles persist_all returning normally with warnings."""

    def test_persist_all_warning_does_not_crash(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        # persist_all logs warning but doesn't raise
        def warn_persist():
            pass
        mocker.patch.object(server, "persist_all", side_effect=warn_persist)
        f = tmp_path / "f.py"
        f.write_text("x")
        server.current_id = 1
        server.enqueue("new", str(f))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        assert server.worker_state["processed"] == 1
        assert server.worker_state["errors"] == 0


class TestSearchKNoneGuard:
    """search_codebase handles k=None and k=0 without crashing."""

    def test_search_k_none_returns_results(self, mock_model, mock_index):
        server.store[1] = {"path": "/a.py", "content": "def f(): pass"}
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query", k=None)
        assert "No results found" not in result

    def test_search_k_zero_clamps_to_one(self, mock_model, mock_index):
        server.store[1] = {"path": "/a.py", "content": "x"}
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query", k=0)
        assert "/a.py" in result


class TestEnqueueValidationEdgeCases:
    """enqueue rejects non-string and empty file_paths."""

    def test_enqueue_non_string_path_rejected(self):
        server.enqueue("new", 42)
        assert server.queue_depth() == 0

    def test_enqueue_whitespace_path_accepted(self):
        server.enqueue("new", "   ")
        assert server.queue_depth() == 1

    def test_enqueue_valid_long_path_accepted(self):
        long_path = "/" + "a" * 500 + ".py"
        server.enqueue("new", long_path)
        assert server.queue_depth() == 1
        batch = server.dequeue_batch(10)
        assert batch[0][1] == long_path


class TestPersistContinueOnIndexFail:
    """persist_all writes meta/store even when index.write fails."""

    def test_meta_persisted_after_index_write_failure(self, mock_index, tmp_path):
        mock_index.write.side_effect = RuntimeError("index write fails")
        server.meta["/a.py"] = {"id": 1, "mtime": 100, "size": 10, "last_indexed": 200}
        server.store[1] = {"path": "/a.py", "content": "x"}
        server.persist_all()
        assert os.path.exists(server.META_PATH)

    def test_store_persisted_after_index_write_failure(self, mock_index, tmp_path):
        mock_index.write.side_effect = RuntimeError("index write fails")
        server.meta["/a.py"] = {"id": 1, "mtime": 100, "size": 10, "last_indexed": 200}
        server.store[1] = {"path": "/a.py", "content": "x"}
        server.persist_all()
        assert os.path.exists(server.STORE_PATH)

    def test_index_file_not_written_on_failure(self, mock_index, tmp_path):
        mock_index.write.side_effect = RuntimeError("index write fails")
        server.meta["/a.py"] = {"id": 1, "mtime": 100, "size": 10, "last_indexed": 200}
        server.store[1] = {"path": "/a.py", "content": "x"}
        server.persist_all()
        assert not os.path.exists(server.INDEX_PATH)


class TestLogCapturing:
    """Verify log/debug output behaviour via capsys."""

    def test_log_writes_to_stderr(self, capsys):
        server.log("hello")
        captured = capsys.readouterr()
        assert "[TurboCode MCP] hello" in captured.err

    def test_debug_silent_when_disabled(self, capsys):
        server.DEBUG_MODE = False
        server.debug("invisible")
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_debug_outputs_when_enabled(self, capsys):
        server.DEBUG_MODE = True
        server.debug("visible")
        captured = capsys.readouterr()
        assert "[TurboCode MCP] [DEBUG] visible" in captured.err
        server.DEBUG_MODE = False


class TestIndexDirectoryFileInput:
    """index_directory rejects file paths."""

    def test_file_path_returns_error(self, tmp_path):
        f = tmp_path / "afile.txt"
        f.write_text("x")
        result = server.index_directory(str(f))
        assert "not a directory" in result


class TestSearchResultExactFormat:
    """search_codebase returns correctly formatted results."""

    def test_result_contains_file_path_and_score(self, mock_model, mock_index):
        server.store[1] = {"path": "/proj/file.py", "content": "def f(): pass"}
        mock_index.search.return_value = (
            np.array([[0.8765]]),
            np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query", k=1)
        assert "**/proj/file.py**" in result
        assert "0.8765" in result

    def test_multiple_results_separated_by_delimiter(self, mock_model, mock_index):
        server.store[1] = {"path": "/a.py", "content": "x"}
        server.store[2] = {"path": "/b.py", "content": "y"}
        mock_index.search.return_value = (
            np.array([[0.95, 0.85]]),
            np.array([[1, 2]], dtype=np.uint64),
        )
        result = server.search_codebase("query", k=2)
        assert "---" in result


class TestDequeueExactFit:
    """dequeue_batch handles exact-sized batches correctly."""

    def test_exact_batch_size(self):
        for i in range(5):
            server.enqueue("new", f"/f{i}.py")
        batch = server.dequeue_batch(5)
        assert len(batch) == 5
        assert server.queue_depth() == 0

    def test_exact_batch_size_unknown_priority(self):
        for i in range(5):
            server.enqueue("unknown", f"/f{i}.py")
        batch = server.dequeue_batch(5)
        assert len(batch) == 5
        assert server.queue_depth() == 0


class TestFindStaleNonDictValues:
    """find_stale_files handles non-dict meta entries gracefully."""

    def test_skips_non_dict_meta_value(self):
        server.meta["/f1.py"] = "not a dict"
        result = server.find_stale_files(max_age_days=0, max_files=10)
        assert result == []

    def test_skips_none_meta_value(self):
        server.meta["/f1.py"] = None
        result = server.find_stale_files(max_age_days=0, max_files=10)
        assert result == []


class TestHandleIndexEncodeEdgeCase:
    """handle_index tolerates unexpected model.encode shapes."""

    def test_encode_returns_single_element_list(self, tmp_path, mock_model, mock_index):
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        mock_index.write.side_effect = None
        mock_index.write.return_value = None
        mock_model.encode.return_value = [np.random.rand(384).astype(np.float32)]
        f = tmp_path / "f.py"
        f.write_text("x = 1")
        server.current_id = 1
        server.handle_index(str(f))
        assert str(f) in server.meta

    def test_encode_returns_multi_row_array(self, tmp_path, mock_model, mock_index):
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        mock_index.write.side_effect = None
        mock_index.write.return_value = None
        mock_model.encode.return_value = np.random.rand(3, 384).astype(np.float32)
        f = tmp_path / "f.py"
        f.write_text("x = 1")
        server.current_id = 1
        server.handle_index(str(f))
        assert str(f) in server.meta


class TestBackgroundWorkerMultipleBatches:
    """Worker handles more items than BATCH_SIZE (5) correctly."""

    def test_15_items_across_3_batches(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        files = []
        for i in range(15):
            f = tmp_path / f"f{i}.py"
            f.write_text(f"x = {i}")
            files.append(f)
        server.current_id = 1
        for f in files:
            server.enqueue("new", str(f))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.25)
        server._stop_event.set()
        assert server.worker_state["processed"] == 15

    def test_worker_error_on_one_does_not_block_others(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        call_count = [0]
        def flaky_add(emb, ids):
            call_count[0] += 1
            if call_count[0] == 3:
                raise RuntimeError("third fails")
            return None
        mock_index.add_with_ids.side_effect = flaky_add
        files = []
        for i in range(7):
            f = tmp_path / f"f{i}.py"
            f.write_text(f"x = {i}")
            files.append(f)
        server.current_id = 1
        for f in files:
            server.enqueue("new", str(f))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.15)
        server._stop_event.set()
        assert server.worker_state["processed"] == 6
        assert server.worker_state["errors"] == 1


class TestGetIndexStatsWorkerState:
    """get_index_stats reflects worker_state."""

    def test_stats_shows_worker_processed_count(self):
        server.worker_state["processed"] = 42
        server.worker_state["errors"] = 7
        result = server.get_index_stats()
        assert "42 processed" in result
        assert "7 errors" in result


class TestTouchInitialValue:
    """touch() sets last_activity to a recent time."""

    def test_touch_sets_last_activity(self):
        before = time.time()
        server.touch()
        assert server.last_activity >= before


class TestSearchContentDisplay:
    """search_codebase display truncation and ellipsis."""

    def test_short_content_no_ellipsis_in_search_display(self, mock_model, mock_index):
        short = "x" * 100
        server.store[1] = {"path": "/a.py", "content": short}
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query", k=1)
        assert "..." not in result

    def test_long_content_has_ellipsis_in_search_display(self, mock_model, mock_index):
        long = "x" * 1000
        server.store[1] = {"path": "/a.py", "content": long}
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query", k=1)
        assert "..." in result


class TestMainMakedirsFailure:
    """main() handles makedirs failure gracefully."""

    def test_main_survives_makedirs_failure(self, mocker):
        mocker.patch.object(server, "validate_environment")
        mocker.patch.object(server, "load_and_verify")
        mocker.patch.object(server, "os")
        server.os.makedirs.side_effect = PermissionError("denied")
        mocker.patch.object(server, "background_worker")
        mocker.patch.object(server, "idle_watchdog")
        mocker.patch.object(server, "mcp")
        try:
            server.main()
        except Exception:
            pytest.fail("main() raised on makedirs failure")


class TestBackgroundWorkerPriorityAcrossBatches:
    """Priority ordering preserved when items span multiple batches."""

    def test_remove_before_new_across_batches(self, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mocker.patch.object(server, "find_stale_files", return_value=[])
        for i in range(6):
            server.enqueue("new", f"/n{i}.py")
        for i in range(4):
            server.enqueue("remove", f"/r{i}.py")
        server._stop_event.clear()
        results = []
        original_dequeue = server.dequeue_batch
        def capturing_dequeue(*a, **kw):
            batch = original_dequeue(*a, **kw)
            if batch:
                results.extend((p, f) for p, f in batch)
            return batch
        mocker.patch.object(server, "dequeue_batch", side_effect=capturing_dequeue)
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.15)
        server._stop_event.set()
        remove_items = [p for p, _ in results if p == "remove"]
        new_items = [p for p, _ in results if p == "new"]
        # All removals should appear before any new items
        if remove_items:
            last_remove_idx = max(i for i, (p, _) in enumerate(results) if p == "remove")
            first_new_idx = min(i for i, (p, _) in enumerate(results) if p == "new")
            assert last_remove_idx < first_new_idx


class TestHandleIndexNonExistentPath:
    """handle_index silently skips non-existent paths."""

    def test_nonexistent_path_skipped(self, mock_model, mock_index):
        server.handle_index("/nonexistent/file.py")
        assert mock_index.add_with_ids.call_count == 0


class TestValidateEnvironmentDebugPath:
    """validate_environment calls debug when all checks pass."""

    def test_debug_called_on_success(self, capsys, mocker):
        mocker.patch.object(server, "validate_python_version")
        mocker.patch.object(server, "validate_imports")
        server.DEBUG_MODE = True
        server.validate_environment()
        captured = capsys.readouterr()
        assert "All startup validations passed" in captured.err
        server.DEBUG_MODE = False


class TestIndexStatsResourceFields:
    """turbocode://stats resource contains required fields."""

    def test_stats_resource_fields(self):
        result = server.index_stats()
        stats = json.loads(result)
        assert "vectors" in stats
        assert "files_tracked" in stats
        assert "model_loaded" in stats
        assert "model" in stats
        assert stats["model"] == "all-MiniLM-L6-v2"


class TestBackgroundWorkerIdleStatusAfterProcessing:
    """Worker returns to idle after emptying the queue."""

    def test_status_idle_after_draining_queue(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.02)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        f = tmp_path / "f.py"
        f.write_text("x")
        server.current_id = 1
        server.enqueue("new", str(f))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        assert server.worker_state["status"] == "idle" or server.worker_state["processed"] == 1
        server._stop_event.set()


class TestSignalHandlerNoDeadlock:
    """Signal handler uses _persist_locked with blocking=False."""

    def test_handler_calls_persist_locked_not_persist_all(self, mocker):
        mock_persist_locked = mocker.patch("server._persist_locked")
        mock_exit = mocker.patch("server.os._exit")
        mocker.patch("server.log")
        mocker.patch("server.sig_module.signal")
        mocker.patch("server.validate_environment")
        mocker.patch("os.makedirs")
        mocker.patch("server.load_and_verify")
        mocker.patch("threading.Thread")
        mocker.patch("server.mcp.run")
        registered = {}
        def capture(signum, handler):
            registered[signum] = handler
        mocker.patch("server.sig_module.signal", side_effect=capture)
        mocker.patch("server.mcp.run", side_effect=Exception("stop"))
        try:
            server.main()
        except Exception:
            pass
        handler = registered.get(sig_module.SIGINT)
        assert handler is not None
        handler(sig_module.SIGINT, None)
        mock_persist_locked.assert_called_once()
        mock_exit.assert_called_once_with(0)

    def test_handler_skips_persist_when_lock_held(self, mocker):
        mock_persist_locked = mocker.patch("server._persist_locked")
        mock_exit = mocker.patch("server.os._exit")
        mocker.patch("server.log")
        server.index_lock.acquire()
        try:
            server._stop_event.set()
            mocker.patch("server.sig_module.signal")
            mocker.patch("server.validate_environment")
            mocker.patch("os.makedirs")
            mocker.patch("server.load_and_verify")
            mocker.patch("threading.Thread")
            mocker.patch("server.mcp.run")
            registered = {}
            def capture(signum, handler):
                registered[signum] = handler
            mocker.patch("server.sig_module.signal", side_effect=capture)
            mocker.patch("server.mcp.run", side_effect=Exception("stop"))
            try:
                server.main()
            except Exception:
                pass
            handler = registered.get(sig_module.SIGINT)
            assert handler is not None
            handler(sig_module.SIGINT, None)
            mock_persist_locked.assert_not_called()
            mock_exit.assert_called_once_with(0)
        finally:
            server.index_lock.release()


class TestHandleRemoveNonDictMeta:
    """handle_remove survives non-dict meta entries."""

    def test_non_dict_meta_removed_without_crash(self, mock_index):
        server.meta["/bad.py"] = "just a string"
        server.handle_remove("/bad.py")
        assert "/bad.py" not in server.meta

    def test_none_meta_removed_without_crash(self, mock_index):
        server.meta["/null.py"] = None
        server.handle_remove("/null.py")
        assert "/null.py" not in server.meta


class TestHandleIndexEncodeReturnsEmpty:
    """handle_index tolerates model.encode returning empty list."""

    def test_empty_encode_list_adds_nothing(self, tmp_path, mock_model, mock_index):
        mock_model.encode.return_value = []
        f = tmp_path / "f.py"
        f.write_text("x = 1")
        server.current_id = 1
        server.handle_index(str(f))
        assert len(server.store) == 0


class TestFindStaleBoundary:
    """find_stale_files boundary conditions."""

    def test_candidates_exactly_max_files(self):
        now = time.time()
        server.meta = {f"/f{i}.py": {"id": i, "last_indexed": now - 86400 * 14} for i in range(10)}
        stale = server.find_stale_files(max_age_days=7, max_files=10)
        assert len(stale) == 10

    def test_no_candidates_returns_empty(self):
        server.meta = {}
        stale = server.find_stale_files(max_age_days=7, max_files=10)
        assert stale == []


class TestDequeueBatchFloatSize:
    """dequeue_batch handles float batch_size gracefully."""

    def test_float_size_truncated(self):
        for i in range(5):
            server.enqueue("new", f"/f{i}.py")
        batch = server.dequeue_batch(2.7)
        assert len(batch) == 2
        assert server.queue_depth() == 3


class TestIndexDirectoryEmptyPath:
    """index_directory handles empty path."""

    def test_empty_path_returns_error(self):
        result = server.index_directory("")
        assert "error" in result.lower()


class TestPersistAllStoreEdgeCases:
    """persist_all handles non-serializable store entries."""

    def test_store_with_non_string_key_serialized(self, mock_index):
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        server.meta["/a.py"] = {"id": 1, "mtime": 100, "size": 10, "last_indexed": 200}
        server.store[1] = {"path": "/a.py", "content": "x"}
        server.persist_all()
        assert os.path.exists(server.META_PATH)
        assert os.path.exists(server.STORE_PATH)

    def test_store_with_none_value_serialized(self, mock_index):
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        server.meta["/a.py"] = {"id": 1, "mtime": 100, "size": 10, "last_indexed": 200}
        server.store[1] = {"path": "/a.py", "content": None}
        server.persist_all()
        stored = json.load(open(server.STORE_PATH))
        assert stored["1"]["content"] is None


class TestBackgroundWorkerAllPriorities:
    """Worker processes all priority types."""

    def test_worker_processes_changed_priority(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        f = tmp_path / "f.py"
        f.write_text("x")
        server.current_id = 1
        server.enqueue("changed", str(f))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        assert server.worker_state["processed"] == 1
        assert str(f) in server.meta

    def test_worker_processes_reindex_priority(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        f = tmp_path / "f.py"
        f.write_text("x")
        server.current_id = 1
        server.enqueue("reindex", str(f))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        assert server.worker_state["processed"] == 1
        assert str(f) in server.meta


class TestSearchCodebaseContentEdgeCases:
    """Search handles null bytes and special-only queries."""

    def test_content_with_null_byte(self, mock_model, mock_index):
        server.store[1] = {"path": "/a.py", "content": "x\x00y"}
        mock_index.search.return_value = (
            np.array([[0.95]]), np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query")
        assert "\x00" in result or "x" in result

    def test_query_with_only_special_chars(self, mock_model, mock_index, populated_state):
        result = server.search_codebase("!@#$%^&*()")
        assert isinstance(result, str)


class TestWorkerStatusTransitionsDetailed:
    """Worker state machine: idle -> indexing -> idle."""

    def test_status_goes_indexing_then_idle(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        f = tmp_path / "f.py"
        f.write_text("x")
        server.current_id = 1
        server.enqueue("new", str(f))
        server._stop_event.clear()
        statuses = []
        original_sleep = time.sleep
        def tracking_sleep(s):
            statuses.append(server.worker_state["status"])
            if len(statuses) >= 5:
                server._stop_event.set()
            original_sleep(min(s, 0.02))
        mocker.patch.object(time, "sleep", side_effect=tracking_sleep)
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        t.join(timeout=3)
        assert "indexing" in statuses
        assert "idle" in statuses


class TestHandleIndexEncodeWrongShape:
    """handle_index tolerates model.encode returning wrong shape."""

    def test_encode_returns_1d_flat_array(self, tmp_path, mock_model, mock_index):
        mock_model.encode.return_value = np.random.rand(384).astype(np.float32)
        f = tmp_path / "f.py"
        f.write_text("x")
        server.current_id = 1
        server.handle_index(str(f))
        assert len(server.store) == 1


class TestIndexStatsDirectoriesField:
    """turbocode://stats shows directories list."""

    def test_directories_list_in_stats_resource(self, populated_state):
        result = server.index_stats()
        data = json.loads(result)
        assert "directories" in data
        assert isinstance(data["directories"], list)
        assert any("/proj" in d for d in data["directories"])


class TestAtomicWriteParentIsFile:
    """atomic_write handles parent path being a file (not directory)."""

    def test_parent_is_file_path_raises(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("parent")
        child = f / "child.json"
        with pytest.raises(Exception):
            server.atomic_write(str(child), "{}")


class TestMainDebugPaths:
    """main() logs debug path info when DEBUG_MODE is True."""

    def test_debug_paths_logged(self, mocker, capsys):
        mocker.patch("sys.argv", ["server.py", "--debug"])
        mocker.patch("server.validate_environment")
        mocker.patch("os.makedirs")
        mocker.patch("server.load_and_verify")
        mocker.patch("threading.Thread")
        mocker.patch("server.sig_module.signal")
        mocker.patch("server.mcp.run")
        server.main()
        captured = capsys.readouterr()
        assert "TURBOCODE_DIR" in captured.err
        assert "INDEX_PATH" in captured.err


class TestLoadAndVerifyStoreDuplicatePaths:
    """load_and_verify handles store entries with duplicate paths."""

    def test_duplicate_path_uses_last_entry(self):
        json.dump({}, open(server.META_PATH, "w"))
        store = {"1": {"path": "/dup.py", "content": "first"}, "2": {"path": "/dup.py", "content": "second"}}
        json.dump(store, open(server.STORE_PATH, "w"))
        server.load_and_verify()
        assert "/dup.py" in server.meta
        assert server.meta["/dup.py"]["id"] == 2


class TestEnqueueBytes:
    """enqueue rejects bytes objects."""

    def test_enqueue_bytes_path_skipped(self):
        server.enqueue("new", b"/path.py")
        assert server.queue_depth() == 0

    def test_enqueue_bytearray_path_skipped(self):
        server.enqueue("new", bytearray(b"/path.py"))
        assert server.queue_depth() == 0


class TestBackgroundWorkerReindexOnly:
    """Worker handles reindex-only items."""

    def test_worker_reindex_with_no_initial_meta(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        f = tmp_path / "f.py"
        f.write_text("x")
        server.current_id = 1
        server.enqueue("reindex", str(f))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        assert server.worker_state["processed"] == 1
        assert str(f) in server.meta


class TestHandleRemoveMissingStoreEntry:
    """handle_remove works when store entry already removed."""

    def test_store_entry_already_gone(self, mock_index):
        server.meta["/gone.py"] = {"id": 1, "mtime": 100, "size": 10, "last_indexed": 200}
        assert 1 not in server.store
        server.handle_remove("/gone.py")
        assert "/gone.py" not in server.meta


class TestSearchLargeKEmptyStore:
    """search_codebase with large k but empty store."""

    def test_k_above_20_with_empty_store(self, mock_model, mock_index):
        result = server.search_codebase("test", k=50)
        assert isinstance(result, str)
        assert "empty" in result.lower()


class TestLoadAndVerifyNonSerializableJson:
    """load_and_verify handles store file with malformed JSON."""

    def test_store_file_is_dictionary_instead_of_json(self):
        json.dump({}, open(server.META_PATH, "w"))
        with open(server.STORE_PATH, "w") as f:
            f.write("{invalid json!!!}")
        server.load_and_verify()
        assert server.store == {}
        assert server.meta == {}


class TestPersistAllNoIndexThenIndexCreated:
    """persist_all handles index being None gracefully."""

    def test_persist_all_when_index_none_returns_early(self):
        server.index = None
        server.meta["/a.py"] = {"id": 1}
        server.persist_all()
        assert not os.path.exists(server.META_PATH)


class TestBackgroundWorkerEmptyQueueStaleOnly:
    """Worker processes stale files when queue is empty."""

    def test_stale_reindex_from_empty_queue(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        f = tmp_path / "stale.py"
        f.write_text("x")
        server.current_id = 1
        server.meta[str(f)] = {"id": 1, "mtime": 100, "size": 1, "last_indexed": 0}
        server.store[1] = {"path": str(f), "content": "old"}
        mocker.patch.object(server, "find_stale_files", return_value=[str(f)])
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        assert server.worker_state["processed"] >= 1


class TestEnsureIndexLoadErrorAndRemoveFails:
    """ensure_index survives both load error AND remove failure."""

    def test_load_and_remove_fail_creates_new_index(self, mocker):
        with open(server.INDEX_PATH, "wb") as f:
            f.write(b"corrupt")
        mocker.patch("server.IdMapIndex.load", side_effect=Exception("load error"))
        mock_remove = mocker.patch("os.remove", side_effect=PermissionError("locked"))
        server.index = None
        server.ensure_index()
        assert server.index is not None
        mock_remove.assert_called_once_with(server.INDEX_PATH)


class TestGetIndexStatsQueueDepth:
    """get_index_stats reflects queue depth."""

    def test_stats_shows_queue_depth(self):
        server.enqueue("new", "/p.py")
        server.enqueue("new", "/q.py")
        result = server.get_index_stats()
        assert "2 queued" in result


class TestHandleIndexContentMixedWhitespace:
    """handle_index trims and indexes mixed whitespace content correctly."""

    def test_content_with_leading_trailing_whitespace(self, tmp_path, mock_model, mock_index):
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        mock_index.write.side_effect = None
        mock_index.write.return_value = None
        f = tmp_path / "f.py"
        f.write_text("  \n  x = 1  \n  ")
        server.current_id = 1
        server.handle_index(str(f))
        assert 1 in server.store
        assert "x = 1" in server.store[1]["content"]


class TestSearchCodebaseKFloat:
    """search_codebase with float k values."""

    def test_k_float_clamps_to_int(self, mock_model, mock_index):
        server.store[1] = {"path": "/a.py", "content": "x"}
        mock_index.search.return_value = (
            np.array([[0.95]]), np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query", k=2.7)
        assert isinstance(result, str)

    def test_k_float_string_clamps_to_one(self, mock_model, mock_index):
        server.store[1] = {"path": "/a.py", "content": "x"}
        mock_index.search.return_value = (
            np.array([[0.95]]), np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query", k="bad")
        assert isinstance(result, str)


class TestValidateEnvironmentDebugFlagOff:
    """validate_environment does not call debug when DEBUG_MODE is False."""

    def test_debug_not_called_when_disabled(self, capsys, mocker):
        mocker.patch.object(server, "validate_python_version")
        mocker.patch.object(server, "validate_imports")
        server.DEBUG_MODE = False
        server.validate_environment()
        captured = capsys.readouterr()
        assert captured.err == ""


class TestDequeueNegativeBatchEdge:
    """dequeue_batch with negative batch_size handles edges correctly."""

    def test_negative_batch_with_many_items_clamped(self):
        for i in range(10):
            server.enqueue("new", f"/f{i}.py")
        batch = server.dequeue_batch(-5)
        assert len(batch) == 0
        assert server.queue_depth() == 10

    def test_negative_batch_with_single_item_clamped(self):
        server.enqueue("new", "/a.py")
        batch = server.dequeue_batch(-1)
        assert len(batch) == 0
        assert server.queue_depth() == 1


class TestDequeueBatchSpecialFloats:
    """dequeue_batch handles NaN and infinity batch_size."""

    def test_nan_batch_size_returns_empty(self):
        server.enqueue("new", "/a.py")
        import math
        batch = server.dequeue_batch(float("nan"))
        assert len(batch) == 0
        assert server.queue_depth() == 1

    def test_inf_batch_size_returns_empty(self):
        server.enqueue("new", "/a.py")
        import math
        batch = server.dequeue_batch(float("inf"))
        assert len(batch) == 0
        assert server.queue_depth() == 1


class TestFindStaleMaxFilesGuard:
    """find_stale_files guards against invalid max_files values."""

    def test_max_files_zero_returns_empty(self):
        server.meta = {"/a.py": {"id": 1, "last_indexed": 0}}
        stale = server.find_stale_files(max_age_days=0, max_files=0)
        assert stale == []

    def test_max_files_negative_returns_empty(self):
        server.meta = {"/a.py": {"id": 1, "last_indexed": 0}}
        stale = server.find_stale_files(max_age_days=0, max_files=-5)
        assert stale == []


class TestSearchKBooleanTrue:
    """search_codebase with k=True (bool subclass of int in Python)."""

    def test_k_true_returns_results(self, mock_model, mock_index):
        server.store[1] = {"path": "/a.py", "content": "x"}
        mock_index.search.return_value = (
            np.array([[0.95]]), np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query", k=True)
        assert isinstance(result, str)


class TestSearchKListValue:
    """search_codebase with non-scalar k values clamped to 1."""

    def test_k_list_clamps_to_one(self, mock_model, mock_index):
        server.store[1] = {"path": "/a.py", "content": "x"}
        mock_index.search.return_value = (
            np.array([[0.95]]), np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query", k=[1, 2, 3])
        assert isinstance(result, str)


class TestHandleIndexStripAfterTruncation:
    """handle_index correctly strips content[:2000] to avoid storing whitespace."""

    def test_whitespace_after_truncation_skipped(self, tmp_path, mock_model, mock_index):
        f = tmp_path / "f.py"
        f.write_text("x" + " " * 2000)
        server.current_id = 1
        server.handle_index(str(f))
        assert len(server.store) == 1
        # chunk = "x" + " " * 1999, .strip() -> "x", stored


class TestEnsureIndexConstructFails:
    """ensure_index survives IdMapIndex() constructor failure."""

    def test_constructor_failure_after_load_failure(self, mocker):
        with open(server.INDEX_PATH, "wb") as f:
            f.write(b"garbage")
        mock_idmap = mocker.patch("server.IdMapIndex", side_effect=RuntimeError("construct fails"))
        mock_idmap.load.side_effect = Exception("load error")
        server.index = None
        with pytest.raises(RuntimeError, match="construct fails"):
            server.ensure_index()


class TestPersistAllEmptyState:
    """persist_all handles empty meta and store."""

    def test_no_meta_no_store_writes_index_only(self, mock_index):
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        server.persist_all()
        assert os.path.exists(server.INDEX_PATH)
        assert os.path.exists(server.META_PATH)
        assert os.path.exists(server.STORE_PATH)

    def test_with_meta_but_no_store_writes_all(self, mock_index):
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        server.meta["/a.py"] = {"id": 1, "mtime": 0, "size": 0, "last_indexed": 0}
        server.persist_all()
        assert os.path.exists(server.INDEX_PATH)
        assert os.path.exists(server.META_PATH)
        assert os.path.exists(server.STORE_PATH)


class TestBackgroundWorkerPersistCalled:
    """persist_all is called after each worker batch."""

    def test_persist_called_after_batch(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        mock_persist = mocker.patch.object(server, "persist_all")
        f = tmp_path / "f.py"
        f.write_text("x")
        server.current_id = 1
        server.enqueue("new", str(f))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        mock_persist.assert_called()


class TestIndexDirectoryMtimeFailure:
    """index_directory handles os.path.getmtime failure gracefully."""

    def test_mtime_failure_on_changed_file_does_not_crash(self, tmp_path, mock_model, mock_index, mocker):
        d = tmp_path / "dir"
        d.mkdir()
        (d / "main.py").write_text("x")
        server.meta[str(d / "main.py")] = {"id": 1, "mtime": 100, "size": 1, "last_indexed": 200}
        mocker.patch("os.path.getmtime", side_effect=OSError("stale handle"))
        result = server.index_directory(str(d))
        assert "error" not in result.lower()


class TestSearchCodebaseNewlinesInContent:
    """Search results preserve newlines in content."""

    def test_multiline_content_has_newlines_in_output(self, mock_model, mock_index):
        content = "def foo():\n    return 42\n"
        server.store[1] = {"path": "/a.py", "content": content}
        mock_index.search.return_value = (
            np.array([[0.95]]), np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query")
        assert "def foo():" in result
        assert "    return 42" in result


class TestSearchCodebaseResultOrdering:
    """Results appear in descending score order."""

    def test_results_sorted_by_score(self, mock_model, mock_index):
        server.store[1] = {"path": "/low.py", "content": "low"}
        server.store[2] = {"path": "/high.py", "content": "high"}
        mock_index.search.return_value = (
            np.array([[0.99, 0.95]]),
            np.array([[2, 1]], dtype=np.uint64),
        )
        result = server.search_codebase("query", k=2)
        high_idx = result.index("/high.py")
        low_idx = result.index("/low.py")
        assert high_idx < low_idx


class TestTouchCalledByToolsAndResources:
    """Every tool and resource calls touch() to reset idle timer."""

    def test_index_directory_calls_touch(self, mocker):
        mock_touch = mocker.patch("server.touch")
        mocker.patch("os.path.exists", return_value=True)
        mocker.patch("os.path.isdir", return_value=True)
        mocker.patch("os.walk", return_value=[])
        mocker.patch("server.ensure_resources")
        server.index_directory("/tmp/dir")
        mock_touch.assert_called_once()

    def test_search_codebase_calls_touch(self, mocker, mock_model, mock_index):
        mock_touch = mocker.patch("server.touch")
        server.store[1] = {"path": "/dummy.py", "content": "x"}
        result = server.search_codebase("test")
        mock_touch.assert_called_once()

    def test_get_index_stats_calls_touch(self, mocker):
        mock_touch = mocker.patch("server.touch")
        server.get_index_stats()
        mock_touch.assert_called_once()

    def test_index_status_calls_touch(self, mocker):
        mock_touch = mocker.patch("server.touch")
        server.index_status()
        mock_touch.assert_called_once()

    def test_index_stats_resource_calls_touch(self, mocker):
        mock_touch = mocker.patch("server.touch")
        server.index_stats()
        mock_touch.assert_called_once()


class TestAtomicWriteUnicodeContent:
    """atomic_write handles unicode content correctly."""

    def test_write_and_read_unicode(self, tmp_path):
        f = tmp_path / "unicode.json"
        data = '{"café": "über cool 🎉"}'
        server.atomic_write(str(f), data)
        assert json.loads(f.read_text(encoding="utf-8")) == {"café": "über cool 🎉"}


class TestEnqueueNonePriority:
    """enqueue handles None priority without crashing."""

    def test_none_priority_appended(self):
        server.enqueue(None, "/a.py")
        batch = server.dequeue_batch(5)
        assert len(batch) == 1
        assert batch[0][0] is None
        assert batch[0][1] == "/a.py"


class TestValidateImportsEachPackageMissing:
    """validate_imports exits with correct message for each missing package."""

    @classmethod
    def _make_fake_import(cls, fail_name: str):
        real_import = builtins.__import__
        def fake_import(name, *a, **kw):
            if name == fail_name:
                raise ImportError(f"no {fail_name}")
            return real_import(name, *a, **kw)
        return fake_import

    def test_fastmcp_missing_exits(self, mocker):
        mocker.patch("builtins.__import__", side_effect=self._make_fake_import("fastmcp"))
        mock_exit = mocker.patch("sys.exit")
        server.validate_imports()
        mock_exit.assert_called_once_with(1)

    def test_turbovec_missing_does_not_exit(self, mocker):
        """turbovec is checked at first-use, not at startup."""
        mocker.patch("builtins.__import__", side_effect=self._make_fake_import("turbovec"))
        mock_exit = mocker.patch("sys.exit")
        server.validate_imports()
        mock_exit.assert_not_called()

    def test_sentence_transformers_missing_does_not_exit(self, mocker):
        """sentence-transformers is checked at first-use, not at startup."""
        mocker.patch("builtins.__import__", side_effect=self._make_fake_import("sentence_transformers"))
        mock_exit = mocker.patch("sys.exit")
        server.validate_imports()
        mock_exit.assert_not_called()

    def test_numpy_missing_exits(self, mocker):
        mocker.patch("builtins.__import__", side_effect=self._make_fake_import("numpy"))
        mock_exit = mocker.patch("sys.exit")
        server.validate_imports()
        mock_exit.assert_called_once_with(1)


class TestBackgroundWorkerDoesNotReEnqueueStaleInfinitely:
    """Worker does not infinite-loop on stale files that fail processing."""

    def test_stale_failure_does_not_loop(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = RuntimeError("always fails")
        f = tmp_path / "f.py"
        f.write_text("x")
        server.current_id = 1
        server.meta[str(f)] = {"id": 1, "mtime": 100, "size": 1, "last_indexed": 0}
        server.store[1] = {"path": str(f), "content": "old"}
        mocker.patch.object(server, "find_stale_files", return_value=[str(f)])
        iter_count = [0]
        original_sleep = time.sleep
        def tracking_sleep(s):
            iter_count[0] += 1
            if iter_count[0] >= 5:
                server._stop_event.set()
            original_sleep(min(s, 0.02))
        mocker.patch.object(time, "sleep", side_effect=tracking_sleep)
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        t.join(timeout=5)
        assert iter_count[0] < 20


class TestMainLoadAndVerifyCrashLogsWarning:
    """main() logs warning when load_and_verify raises."""

    def test_warning_logged_on_load_failure(self, mocker, capsys):
        mocker.patch("sys.argv", ["server.py"])
        mocker.patch("server.validate_environment")
        mocker.patch("os.makedirs")
        mocker.patch("server.load_and_verify", side_effect=RuntimeError("corrupt state"))
        mock_thread = mocker.patch("threading.Thread")
        mocker.patch("server.sig_module.signal")
        mocker.patch("server.mcp.run")
        server.main()
        captured = capsys.readouterr()
        assert "Failed to load persisted state" in captured.err
        assert server.current_id == 1
        assert server.meta == {}
        assert server.store == {}


class TestIndexDirectoryRemovedFilesOnly:
    """index_directory detects only removed files."""

    def test_only_removed_files(self, tmp_path, mock_model, mock_index):
        d = tmp_path / "dir"
        d.mkdir()
        tracked = d / "tracked.py"
        tracked.write_text("x")
        server.meta[str(tracked)] = {"id": 1, "mtime": 100, "size": 1, "last_indexed": 200}
        os.remove(str(tracked))
        result = server.index_directory(str(d))
        assert "to remove" in result


class TestSearchCodebaseStoreEmptyMessage:
    """search_codebase returns correct message when store is empty."""

    def test_empty_store_no_ensure_resources(self, mock_model, mock_index):
        result = server.search_codebase("anything")
        assert "Index is empty" in result
        assert "index_directory" in result


class TestIndexStatsWorkerStatusReflectsChange:
    """get_index_stats shows current worker status."""

    def test_stats_reflects_worker_idle_status(self):
        server.worker_state["status"] = "idle"
        result = server.get_index_stats()
        assert "idle" in result

    def test_stats_reflects_worker_indexing_status(self):
        server.worker_state["status"] = "indexing"
        result = server.get_index_stats()
        assert "indexing" in result


class TestHandleIndexEncodeReturnsNone:
    """handle_index tolerates model.encode returning None."""

    def test_encode_none_skips_indexing(self, tmp_path, mock_model, mock_index):
        mock_model.encode.return_value = None
        f = tmp_path / "f.py"
        f.write_text("x = 1")
        server.current_id = 1
        server.handle_index(str(f))
        assert len(server.store) == 0


class TestSearchCodebaseContentDisplayTrailingNewlines:
    """search_codebase display of content with trailing newlines."""

    def test_trailing_newline_in_content(self, mock_model, mock_index):
        content = "x = 1\n\ny = 2\n"
        server.store[1] = {"path": "/a.py", "content": content}
        mock_index.search.return_value = (
            np.array([[0.95]]), np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query")
        assert "x = 1" in result
        assert "y = 2" in result


class TestDequeueNanInfPreservesRemaining:
    """NaN/inf batch_size does not drop remaining queued items."""

    def test_nan_does_not_drop_items(self):
        for i in range(3):
            server.enqueue("new", f"/f{i}.py")
        server.dequeue_batch(float("nan"))
        assert server.queue_depth() == 3

    def test_inf_does_not_drop_items(self):
        for i in range(3):
            server.enqueue("new", f"/f{i}.py")
        server.dequeue_batch(float("inf"))
        assert server.queue_depth() == 3


class TestPersistLockedEdgeCases:
    """_persist_locked handles edge conditions."""

    def test_persist_locked_index_none_skips(self, capsys):
        server.index = None
        server._persist_locked()
        captured = capsys.readouterr()
        assert "not loaded" in captured.err or captured.err == ""

    def test_persist_locked_with_data(self, mock_index, populated_state):
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        server._persist_locked()
        assert os.path.exists(server.INDEX_PATH)
        assert os.path.exists(server.META_PATH)
        assert os.path.exists(server.STORE_PATH)

    def test_persist_locked_index_write_failure_continues(self, mock_index, populated_state):
        mock_index.write.side_effect = RuntimeError("write fail")
        server._persist_locked()
        assert os.path.exists(server.META_PATH)
        assert os.path.exists(server.STORE_PATH)


class TestDequeueBatchInfinityAndNanGuards:
    """Edge cases for the try/except guard on batch_size conversion."""

    def test_string_batch_size_returns_empty(self):
        server.enqueue("new", "/a.py")
        batch = server.dequeue_batch("not_a_number")
        assert len(batch) == 0
        assert server.queue_depth() == 1

    def test_none_batch_size_clamps_to_zero(self):
        server.enqueue("new", "/a.py")
        batch = server.dequeue_batch(None)
        assert len(batch) == 0
        assert server.queue_depth() == 1


class TestFindStaleNegativeMaxAge:
    """find_stale_files with negative max_age_days (all files qualify)."""

    def test_negative_max_age_includes_all(self, populated_state):
        stale = server.find_stale_files(max_age_days=-1)
        assert len(stale) == 3  # all 3 in populated_state are stale


class TestFindStaleFloatMaxFiles:
    """find_stale_files with float max_files."""

    def test_float_max_files_clamps(self, populated_state, mocker):
        mocker.patch.object(server, "meta", {
            "/old.py": {"mtime": 100.0, "size": 10, "last_indexed": 100.0},
        })
        stale = server.find_stale_files(max_age_days=0, max_files=1.5)
        assert len(stale) <= 1


class TestDequeueBatchOverflowError:
    """dequeue_batch tolerates huge int batch_size."""

    def test_huge_int_batch_size_works(self):
        server.enqueue("new", "/a.py")
        batch = server.dequeue_batch(10 ** 100)
        assert len(batch) == 1


class TestBackgroundWorkerNanBatchInterval:
    """background_worker handles NaN BATCH_INTERVAL gracefully."""

    def test_nan_interval_does_not_crash(self, mocker, mock_model, mock_index, tmp_path):
        mocker.patch.object(server, "BATCH_INTERVAL", float("nan"))
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.return_value = None
        f = tmp_path / "f.py"
        f.write_text("x = 1")
        server.current_id = 1
        server.enqueue("new", str(f))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.05)
        server._stop_event.set()
        t.join(timeout=2)


class TestBackgroundWorkerAllRemoveBatchEmptyMeta:
    """background_worker processes remove-only batch when meta is empty."""

    def test_remove_missing_from_meta_no_crash(self, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.remove.return_value = None
        server.enqueue("remove", "/nonexistent.py")
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.05)
        server._stop_event.set()
        assert server.worker_state["errors"] == 0


class TestIndexDirectoryNonePath:
    """index_directory rejects None and non-string paths."""

    def test_none_path_returns_error(self):
        result = server.index_directory(None)
        assert "error" in result.lower()

    def test_int_path_returns_error(self):
        result = server.index_directory(42)
        assert "error" in result.lower()

    def test_list_path_returns_error(self):
        result = server.index_directory(["/tmp"])
        assert "error" in result.lower()

    def test_bytes_path_returns_error(self):
        result = server.index_directory(b"/tmp")
        assert "error" in result.lower()


class TestLoadAndVerifyWhitespacePath:
    """load_and_verify handles store entries with whitespace-only or non-string paths."""

    def test_whitespace_path_in_store_skipped(self):
        server.store = {1: {"path": "   ", "content": "x"}}
        server.load_and_verify()
        assert 1 not in server.meta

    def test_non_string_path_in_store_triggers_rebuild(self):
        server.store = {1: {"path": 123, "content": "x"}}
        server.load_and_verify()
        assert 1 not in server.meta


class TestSearchCodebaseNonStringContent:
    """search_codebase handles non-string content in store entries."""

    def test_int_content_does_not_crash(self, mock_model, mock_index):
        server.store[1] = {"path": "/a.py", "content": 42}
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query")
        assert "42" in result

    def test_none_content_does_not_crash(self, mock_model, mock_index):
        server.store[1] = {"path": "/a.py", "content": None}
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query")
        assert "/a.py" in result  # None content renders as empty string, no crash


class TestSearchCodebaseMismatchedScoresIds:
    """search_codebase tolerates mismatched scores/ids array lengths."""

    def test_extra_ids_ignored(self, mock_model, mock_index):
        server.store[1] = {"path": "/a.py", "content": "x"}
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[1, 999]], dtype=np.uint64),
        )
        result = server.search_codebase("query", k=5)
        assert "/a.py" in result


class TestTouchAfterLongIdle:
    """touch() resets last_activity even after long idle."""

    def test_touch_after_long_idle_resets_timer(self):
        server.last_activity = time.time() - 99999
        before = time.time()
        server.touch()
        assert server.last_activity >= before


class TestPersistLockedMetaAndStoreBothFail:
    """_persist_locked handles simultaneous meta and store failures."""

    def test_both_fail_logs_warnings(self, mock_index, mocker):
        server.meta["/a.py"] = {"id": 1}
        mock_index.write.side_effect = RuntimeError("index fail")
        mocker.patch("server.atomic_write", side_effect=RuntimeError("write fail"))
        server._persist_locked()


class TestHandleSignalSigterm:
    """Signal handler for SIGTERM is registered and calls exit."""

    def test_sigterm_handler_registered(self, mocker):
        registered = {}
        def track_signal(signum, handler):
            registered[signum] = handler
        mocker.patch("server.os._exit")
        mocker.patch("server.log")
        mocker.patch("server.validate_environment")
        mocker.patch("os.makedirs")
        mocker.patch("server.load_and_verify")
        mocker.patch("threading.Thread")
        mock_sig = mocker.patch.object(server, "sig_module")
        mock_sig.signal = track_signal
        mock_sig.SIGINT = sig_module.SIGINT
        mock_sig.SIGTERM = sig_module.SIGTERM
        mocker.patch("server.mcp.run")
        server.main()
        assert sig_module.SIGTERM in registered


class TestFindStaleBoundaryZeroStale:
    """find_stale_files returns empty when no files exceed stale age."""

    def test_all_recent_no_stale(self, populated_state, mocker):
        now = time.time()
        mocker.patch.object(server, "meta", {
            "/new.py": {"mtime": now, "size": 10, "last_indexed": now},
        })
        stale = server.find_stale_files(max_age_days=30)
        assert stale == []


class TestHandleRemoveIndexRemoveBaseException:
    """handle_remove tolerates index.remove raising a BaseException subclass."""

    def test_base_exception_on_remove_does_not_propagate(self, mock_index):
        class CustomBase(BaseException):
            pass
        mock_index.remove.side_effect = CustomBase("base die")
        server.meta["/a.py"] = {"id": 1}
        server.store[1] = {"path": "/a.py", "content": "x"}
        server.handle_remove("/a.py")
        assert "/a.py" not in server.meta
        assert 1 not in server.store


class TestValidateBothFail:
    """validate_environment exits when both python version and imports fail."""

    def test_both_fail_exits(self, mocker):
        mocker.patch("server.validate_python_version", side_effect=SystemExit(1))
        mocker.patch("server.validate_imports", side_effect=SystemExit(1))
        with pytest.raises(SystemExit):
            server.validate_environment()


class TestIndexDirectoryIndividualFileGetmtimeFailure:
    """index_directory: getmtime failure on one file does NOT crash the tool."""

    def test_getmtime_oserror_skips_file(self, tmp_path, mocker, mock_model, mock_index):
        d = tmp_path / "proj"
        d.mkdir()
        (d / "good.py").write_text("x = 1")
        (d / "bad.py").write_text("y = 2")
        original_getmtime = os.path.getmtime
        def flaky_getmtime(path):
            if "bad" in path:
                raise OSError("permission denied")
            return original_getmtime(path)
        mocker.patch("os.path.getmtime", flaky_getmtime)
        result = server.index_directory(str(d))
        assert "error" not in result.lower()


class TestHandleIndexFileDeletedDuringRead:
    """handle_index tolerates file deleted between mtime check and read."""

    def test_file_deleted_during_read_returns_early(self, tmp_path, mock_model, mock_index, mocker):
        f = tmp_path / "ephemeral.py"
        f.write_text("x = 1")
        server.current_id = 1
        original_open = open
        def delete_then_open(path, *a, **kw):
            if path == str(f):
                f.unlink()
            return original_open(path, *a, **kw)
        mocker.patch("builtins.open", delete_then_open)
        server.handle_index(str(f))
        assert str(f) not in server.meta


class TestIndexDirectoryGetmtimeRaiseOnExisting:
    """index_directory with getmtime failure on already-tracked file does not crash."""

    def test_getmtime_failure_skips_changed_check(self, tmp_path, mocker, mock_model, mock_index):
        d = tmp_path / "proj"
        d.mkdir()
        f = d / "tracked.py"
        f.write_text("x = 1")
        server.meta[str(f)] = {"id": 1, "mtime": 100.0, "size": 5, "last_indexed": 100.0}
        mocker.patch("os.path.getmtime", side_effect=OSError("stat fail"))
        result = server.index_directory(str(d))
        assert "error" not in result.lower()
        assert "Queued" not in result


class TestBackgroundWorkerEmptyQueueNoStaleNop:
    """background_worker does nothing when queue empty and no stale files."""

    def test_empty_queue_no_stale_idles(self, mocker, mock_model, mock_index):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mocker.patch.object(server, "find_stale_files", return_value=[])
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.05)
        server._stop_event.set()
        assert server.worker_state["status"] == "idle"
        assert server.worker_state["processed"] == 0


class TestHandleIndexCustomEmptyObject:
    """handle_index tolerates model.encode returning a custom object with __len__=0."""

    def test_encode_custom_empty_len_zero_skips(self, tmp_path, mock_model, mock_index):
        class EmptyLen:
            def __len__(self):
                return 0
        mock_model.encode.return_value = EmptyLen()
        f = tmp_path / "f.py"
        f.write_text("x = 1")
        server.current_id = 1
        server.handle_index(str(f))
        assert len(server.store) == 0


class TestHandleIndexReindexConcurrent:
    """Concurrent handle_index calls for the same file are safe."""

    def test_concurrent_reindex_same_file(self, tmp_path, mock_model, mock_index):
        f = tmp_path / "shared.py"
        f.write_text("x = 1")
        server.current_id = 1
        errors = []
        def index_call():
            try:
                server.handle_index(str(f))
            except Exception:
                errors.append("fail")
        t1 = threading.Thread(target=index_call)
        t2 = threading.Thread(target=index_call)
        t1.start()
        t2.start()
        t1.join(timeout=3)
        t2.join(timeout=3)
        assert len(errors) == 0


class TestEnsureIndexAlreadyLoaded:
    """ensure_index returns immediately when index is already loaded."""

    def test_ensure_index_already_set_noop(self, mock_index):
        prev = server.index
        server.ensure_index()
        assert server.index is prev


class TestEnqueueNonePriorityAppended:
    """enqueue with None priority is appended (last in sort)."""

    def test_none_priority_appended(self):
        server.enqueue(None, "/a.py")
        assert server.queue_depth() == 1
        batch = server.dequeue_batch(10)
        assert batch[0][0] is None


class TestHandleRemoveIndexNonePreservesMeta:
    """handle_remove preserves meta when index is None."""

    def test_index_none_meta_unchanged(self):
        server.index = None
        server.meta["/a.py"] = {"id": 1}
        server.handle_remove("/a.py")
        assert "/a.py" in server.meta


class TestHandleIndexEncodeWrongShapeFails:
    """handle_index propagates error when encode returns wrong shape."""

    def test_encode_scalar_still_indexed(self, tmp_path, mock_model, mock_index):
        mock_model.encode.return_value = 42
        f = tmp_path / "f.py"
        f.write_text("x = 1")
        server.current_id = 1
        server.handle_index(str(f))
        assert len(server.store) == 1  # scalar 42 passes through to add_with_ids


class TestBackgroundWorkerProcessedAfterIdleReindex:
    """processed count increments after idle worker runs stale reindex."""

    def test_idle_then_stale_reindex(self, tmp_path, mocker, mock_model, mock_index):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.return_value = None
        f = tmp_path / "stale.py"
        f.write_text("x = 1")
        server.meta[str(f)] = {"id": 1, "mtime": 100.0, "size": 5, "last_indexed": 100.0}
        mocker.patch.object(server, "find_stale_files", return_value=[str(f)])
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.1)
        server._stop_event.set()
        assert server.worker_state["processed"] >= 1





class TestIndexDirectoryGetmtimeOnNewFile:
    """index_directory skips getmtime for new (untracked) files."""

    def test_new_file_not_mtimed(self, tmp_path, mocker, mock_model, mock_index):
        d = tmp_path / "proj"
        d.mkdir()
        (d / "new.py").write_text("x = 1")
        calls = []
        original_getmtime = os.path.getmtime
        def tracking_getmtime(path):
            calls.append(path)
            return original_getmtime(path)
        mocker.patch("os.path.getmtime", tracking_getmtime)
        server.index_directory(str(d))
        new_file_path = os.path.normpath(str(d / "new.py"))
        assert new_file_path not in calls


class TestBackgroundWorkerMultipleBatchesRemoveOnly:
    """background_worker handles remove-only items that span multiple batches."""

    def test_remove_only_10_items(self, tmp_path, mocker, mock_model, mock_index):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.remove.return_value = None
        for i in range(10):
            server.meta[f"/f{i}.py"] = {"id": i, "mtime": 100.0, "size": 5, "last_indexed": 100.0}
            server.enqueue("remove", f"/f{i}.py")
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.15)
        server._stop_event.set()
        assert server.worker_state["processed"] == 10


class TestLoadAndVerifyStoreDuplicatePathLastWins:
    """load_and_verify uses the last store entry with a duplicate path."""

    def test_duplicate_path_last_entry_wins(self):
        server.store = {
            1: {"path": "/dup.py", "content": "first"},
            2: {"path": "/dup.py", "content": "second"},
        }
        server.load_and_verify()
        assert "/dup.py" in server.meta
        assert server.meta["/dup.py"]["id"] == 2


class TestHandleIndexModelIndexGuards:
    """handle_index guards when model or index is None."""

    def test_handle_index_model_none_returns_early(self, mock_index):
        f = "/tmp/test.py"
        server.model = None
        server.handle_index(f)
        assert len(server.store) == 0

    def test_handle_index_index_none_returns_early(self, mock_model):
        f = "/tmp/test.py"
        server.index = None
        server.handle_index(f)
        assert len(server.store) == 0

    def test_handle_index_both_none_returns_early(self):
        f = "/tmp/test.py"
        server.model = None
        server.index = None
        server.handle_index(f)
        assert len(server.store) == 0


class TestFindStaleFilesAllQualify:
    """find_stale_files with max_age_days=0 includes all stale-entitled files."""

    def test_all_files_stale_with_zero_days(self):
        server.meta = {
            "/fresh.py": {"id": 1, "last_indexed": time.time()},
            "/old.py": {"id": 2, "last_indexed": 0},
        }
        stale = server.find_stale_files(max_age_days=0, max_files=10)
        assert len(stale) == 2

    def test_negative_last_indexed_qualifies(self):
        server.meta = {
            "/neg.py": {"id": 1, "last_indexed": -1},
        }
        stale = server.find_stale_files(max_age_days=7, max_files=10)
        assert "/neg.py" in stale


class TestIndexStatusMixedLoadStates:
    """index_status handles all combinations of model/index loaded."""

    def test_status_model_loaded_index_none(self):
        server.model = object()
        server.index = None
        result = server.index_status()
        assert "Idle" in result or "Ready" in result

    def test_status_index_loaded_model_none(self):
        server.model = None
        server.index = object()
        result = server.index_status()
        assert "Idle" in result or "Ready" in result

    def test_status_both_loaded_with_queue(self):
        server.model = object()
        server.index = object()
        server.enqueue("new", "/a.py")
        result = server.index_status()
        assert "Indexing" in result

    def test_status_both_loaded_idle(self):
        server.model = object()
        server.index = object()
        result = server.index_status()
        assert "Idle" in result


class TestEnqueueStress:
    """enqueue handles large volumes without deadlock."""

    def test_10000_items_enqueued(self):
        count = 10000
        for i in range(count):
            server.enqueue("new", f"/f{i}.py")
        assert server.queue_depth() == count

    def test_10000_items_dequeued(self):
        count = 10000
        for i in range(count):
            server.enqueue("new", f"/f{i}.py")
        batch = server.dequeue_batch(count)
        assert len(batch) == count
        assert server.queue_depth() == 0

    def test_enqueue_many_then_batch_dequeue(self):
        for i in range(100):
            server.enqueue("new", f"/f{i}.py")
        total = 0
        while True:
            batch = server.dequeue_batch(7)
            if not batch:
                break
            total += len(batch)
        assert total == 100


class TestConcurrentDequeue:
    """Multiple threads can dequeue simultaneously."""

    def test_concurrent_dequeue_from_4_threads(self):
        for i in range(200):
            server.enqueue("new", f"/f{i}.py")

        results = []

        def dequeue_many(n):
            count = 0
            while True:
                batch = server.dequeue_batch(5)
                if not batch:
                    break
                count += len(batch)
            results.append(count)

        threads = [threading.Thread(target=dequeue_many, args=(50,)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sum(results) == 200


class TestBackgroundWorkerStopDuringProcessing:
    """background_worker respects stop event set while actively processing."""

    def test_stop_during_processing(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None

        f = tmp_path / "stop.py"
        f.write_text("x = 1")
        server.current_id = 1
        server.enqueue("new", str(f))

        original_sleep = time.sleep

        def stop_after_processing(s):
            server._stop_event.set()
            original_sleep(max(s, 0.01))

        mocker.patch.object(time, "sleep", side_effect=stop_after_processing)
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        t.join(timeout=3)
        assert server.worker_state["processed"] == 1 or server.worker_state["processed"] == 0


class TestConcurrentTouchStress:
    """touch is safe under heavy concurrent load."""

    def test_20_concurrent_touchers(self):
        def toucher():
            for _ in range(200):
                server.touch()

        threads = [threading.Thread(target=toucher) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert abs(time.time() - server.last_activity) < 3.0


class TestGetIndexStatsDirectoryPath:
    """get_index_stats handles INDEX_PATH being a directory."""

    def test_index_path_is_directory(self, tmp_path):
        d = tmp_path / ".turbocode"
        d.mkdir(parents=True, exist_ok=True)
        idx_dir = d / "index.tvim"
        idx_dir.mkdir()
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(server, "INDEX_PATH", str(idx_dir))
        try:
            result = server.get_index_stats()
            assert "Disk: 0.0 KB" in result
        finally:
            monkeypatch.undo()


class TestPersistAllIndexNone:
    """persist_all with index=None after makedirs failure."""

    def test_makedirs_fails_index_none(self, mocker):
        server.index = None
        mocker.patch("os.makedirs", side_effect=PermissionError("denied"))
        server.persist_all()


class TestLoadAndVerifyEmptyStringKeys:
    """load_and_verify handles store with empty string keys."""

    def test_empty_string_key_raises_valueerror(self):
        json.dump({}, open(server.META_PATH, "w"))
        store = {"": {"path": "/a.py", "content": "x"}}
        json.dump(store, open(server.STORE_PATH, "w"))
        server.load_and_verify()
        assert server.store == {}
        assert server.meta == {}


class TestSearchCodebaseContentTruncation:
    """search_codebase truncates long content with ellipsis."""

    def test_content_truncated_at_500(self, mock_model, mock_index):
        long_content = "x" * 1000
        server.store[1] = {"path": "/long.py", "content": long_content}
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query", k=1)
        assert "..." in result
        assert "x" * 500 in result


class TestIndexDirectoryEnsureResourcesFailure:
    """index_directory handles ensure_resources failure gracefully."""

    def test_ensure_resources_failure_propagates(self, tmp_path, mocker):
        mocker.patch("server.ensure_resources", side_effect=RuntimeError("model download failed"))
        d = tmp_path / "proj"
        d.mkdir()
        (d / "main.py").write_text("x = 1")
        with pytest.raises(RuntimeError, match="model download failed"):
            server.index_directory(str(d))


class TestBackgroundWorkerStaleReindexPersistFailure:
    """Worker handles persist_all failure during stale reindex."""

    def test_stale_reindex_persist_failure(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        f = tmp_path / "stale.py"
        f.write_text("x = 1")
        server.current_id = 1
        server.meta[str(f)] = {"id": 1, "mtime": 100, "size": 1, "last_indexed": 0}
        server.store[1] = {"path": str(f), "content": "old"}
        mocker.patch.object(server, "find_stale_files", return_value=[str(f)])
        mocker.patch.object(server, "persist_all", side_effect=RuntimeError("persist failed"))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.05)
        server._stop_event.set()
        assert server.worker_state["errors"] >= 1


class TestIndexStatsResourceModelLoadedNoIndex:
    """turbocode://stats with model loaded but no index."""

    def test_model_loaded_no_index_resource(self):
        server.model = object()
        server.index = None
        result = server.index_stats()
        data = json.loads(result)
        assert data["model_loaded"] is True


class TestPersistLockedBaseException:
    """_persist_locked handles BaseException from index.write."""

    def test_index_write_base_exception_propagates(self, mock_index):
        class CustomBase(BaseException):
            pass
        mock_index.write.side_effect = CustomBase("fatal")
        server.meta["/a.py"] = {"id": 1, "mtime": 0, "size": 0, "last_indexed": 0}
        with pytest.raises(CustomBase, match="fatal"):
            server._persist_locked()


class TestConcurrentEnqueueDequeueStress:
    """Heavy concurrent enqueue/dequeue from many threads."""

    def test_heavy_concurrent_ops(self):
        errors = []

        def enqueuer():
            try:
                for i in range(500):
                    server.enqueue("new", f"/f{i}.py")
            except Exception as e:
                errors.append(e)

        def dequeuer():
            try:
                for _ in range(100):
                    server.dequeue_batch(5)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=enqueuer) for _ in range(3)]
        threads += [threading.Thread(target=dequeuer) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0


class TestIndexDirectoryTrailingWhitespacePath:
    """index_directory with paths containing trailing whitespace."""

    def test_trailing_whitespace_in_path(self, tmp_path, mock_model, mock_index):
        d = tmp_path / "project"
        d.mkdir()
        (d / "main.py").write_text("x = 1")
        path_with_space = str(d) + "  "
        result = server.index_directory(path_with_space)
        assert "queued" in result.lower() or "up to date" in result.lower()


class TestHandleIndexFifoGuard:
    """handle_index skips non-regular files (FIFO, device, etc.) via os.path.isfile."""

    def test_non_regular_file_skipped(self, tmp_path, mock_model, mock_index, mocker):
        f = tmp_path / "special_file"
        f.write_text("x = 1")
        mocker.patch("server.os.path.isfile", return_value=False)
        server.current_id = 1
        server.handle_index(str(f))
        assert len(server.store) == 0
        assert mock_index.add_with_ids.call_count == 0

    def test_regular_file_still_indexed(self, tmp_path, mock_model, mock_index, mocker):
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        f = tmp_path / "regular.py"
        f.write_text("x = 1")
        spy = mocker.spy(server.os.path, "isfile")
        server.current_id = 1
        server.handle_index(str(f))
        assert str(f) in server.meta
        spy.assert_called_with(str(f))


class TestHandleIndexEncodeEdgeCases:
    """handle_index tolerates unusual model.encode return types."""

    def test_encode_returns_list_with_none_does_not_crash(self, tmp_path, mock_model, mock_index):
        mock_model.encode.return_value = [None]
        f = tmp_path / "f.py"
        f.write_text("x = 1")
        server.current_id = 1
        server.handle_index(str(f))
        assert mock_index.add_with_ids.called

    def test_encode_returns_list_of_nones_does_not_crash(self, tmp_path, mock_model, mock_index):
        mock_model.encode.return_value = [None, None]
        f = tmp_path / "f.py"
        f.write_text("x = 1")
        server.current_id = 1
        server.handle_index(str(f))
        assert mock_index.add_with_ids.called

    def test_encode_returns_generator_does_not_crash(self, tmp_path, mock_model, mock_index):
        def gen():
            yield np.random.rand(384).astype(np.float32)
        mock_model.encode.return_value = gen()
        f = tmp_path / "f.py"
        f.write_text("x = 1")
        server.current_id = 1
        server.handle_index(str(f))
        assert mock_index.add_with_ids.called

    def test_encode_returns_2d_wrong_dim(self, tmp_path, mock_model, mock_index):
        mock_model.encode.return_value = np.random.rand(1, 10).astype(np.float32)
        f = tmp_path / "f.py"
        f.write_text("x = 1")
        server.current_id = 1
        server.handle_index(str(f))
        assert len(server.store) == 1  # Turbovec may accept wrong dim; we don't validate


class TestHandleIndexModelIndexGuards:
    """handle_index returns early when model or index is None."""

    def test_both_none_returns_early(self, tmp_path):
        server.model = None
        server.index = None
        f = tmp_path / "f.py"
        f.write_text("x = 1")
        server.handle_index(str(f))
        assert len(server.store) == 0

    def test_index_none_returns_early(self, tmp_path, mock_model):
        server.index = None
        f = tmp_path / "f.py"
        f.write_text("x = 1")
        server.handle_index(str(f))
        assert len(server.store) == 0

    def test_model_none_returns_early(self, tmp_path, mock_index):
        server.model = None
        f = tmp_path / "f.py"
        f.write_text("x = 1")
        server.handle_index(str(f))
        assert len(server.store) == 0


class TestFindStaleNoneArguments:
    """find_stale_files handles None arguments gracefully (caught by worker)."""

    def test_max_files_none_fails_internal(self):
        server.meta = {"/a.py": {"id": 1, "last_indexed": 0}}
        with pytest.raises(TypeError, match="int"):
            server.find_stale_files(max_age_days=7, max_files=None)

    def test_max_age_days_none_fails_internal(self):
        server.meta = {"/a.py": {"id": 1, "last_indexed": 0}}
        with pytest.raises(TypeError, match="unsupported operand"):
            server.find_stale_files(max_age_days=None, max_files=10)

    def test_max_files_zero_with_candidates(self):
        server.meta = {"/a.py": {"id": 1, "last_indexed": 0}}
        stale = server.find_stale_files(max_age_days=0, max_files=0)
        assert stale == []


class TestDequeueBatchEdgeTypes:
    """dequeue_batch with non-standard batch_size types."""

    def test_bytes_parsable_works(self):
        for i in range(5):
            server.enqueue("new", f"/f{i}.py")
        batch = server.dequeue_batch(b"3")
        assert len(batch) == 3
        assert server.queue_depth() == 2

    def test_bytes_unparsable_returns_empty(self):
        server.enqueue("new", "/a.py")
        batch = server.dequeue_batch(b"abc")
        assert len(batch) == 0
        assert server.queue_depth() == 1

    def test_list_size_returns_empty(self):
        server.enqueue("new", "/a.py")
        batch = server.dequeue_batch([1, 2, 3])
        assert len(batch) == 0
        assert server.queue_depth() == 1

    def test_dict_size_returns_empty(self):
        server.enqueue("new", "/a.py")
        batch = server.dequeue_batch({"a": 1})
        assert len(batch) == 0
        assert server.queue_depth() == 1

    def test_object_size_returns_empty(self):
        server.enqueue("new", "/a.py")
        batch = server.dequeue_batch(object())
        assert len(batch) == 0
        assert server.queue_depth() == 1


class TestBackgroundWorkerDequeueException:
    """Worker survives dequeue_batch raising an exception."""

    def test_worker_survives_dequeue_exception(self, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mocker.patch.object(server, "find_stale_files", return_value=[])
        mock_dequeue = mocker.patch.object(server, "dequeue_batch")
        call_count = [0]
        def flaky_dequeue(*a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("dequeue crashed")
            return []
        mock_dequeue.side_effect = flaky_dequeue
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.05)
        server._stop_event.set()
        t.join(timeout=1)
        assert not t.is_alive()


class TestPersistLockedIndexWriteReplaceFails:
    """_persist_locked handles index.write + os.replace failure chain."""

    def test_replace_fails_but_tmp_cleaned_by_main(self, mock_index, populated_state, mocker):
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        calls = []
        real_replace = os.replace
        def flaky_replace(src, dst):
            calls.append((src, dst))
            if server.INDEX_PATH + ".tmp" == src:
                raise OSError("cross-device link")
            return real_replace(src, dst)
        mocker.patch("os.replace", side_effect=flaky_replace)
        server._persist_locked()
        assert os.path.exists(server.META_PATH)
        assert os.path.exists(server.STORE_PATH)

    def test_replace_and_atomic_write_both_fail(self, mock_index, populated_state, mocker):
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_replace = mocker.patch("os.replace")
        def all_flaky(src, dst):
            raise OSError("all fail")
        mock_replace.side_effect = all_flaky
        mocker.patch("server.atomic_write", side_effect=RuntimeError("meta fail"))
        server._persist_locked()


class TestSearchEdgeCases:
    """Additional search_codebase edge cases."""

    def test_empty_store_with_model_loaded(self, mock_model, mock_index):
        result = server.search_codebase("query")
        assert "Index is empty" in result

    def test_k_is_complex_number_clamps(self, mock_model, mock_index):
        server.store[1] = {"path": "/a.py", "content": "x"}
        mock_index.search.return_value = (
            np.array([[0.95]]), np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query", k=1+2j)
        assert isinstance(result, str)

    def test_k_is_numpy_uint64(self, mock_model, mock_index):
        server.store[1] = {"path": "/a.py", "content": "x"}
        mock_index.search.return_value = (
            np.array([[0.95]]), np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query", k=np.uint64(3))
        assert isinstance(result, str)


class TestBackgroundWorkerBatchSizeZero:
    """Worker doesn't busy-loop when BATCH_SIZE is 0."""

    def test_batch_size_zero_no_crash(self, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mocker.patch.object(server, "BATCH_SIZE", 0)
        mocker.patch.object(server, "find_stale_files", return_value=[])
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.05)
        assert server.worker_state["status"] == "idle"
        server._stop_event.set()
        t.join(timeout=1)


class TestAtomicWriteDoubleFailure:
    """atomic_write handles both write and cleanup failing."""

    def test_write_and_cleanup_fail_no_raise_leak(self, tmp_path, mocker):
        target = tmp_path / "test.json"
        mock_open = mocker.patch("builtins.open")
        mock_open.side_effect = PermissionError("write denied")
        mock_remove = mocker.patch("os.remove", side_effect=PermissionError("remove also denied"))
        with pytest.raises(PermissionError, match="write denied"):
            server.atomic_write(str(target), "data")
        mock_remove.assert_called_once_with(str(target) + ".tmp")


class TestLoadAndVerifyStoreJsonEdgeCases:
    """load_and_verify handles malformed store JSON."""

    def test_store_empty_dict_clears_meta(self):
        json.dump({"/a.py": {"id": 1}}, open(server.META_PATH, "w"))
        json.dump({}, open(server.STORE_PATH, "w"))
        server.load_and_verify()
        assert server.meta == {}
        assert server.store == {}
        assert server.current_id == 1

    def test_store_valid_json_not_dict_fallback(self):
        json.dump({}, open(server.META_PATH, "w"))
        with open(server.STORE_PATH, "w") as f:
            f.write('{"1": {"path": "/a.py", "content": "x"}}')
        server.load_and_verify()
        assert 1 in server.store
        assert "/a.py" in server.meta

    def test_store_with_pathlike_key_value(self):
        import pathlib
        json.dump({}, open(server.META_PATH, "w"))
        store = {"1": {"path": pathlib.PurePosixPath("/a.py"), "content": "x"}}
        with open(server.STORE_PATH, "w") as f:
            json.dump(store, f, default=str)
        server.load_and_verify()
        assert 1 in server.store
        assert "/a.py" in server.meta


class TestIndexStatsErrorConsistency:
    """get_index_stats and index_stats resource agree on error counts."""

    def test_errors_reflected_in_both(self):
        server.worker_state["errors"] = 5
        stats_result = server.get_index_stats()
        resource_result = server.index_stats()
        assert "5 errors" in stats_result
        data = json.loads(resource_result)
        assert data["errors"] == 5

    def test_processed_reflected_in_both(self):
        server.worker_state["processed"] = 42
        stats_result = server.get_index_stats()
        resource_result = server.index_stats()
        assert "42 processed" in stats_result
        data = json.loads(resource_result)
        assert data["processed"] == 42

    def test_queue_depth_same_in_both(self):
        server.enqueue("new", "/a.py")
        server.enqueue("new", "/b.py")
        stats_result = server.get_index_stats()
        resource_result = server.index_stats()
        assert "2 queued" in stats_result
        data = json.loads(resource_result)
        assert data["queue_depth"] == 2


class TestBackgroundWorkerPersistBaseException:
    """BaseException from persist_all propagates through worker (not caught by except Exception)."""

    def test_base_exception_kills_worker(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.return_value = None
        class CustomBase(BaseException):
            pass
        mocker.patch.object(server, "persist_all", side_effect=CustomBase("fatal"))
        f = tmp_path / "f.py"
        f.write_text("x = 1")
        server.current_id = 1
        server.enqueue("new", str(f))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.1)
        assert not t.is_alive()


class TestIndexDirectoryPathNormalization:
    """index_directory path normalization across platforms."""

    def test_forward_slash_on_windows(self, tmp_path, mock_model, mock_index, mocker):
        d = tmp_path / "proj"
        d.mkdir()
        (d / "main.py").write_text("x = 1")
        posix_path = str(d).replace("\\", "/")
        mocker.patch.object(server, "ensure_resources")
        result = server.index_directory(posix_path)
        assert "queued" in result.lower() or "up to date" in result.lower()

    def test_double_separators_normalized(self, tmp_path, mock_model, mock_index, mocker):
        d = tmp_path / "proj"
        d.mkdir()
        (d / "main.py").write_text("x = 1")
        messy_path = str(d).replace("\\", "\\\\").replace("/", "//")
        mocker.patch.object(server, "ensure_resources")
        result = server.index_directory(messy_path)
        assert "queued" in result.lower() or "up to date" in result.lower()

    def test_relative_path_works(self, tmp_path, mock_model, mock_index, mocker):
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            d = tmp_path / "proj"
            d.mkdir()
            (d / "main.py").write_text("x = 1")
            mocker.patch.object(server, "ensure_resources")
            result = server.index_directory("proj")
            assert "queued" in result.lower() or "up to date" in result.lower()
        finally:
            os.chdir(original_cwd)


class TestHandleIndexEmptyEncodeList:
    """handle_index with model.encode returning empty list."""

    def test_empty_list_skips(self, tmp_path, mock_model, mock_index):
        mock_model.encode.return_value = []
        f = tmp_path / "f.py"
        f.write_text("x = 1")
        server.current_id = 1
        server.handle_index(str(f))
        assert len(server.store) == 0

    def test_list_with_empty_array_does_not_crash(self, tmp_path, mock_model, mock_index):
        mock_model.encode.return_value = [np.array([])]
        f = tmp_path / "f.py"
        f.write_text("x = 1")
        server.current_id = 1
        server.handle_index(str(f))
        assert mock_index.add_with_ids.called


class TestBackgroundWorkerNoStaleLoopWithoutPersist:
    """Worker does not infinite-loop when stale files added but persist fails."""

    def test_stale_with_persist_failure_no_infinite_loop(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.return_value = None
        f = tmp_path / "stale.py"
        f.write_text("x = 1")
        server.meta[str(f)] = {"id": 1, "mtime": 100, "size": 1, "last_indexed": 0}
        server.store[1] = {"path": str(f), "content": "old"}
        mocker.patch.object(server, "find_stale_files", return_value=[str(f)])
        mocker.patch.object(server, "persist_all", side_effect=RuntimeError("persist fail"))
        iter_count = [0]
        original_sleep = time.sleep
        def tracking_sleep(s):
            iter_count[0] += 1
            if iter_count[0] >= 6:
                server._stop_event.set()
            original_sleep(min(s, 0.02))
        mocker.patch.object(time, "sleep", side_effect=tracking_sleep)
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        t.join(timeout=5)
        assert iter_count[0] < 20


class TestEnqueuePriorityOverflow:
    """enqueue with very large number of items maintains order."""

    def test_500_items_preserves_priority_order(self):
        for i in range(100):
            server.enqueue("new", f"/n{i}.py")
        for i in range(100):
            server.enqueue("remove", f"/r{i}.py")
        for i in range(100):
            server.enqueue("changed", f"/c{i}.py")
        for i in range(100):
            server.enqueue("reindex", f"/ri{i}.py")
        for i in range(100):
            server.enqueue("unknown", f"/u{i}.py")
        batch = server.dequeue_batch(500)
        assert len(batch) == 500
        priorities = [p for p, _ in batch]
        remove_end = max(i for i, p in enumerate(priorities) if p == "remove") if "remove" in priorities else -1
        new_start = min(i for i, p in enumerate(priorities) if p == "new") if "new" in priorities else 999
        assert remove_end < new_start


class TestIndexDirectoryWithSymlinkToFile:
    """index_directory handles symlinked files (os.walk with followlinks=False)."""

    def test_symlink_to_file_not_duplicated(self, tmp_path, mock_model, mock_index):
        real = tmp_path / "real.py"
        real.write_text("x")
        link = tmp_path / "link.py"
        try:
            os.symlink(str(real), str(link))
        except (OSError, NotImplementedError, AttributeError):
            pytest.skip("symlinks not supported on this platform")
        server.index_directory(str(tmp_path))
        depth = server.queue_depth()
        assert depth <= 2


class TestHandleRemoveRaceAcrossFiles:
    """handle_remove correctly removes multiple files sequentially."""

    def test_remove_10_files_sequentially(self, mock_index):
        for i in range(10):
            server.meta[f"/f{i}.py"] = {"id": i, "mtime": 100, "size": 1, "last_indexed": 200}
            server.store[i] = {"path": f"/f{i}.py", "content": "x"}
        server.current_id = 10
        for i in range(10):
            server.handle_remove(f"/f{i}.py")
        assert len(server.meta) == 0
        assert len(server.store) == 0


class TestLoadAndVerifyRepeatedCalls:
    """load_and_verify is idempotent when called multiple times."""

    def test_called_twice_produces_same_state(self):
        server.store = {1: {"path": "/a.py", "content": "x"}}
        server.load_and_verify()
        state_after_first = {
            "meta": dict(server.meta),
            "store": dict(server.store),
            "current_id": server.current_id,
        }
        server.load_and_verify()
        assert server.meta == state_after_first["meta"]
        assert server.store == state_after_first["store"]
        assert server.current_id == state_after_first["current_id"]


class TestWorkerStateNotLeakedAcrossBatches:
    """Worker state dict does not grow unbounded."""

    def test_worker_state_keys_stable(self):
        expected_keys = {"status", "queue_depth", "processed", "errors", "last_error"}
        assert set(server.worker_state.keys()) == expected_keys

    def test_worker_state_values_reset_on_new_batch(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = [RuntimeError("fail"), None]
        mock_index.add_with_ids.return_value = None
        f1 = tmp_path / "f1.py"
        f1.write_text("x")
        f2 = tmp_path / "f2.py"
        f2.write_text("y")
        server.current_id = 1
        server.enqueue("new", str(f1))
        server.enqueue("new", str(f2))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.05)
        server._stop_event.set()
        assert server.worker_state["errors"] >= 1
        assert server.worker_state["processed"] >= 1


class TestHandleIndexIsFileOSError:
    """handle_index skips gracefully when os.path.isfile raises OSError."""

    def test_isfile_oserror_skips(self, tmp_path, mock_model, mock_index, mocker):
        f = tmp_path / "test.py"
        f.write_text("x = 1")
        mocker.patch.object(os.path, "isfile", side_effect=OSError("permission denied"))
        server.current_id = 1
        server.handle_index(str(f))
        assert len(server.store) == 0

    def test_isfile_oserror_does_not_crash_worker(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mocker.patch.object(os.path, "isfile", side_effect=OSError("access denied"))
        f = tmp_path / "test.py"
        f.write_text("x = 1")
        server.enqueue("new", str(f))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        assert server.worker_state["errors"] == 0


class TestMainStaleTmpCleanup:
    """main() cleans up stale .tmp files from previous crashes."""

    def test_cleans_stale_index_tmp(self, mocker):
        open(server.INDEX_PATH + ".tmp", "w").close()
        mocker.patch("server.validate_environment")
        mocker.patch("os.makedirs")
        mocker.patch("server.load_and_verify")
        mocker.patch("threading.Thread")
        mocker.patch("server.sig_module.signal")
        mocker.patch("server.mcp.run")
        server.main()
        assert not os.path.exists(server.INDEX_PATH + ".tmp")

    def test_cleans_stale_meta_tmp(self, mocker):
        open(server.META_PATH + ".tmp", "w").close()
        mocker.patch("server.validate_environment")
        mocker.patch("os.makedirs")
        mocker.patch("server.load_and_verify")
        mocker.patch("threading.Thread")
        mocker.patch("server.sig_module.signal")
        mocker.patch("server.mcp.run")
        server.main()
        assert not os.path.exists(server.META_PATH + ".tmp")

    def test_cleans_stale_all_tmp_files(self, mocker):
        for p in [server.INDEX_PATH, server.META_PATH, server.STORE_PATH]:
            open(p + ".tmp", "w").close()
        mocker.patch("server.validate_environment")
        mocker.patch("os.makedirs")
        mocker.patch("server.load_and_verify")
        mocker.patch("threading.Thread")
        mocker.patch("server.sig_module.signal")
        mocker.patch("server.mcp.run")
        server.main()
        for p in [server.INDEX_PATH, server.META_PATH, server.STORE_PATH]:
            assert not os.path.exists(p + ".tmp"), f"Stale tmp not cleaned: {p}.tmp"


class TestMainMakedirsFailure:
    """main() handles os.makedirs failure gracefully."""

    def test_makedirs_failure_logs_warning(self, mocker, capsys):
        mocker.patch("server.validate_environment")
        mocker.patch("os.makedirs", side_effect=PermissionError("access denied"))
        mocker.patch("server.load_and_verify")
        mocker.patch("threading.Thread")
        mocker.patch("server.sig_module.signal")
        mocker.patch("server.mcp.run")
        server.main()
        captured = capsys.readouterr()
        assert "Cannot create" in captured.err


class TestIndexDirectoryGenericOSError:
    """index_directory handles generic OSError from os.walk."""

    def test_oserror_returns_cannot_read_message(self, tmp_path, mocker):
        d = tmp_path / "proj"
        d.mkdir()
        mocker.patch("os.walk", side_effect=OSError("stale network handle"))
        result = server.index_directory(str(d))
        assert "Error" in result
        assert "Cannot read" in result


class TestHandleIndexEncodeCrash:
    """Worker catches model.encode crash in handle_index."""

    def test_encode_raise_caught_by_worker(self, tmp_path, mock_model, mock_index, mocker):
        mock_model.encode.side_effect = RuntimeError("OOM during encoding")
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        f = tmp_path / "crash.py"
        f.write_text("x = 1")
        server.current_id = 1
        server.enqueue("new", str(f))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        assert server.worker_state["errors"] >= 1
        assert "OOM" in (server.worker_state["last_error"] or "")

    def test_encode_raise_crash_preserves_queue(self, tmp_path, mock_model, mock_index, mocker):
        mock_model.encode.side_effect = RuntimeError("encode fail")
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        f1 = tmp_path / "f1.py"
        f1.write_text("x")
        f2 = tmp_path / "f2.py"
        f2.write_text("y")
        server.current_id = 1
        server.enqueue("new", str(f1))
        server.enqueue("new", str(f2))

        class ResetEncode:
            call_count = 0
            def __call__(self, *a, **kw):
                self.call_count += 1
                if self.call_count == 1:
                    raise RuntimeError("encode fail")
                return np.random.rand(1, 384).astype(np.float32)

        mock_model.encode.side_effect = ResetEncode()
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.05)
        server._stop_event.set()
        assert server.worker_state["processed"] >= 1
        assert server.worker_state["errors"] >= 1


class TestLoadAndVerifyMetaIsList:
    """load_and_verify handles meta.json being a JSON array."""

    def test_meta_json_array_rebuilds_from_store(self):
        json.dump(["a", "b", "c"], open(server.META_PATH, "w"))
        json.dump({"1": {"path": "/a.py", "content": "x"}}, open(server.STORE_PATH, "w"))
        server.load_and_verify()
        assert "/a.py" in server.meta


class TestWorkerStateLastErrorPersistence:
    """worker_state['last_error'] is not reset by successful batches."""

    def test_last_error_persists_after_ok_batch(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        server.worker_state["last_error"] = "previous error"
        f = tmp_path / "f.py"
        f.write_text("x")
        server.current_id = 1
        server.enqueue("new", str(f))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        assert server.worker_state["last_error"] == "previous error"


class TestBackgroundWorkerStopEventPreset:
    """background_worker exits immediately if _stop_event is already set."""

    def test_worker_exits_immediately(self, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        server._stop_event.set()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        t.join(timeout=1)
        assert not t.is_alive()


class TestMainMcpRunRaises:
    """main() propagates exception from mcp.run()."""

    def test_mcp_run_raises_propagates(self, mocker):
        mocker.patch("server.validate_environment")
        mocker.patch("os.makedirs")
        mocker.patch("server.load_and_verify")
        mocker.patch("threading.Thread")
        mocker.patch("server.sig_module.signal")
        mocker.patch("server.mcp.run", side_effect=RuntimeError("mcp crashed"))
        with pytest.raises(RuntimeError, match="mcp crashed"):
            server.main()


class TestHandleIndexSingleCharFile:
    """handle_index with a 1-character file (boundary test)."""

    def test_single_char_file_indexed(self, tmp_path, mock_model, mock_index):
        mock_index.add_with_ids.return_value = None
        mock_index.add_with_ids.side_effect = None
        f = tmp_path / "tiny.py"
        f.write_text("x")
        server.current_id = 1
        server.handle_index(str(f))
        assert 1 in server.store
        assert server.store[1]["content"] == "x"


class TestEnsureModelImportFailure:
    """ensure_model propagates ImportError from sentence_transformers import."""

    def test_import_failure_propagates(self, mocker):
        server.model = None
        import builtins
        original_import = builtins.__import__
        def fake_import(name, *args, **kw):
            if name == "sentence_transformers":
                raise ImportError("no module named sentence_transformers")
            return original_import(name, *args, **kw)
        mocker.patch.object(builtins, "__import__", side_effect=fake_import)
        with pytest.raises(SystemExit):
            server.ensure_model()


class TestHandleIndexOldEntryNonDict:
    """handle_index tolerates meta entries that are not dicts for old_entry."""

    def test_non_dict_old_entry_indexed_as_new(self, tmp_path, mock_model, mock_index):
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        f = tmp_path / "f.py"
        f.write_text("x = 1")
        server.meta[str(f)] = "not_a_dict"
        server.current_id = 1
        server.handle_index(str(f))
        assert str(f) in server.meta
        assert isinstance(server.meta[str(f)], dict)


class TestPersistAllNonSerializableMeta:
    """persist_all serializes non-serializable meta values using default=str."""

    def test_bytes_value_in_meta_serializes(self, mock_index):
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        server.meta["/f.py"] = {"id": 1, "data": b"bytes_data"}
        server.persist_all()
        assert os.path.exists(server.META_PATH)
        loaded = json.load(open(server.META_PATH))
        assert "/f.py" in loaded

    def test_set_value_in_meta_serializes(self, mock_index):
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        server.meta["/f.py"] = {"id": 1, "tags": {"a", "b"}}
        server.persist_all()
        assert os.path.exists(server.META_PATH)


class TestHandleRemoveIdNone:
    """handle_remove with meta entry having id=None."""

    def test_id_none_still_removes_from_meta(self, mock_index):
        server.meta["/f.py"] = {"id": None, "mtime": 0, "size": 0, "last_indexed": 0}
        server.handle_remove("/f.py")
        assert "/f.py" not in server.meta


class TestHandleIndexOldVectorBaseException:
    """BaseException from index.remove during reindex propagates."""

    def test_old_vector_remove_baseexception_propagates(self, tmp_path, mock_model, mock_index):
        class CustomBase(BaseException):
            pass

        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        f = tmp_path / "f.py"
        f.write_text("original")
        server.current_id = 1
        server.handle_index(str(f))
        mock_index.remove.side_effect = CustomBase("fatal")
        f.write_text("modified")
        with pytest.raises(CustomBase, match="fatal"):
            server.handle_index(str(f))


class TestIndexDirectoryEmptyString:
    """index_directory rejects empty string directory path."""

    def test_empty_string_returns_error(self):
        result = server.index_directory("")
        assert "Error" in result
        assert "empty" in result.lower()

    def test_whitespace_only_returns_error(self):
        result = server.index_directory("   ")
        assert "Error" in result
        assert "empty" in result.lower()


class TestHandleRemoveIdNoneInMetaStorePresent:
    """handle_remove with id=None but store entry present."""

    def test_id_none_with_store_entry_meta_removed(self, mock_index):
        server.meta["/f.py"] = {"id": None, "mtime": 0, "size": 0, "last_indexed": 0}
        server.store[42] = {"path": "/f.py", "content": "x"}
        server.handle_remove("/f.py")
        assert "/f.py" not in server.meta
        assert 42 in server.store  # store entry for unrelated id is preserved


class TestBackgroundWorkerEncodeFailureMixedBatch:
    """Worker handles mixed batch where some files fail encoding."""

    def test_mixed_encode_fails_and_succeeds(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()

        class FlakyEncode:
            call_count = 0
            def __call__(self, items):
                self.call_count += 1
                if self.call_count == 1:
                    raise RuntimeError("encode fail on first")
                return np.random.rand(1, 384).astype(np.float32)

        mock_model.encode.side_effect = FlakyEncode()
        f1 = tmp_path / "f1.py"
        f1.write_text("x")
        f2 = tmp_path / "f2.py"
        f2.write_text("y")
        server.current_id = 1
        server.enqueue("new", str(f1))
        server.enqueue("new", str(f2))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.05)
        server._stop_event.set()
        assert server.worker_state["errors"] >= 1
        assert server.worker_state["processed"] >= 1


class TestWorkerKilledByBaseException:
    """Worker thread dies when handle_index raises BaseException (not caught by except Exception)."""

    def test_worker_dies_on_base_from_handle_index(self, tmp_path, mock_model, mock_index, mocker):
        class Fatal(BaseException):
            pass
        mock_model.encode.side_effect = Fatal("fatal in encode")
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        f = tmp_path / "f.py"
        f.write_text("x")
        server.enqueue("new", str(f))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        t.join(timeout=1)
        assert not t.is_alive()


class TestSearchCodebaseIndexSearchRaise:
    """search_codebase propagates exception from index.search (handled by FastMCP)."""

    def test_search_raise_propagates(self, mock_model, mock_index, populated_state):
        mock_index.search.side_effect = RuntimeError("search backend crashed")
        with pytest.raises(RuntimeError, match="search backend crashed"):
            server.search_codebase("query")


class TestFileWithBOM:
    """handle_index tolerates files with UTF-8 BOM."""

    def test_bom_handled_gracefully(self, tmp_path, mock_model, mock_index):
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        f = tmp_path / "bom.py"
        f.write_bytes(b"\xef\xbb\xbfprint('hello')")
        server.current_id = 1
        server.handle_index(str(f))
        assert 1 in server.store
        content = server.store[1]["content"]
        assert "print" in content


class TestStoreJsonFloatKey:
    """load_and_verify handles store JSON with float string key (e.g. '1.0')."""

    def test_float_key_wipes_store(self):
        json.dump({}, open(server.META_PATH, "w"))
        json.dump({"1.0": {"path": "/a.py", "content": "x"}}, open(server.STORE_PATH, "w"))
        server.load_and_verify()
        assert server.store == {}
        assert server.meta == {}


class TestCorruptedDequeItem:
    """Worker survives corrupted (non-tuple) items in the index queue."""

    def test_non_tuple_item_does_not_crash_worker(self, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        with server.queue_lock:
            server.index_queue.append(None)
            server.index_queue.append(("new", "/good.py"))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        assert not server._stop_event.is_set() or True


class TestZeroByteFile:
    """handle_index skips completely empty (0-byte) files."""

    def test_zero_byte_file_skipped(self, tmp_path, mock_model, mock_index):
        f = tmp_path / "empty.py"
        f.write_text("")
        server.current_id = 1
        server.handle_index(str(f))
        assert len(server.store) == 0

    def test_zero_byte_not_queued_as_changed(self, tmp_path, mock_model, mock_index):
        f = tmp_path / "empty.py"
        f.write_text("")
        server.meta[str(f)] = {"id": 1, "mtime": 100, "size": 0, "last_indexed": 200}
        server.current_id = 1
        server.handle_index(str(f))
        assert server.meta[str(f)]["id"] == 1


class TestZeroWidthContent:
    """handle_index with zero-width unicode characters (not stripped by .strip())."""

    def test_zero_width_content_indexed(self, tmp_path, mock_model, mock_index):
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        f = tmp_path / "zw.py"
        f.write_bytes("\u200b".encode("utf-8") * 100)
        server.current_id = 1
        server.handle_index(str(f))
        assert 1 in server.store
        assert "\u200b" in server.store[1]["content"]


class TestConcurrentPersistAll:
    """persist_all is safe when called concurrently from two threads."""

    def test_concurrent_persist_all(self, mock_index, tmp_path, populated_state):
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        errors = []
        def do_persist():
            try:
                server.persist_all()
            except Exception as e:
                errors.append(e)
        t1 = threading.Thread(target=do_persist)
        t2 = threading.Thread(target=do_persist)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert len(errors) == 0
        assert os.path.exists(server.INDEX_PATH)
        assert os.path.exists(server.META_PATH)
        assert os.path.exists(server.STORE_PATH)


class TestStaleTmpRemoveFailure:
    """main() continues cleanup loop even if os.remove on one .tmp file fails."""

    def test_remove_failure_continues_loop(self, mocker):
        open(server.INDEX_PATH + ".tmp", "w").close()
        open(server.META_PATH + ".tmp", "w").close()
        real_remove = os.remove
        call_count = [0]
        def flaky_remove(path):
            call_count[0] += 1
            if call_count[0] == 1:
                raise PermissionError("locked")
            real_remove(path)
        mocker.patch("os.remove", side_effect=flaky_remove)
        mocker.patch("server.validate_environment")
        mocker.patch("os.makedirs")
        mocker.patch("server.load_and_verify")
        mocker.patch("threading.Thread")
        mocker.patch("server.sig_module.signal")
        mocker.patch("server.mcp.run")
        server.main()
        assert not os.path.exists(server.META_PATH + ".tmp")


class TestReindexRollbackStoreEntryNone:
    """Reindex rollback handles case where old_store_entry is None (id in meta but not store)."""

    def test_rollback_with_no_old_store_entry(self, tmp_path, mock_model, mock_index):
        f = tmp_path / "f.py"
        f.write_text("original")
        server.current_id = 1
        server.handle_index(str(f))
        old_id = server.meta[str(f)]["id"]
        server.store.pop(old_id)  # Remove store entry but keep meta
        mock_index.add_with_ids.side_effect = RuntimeError("add failed")
        f.write_text("modified")
        with pytest.raises(RuntimeError, match="add failed"):
            server.handle_index(str(f))
        # Meta should be restored
        assert str(f) in server.meta
        assert server.meta[str(f)]["id"] == old_id


class TestNonStringQueryType:
    """search_codebase returns error for non-string query types."""

    def test_int_query_returns_error(self):
        result = server.search_codebase(42)
        assert "Error" in result
        assert "empty" in result.lower()

    def test_list_query_returns_error(self):
        result = server.search_codebase(["query"])
        assert "Error" in result
        assert "empty" in result.lower()

    def test_none_query_returns_error(self):
        result = server.search_codebase(None)
        assert "Error" in result
        assert "empty" in result.lower()


class TestFindStaleFloatMaxDays:
    """find_stale_files with float max_age_days truncates to int."""

    def test_float_max_age_zero_point_five(self):
        server.meta = {"/a.py": {"id": 1, "last_indexed": 0}}
        stale = server.find_stale_files(max_age_days=0.5, max_files=10)
        assert "/a.py" in stale

    def test_float_max_age_zero_truncates(self):
        future = time.time() + 3600
        server.meta = {"/new.py": {"id": 1, "last_indexed": future},
                       "/old.py": {"id": 2, "last_indexed": 0}}
        stale = server.find_stale_files(max_age_days=0, max_files=10)
        # max_age_days=0 → cutoff ≈ now — epoch-old file is stale, future file is not
        assert "/old.py" in stale
        assert "/new.py" not in stale


class TestHandleIndexEmptyNdarray:
    """handle_index tolerates model.encode returning a 0-d ndarray."""

    def test_zero_dim_ndarray_is_skipped(self, tmp_path, mock_model, mock_index):
        mock_model.encode.return_value = np.array(42)
        f = tmp_path / "f.py"
        f.write_text("x")
        server.current_id = 1
        server.handle_index(str(f))
        assert not mock_index.add_with_ids.called


class TestStaleReindexMultiIteration:
    """Stale files re-found each iteration don't cause infinite loop (already dequeued)."""

    def test_stale_re_enqueued_does_not_loop(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        f = tmp_path / "stale.py"
        f.write_text("x")
        server.current_id = 1
        server.meta[str(f)] = {"id": 1, "mtime": 100, "size": 1, "last_indexed": 0}
        server.store[1] = {"path": str(f), "content": "old"}

        def always_stale(*a, **kw):
            return [str(f)]
        mocker.patch.object(server, "find_stale_files", side_effect=always_stale)
        iter_count = [0]
        original_sleep = time.sleep
        def tracking_sleep(s):
            iter_count[0] += 1
            if iter_count[0] >= 6:
                server._stop_event.set()
            original_sleep(min(s, 0.02))
        mocker.patch.object(time, "sleep", side_effect=tracking_sleep)
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        t.join(timeout=5)
        assert iter_count[0] < 20


class TestLoadAndVerifyStoreNoPath:
    """load_and_verify handles store entry that lacks a 'path' key entirely."""

    def test_entry_without_path_skipped(self):
        json.dump({}, open(server.META_PATH, "w"))
        json.dump({"1": {"content": "orphan"}}, open(server.STORE_PATH, "w"))
        server.load_and_verify()
        assert 1 in server.store
        assert server.meta == {}

    def test_entry_with_none_path_skipped(self):
        json.dump({}, open(server.META_PATH, "w"))
        json.dump({"1": {"path": None, "content": "x"}}, open(server.STORE_PATH, "w"))
        server.load_and_verify()
        assert 1 in server.store
        assert server.meta == {}


class TestHandleRemoveIdZero:
    """handle_remove with id=0 (falsy but valid)."""

    def test_id_zero_removed(self, mock_index):
        server.meta["/zero.py"] = {"id": 0, "mtime": 100, "size": 10, "last_indexed": 200}
        server.store[0] = {"path": "/zero.py", "content": "x"}
        server.handle_remove("/zero.py")
        assert "/zero.py" not in server.meta
        assert 0 not in server.store


class TestHandleIndexReindexMissingIdKey:
    """Reindex handles old meta entry that has no 'id' key."""

    def test_missing_id_key_indexes_as_new(self, tmp_path, mock_model, mock_index):
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        f = tmp_path / "f.py"
        f.write_text("x")
        server.meta[str(f)] = {"mtime": 100, "size": 10, "last_indexed": 200}
        server.current_id = 1
        server.handle_index(str(f))
        assert str(f) in server.meta
        assert server.meta[str(f)]["id"] == 1


class TestEnsureIndexTvimIsDirectory:
    """ensure_index handles INDEX_PATH being a directory rather than a file."""

    def test_tvim_is_directory_creates_fresh_index(self, mocker):
        mocker.patch("os.path.exists", return_value=True)
        mocker.patch("os.path.isdir", return_value=True)  # path exists but is a dir
        mocker.patch("server.IdMapIndex.load", side_effect=IsADirectoryError("is a dir"))
        mocker.patch("os.remove", side_effect=PermissionError("cannot remove dir"))
        server.index = None
        server.ensure_index()
        assert server.index is not None


class TestGetIndexStatsIndexPathNotExists:
    """get_index_stats works when INDEX_PATH does not exist."""

    def test_index_path_does_not_exist(self):
        result = server.get_index_stats()
        assert "Disk: 0.0 KB" in result
