"""Тесты MCP чтения кода 1С (onec_mcp). Запуск:  uv run --with mcp --with pytest pytest -q
Создают временный «репозиторий» с модулем и проверяют discovery/search/find/read."""
import importlib
import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parents[1] / "mcp"


def _make_src(tmp_path):
    # layout EDT: <repo>/src/<имя>/src/CommonModules/<Модуль>/Module.bsl
    mod = tmp_path / "myconf" / "src" / "ext" / "src" / "CommonModules" / "ТестМодуль"
    mod.mkdir(parents=True)
    (mod / "Module.bsl").write_text(
        "Функция ТестоваяФункция() Экспорт\n\tВозврат 1;\nКонецФункции\n", encoding="utf-8")
    return tmp_path


def _load(tmp_path, monkeypatch):
    monkeypatch.setenv("ONEC_SRC_DIR", str(tmp_path).replace("\\", "/"))
    sys.modules.pop("onec_mcp", None)               # переимпорт: discovery в момент импорта
    if str(MCP_DIR) not in sys.path:
        sys.path.insert(0, str(MCP_DIR))
    return importlib.import_module("onec_mcp")


def test_discovery_finds_root(tmp_path, monkeypatch):
    _make_src(tmp_path)
    m = _load(tmp_path, monkeypatch)
    assert m._ROOTS, "должен найти корень конфигурации по CommonModules"
    assert any("myconf" in r for r in m._ROOTS)


def test_search_returns_match_with_repo_tag(tmp_path, monkeypatch):
    _make_src(tmp_path)
    m = _load(tmp_path, monkeypatch)
    out = m.search_1c("ТестоваяФункция")
    assert "ТестоваяФункция" in out
    assert "[myconf]" in out, "слой должен быть помечен тегом репозитория"


def test_search_no_match(tmp_path, monkeypatch):
    _make_src(tmp_path)
    m = _load(tmp_path, monkeypatch)
    assert "ничего не найдено" in m.search_1c("ОтсутствующийТекстXYZ")


def test_find_object(tmp_path, monkeypatch):
    _make_src(tmp_path)
    m = _load(tmp_path, monkeypatch)
    out = m.find_object("ТестМодуль")
    assert "ТестМодуль" in out and "[myconf]" in out


def test_read_module(tmp_path, monkeypatch):
    _make_src(tmp_path)
    m = _load(tmp_path, monkeypatch)
    line = m.find_object("ТестМодуль").splitlines()[0]   # "[myconf] CommonModules/ТестМодуль"
    rel = line.split("] ", 1)[1] + "/Module.bsl"
    out = m.read_module(rel)
    assert "ТестоваяФункция" in out


def test_list_modules(tmp_path, monkeypatch):
    _make_src(tmp_path)
    m = _load(tmp_path, monkeypatch)
    assert "ТестМодуль" in m.list_modules()
