# DISCIPLINE_ALLOW_TEST_EDIT: новый red-тест TDD — сужение scope до конкретного слоя (T-177)
"""Сужение scope до КОНКРЕТНОГО слоя (а не scope-группы) в onec_mcp:
- подстрока имени репозитория или тега/ярлыка → только этот слой (generic, без конфига);
- [aliases] в конфиге слоёв: синоним → regex по имени репо (локализация БЕЗ правки кода);
- неизвестный scope → явная ошибка с подсказкой, а не тихий пустой результат.
Перенос team-фичи в upstream: спец-имена контуров живут в конфиге, не в коде.
"""
import importlib
import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parents[1] / "mcp"


def _make_repo(src, repo, mod_name, func):
    mod = src / repo / "src" / "main" / "src" / "CommonModules" / mod_name
    mod.mkdir(parents=True)
    (mod / "Module.bsl").write_text(
        f"Функция {func}() Экспорт\n\tВозврат 1;\nКонецФункции\n", encoding="utf-8")


CONFIG = """
[options]
default_scope = "both"

[[classify]]
regex = "-ext-"
role = "extension"
tag = "расш"

[[classify]]
regex = "^erp-"
role = "baseline"
tag = "конфиг"

[classify_default]
role = "extension"
tag = "расш"

[scopes]
baseline = ["baseline"]
extension = ["extension"]
both = ["baseline", "extension"]

# Локализация синонимов: алиас scope → regex по имени репозитория.
[aliases]
"рб" = "-by(-|$)"
"беларусь" = "-by(-|$)"
"ядро" = "all-ext-core"
"""


def _setup(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_repo(src, "erp-cis-by", "МодульБел", "ФункцияБел")
    _make_repo(src, "erp-cis-kz", "МодульКаз", "ФункцияКаз")
    _make_repo(src, "erp-all-ext-core-common", "МодульЯдро", "ФункцияЯдро")
    cfg = tmp_path / "layers.toml"
    cfg.write_text(CONFIG, encoding="utf-8")
    return src, cfg


def _load(src, cfg, monkeypatch):
    monkeypatch.setenv("ONEC_SRC_DIR", str(src).replace("\\", "/"))
    monkeypatch.setenv("ONEC_LAYERS_CONFIG", str(cfg))
    monkeypatch.delenv("ONEC_PROFILE", raising=False)
    sys.modules.pop("onec_mcp", None)
    if str(MCP_DIR) not in sys.path:
        sys.path.insert(0, str(MCP_DIR))
    return importlib.import_module("onec_mcp")


def test_scope_by_repo_name_narrows_to_layer(tmp_path, monkeypatch):
    m = _load(*_setup(tmp_path), monkeypatch)
    dirs = m._dirs("erp-cis-by")
    assert len(dirs) == 1 and "erp-cis-by" in dirs[0]


def test_scope_by_repo_substring(tmp_path, monkeypatch):
    m = _load(*_setup(tmp_path), monkeypatch)
    dirs = m._dirs("cis-kz")
    assert len(dirs) == 1 and "erp-cis-kz" in dirs[0]


def test_scope_by_tag_matches_tagged_layers(tmp_path, monkeypatch):
    m = _load(*_setup(tmp_path), monkeypatch)
    dirs = m._dirs("конфиг")
    assert dirs and all("erp-cis-" in d for d in dirs) and len(dirs) == 2


def test_scope_group_still_works(tmp_path, monkeypatch):
    m = _load(*_setup(tmp_path), monkeypatch)
    assert len(m._dirs("both")) == 3
    assert len(m._dirs("baseline")) == 2


def test_alias_from_config(tmp_path, monkeypatch):
    m = _load(*_setup(tmp_path), monkeypatch)
    dirs = m._dirs("РБ")                      # регистр не важен
    assert len(dirs) == 1 and "erp-cis-by" in dirs[0]
    dirs = m._dirs("Беларусь")
    assert len(dirs) == 1 and "erp-cis-by" in dirs[0]
    dirs = m._dirs("ядро")
    assert len(dirs) == 1 and "all-ext-core" in dirs[0]


def test_direct_hit_wins_over_alias(tmp_path, monkeypatch):
    """Алиасы — только фолбэк: прямое совпадение по репо/тегу приоритетнее."""
    m = _load(*_setup(tmp_path), monkeypatch)
    dirs = m._dirs("by")                      # подстрока имени репо erp-cis-by
    assert len(dirs) == 1 and "erp-cis-by" in dirs[0]


def test_unknown_scope_is_error_with_hint(tmp_path, monkeypatch):
    m = _load(*_setup(tmp_path), monkeypatch)
    out = m.search_1c("ФункцияБел", scope="несуществующий")
    assert "неизвестный scope" in out
    assert "конфиг" in out                    # подсказка перечисляет теги слоёв
    out = m.find_object("МодульБел", scope="несуществующий")
    assert "неизвестный scope" in out


def test_search_narrowed_by_layer_scope(tmp_path, monkeypatch):
    m = _load(*_setup(tmp_path), monkeypatch)
    out = m.search_1c("Функция", scope="erp-cis-by")
    assert "ФункцияБел" in out
    assert "ФункцияКаз" not in out


def test_no_aliases_section_is_fine(tmp_path, monkeypatch):
    """Без [aliases] generic-сужение по репо/тегу работает, алиасы просто нет."""
    src, cfg = _setup(tmp_path)
    cfg.write_text(CONFIG.split("[aliases]")[0], encoding="utf-8")
    m = _load(src, cfg, monkeypatch)
    assert len(m._dirs("erp-cis-by")) == 1
    assert m._dirs("рб") == []
