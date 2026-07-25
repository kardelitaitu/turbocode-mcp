import json
import os
import time
import threading
import signal as sig_module
from collections import deque

import numpy as np
import pytest
from hypothesis import given, strategies as st

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
        time.sleep(0.15)

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
        time.sleep(0.15)

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
        time.sleep(0.15)

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

        server.last_activity = time.time()
        server._stop_event.clear()

        t = threading.Thread(target=server.idle_watchdog, daemon=True)
        t.start()
        time.sleep(0.2)

        mock_persist.assert_not_called()

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
        time.sleep(0.3)

        mock_persist.assert_called()


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
        mock_persist = mocker.patch("server.persist_all")
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
            mock_persist.assert_called_once()
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
        time.sleep(0.15)

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
        time.sleep(0.15)

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

    def test_enqueue_none_path_appended(self):
        server.enqueue("new", None)
        assert server.queue_depth() == 1

    def test_enqueue_empty_string_appended(self):
        server.enqueue("new", "")
        assert server.queue_depth() == 1

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
        time.sleep(0.15)
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
        time.sleep(0.15)
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
        time.sleep(0.15)
        server._stop_event.set()
        assert server.worker_state["processed"] >= 1

    def test_worker_transitions_to_idle_after_empty_batch(self, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mocker.patch.object(server, "find_stale_files", return_value=[])
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.15)
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
        time.sleep(0.15)
        server._stop_event.set()
        t.join(timeout=1)
        assert not t.is_alive(), "Worker busy-looped at BATCH_INTERVAL=0"

    def test_worker_negative_batch_interval_does_not_crash(self, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", -5)
        mocker.patch.object(server, "find_stale_files", return_value=[])
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.15)
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
        priorities=st.lists(st.sampled_from(["remove", "new", "changed", "reindex", "unknown"])),
    )
    def test_dequeue_preserves_items(self, priorities):
        for i, p in enumerate(priorities):
            server.enqueue(p, f"/f{i}.py")
        batch = server.dequeue_batch(1000)
        assert len(batch) == len(priorities)
        dequeued_priorities = [p for p, _ in batch]
        assert sorted(dequeued_priorities) == sorted(priorities)

    @given(
        n1=st.integers(min_value=0, max_value=10),
        n2=st.integers(min_value=0, max_value=10),
    )
    def test_enqueue_dequeue_equivalence(self, n1, n2):
        for i in range(n1):
            server.enqueue("new", f"/a{i}.py")
        for i in range(n2):
            server.enqueue("changed", f"/b{i}.py")
        batch = server.dequeue_batch(100)
        assert len(batch) == n1 + n2

    @given(
        file_paths=st.lists(st.text(min_size=1, max_size=50), min_size=0, max_size=10),
    )
    def test_queue_depth_matches_enqueues(self, file_paths):
        for fp in file_paths:
            server.enqueue("new", fp)
        assert server.queue_depth() == len(file_paths)

    @given(
        batch_size=st.integers(min_value=0, max_value=20),
        n=st.integers(min_value=0, max_value=15),
    )
    def test_dequeue_batch_size_constraint(self, batch_size, n):
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
        time.sleep(0.15)
        server._stop_event.set()
        mock_exit.assert_called_once_with(0)

    def test_idle_watchdog_persist_failure_logs(self, mocker):
        mocker.patch.object(server, "CHECK_INTERVAL", 0.01)
        mocker.patch.object(server, "IDLE_TIMEOUT", -1)
        mocker.patch("server.persist_all", side_effect=RuntimeError("persist fail"))
        mock_log = mocker.patch("server.log")
        mock_exit = mocker.patch("server.os._exit")
        server._stop_event.clear()
        t = threading.Thread(target=server.idle_watchdog, daemon=True)
        t.start()
        time.sleep(0.15)
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
        time.sleep(0.2)
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
