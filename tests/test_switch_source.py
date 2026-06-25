"""Тесты переключателя onec-code local↔central (scripts/switch_source.py): переписывается ТОЛЬКО блок onec-code,
остальные серверы целы; central пишет SSE с env-плейсхолдерами (без секрета в файле)."""
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _load():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    sys.modules.pop("switch_source", None)
    return importlib.import_module("switch_source")


def _mcp(tmp_path):
    cfg = {"mcpServers": {
        "onec-code": {"type": "stdio", "command": "uv",
                   "args": ["run", "--quiet", "${ONEC_SRC_DIR}/onec_mcp.py"],
                   "env": {"ONEC_SRC_DIR": "${ONEC_SRC_DIR}"}},
        "bsl-platform": {"type": "stdio", "command": "java", "args": [], "env": {}},
    }}
    p = tmp_path / ".mcp.json"
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return p


def test_central_writes_sse_with_placeholders(tmp_path):
    m = _load()
    p = _mcp(tmp_path)
    assert m.switch(str(tmp_path), "central", "dev") == 0
    erp = json.loads(p.read_text(encoding="utf-8"))["mcpServers"]["onec-code"]
    assert erp["type"] == "sse"
    assert erp["url"] == "${ONEC_MCP_URL}"
    assert erp["headers"]["Authorization"] == "Bearer ${ONEC_MCP_TOKEN}"


def test_other_servers_preserved(tmp_path):
    m = _load()
    p = _mcp(tmp_path)
    m.switch(str(tmp_path), "central", "dev")
    servers = json.loads(p.read_text(encoding="utf-8"))["mcpServers"]
    assert "bsl-platform" in servers  # соседний сервер не тронут


def test_local_restores_stdio(tmp_path):
    m = _load()
    p = _mcp(tmp_path)
    m.switch(str(tmp_path), "central", "dev")
    m.switch(str(tmp_path), "local", "dev")
    erp = json.loads(p.read_text(encoding="utf-8"))["mcpServers"]["onec-code"]
    assert erp["type"] == "stdio"
    assert "onec_mcp.py" in erp["args"][-1]


def test_auto_without_url_is_local(tmp_path, monkeypatch):
    m = _load()
    p = _mcp(tmp_path)
    monkeypatch.delenv("ONEC_MCP_URL", raising=False)
    m.switch(str(tmp_path), "auto", "dev")
    erp = json.loads(p.read_text(encoding="utf-8"))["mcpServers"]["onec-code"]
    assert erp["type"] == "stdio"
