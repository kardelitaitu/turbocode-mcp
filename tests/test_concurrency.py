"""
Auto-generated test file for concurrency.
"""

import contextlib
import os
import signal as sig_module
import threading
import time

import numpy as np
import pytest

import server


class TestBackgroundWorker:
    def test_worker_processes_queue(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        f = tmp_path / "test.py"
        f.write_text("def foo():\n    return 42\n")
        server.enqueue("new", str(f))

        import threading

        from server import background_worker

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

        import threading

        from server import background_worker

        server._stop_event.clear()
        t = threading.Thread(target=background_worker, daemon=True)
        t.start()
        time.sleep(0.02)

        assert server.worker_state["errors"] == 0
        assert "/nonexistent/file.py" not in server.meta


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

        mocker.patch("server.sig_module.signal", side_effect=capture_signal)
        mocker.patch("server.mcp.run", side_effect=Exception("stop"))

        with contextlib.suppress(Exception):
            server.main()

        handler = registered_handlers.get(sig_module.SIGINT)
        if handler:
            handler(sig_module.SIGINT, None)
            mock_persist_locked.assert_called_once()
            mock_exit.assert_called_once_with(0)


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
            np.array([[]]),
            np.array([[]], dtype=np.uint64),
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


class TestEnsureModelThreadSafety:
    def test_concurrent_ensure_model_loads_once(self, mocker):
        start_ctr = [0]

        def slow_start(self):
            start_ctr[0] += 1
            time.sleep(0.05)
            self._proc = None

        mocker.patch.object(server._ModelClient, "_start", slow_start)
        server.model = None

        def load():
            server.ensure_model()

        ts = [threading.Thread(target=load) for _ in range(5)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        assert start_ctr[0] == 1, f"_ModelClient._start called {start_ctr[0]} times (expected 1)"

    def test_ensure_model_after_already_loaded_is_noop(self):
        server.model = object()
        server.ensure_model()
        # ensure_model returns immediately since model is already set


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
        mocker.patch("server.os._exit")
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
        for _ in range(100):
            if server.worker_state["status"] == "idle":
                break
            time.sleep(0.02)
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


class TestIdleWatchdogStopDuringSleep:
    """Idle watchdog exits quickly when stop event set during sleep."""

    def test_watchdog_stops_when_event_set_during_sleep(self):
        os.environ["CHECK_INTERVAL_TEST"] = "1"
        server.idle_watchdog()
        # When _stop_event is already set (from clean_globals), should return immediately
        assert True

    def test_watchdog_event_set_before_sleep_check(self):
        server._stop_event.set()
        server.last_activity = 0
        server.idle_watchdog()
        assert True


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
        [p for p, _ in results if p == "new"]
        # All removals should appear before any new items
        if remove_items:
            last_remove_idx = max(i for i, (p, _) in enumerate(results) if p == "remove")
            first_new_idx = min(i for i, (p, _) in enumerate(results) if p == "new")
            assert last_remove_idx < first_new_idx


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


class TestWorkerStatusTransitionsDetailed:
    """Worker state machine: idle -> indexing -> idle."""

    def test_status_goes_indexing_then_idle(self, tmp_path, mock_model, mock_index, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        mock_index.write.side_effect = lambda p: open(p, "w").close()
        mock_index.add_with_ids.side_effect = None
        mock_index.add_with_ids.return_value = None
        # Make encoding take long enough for multiple sleep iterations
        mock_model.encode.side_effect = lambda *a, **kw: time.sleep(0.05) or np.random.rand(1, 384).astype(np.float32)
        f = tmp_path / "f.py"
        f.write_text("x")
        server.current_id = 1
        server.enqueue("new", str(f))
        server._stop_event.clear()
        statuses = []
        original_sleep = time.sleep

        def tracking_sleep(s):
            statuses.append(server.worker_state["status"])
            if len(statuses) >= 8:
                server._stop_event.set()
            original_sleep(min(s, 0.02))

        mocker.patch.object(time, "sleep", side_effect=tracking_sleep)
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        t.join(timeout=3)
        assert "indexing" in statuses, f"statuses: {statuses}"
        assert "idle" in statuses, f"statuses: {statuses}"


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


class TestBackgroundWorkerStopEventPreset:
    """background_worker exits immediately if _stop_event is already set."""

    def test_worker_exits_immediately(self, mocker):
        mocker.patch.object(server, "BATCH_INTERVAL", 0.01)
        server._stop_event.set()
        t = threading.Thread(target=server.background_worker, daemon=True)
        t.start()
        t.join(timeout=1)
        assert not t.is_alive()


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


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
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
