"""Full MCP protocol integration test — spawns the actual server over stdio."""

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid

import pytest

SERVER_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "src", "server.py")
_msg_id = 0


def _next_id():
    global _msg_id
    _msg_id += 1
    return _msg_id


def _send(pipe, msg):
    """Send a JSON-RPC message as newline-delimited JSON."""
    body = json.dumps(msg)
    pipe.write(body + "\n")
    pipe.flush()


def _recv(pipe, timeout=60):
    """Read one JSON-RPC message (one line, newline-delimited)."""
    start = time.time()
    while time.time() - start < timeout:
        line = pipe.readline()
        if not line:
            time.sleep(0.05)
            continue
        return json.loads(line.strip())
    raise TimeoutError("No response from server within timeout")


@pytest.fixture(scope="module")
def mcp_server():
    proc = subprocess.Popen(
        [sys.executable, "-u", SERVER_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=0,
    )
    yield proc
    try:
        proc.stdin.close()
        proc.wait(timeout=3)
    except Exception:
        proc.kill()
        proc.wait()


class TestMCPProtocol:
    def test_initialize_handshake(self, mcp_server):
        _send(
            mcp_server.stdin,
            {
                "jsonrpc": "2.0",
                "id": _next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "turbocode-test", "version": "1.0"},
                },
            },
        )
        resp = _recv(mcp_server.stdout)
        assert resp["jsonrpc"] == "2.0"
        assert "result" in resp
        assert resp["result"]["serverInfo"]["name"] == "TurboCode MCP"

    def test_initialized_notification(self, mcp_server):
        _send(
            mcp_server.stdin,
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
        )

    def test_tools_list(self, mcp_server):
        _send(
            mcp_server.stdin,
            {
                "jsonrpc": "2.0",
                "id": _next_id(),
                "method": "tools/list",
            },
        )
        resp = _recv(mcp_server.stdout)
        assert resp["jsonrpc"] == "2.0"
        tools = resp["result"]["tools"]
        names = [t["name"] for t in tools]
        assert "index_directory" in names
        assert "search_codebase" in names
        assert "get_index_stats" in names

    def test_get_index_stats_tool(self, mcp_server):
        _send(
            mcp_server.stdin,
            {
                "jsonrpc": "2.0",
                "id": _next_id(),
                "method": "tools/call",
                "params": {"name": "get_index_stats", "arguments": {}},
            },
        )
        resp = _recv(mcp_server.stdout)
        assert resp["jsonrpc"] == "2.0"
        content = resp["result"]["content"][0]["text"]
        assert "Index Stats" in content
        assert "Vectors:" in content

    def test_index_directory_tool_not_found(self, mcp_server):
        nonexistent = os.path.join(os.sep, uuid.uuid4().hex[:16])
        _send(
            mcp_server.stdin,
            {
                "jsonrpc": "2.0",
                "id": _next_id(),
                "method": "tools/call",
                "params": {"name": "index_directory", "arguments": {"directory_path": nonexistent}},
            },
        )
        resp = _recv(mcp_server.stdout)
        assert resp["jsonrpc"] == "2.0"
        content = resp["result"]["content"][0]["text"]
        assert "not found" in content.lower()

    def test_resources_list(self, mcp_server):
        _send(
            mcp_server.stdin,
            {
                "jsonrpc": "2.0",
                "id": _next_id(),
                "method": "resources/list",
            },
        )
        resp = _recv(mcp_server.stdout)
        assert resp["jsonrpc"] == "2.0"
        resources = resp["result"]["resources"]
        uris = [r["uri"] for r in resources]
        assert "turbocode://status" in uris
        assert "turbocode://stats" in uris

    def test_resource_status(self, mcp_server):
        _send(
            mcp_server.stdin,
            {
                "jsonrpc": "2.0",
                "id": _next_id(),
                "method": "resources/read",
                "params": {"uri": "turbocode://status"},
            },
        )
        resp = _recv(mcp_server.stdout)
        assert resp["jsonrpc"] == "2.0"
        content = resp["result"]["contents"][0]["text"]
        assert "Ready" in content or "Idle" in content

    def test_resource_stats(self, mcp_server):
        _send(
            mcp_server.stdin,
            {
                "jsonrpc": "2.0",
                "id": _next_id(),
                "method": "resources/read",
                "params": {"uri": "turbocode://stats"},
            },
        )
        resp = _recv(mcp_server.stdout)
        assert resp["jsonrpc"] == "2.0"
        stats = json.loads(resp["result"]["contents"][0]["text"])
        assert "vectors" in stats
        assert "files_tracked" in stats
        assert "model_loaded" in stats

    def test_search_empty_index(self, mcp_server):
        _send(
            mcp_server.stdin,
            {
                "jsonrpc": "2.0",
                "id": _next_id(),
                "method": "tools/call",
                "params": {"name": "search_codebase", "arguments": {"query": "test"}},
            },
        )
        resp = _recv(mcp_server.stdout)
        assert resp["jsonrpc"] == "2.0"
        content = resp["result"]["content"][0]["text"]
        # May be "Index is empty" or actual results from a previous test run
        assert len(content) > 0

    def test_search_empty_query(self, mcp_server):
        _send(
            mcp_server.stdin,
            {
                "jsonrpc": "2.0",
                "id": _next_id(),
                "method": "tools/call",
                "params": {"name": "search_codebase", "arguments": {"query": ""}},
            },
        )
        resp = _recv(mcp_server.stdout)
        assert resp["jsonrpc"] == "2.0"
        content = resp["result"]["content"][0]["text"]
        assert "Error" in content

    def test_index_directory_success(self, mcp_server):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "main.py"), "w") as f:
                f.write("def hello(name):\n    return f'Hi {name}'\n")
            with open(os.path.join(d, "readme.md"), "w") as f:
                f.write("# Test Project")
            _send(
                mcp_server.stdin,
                {
                    "jsonrpc": "2.0",
                    "id": _next_id(),
                    "method": "tools/call",
                    "params": {"name": "index_directory", "arguments": {"directory_path": d}},
                },
            )
            resp = _recv(mcp_server.stdout)
            assert resp["jsonrpc"] == "2.0"
            content = resp["result"]["content"][0]["text"]
            assert "queued" in content.lower() or "up to date" in content.lower()

    def test_z_search_after_index_api_works(self, mcp_server):
        """Verify search API can be called after indexing (no crash)."""
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "main.py"), "w", encoding="utf-8") as f:
                f.write("def greet(name):\n    return f'Hello {name}'\n")
            _send(
                mcp_server.stdin,
                {
                    "jsonrpc": "2.0",
                    "id": _next_id(),
                    "method": "tools/call",
                    "params": {"name": "index_directory", "arguments": {"directory_path": d}},
                },
            )
            resp = _recv(mcp_server.stdout, timeout=10)
            assert resp["result"]["content"][0]["text"].lower().startswith("queued")

            import time

            time.sleep(3)
            _send(
                mcp_server.stdin,
                {
                    "jsonrpc": "2.0",
                    "id": _next_id(),
                    "method": "tools/call",
                    "params": {"name": "search_codebase", "arguments": {"query": "hello", "k": 3}},
                },
            )
            resp = _recv(mcp_server.stdout, timeout=30)
            assert resp["jsonrpc"] == "2.0"
            assert "result" in resp, f"Error: {resp.get('error', resp)}"

    def test_resource_status_after_index(self, mcp_server):
        _send(
            mcp_server.stdin,
            {
                "jsonrpc": "2.0",
                "id": _next_id(),
                "method": "resources/read",
                "params": {"uri": "turbocode://status"},
            },
        )
        resp = _recv(mcp_server.stdout)
        assert resp["jsonrpc"] == "2.0"
        assert isinstance(resp["result"]["contents"][0]["text"], str)
