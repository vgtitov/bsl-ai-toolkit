"""Тесты ПРЕСЕТОВ КОНТУРОВ Zabbix: «имя контура → полный охват съёма» (url, дашборды, хосты,
окно, лимит ядер лицензии, база карты хранения, профиль кода). Пресеты лежат в TOML
(env ZABBIX_CONTOURS_CONFIG, иначе <репо>/config/zabbix-contours.toml — файл локализации);
явные аргументы CLI/MCP главнее пресета. Без пресета простой запрос «проанализируй контур X»
вырождается в бедный охват — главный анти-паттерн методики 1c-expert.
Запуск:  uv run --with mcp --with pytest pytest -q tests/test_onec_ops_zabbix_contours.py
"""
import importlib
import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parents[1] / "mcp"

TOML = """\
[kz]
url = "http://zbx.example/api_jsonrpc.php"
dashboards = "408,410"
hosts = "vc-1c-*"
window_hours = 24
licensed_cores = { "vc-1c-app-01" = 12 }
storage_map_base = "OrgKZ_ERP"
onec_profile = "kz"

[by]
dashboards = "300"
hosts = ["srv-by-app", "srv-by-db"]
"""


def _load():
    sys.path.insert(0, str(MCP_DIR))
    return importlib.reload(importlib.import_module("onec_ops_mcp"))


def _cfg(tmp_path):
    p = tmp_path / "zabbix-contours.toml"
    p.write_text(TOML, encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# Загрузка и резолв пресета
# ---------------------------------------------------------------------------
def test_resolve_contour_full_preset(tmp_path):
    m = _load()
    c = m.resolve_zabbix_contour("kz", _cfg(tmp_path))
    assert c["url"] == "http://zbx.example/api_jsonrpc.php"
    assert c["dashboards"] == "408,410"
    assert c["hosts"] == ["vc-1c-*"]
    assert c["window_hours"] == 24.0
    assert c["licensed_cores"] == {"vc-1c-app-01": 12}
    assert c["storage_map_base"] == "OrgKZ_ERP"
    assert c["onec_profile"] == "kz"


def test_resolve_contour_hosts_list_and_defaults(tmp_path):
    m = _load()
    c = m.resolve_zabbix_contour("by", _cfg(tmp_path))
    assert c["hosts"] == ["srv-by-app", "srv-by-db"]
    assert c["window_hours"] == 24.0  # дефолт методики — сутки, не час
    assert c["licensed_cores"] == {}
    assert c["url"] == ""  # url добирается из env ZABBIX_URL


def test_resolve_contour_case_insensitive(tmp_path):
    m = _load()
    assert m.resolve_zabbix_contour("KZ", _cfg(tmp_path))["dashboards"] == "408,410"


def test_unknown_contour_lists_available(tmp_path):
    m = _load()
    try:
        m.resolve_zabbix_contour("ae", _cfg(tmp_path))
        assert False, "неизвестный контур должен падать с перечнем доступных"
    except RuntimeError as e:
        assert "ae" in str(e) and "kz" in str(e) and "by" in str(e)


def test_missing_config_hints_where_to_put_it(tmp_path, monkeypatch):
    m = _load()
    monkeypatch.delenv("ZABBIX_CONTOURS_CONFIG", raising=False)
    try:
        m.resolve_zabbix_contour("kz", str(tmp_path / "нет.toml"))
        assert False, "нет файла пресетов — ошибка с подсказкой"
    except RuntimeError as e:
        assert "zabbix-contours" in str(e) or "ZABBIX_CONTOURS_CONFIG" in str(e)


def test_env_points_to_config(tmp_path, monkeypatch):
    m = _load()
    monkeypatch.setenv("ZABBIX_CONTOURS_CONFIG", _cfg(tmp_path))
    assert m.resolve_zabbix_contour("kz")["dashboards"] == "408,410"


# ---------------------------------------------------------------------------
# Слияние пресета с явными аргументами (явное главнее) — общий вход CLI и MCP
# ---------------------------------------------------------------------------
def test_scope_from_contour_only(tmp_path, monkeypatch):
    m = _load()
    monkeypatch.delenv("ZABBIX_URL", raising=False)
    monkeypatch.delenv("ZABBIX_TOKEN", raising=False)
    s = m.resolve_zabbix_scope(contour="kz", contours_path=_cfg(tmp_path))
    assert s["url"] == "http://zbx.example/api_jsonrpc.php"
    assert s["dashboardid"] == "408,410"
    assert s["hosts"] == ["vc-1c-*"]
    assert s["window_hours"] == 24.0
    assert s["licensed_cores"] == {"vc-1c-app-01": 12}
    assert s["contour"] == "kz"


def test_scope_explicit_args_override_preset(tmp_path):
    m = _load()
    s = m.resolve_zabbix_scope(contour="kz", contours_path=_cfg(tmp_path),
                               dashboardid="99", hosts=["only-this"], window_hours=2,
                               licensed_cores={"x": 4})
    assert s["dashboardid"] == "99"
    assert s["hosts"] == ["only-this"]
    assert s["window_hours"] == 2.0
    assert s["licensed_cores"] == {"x": 4}


def test_scope_env_url_beats_preset(tmp_path, monkeypatch):
    m = _load()
    monkeypatch.setenv("ZABBIX_URL", "http://env-zbx/api_jsonrpc.php")
    s = m.resolve_zabbix_scope(contour="kz", contours_path=_cfg(tmp_path))
    assert s["url"] == "http://env-zbx/api_jsonrpc.php"


def test_scope_without_contour_keeps_old_behaviour(monkeypatch):
    m = _load()
    monkeypatch.delenv("ZABBIX_URL", raising=False)
    s = m.resolve_zabbix_scope(url="http://z/api_jsonrpc.php", dashboardid="1", window_hours=None)
    assert s["url"] == "http://z/api_jsonrpc.php"
    assert s["dashboardid"] == "1"
    assert s["window_hours"] == 1.0  # старый дефолт без пресета


# ---------------------------------------------------------------------------
# Пресет доезжает до отчёта: scope в отчёте несёт имя контура («до/после тем же охватом»)
# ---------------------------------------------------------------------------
def test_report_scope_carries_contour():
    m = _load()

    def fake_post(url, payload, headers=None):
        return {"jsonrpc": "2.0", "result": [], "id": 1}

    rep = m.zabbix_perf_report("http://z/api_jsonrpc.php", hosts=["h1"], window_hours=24,
                               contour="kz", _post=fake_post)
    assert rep["scope"].get("contour") == "kz"


# ---------------------------------------------------------------------------
# Кривой рукописный TOML (файл локализации правят руками) — человеческая ошибка,
# не сырой трейсбек tomllib/ValueError/TypeError
# ---------------------------------------------------------------------------
def _expect_runtime_error(m, toml_text, tmp_path, name="kz"):
    p = tmp_path / "bad.toml"
    p.write_text(toml_text, encoding="utf-8")
    try:
        m.resolve_zabbix_contour(name, str(p))
        assert False, "ожидалась RuntimeError с человеческим текстом"
    except RuntimeError as e:
        assert "bad.toml" in str(e) or "контур" in str(e)


def test_broken_toml_syntax_is_runtime_error(tmp_path):
    _expect_runtime_error(_load(), "[kz\nне toml", tmp_path)


def test_bad_licensed_cores_is_runtime_error(tmp_path):
    _expect_runtime_error(_load(), '[kz]\nlicensed_cores = { app = "twelve" }\n', tmp_path)


def test_bad_window_hours_is_runtime_error(tmp_path):
    _expect_runtime_error(_load(), '[kz]\nwindow_hours = "day"\n', tmp_path)


def test_bad_hosts_type_is_runtime_error(tmp_path):
    _expect_runtime_error(_load(), '[kz]\nhosts = 5\n', tmp_path)


def test_scope_explicit_window_zero_is_honored(tmp_path):
    m = _load()
    s = m.resolve_zabbix_scope(contour="kz", contours_path=_cfg(tmp_path), window_hours=0)
    assert s["window_hours"] == 0.0  # явный 0 не подменяется дефолтом пресета
