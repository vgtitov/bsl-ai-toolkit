"""Тесты конфиг-driven слоёв erp_mcp: классификация (роли+теги), scope-фильтрация, pin-ярлык, профиль.
Проверяют, что универсальность достигается КОНФИГОМ (TOML), без правок кода — основа локализации."""
import importlib
import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parents[1] / "mcp"


def _make_repo(src, repo, mod_name, func):
    # layout EDT: <repo>/src/<имя>/src/CommonModules/<Модуль>/Module.bsl
    mod = src / repo / "src" / "main" / "src" / "CommonModules" / mod_name
    mod.mkdir(parents=True)
    (mod / "Module.bsl").write_text(
        f"Функция {func}() Экспорт\n\tВозврат 1;\nКонецФункции\n", encoding="utf-8")


def _make_pinned(src):
    # pinned-слой по фиксированному относительному пути (как BASE/EXT в локализации)
    mod = src / "pinned" / "src" / "main" / "src" / "CommonModules" / "ЯдроМодуль"
    mod.mkdir(parents=True)
    (mod / "Module.bsl").write_text(
        "Функция ФункцияЯдро() Экспорт\n\tВозврат 1;\nКонецФункции\n", encoding="utf-8")


CONFIG = """
[options]
default_scope = "both"

[[classify]]
regex = "^cfg"
role = "baseline"
tag = "конфиг"

[classify_default]
role = "extension"
tag = "расш"

[[pin]]
path = "pinned/src/main/src"
label = "ядро"
role = "extension"

[scopes]
baseline = ["baseline"]
extension = ["extension"]
both = ["baseline", "extension"]

[profiles.only_cfg]
include = ["cfgby"]
"""


def _setup(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_repo(src, "cfgby", "БазаМодуль", "ФункцияБаза")
    _make_repo(src, "extfoo", "РасшМодуль", "ФункцияРасш")
    _make_pinned(src)
    cfg = tmp_path / "layers.toml"
    cfg.write_text(CONFIG, encoding="utf-8")
    return src, cfg


def _load(src, cfg, monkeypatch, profile=""):
    monkeypatch.setenv("ONEC_SRC_DIR", str(src).replace("\\", "/"))
    monkeypatch.setenv("ONEC_LAYERS_CONFIG", str(cfg).replace("\\", "/"))
    monkeypatch.setenv("ONEC_PROFILE", profile)
    sys.modules.pop("erp_mcp", None)
    if str(MCP_DIR) not in sys.path:
        sys.path.insert(0, str(MCP_DIR))
    return importlib.import_module("erp_mcp")


def test_classify_roles_and_tags(tmp_path, monkeypatch):
    src, cfg = _setup(tmp_path)
    m = _load(src, cfg, monkeypatch)
    # baseline-репо помечается [конфиг:cfgby], extension-репо — [расш:extfoo]
    assert "[конфиг:cfgby]" in m.search_1c("ФункцияБаза")
    assert "[расш:extfoo]" in m.search_1c("ФункцияРасш")


def test_scope_filters_layers(tmp_path, monkeypatch):
    src, cfg = _setup(tmp_path)
    m = _load(src, cfg, monkeypatch)
    # scope=baseline не видит расширение, scope=extension не видит базу
    assert "ничего не найдено" in m.search_1c("ФункцияРасш", scope="baseline")
    assert "ничего не найдено" in m.search_1c("ФункцияБаза", scope="extension")
    # both (дефолт) видит оба
    assert "ФункцияБаза" in m.search_1c("ФункцияБаза", scope="both")
    assert "ФункцияРасш" in m.search_1c("ФункцияРасш")


def test_pin_custom_label(tmp_path, monkeypatch):
    src, cfg = _setup(tmp_path)
    m = _load(src, cfg, monkeypatch)
    # приколотый слой выводится с ярлыком [ядро], а не именем репо
    out = m.find_object("ЯдроМодуль")
    assert "[ядро]" in out


def test_profile_restricts_repos(tmp_path, monkeypatch):
    src, cfg = _setup(tmp_path)
    m = _load(src, cfg, monkeypatch, profile="only_cfg")
    # профиль only_cfg оставляет только cfgby — extfoo и pinned выпадают
    repos = " ".join(m._ROOTS)
    assert "cfgby" in repos
    assert "extfoo" not in repos
    assert "ничего не найдено" in m.search_1c("ФункцияРасш")


def test_default_no_config_still_works(tmp_path, monkeypatch):
    # Без конфига (ONEC_LAYERS_CONFIG не задан) — чистое auto-discovery, тег = имя репо.
    src = tmp_path / "src"
    src.mkdir()
    _make_repo(src, "plainrepo", "Мод", "ФункцияОбычная")
    monkeypatch.setenv("ONEC_SRC_DIR", str(src).replace("\\", "/"))
    monkeypatch.delenv("ONEC_LAYERS_CONFIG", raising=False)
    monkeypatch.delenv("ONEC_PROFILE", raising=False)
    sys.modules.pop("erp_mcp", None)
    if str(MCP_DIR) not in sys.path:
        sys.path.insert(0, str(MCP_DIR))
    m = importlib.import_module("erp_mcp")
    assert "[plainrepo]" in m.search_1c("ФункцияОбычная")
