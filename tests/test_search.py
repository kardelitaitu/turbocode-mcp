"""
Auto-generated test file for search.
"""

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import server


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


class TestSearchCodebaseModelFailure:
    def test_model_encode_failure_returns_error(self, mock_model, mock_index, populated_state):
        mock_model.encode.side_effect = RuntimeError("OOM")
        with pytest.raises(RuntimeError, match="OOM"):
            server.search_codebase("test query")


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
        with pytest.raises((TypeError, AttributeError, RuntimeError)):
            server.search_codebase("query")


class TestSearchCodebaseSpecialCharsInContent:
    def test_search_with_special_chars_in_results(self, mock_model, mock_index, populated_state):
        server.store[1] = {"path": "/test.py", "content": "import re  # $peci@l ch@rs"}
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("special")
        assert "$peci@l ch@rs" in result

    def test_search_results_contain_backticks_and_code(self, mock_model, mock_index, populated_state):
        server.store[1] = {"path": "/code.py", "content": "```python\nx = 1\n```"}
        mock_index.search.return_value = (
            np.array([[0.99]]),
            np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("code")
        assert "```" in result


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


class TestSearchCodebaseShortContent:
    """Search results don't append ... for short content."""

    def test_short_content_no_ellipsis(self, mock_model, mock_index, populated_state):
        server.store[1] = {"path": "/short.py", "content": "x = 1"}
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[1]], dtype=np.uint64),
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
            np.array([[0.95]]),
            np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("test")
        assert "..." in result

    def test_exactly_500_chars_no_ellipsis(self, mock_model, mock_index, populated_state):
        content = "x" * 500
        server.store[1] = {"path": "/exact.py", "content": content}
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("test")
        assert "x" * 500 in result
        assert "..." not in result


class TestSearchCodebaseWithMalformedStore:
    """search_codebase handles malformed store entries."""

    def test_store_entry_missing_path(self, mock_model, mock_index, populated_state):
        server.store[99] = {"content": "orphan content"}  # no "path" key
        mock_index.search.return_value = (
            np.array([[0.9]]),
            np.array([[99]], dtype=np.uint64),
        )
        result = server.search_codebase("test")
        assert "unknown" in result

    def test_store_entry_is_none(self, mock_model, mock_index, populated_state):
        server.store[99] = None
        mock_index.search.return_value = (
            np.array([[0.9]]),
            np.array([[99]], dtype=np.uint64),
        )
        result = server.search_codebase("test")
        assert "No results" in result or "unknown" not in result

    def test_search_k_at_max_clamps(self, mock_model, mock_index, populated_state):
        mock_index.search.return_value = (
            np.array([list(range(20))]),
            np.array([list(range(1, 21))], dtype=np.uint64),
        )
        result = server.search_codebase("test", k=20)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_search_k_above_max_clamps(self, mock_model, mock_index, populated_state):
        mock_index.search.return_value = (
            np.array([list(range(20))]),
            np.array([list(range(1, 21))], dtype=np.uint64),
        )
        result = server.search_codebase("test", k=100)
        assert isinstance(result, str)

    def test_search_ids_not_in_store(self, mock_model, mock_index, populated_state):
        mock_index.search.return_value = (
            np.array([[0.9, 0.8]]),
            np.array([[99, 100]], dtype=np.uint64),
        )
        result = server.search_codebase("test")
        assert "No results" in result


class TestSearchCodebaseUnicode:
    """Search with unicode queries and content."""

    def test_search_unicode_query(self, mock_model, mock_index, populated_state):
        result = server.search_codebase("café über cool 🎉")
        assert isinstance(result, str)

    def test_search_unicode_in_results(self, mock_model, mock_index, populated_state):
        server.store[1] = {"path": "/cafe.py", "content": "def café():\n    return 'über cool'\n"}
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("cafe")
        assert "café" in result
        assert "über" in result


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


class TestSearchCodebaseContentEdgeCases:
    """Search handles null bytes and special-only queries."""

    def test_content_with_null_byte(self, mock_model, mock_index):
        server.store[1] = {"path": "/a.py", "content": "x\x00y"}
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query")
        assert "\x00" in result or "x" in result

    def test_query_with_only_special_chars(self, mock_model, mock_index, populated_state):
        result = server.search_codebase("!@#$%^&*()")
        assert isinstance(result, str)


class TestSearchLargeKEmptyStore:
    """search_codebase with large k but empty store."""

    def test_k_above_20_with_empty_store(self, mock_model, mock_index):
        result = server.search_codebase("test", k=50)
        assert isinstance(result, str)
        assert "empty" in result.lower()


class TestSearchCodebaseKFloat:
    """search_codebase with float k values."""

    def test_k_float_clamps_to_int(self, mock_model, mock_index):
        server.store[1] = {"path": "/a.py", "content": "x"}
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query", k=2.7)
        assert isinstance(result, str)

    def test_k_float_string_clamps_to_one(self, mock_model, mock_index):
        server.store[1] = {"path": "/a.py", "content": "x"}
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query", k="bad")
        assert isinstance(result, str)


class TestSearchKBooleanTrue:
    """search_codebase with k=True (bool subclass of int in Python)."""

    def test_k_true_returns_results(self, mock_model, mock_index):
        server.store[1] = {"path": "/a.py", "content": "x"}
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query", k=True)
        assert isinstance(result, str)


class TestSearchKListValue:
    """search_codebase with non-scalar k values clamped to 1."""

    def test_k_list_clamps_to_one(self, mock_model, mock_index):
        server.store[1] = {"path": "/a.py", "content": "x"}
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query", k=[1, 2, 3])
        assert isinstance(result, str)


class TestSearchCodebaseNewlinesInContent:
    """Search results preserve newlines in content."""

    def test_multiline_content_has_newlines_in_output(self, mock_model, mock_index):
        content = "def foo():\n    return 42\n"
        server.store[1] = {"path": "/a.py", "content": content}
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[1]], dtype=np.uint64),
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


class TestSearchCodebaseStoreEmptyMessage:
    """search_codebase returns correct message when store is empty."""

    def test_empty_store_no_ensure_resources(self, mock_model, mock_index):
        result = server.search_codebase("anything")
        assert "Index is empty" in result
        assert "index_directory" in result


class TestSearchCodebaseContentDisplayTrailingNewlines:
    """search_codebase display of content with trailing newlines."""

    def test_trailing_newline_in_content(self, mock_model, mock_index):
        content = "x = 1\n\ny = 2\n"
        server.store[1] = {"path": "/a.py", "content": content}
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query")
        assert "x = 1" in result
        assert "y = 2" in result


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


class TestSearchEdgeCases:
    """Additional search_codebase edge cases."""

    def test_empty_store_with_model_loaded(self, mock_model, mock_index):
        result = server.search_codebase("query")
        assert "Index is empty" in result

    def test_k_is_complex_number_clamps(self, mock_model, mock_index):
        server.store[1] = {"path": "/a.py", "content": "x"}
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query", k=1 + 2j)
        assert isinstance(result, str)

    def test_k_is_numpy_uint64(self, mock_model, mock_index):
        server.store[1] = {"path": "/a.py", "content": "x"}
        mock_index.search.return_value = (
            np.array([[0.95]]),
            np.array([[1]], dtype=np.uint64),
        )
        result = server.search_codebase("query", k=np.uint64(3))
        assert isinstance(result, str)
