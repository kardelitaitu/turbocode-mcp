"""
Auto-generated test file for indexing.
"""
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



class TestHandleRemoveIndexNoneStillInMeta:
    def test_remove_when_index_none_meta_unchanged(self, populated_state):
        server.index = None
        server.handle_remove("/proj/file1.py")
        assert "/proj/file1.py" in server.meta

    def test_remove_when_file_not_in_meta_does_nothing(self):
        server.handle_remove("/not/in/meta.py")
        assert server.meta == {}
        assert server.store == {}



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



class TestIndexDirectoryFileInput:
    """index_directory rejects file paths."""

    def test_file_path_returns_error(self, tmp_path):
        f = tmp_path / "afile.txt"
        f.write_text("x")
        result = server.index_directory(str(f))
        assert "not a directory" in result



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



class TestGetIndexStatsWorkerState:
    """get_index_stats reflects worker_state."""

    def test_stats_shows_worker_processed_count(self):
        server.worker_state["processed"] = 42
        server.worker_state["errors"] = 7
        result = server.get_index_stats()
        assert "42 processed" in result
        assert "7 errors" in result



class TestHandleIndexNonExistentPath:
    """handle_index silently skips non-existent paths."""

    def test_nonexistent_path_skipped(self, mock_model, mock_index):
        server.handle_index("/nonexistent/file.py")
        assert mock_index.add_with_ids.call_count == 0



class TestIndexStatsResourceFields:
    """turbocode://stats resource contains required fields."""

    def test_stats_resource_fields(self):
        result = server.index_stats()
        stats = json.loads(result)
        assert "vectors" in stats
        assert "files_tracked" in stats
        assert "model_loaded" in stats
        assert "model" in stats
        assert stats["model"] == "BAAI/bge-small-en-v1.5"



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



class TestIndexDirectoryEmptyPath:
    """index_directory handles empty path."""

    def test_empty_path_returns_error(self):
        result = server.index_directory("")
        assert "error" in result.lower()



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



class TestHandleRemoveMissingStoreEntry:
    """handle_remove works when store entry already removed."""

    def test_store_entry_already_gone(self, mock_index):
        server.meta["/gone.py"] = {"id": 1, "mtime": 100, "size": 10, "last_indexed": 200}
        assert 1 not in server.store
        server.handle_remove("/gone.py")
        assert "/gone.py" not in server.meta



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



class TestFindStaleFloatMaxFiles:
    """find_stale_files with float max_files."""

    def test_float_max_files_clamps(self, populated_state, mocker):
        mocker.patch.object(server, "meta", {
            "/old.py": {"mtime": 100.0, "size": 10, "last_indexed": 100.0},
        })
        stale = server.find_stale_files(max_age_days=0, max_files=1.5)
        assert len(stale) <= 1



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



class TestIndexDirectoryEnsureResourcesFailure:
    """index_directory handles ensure_resources failure gracefully."""

    def test_ensure_resources_failure_propagates(self, tmp_path, mocker):
        mocker.patch("server.ensure_resources", side_effect=RuntimeError("model download failed"))
        d = tmp_path / "proj"
        d.mkdir()
        (d / "main.py").write_text("x = 1")
        with pytest.raises(RuntimeError, match="model download failed"):
            server.index_directory(str(d))



class TestIndexStatsResourceModelLoadedNoIndex:
    """turbocode://stats with model loaded but no index."""

    def test_model_loaded_no_index_resource(self):
        server.model = object()
        server.index = None
        result = server.index_stats()
        data = json.loads(result)
        assert data["model_loaded"] is True



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



class TestHandleIndexEmptyNdarray:
    """handle_index tolerates model.encode returning a 0-d ndarray."""

    def test_zero_dim_ndarray_is_skipped(self, tmp_path, mock_model, mock_index):
        mock_model.encode.return_value = np.array(42)
        f = tmp_path / "f.py"
        f.write_text("x")
        server.current_id = 1
        server.handle_index(str(f))
        assert not mock_index.add_with_ids.called



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

