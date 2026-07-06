"""Подхват .env для CLI/MCP onec-ops: если ZABBIX_* нет в окружении, ищем .env
(текущий каталог → корень репозитория) и берём значения оттуда, НЕ перетирая окружение.
Запуск:  uv run --with mcp --with pytest pytest -q tests/test_onec_ops_dotenv.py
"""
import importlib
import os
import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parents[1] / "mcp"


def _load():
    sys.path.insert(0, str(MCP_DIR))
    return importlib.reload(importlib.import_module("onec_ops_mcp"))


def test_dotenv_loaded_and_not_overriding(tmp_path, monkeypatch):
    m = _load()
    (tmp_path / ".env").write_text(
        "# комментарий\nZABBIX_URL=http://zbx.example\nZABBIX_TOKEN=\"secret123\"\r\nOTHER=x\n",
        encoding="utf-8")
    monkeypatch.delenv("ZABBIX_URL", raising=False)
    monkeypatch.setenv("ZABBIX_TOKEN", "уже_из_окружения")
    loaded = m.load_dotenv_defaults(str(tmp_path))
    assert os.environ["ZABBIX_URL"] == "http://zbx.example"  # взято из .env (кавычки/CR сняты)
    assert os.environ["ZABBIX_TOKEN"] == "уже_из_окружения"  # окружение главнее .env
    assert "ZABBIX_URL" in loaded


def test_dotenv_missing_dir_noop(tmp_path):
    m = _load()
    assert m.load_dotenv_defaults(str(tmp_path / "нет_такого")) == {}
