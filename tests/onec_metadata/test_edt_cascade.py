# DISCIPLINE_ALLOW_TEST_EDIT: новый red-тест TDD — клиент каскада EDT-MCP (T-178)
"""apply.edt_cascade: клиент делегирования каскадного рефакторинга (rename/delete)
внешнему EDT-MCP (DitriX) по MCP streamable-HTTP.

Юниты — против ФЕЙК-сервера (stdlib http.server, SSE-ответы как у реального
плагина: `event: message\\ndata: {json}`); живой EDT в юнитах не поднимается.
Контракт протокола снят с реального обмена 2026-07-14 (EDT 2025.2.6, плагин 2.6.1):
initialize → Mcp-Session-Id в заголовке ответа; tools/call rename_metadata_object
(projectName/objectFqn/newName [confirm]) — preview без confirm, executed с ним.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from onec_metadata.apply.edt_cascade import CascadeClient, CascadeError

SESSION = "test-session-123"


class _FakeEdtMcp(BaseHTTPRequestHandler):
    calls = []          # (method, params, session)

    def log_message(self, *a):  # тишина в тестах
        pass

    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"status": "ok", "ready": True,
                               "edt_version": "2025.2.6.4"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).calls.append((req.get("method"), req.get("params"),
                                 self.headers.get("Mcp-Session-Id")))
        if req.get("method") == "initialize":
            result = {"protocolVersion": "2025-11-25",
                      "serverInfo": {"name": "edt-mcp-server", "version": "2.6.1"}}
            self._sse(req, result, session=SESSION)
        elif req.get("method") == "notifications/initialized":
            self.send_response(202); self.end_headers()
        elif req.get("method") == "tools/call":
            args = req["params"]["arguments"]
            if req["params"]["name"] != "rename_metadata_object":
                self._sse(req, {"content": [{"type": "text", "text": "unknown"}],
                                "isError": True})
                return
            if args.get("confirm"):
                text = ("---\naction: executed\nperformedCount: 1\nerrors: 0\n---\n"
                        f"# Rename Completed: `{args['objectFqn']}` -> `{args['newName']}`")
            else:
                text = ("---\naction: preview\ntotalChanges: 2\nproblems: 0\n---\n"
                        "# Refactoring Preview\n| # | Type |\n| 0 | bslRef |")
            self._sse(req, {"content": [{"type": "resource",
                                         "resource": {"uri": "embedded://x.md",
                                                      "mimeType": "text/markdown",
                                                      "text": text}}]})
        else:
            self._sse(req, {})

    def _sse(self, req, result, session=None):
        data = json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "result": result})
        body = f"event: message\nid: {req.get('id')}\ndata: {data}\n\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        if session:
            self.send_header("Mcp-Session-Id", session)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def server():
    _FakeEdtMcp.calls = []
    srv = HTTPServer(("127.0.0.1", 0), _FakeEdtMcp)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_health_and_initialize(server):
    c = CascadeClient(server)
    h = c.health()
    assert h["ready"] is True and h["edt_version"].startswith("2025.2.6")
    c.initialize()
    assert c.session_id == SESSION
    # после initialize отправлен notifications/initialized С session id
    methods = [(m, s) for m, _, s in _FakeEdtMcp.calls]
    assert ("notifications/initialized", SESSION) in methods


def test_rename_two_phase(server):
    c = CascadeClient(server)
    c.initialize()
    prev = c.rename("TestConf", "CommonModule.X", "Y")           # фаза 1: preview
    assert prev["action"] == "preview"
    assert prev["totalChanges"] == 2
    done = c.rename("TestConf", "CommonModule.X", "Y", confirm=True)
    assert done["action"] == "executed"
    assert done["errors"] == 0
    # оба вызова ушли с session id и правильными аргументами
    calls = [(p["name"], p["arguments"].get("confirm", False))
             for m, p, s in _FakeEdtMcp.calls if m == "tools/call"]
    assert calls == [("rename_metadata_object", False), ("rename_metadata_object", True)]


def test_rename_without_initialize_raises(server):
    c = CascadeClient(server)
    with pytest.raises(CascadeError):
        c.rename("TestConf", "CommonModule.X", "Y")


def test_error_result_raises(server):
    c = CascadeClient(server)
    c.initialize()
    with pytest.raises(CascadeError):
        c.call_tool("no_such_tool", {})


def test_wait_project_ready(server):
    """wait_project поллит list_projects до ready-строки таблицы.

    DISCIPLINE_ALLOW_TEST_EDIT: фикс невалидного escape в тест-данных (C:\\x → C:/x).
    """
    _FakeEdtMcp.project_rows = ["| TestConf | indexing | C:/x | Yes | Yes |",
                                "| TestConf | ready | C:/x | Yes | Yes |"]
    orig = _FakeEdtMcp.do_POST

    def patched(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).calls.append((req.get("method"), req.get("params"),
                                 self.headers.get("Mcp-Session-Id")))
        if req.get("method") == "tools/call" and req["params"]["name"] == "list_projects":
            row = (type(self).project_rows.pop(0) if len(type(self).project_rows) > 1
                   else type(self).project_rows[0])
            text = "## Workspace Projects\n| Name | State |\n" + row
            self._sse(req, {"content": [{"type": "resource",
                                         "resource": {"uri": "embedded://p.md",
                                                      "mimeType": "text/markdown",
                                                      "text": text}}]})
        elif req.get("method") == "initialize":
            self._sse(req, {"serverInfo": {}}, session=SESSION)
        elif req.get("method") == "notifications/initialized":
            self.send_response(202); self.end_headers()
        else:
            orig(self)

    _FakeEdtMcp.do_POST = patched
    try:
        c = CascadeClient(server)
        c.initialize()
        assert c.wait_project("TestConf", timeout=30, poll=0.05) is True
        assert c.wait_project("НетТакого", timeout=1, poll=0.2) is False
    finally:
        _FakeEdtMcp.do_POST = orig
