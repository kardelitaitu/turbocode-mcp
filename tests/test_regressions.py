import json
import time

import numpy as np
import pytest

import server


class TestInputGuards:
    @pytest.mark.parametrize("bad_path", [None, "", 0, [], {}])
    def test_enqueue_ignores_invalid_paths(self, bad_path):
        server.enqueue("new", bad_path)
        assert server.queue_depth() == 0

    @pytest.mark.parametrize("query", [" ", "\t", "\n", "  \n\t  "])
    def test_search_rejects_blank_queries(self, query):
        assert "cannot be empty" in server.search_codebase(query).lower()

    @pytest.mark.parametrize("query", [None, 123, [], {}])
    def test_search_rejects_non_string_queries(self, query):
        assert "cannot be empty" in server.search_codebase(query).lower()


class TestSearchClamp:
    @pytest.mark.parametrize("k", [21, 50, 999, 10**6])
    def test_search_clamps_large_k_to_twenty(self, mock_model, mock_index, k):
        server.store[1] = {"path": "/a.py", "content": "print('x')"}
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[1]], dtype=np.uint64),
        )

        server.search_codebase("query", k=k)

        assert mock_index.search.call_args.kwargs["k"] == 20


class TestColdStartRecovery:
    @pytest.mark.parametrize("payload", ["[]", '"oops"', "123", "null"])
    def test_load_and_verify_rebuilds_meta_from_invalid_payloads(self, monkeypatch, tmp_path, payload):
        d = tmp_path / ".turbocode"
        meta_path = d / "meta.json"
        store_path = d / "store.json"
        meta_path.write_text(payload, encoding="utf-8")
        store_path.write_text(
            json.dumps(
                {
                    "1": {
                        "path": "/a.py",
                        "content": "x",
                        "mtime": 10,
                        "size": 1,
                        "last_indexed": 20,
                    }
                }
            ),
            encoding="utf-8",
        )

        monkeypatch.setattr(server, "META_PATH", str(meta_path))
        monkeypatch.setattr(server, "STORE_PATH", str(store_path))
        server.meta = {}
        server.store = {}
        server.current_id = 0

        server.load_and_verify()

        assert "/a.py" in server.meta
        assert server.current_id == 2


class TestStaleSampling:
    @pytest.mark.parametrize("tracked_count", [6, 10, 25, 100])
    def test_find_stale_files_samples_when_over_limit(self, mocker, tracked_count):
        now = time.time()
        server.meta = {
            f"/stale-{idx}.py": {"id": idx, "last_indexed": now - 14 * 86400} for idx in range(tracked_count)
        }
        sample = mocker.patch("server.random.sample", side_effect=lambda items, n: items[:n])

        stale = server.find_stale_files(max_age_days=7, max_files=5)

        assert len(stale) == 5
        sample.assert_called_once()


class TestDirectoryScans:
    def test_index_directory_rejects_missing_path(self):
        result = server.index_directory("/definitely/missing")
        assert "not found" in result.lower()

    def test_index_directory_rejects_file_path(self, tmp_path):
        path = tmp_path / "file.py"
        path.write_text("print('x')", encoding="utf-8")
        result = server.index_directory(str(path))
        assert "file, not a directory" in result.lower()

    @pytest.mark.parametrize("exc", [PermissionError("denied"), OSError("boom")])
    def test_index_directory_walk_errors(self, tmp_path, mock_model, mock_index, mocker, exc):
        root = tmp_path / "project"
        root.mkdir()
        mocker.patch("os.walk", side_effect=exc)

        result = server.index_directory(str(root))

        assert "error" in result.lower()


class TestStartupAndShutdown:
    def test_main_removes_stale_temp_files(self, tmp_path, mocker, monkeypatch):
        root = tmp_path / ".turbocode"
        index_tmp = root / "index.tvim.tmp"
        meta_tmp = root / "meta.json.tmp"
        store_tmp = root / "store.json.tmp"
        for path in [index_tmp, meta_tmp, store_tmp]:
            path.write_text("tmp", encoding="utf-8")

        monkeypatch.setattr(server, "TURBOCODE_DIR", str(root))
        monkeypatch.setattr(server, "INDEX_PATH", str(root / "index.tvim"))
        monkeypatch.setattr(server, "META_PATH", str(root / "meta.json"))
        monkeypatch.setattr(server, "STORE_PATH", str(root / "store.json"))
        mocker.patch("server.validate_environment")
        mocker.patch("server.load_and_verify")
        mocker.patch("threading.Thread")
        mocker.patch("server.sig_module.signal")
        mocker.patch("server.mcp.run")

        server.main()

        assert not index_tmp.exists()
        assert not meta_tmp.exists()
        assert not store_tmp.exists()

    def test_main_sets_debug_mode_from_flag(self, mocker, monkeypatch):
        monkeypatch.setattr(server, "DEBUG_MODE", False)
        mocker.patch("sys.argv", ["server.py", "--debug"])
        mocker.patch("server.validate_environment")
        mocker.patch("os.makedirs")
        mocker.patch("server.load_and_verify")
        mock_thread = mocker.patch("threading.Thread")
        mock_thread.return_value.start.return_value = None
        mocker.patch("server.sig_module.signal")
        mocker.patch("server.mcp.run")

        server.main()

        assert server.DEBUG_MODE is True

    def test_idle_watchdog_persists_before_exit_when_idle(self, mocker):
        mocker.patch.object(server, "CHECK_INTERVAL", 0)
        mocker.patch.object(server, "IDLE_TIMEOUT", 1)
        mocker.patch.object(server, "last_activity", time.time() - 100)
        mock_persist = mocker.patch.object(server, "persist_all")
        exit_codes = []

        def fake_exit(code):
            exit_codes.append(code)
            raise SystemExit(code)

        mocker.patch("os._exit", side_effect=fake_exit)

        server._stop_event.clear()
        try:
            with pytest.raises(SystemExit):
                server.idle_watchdog()
        finally:
            server._stop_event.set()

        mock_persist.assert_called_once()
        assert exit_codes == [0]


class TestRemovalGuards:
    def test_handle_remove_deletes_corrupt_meta_without_id(self, mock_index):
        server.meta["/bad.py"] = {"mtime": 0, "size": 0}
        server.store[1] = {"path": "/bad.py", "content": "old"}

        server.handle_remove("/bad.py")

        assert "/bad.py" not in server.meta
        assert 1 in server.store

    def test_handle_remove_deletes_non_dict_meta_entry(self, mock_index):
        server.meta["/bad.py"] = ["corrupt"]
        server.store[1] = {"path": "/bad.py", "content": "old"}

        server.handle_remove("/bad.py")

        assert "/bad.py" not in server.meta
        assert 1 in server.store

    def test_search_none_query_returns_error(self, mock_model, mock_index):
        assert "cannot be empty" in server.search_codebase(None).lower()
