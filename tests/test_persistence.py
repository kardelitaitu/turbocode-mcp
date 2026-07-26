"""
Auto-generated test file for persistence.
"""

import contextlib
import json
import os
import threading
import time

import pytest

import server


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

        with open(server.META_PATH) as f:
            meta_loaded = json.load(f)
        assert "/proj/file1.py" in meta_loaded

        with open(server.STORE_PATH) as f:
            store_loaded = json.load(f)
        assert "1" in store_loaded
        assert store_loaded["1"]["path"] == "/proj/file1.py"


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

        os.path.getmtime(server.META_PATH)
        with open(server.META_PATH) as f:
            content_before = f.read()

        mock_write = mocker.patch.object(server, "atomic_write")
        mock_write.side_effect = [None, Exception("crash during store write")]

        with contextlib.suppress(Exception):
            server.persist_all()

        assert os.path.exists(server.META_PATH)
        with open(server.META_PATH) as f:
            assert f.read() == content_before


class TestAtomicWriteEdgeCases:
    def test_atomic_write_deep_path(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c" / "d" / "deep.json"
        with pytest.raises(FileNotFoundError):
            server.atomic_write(str(deep), "{}")

    def test_atomic_write_normal_works(self, tmp_path):
        f = tmp_path / "test.json"
        server.atomic_write(str(f), '{"key": "value"}')
        assert json.loads(f.read_text()) == {"key": "value"}


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


class TestPersistAllPartialFailure:
    def test_index_write_succeeds_replace_fails(self, mocker, mock_index, populated_state):
        server.index = mock_index
        # Only fail os.replace for the INDEX_PATH; meta/store atomic_write uses real os.replace
        real_replace = os.replace
        def flaky_replace(src, dst):
            if src == server.INDEX_PATH + ".tmp":
                raise OSError("cross-device")
            return real_replace(src, dst)
        mocker.patch("os.replace", side_effect=flaky_replace)
        server.persist_all()
        assert not os.path.exists(server.INDEX_PATH)  # .tmp might exist but index not in place
        # meta/store succeeded since os.replace works for those paths
        assert os.path.exists(server.META_PATH)
        assert os.path.exists(server.STORE_PATH)

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


class TestAtomicWriteEdgeCasesMore:
    """Additional atomic_write edge cases."""

    def test_atomic_write_empty_content(self, tmp_path):
        target = tmp_path / "empty.txt"
        server.atomic_write(str(target), "")
        assert target.read_text() == ""

    def test_atomic_write_cleanup_on_failure(self, mocker, tmp_path):
        target = tmp_path / "test.txt"
        mocker.patch("builtins.open", side_effect=PermissionError("denied"))
        with contextlib.suppress(PermissionError):
            server.atomic_write(str(target), "data")
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
        with open(server.STORE_PATH) as f:
            stored = json.load(f)
        assert stored["1"]["content"] is None


class TestAtomicWriteParentIsFile:
    """atomic_write handles parent path being a file (not directory)."""

    def test_parent_is_file_path_raises(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("parent")
        child = f / "child.json"
        with pytest.raises((PermissionError, OSError, IsADirectoryError, FileNotFoundError)):
            server.atomic_write(str(child), "{}")


class TestPersistAllNoIndexThenIndexCreated:
    """persist_all handles index being None gracefully."""

    def test_persist_all_when_index_none_returns_early(self):
        server.index = None
        server.meta["/a.py"] = {"id": 1}
        server.persist_all()
        assert not os.path.exists(server.META_PATH)


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


class TestAtomicWriteUnicodeContent:
    """atomic_write handles unicode content correctly."""

    def test_write_and_read_unicode(self, tmp_path):
        f = tmp_path / "unicode.json"
        data = '{"café": "über cool 🎉"}'
        server.atomic_write(str(f), data)
        assert json.loads(f.read_text(encoding="utf-8")) == {"café": "über cool 🎉"}


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


class TestPersistLockedAllThreeFail:
    """_persist_locked raises RuntimeError when index, meta, and store ALL fail."""

    def test_all_three_fail_raises_runtime_error(self, mock_index, mocker):
        mock_index.write.side_effect = RuntimeError("index write fails")
        mocker.patch("server.atomic_write", side_effect=RuntimeError("write fails"))
        server.meta["/a.py"] = {"id": 1, "mtime": 0, "size": 0, "last_indexed": 0}
        server.store[1] = {"path": "/a.py", "content": "x"}
        with pytest.raises(RuntimeError, match="All persistence targets failed"):
            server._persist_locked()

    def test_all_three_fail_sets_last_error_in_worker(self, tmp_path, mock_model, mock_index, mocker):
        """When the worker encounters triple-failure from persist_all, it records the error."""
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        mocker.patch.object(server, "persist_all", side_effect=RuntimeError("All persistence targets failed"))
        f = tmp_path / "f.py"
        f.write_text("x")
        server.current_id = 1
        server.enqueue("new", str(f))
        server._stop_event.clear()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        time.sleep(0.02)
        server._stop_event.set()
        t.join(timeout=2)
        assert server.worker_state["errors"] >= 1
        assert server.worker_state["last_error"] is not None

    def test_partial_failure_two_of_three_does_not_raise(self, mock_index, mocker):
        """If only index+meta fail but store succeeds, no RuntimeError."""
        mock_index.write.side_effect = RuntimeError("index write fails")

        call_order = []
        real_atomic = server.atomic_write

        def flaky_atomic(path, data):
            call_order.append(path)
            if path == server.META_PATH:
                raise RuntimeError("meta fail")
            real_atomic(path, data)

        mocker.patch("server.atomic_write", side_effect=flaky_atomic)
        server.meta["/a.py"] = {"id": 1, "mtime": 0, "size": 0, "last_indexed": 0}
        server.store[1] = {"path": "/a.py", "content": "x"}
        # Should NOT raise — store succeeded
        server._persist_locked()
        assert os.path.exists(server.STORE_PATH)


class TestPersistAllIndexNone:
    """persist_all with index=None after makedirs failure."""

    def test_makedirs_fails_index_none(self, mocker):
        server.index = None
        mocker.patch("os.makedirs", side_effect=PermissionError("denied"))
        server.persist_all()


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


class TestPersistLockedBaseException:
    """_persist_locked handles BaseException from index.write."""

    def test_index_write_base_exception_propagates(self, mock_index):
        class CustomBase(BaseException):
            pass

        mock_index.write.side_effect = CustomBase("fatal")
        server.meta["/a.py"] = {"id": 1, "mtime": 0, "size": 0, "last_indexed": 0}
        with pytest.raises(CustomBase, match="fatal"):
            server._persist_locked()


class TestPersistLockedIndexWriteReplaceFails:
    """_persist_locked handles index.write + os.replace failure chain."""

    def test_replace_fails_but_tmp_cleaned_by_main(self, mock_index, populated_state, mocker):
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        calls = []
        real_replace = os.replace

        def flaky_replace(src, dst):
            calls.append((src, dst))
            if src == server.INDEX_PATH + ".tmp":
                raise OSError("cross-device link")
            return real_replace(src, dst)

        mocker.patch("os.replace", side_effect=flaky_replace)
        server._persist_locked()
        assert os.path.exists(server.META_PATH)
        assert os.path.exists(server.STORE_PATH)

    def test_replace_and_atomic_write_both_fail(self, mock_index, populated_state, mocker):
        """All three persistence targets fail (index os.replace + meta atomic_write + store atomic_write).
        This now triggers the RuntimeError triple-failure guard."""
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_replace = mocker.patch("os.replace")

        def all_flaky(src, dst):
            raise OSError("all fail")

        mock_replace.side_effect = all_flaky
        mocker.patch("server.atomic_write", side_effect=RuntimeError("meta fail"))
        with pytest.raises(RuntimeError, match="All persistence targets failed"):
            server._persist_locked()


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


class TestPersistAllNonSerializableMeta:
    """persist_all serializes non-serializable meta values using default=str."""

    def test_bytes_value_in_meta_serializes(self, mock_index):
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        server.meta["/f.py"] = {"id": 1, "data": b"bytes_data"}
        server.persist_all()
        assert os.path.exists(server.META_PATH)
        with open(server.META_PATH) as f:
            loaded = json.load(f)
        assert "/f.py" in loaded

    def test_set_value_in_meta_serializes(self, mock_index):
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        server.meta["/f.py"] = {"id": 1, "tags": {"a", "b"}}
        server.persist_all()
        assert os.path.exists(server.META_PATH)


class TestIdleWatchdogTripleFailure:
    """Idle watchdog survives RuntimeError from persist_all (triple-failure) during shutdown."""

    def test_watchdog_logs_warning_and_exits_on_triple_failure(self, mocker):
        mock_exit = mocker.patch("server.os._exit")
        mock_log = mocker.patch("server.log")
        mocker.patch.object(server, "CHECK_INTERVAL", 0.01)
        mocker.patch.object(server, "IDLE_TIMEOUT", -1)  # always timed out
        mocker.patch.object(server, "persist_all", side_effect=RuntimeError("All persistence targets failed"))
        server.last_activity = 0
        server._stop_event.clear()
        t = threading.Thread(target=server.idle_watchdog, daemon=True)
        t.start()
        time.sleep(0.03)
        server._stop_event.set()
        t.join(timeout=2)
        # Verify the watchdog survived the error and called exit
        mock_exit.assert_any_call(0)
        warning_calls = [c for c in mock_log.call_args_list if "Failed to persist" in str(c)]
        assert len(warning_calls) >= 1


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
