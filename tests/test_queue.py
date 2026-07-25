"""
Auto-generated test file for queue.
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



class TestDequeueBatchFloatSize:
    """dequeue_batch handles float batch_size gracefully."""

    def test_float_size_truncated(self):
        for i in range(5):
            server.enqueue("new", f"/f{i}.py")
        batch = server.dequeue_batch(2.7)
        assert len(batch) == 2
        assert server.queue_depth() == 3



class TestEnqueueBytes:
    """enqueue rejects bytes objects."""

    def test_enqueue_bytes_path_skipped(self):
        server.enqueue("new", b"/path.py")
        assert server.queue_depth() == 0

    def test_enqueue_bytearray_path_skipped(self):
        server.enqueue("new", bytearray(b"/path.py"))
        assert server.queue_depth() == 0



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



class TestGetIndexStatsQueueDepth:
    """get_index_stats reflects queue depth."""

    def test_stats_shows_queue_depth(self):
        server.enqueue("new", "/p.py")
        server.enqueue("new", "/q.py")
        result = server.get_index_stats()
        assert "2 queued" in result



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



class TestEnqueueNonePriority:
    """enqueue handles None priority without crashing."""

    def test_none_priority_appended(self):
        server.enqueue(None, "/a.py")
        batch = server.dequeue_batch(5)
        assert len(batch) == 1
        assert batch[0][0] is None
        assert batch[0][1] == "/a.py"



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



class TestDequeueBatchOverflowError:
    """dequeue_batch tolerates huge int batch_size."""

    def test_huge_int_batch_size_works(self):
        server.enqueue("new", "/a.py")
        batch = server.dequeue_batch(10 ** 100)
        assert len(batch) == 1



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



class TestEnqueueNonePriorityAppended:
    """enqueue with None priority is appended (last in sort)."""

    def test_none_priority_appended(self):
        server.enqueue(None, "/a.py")
        assert server.queue_depth() == 1
        batch = server.dequeue_batch(10)
        assert batch[0][0] is None



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


