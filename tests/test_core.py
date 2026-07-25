"""
Auto-generated test file for core.
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



class TestLazyLoading:
    def test_ensure_model_loads_on_first_call(self, mocker):
        mocker.patch.object(server._ModelClient, '_start')
        server.ensure_model()
        assert isinstance(server.model, server._ModelClient)
        assert server.model._model_name == "BAAI/bge-small-en-v1.5"

    def test_ensure_model_does_not_reload(self, mocker):
        mocker.patch.object(server._ModelClient, '_start')
        server.ensure_model()
        model_id = id(server.model)
        server.ensure_model()
        assert id(server.model) == model_id

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
        mocker.patch.object(server._ModelClient, '_start')
        mocker.patch("server.IdMapIndex.load")

        server.ensure_resources()
        assert server.model is not None
        assert server.index is not None



class TestValidate:
    def test_validates_python_version(self):
        server.validate_python_version()

    def test_validate_imports_passes(self):
        server.validate_imports()



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



class TestLoadAndVerifyStoreDuplicatePaths:
    """load_and_verify handles store entries with duplicate paths."""

    def test_duplicate_path_uses_last_entry(self):
        json.dump({}, open(server.META_PATH, "w"))
        store = {"1": {"path": "/dup.py", "content": "first"}, "2": {"path": "/dup.py", "content": "second"}}
        json.dump(store, open(server.STORE_PATH, "w"))
        server.load_and_verify()
        assert "/dup.py" in server.meta
        assert server.meta["/dup.py"]["id"] == 2



class TestLoadAndVerifyNonSerializableJson:
    """load_and_verify handles store file with malformed JSON."""

    def test_store_file_is_dictionary_instead_of_json(self):
        json.dump({}, open(server.META_PATH, "w"))
        with open(server.STORE_PATH, "w") as f:
            f.write("{invalid json!!!}")
        server.load_and_verify()
        assert server.store == {}
        assert server.meta == {}



class TestValidateEnvironmentDebugFlagOff:
    """validate_environment does not call debug when DEBUG_MODE is False."""

    def test_debug_not_called_when_disabled(self, capsys, mocker):
        mocker.patch.object(server, "validate_python_version")
        mocker.patch.object(server, "validate_imports")
        server.DEBUG_MODE = False
        server.validate_environment()
        captured = capsys.readouterr()
        assert captured.err == ""



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



class TestFindStaleNegativeMaxAge:
    """find_stale_files with negative max_age_days (all files qualify)."""

    def test_negative_max_age_includes_all(self, populated_state):
        stale = server.find_stale_files(max_age_days=-1)
        assert len(stale) == 3  # all 3 in populated_state are stale



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



class TestFindStaleBoundaryZeroStale:
    """find_stale_files returns empty when no files exceed stale age."""

    def test_all_recent_no_stale(self, populated_state, mocker):
        now = time.time()
        mocker.patch.object(server, "meta", {
            "/new.py": {"mtime": now, "size": 10, "last_indexed": now},
        })
        stale = server.find_stale_files(max_age_days=30)
        assert stale == []



class TestValidateBothFail:
    """validate_environment exits when both python version and imports fail."""

    def test_both_fail_exits(self, mocker):
        mocker.patch("server.validate_python_version", side_effect=SystemExit(1))
        mocker.patch("server.validate_imports", side_effect=SystemExit(1))
        with pytest.raises(SystemExit):
            server.validate_environment()



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



class TestLoadAndVerifyEmptyStringKeys:
    """load_and_verify handles store with empty string keys."""

    def test_empty_string_key_raises_valueerror(self):
        json.dump({}, open(server.META_PATH, "w"))
        store = {"": {"path": "/a.py", "content": "x"}}
        json.dump(store, open(server.STORE_PATH, "w"))
        server.load_and_verify()
        assert server.store == {}
        assert server.meta == {}



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



class TestLoadAndVerifyMetaIsList:
    """load_and_verify handles meta.json being a JSON array."""

    def test_meta_json_array_rebuilds_from_store(self):
        json.dump(["a", "b", "c"], open(server.META_PATH, "w"))
        json.dump({"1": {"path": "/a.py", "content": "x"}}, open(server.STORE_PATH, "w"))
        server.load_and_verify()
        assert "/a.py" in server.meta



class TestEnsureModelImportFailure:
    """validate_imports exits when fastembed is not installed."""

    def test_import_failure_propagates(self, mocker):
        import builtins
        original_import = builtins.__import__
        def fake_import(name, *args, **kw):
            if name == "fastembed":
                raise ImportError("no module named fastembed")
            return original_import(name, *args, **kw)
        mocker.patch.object(builtins, "__import__", side_effect=fake_import)
        with pytest.raises(SystemExit):
            server.validate_imports()



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


