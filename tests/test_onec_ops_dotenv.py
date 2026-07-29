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


# DISCIPLINE_ALLOW_TEST_EDIT — новый тест под фикс парсера .env (inline-коммент + пустые значения)
def _load_data_loader():
    sys.path.insert(0, str(MCP_DIR))
    return importlib.reload(importlib.import_module("onec_data_mcp")).load_dotenv_defaults


def test_dotenv_strips_inline_comment_and_skips_empty(tmp_path, monkeypatch):
    """`KEY=  # коммент` не должен становиться значением-комментарием; пустое = взять дефолт.

    Регресс: локальный .env с `ONEC_DATA_DEBUG_PATH=  # по умолчанию /hs/aidbg` ронял слой
    данных — значение '# ...' обрезало URL по '#'-фрагменту до /base (HTTP 404)."""
    (tmp_path / ".env").write_text(
        "ZABBIX_URL=http://zbx  # inline-комментарий\n"
        "ZABBIX_TOKEN=            # только комментарий, значения нет\n"
        "EMPTY_KEY=\n"
        'QUOTED=\"http://h/#frag\"\n',
        encoding="utf-8")
    for k in ("ZABBIX_URL", "ZABBIX_TOKEN", "EMPTY_KEY", "QUOTED"):
        monkeypatch.delenv(k, raising=False)
    for loader in (_load().load_dotenv_defaults, _load_data_loader()):
        for k in ("ZABBIX_URL", "ZABBIX_TOKEN", "EMPTY_KEY", "QUOTED"):
            monkeypatch.delenv(k, raising=False)
        loader(str(tmp_path))
        assert os.environ["ZABBIX_URL"] == "http://zbx"   # inline-комментарий срезан
        assert "ZABBIX_TOKEN" not in os.environ            # только комментарий → значения нет
        assert "EMPTY_KEY" not in os.environ               # пустое не выставляем (= взять дефолт)
        assert os.environ["QUOTED"] == "http://h/#frag"    # '#' внутри кавычек сохранён
