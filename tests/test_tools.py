"""
Auto-generated test file for tools.
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



class TestTouch:
    def test_touch_resets_timer(self):
        before = server.last_activity
        server.last_activity = before - 1000
        server.touch()
        assert server.last_activity > before



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



class TestEnsureResourcesFailure:
    def test_model_load_failure_propagates(self, mocker):
        mocker.patch.object(server._ModelClient, '_start', side_effect=RuntimeError("download failed"))
        with pytest.raises(RuntimeError, match="download failed"):
            server.ensure_model()

    def test_index_created_when_tvim_missing(self, mocker):
        mocker.patch("os.path.exists", return_value=False)
        mocker.patch("server.IdMapIndex")
        server.index = None
        server.ensure_index()
        assert server.index is not None



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



class TestTouchInitialValue:
    """touch() sets last_activity to a recent time."""

    def test_touch_sets_last_activity(self):
        before = time.time()
        server.touch()
        assert server.last_activity >= before



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



class TestTouchAfterLongIdle:
    """touch() resets last_activity even after long idle."""

    def test_touch_after_long_idle_resets_timer(self):
        server.last_activity = time.time() - 99999
        before = time.time()
        server.touch()
        assert server.last_activity >= before



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


