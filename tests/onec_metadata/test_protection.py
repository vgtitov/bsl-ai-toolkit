# DISCIPLINE_ALLOW_TEST_EDIT: новый тест — предполёт по защите от опасных действий
"""Защита от опасных действий: проверка КОНКРЕТНОЙ базы по маскам conf.cfg.

Требование, из которого выросла эта проверка: баз в контуре бывают десятки, и опрашивать
их подключением «на всякий случай» (в doctor при каждом обновлении) слишком долго. Поэтому
проверка ленивая и локальная — сопоставление строки соединения с маской в файле, ноль
подключений. Все пути тестов берутся из tmp_path: букв дисков на Linux/macOS нет.
"""
# DISCIPLINE_ALLOW_TEST_EDIT: нужен pytest.raises для проверки прав доступа
import pytest

from onec_metadata.apply import protection as pr


def _platform(tmp_path, conf_line=None):
    """Каталог платформы: <root>/<версия>/bin/1cv8 и <root>/conf/conf.cfg."""
    root = tmp_path / "1cv8"
    (root / "8.3.27.2214" / "bin").mkdir(parents=True)
    if conf_line is not None:
        (root / "conf").mkdir()
        (root / "conf" / "conf.cfg").write_text(
            "SystemLanguage=RU\n" + conf_line, encoding="utf-8")
    return root


def test_no_conf_means_unknown(tmp_path):
    root = _platform(tmp_path, conf_line=None)
    assert pr.status_for_base("srv-1c\\ERP_Test", root) == pr.UNKNOWN


def test_mask_matches_server_base(tmp_path):
    root = _platform(tmp_path, "DisableUnsafeActionProtection=*ERP_Test*\n")
    assert pr.status_for_base("srv-1c\\ERP_Test", root) == pr.OFF_BY_MASK
    assert pr.status_for_base("srv-1c\\ERP_Prod", root) == pr.UNKNOWN


def test_mask_is_case_and_separator_insensitive(tmp_path):
    """Строку соединения пишут и с обратным слэшем, и с прямым, и в разном регистре."""
    root = _platform(tmp_path, "DisableUnsafeActionProtection=*bases/erp_test*\n")
    assert pr.status_for_base("/srv/BASES/ERP_Test", root) == pr.OFF_BY_MASK
    assert pr.status_for_base("D:\\Bases\\Erp_Test", root) == pr.OFF_BY_MASK


def test_several_masks_separated_by_semicolon(tmp_path):
    root = _platform(tmp_path, "DisableUnsafeActionProtection=*_Test*; *_Dev*\n")
    assert pr.status_for_base("srv\\ERP_Dev", root) == pr.OFF_BY_MASK
    assert pr.status_for_base("srv\\ERP_Test", root) == pr.OFF_BY_MASK
    assert pr.status_for_base("srv\\ERP", root) == pr.UNKNOWN


# DISCIPLINE_ALLOW_TEST_EDIT: тест кодировал семантику fnmatch, а платформа считает
# `*.*` универсальной маской «все базы» — исправлено вслед за продукт-кодом
def test_wildcard_all_covers_everything(tmp_path):
    """`*.*` документирована как «все базы», хотя по правилам подстановки требовала бы
    точку в строке соединения. Обрабатываем как универсальную — иначе предполёт врал бы,
    что база не покрыта, при снятой на всей машине защите."""
    root = _platform(tmp_path, "DisableUnsafeActionProtection=*.*\n")
    assert pr.status_for_base("что угодно", root) == pr.OFF_BY_MASK
    assert pr.status_for_base("srv-1c\\ERP_Test", root) == pr.OFF_BY_MASK

    star = _platform(tmp_path / "star", "DisableUnsafeActionProtection=*\n")
    assert pr.status_for_base("srv-1c\\ERP_Test", star) == pr.OFF_BY_MASK


def test_set_mask_is_idempotent_and_keeps_other_lines(tmp_path):
    cfg = tmp_path / "conf.cfg"
    cfg.write_text("SystemLanguage=RU\n", encoding="utf-8")

    pr.set_mask("*Test*", cfg_path=cfg)
    text = cfg.read_text(encoding="utf-8")
    assert "SystemLanguage=RU" in text                      # чужие настройки не потеряны
    assert "DisableUnsafeActionProtection=*Test*" in text
    assert (tmp_path / "conf.cfg.toolkit-backup").is_file()  # бэкап до правки

    pr.set_mask("*.*", cfg_path=cfg)                        # повторно — замена, не дубль
    text = cfg.read_text(encoding="utf-8")
    assert text.count("DisableUnsafeActionProtection") == 1
    assert "DisableUnsafeActionProtection=*.*" in text


def test_clear_mask_restores_protection(tmp_path):
    cfg = tmp_path / "conf.cfg"
    cfg.write_text("SystemLanguage=RU\nDisableUnsafeActionProtection=*.*\n", encoding="utf-8")
    pr.clear_mask(cfg_path=cfg)
    text = cfg.read_text(encoding="utf-8")
    assert "DisableUnsafeActionProtection" not in text
    assert "SystemLanguage=RU" in text


def test_set_mask_creates_file_when_absent(tmp_path):
    cfg = tmp_path / "conf.cfg"
    pr.set_mask("*.*", cfg_path=cfg)
    assert "DisableUnsafeActionProtection=*.*" in cfg.read_text(encoding="utf-8")


def test_set_mask_without_rights_explains(tmp_path, monkeypatch):
    """conf.cfg лежит в каталоге программы — без прав администратора он не пишется.
    Сообщение обязано это назвать, а не отдать голый PermissionError."""
    cfg = tmp_path / "conf.cfg"
    cfg.write_text("SystemLanguage=RU\n", encoding="utf-8")

    def deny(self, *a, **kw):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(pr.Path, "write_text", deny)
    with pytest.raises(PermissionError) as e:
        pr.set_mask("*.*", cfg_path=cfg)
    assert "администратор" in str(e.value)


def test_preflight_note_only_when_risk(tmp_path):
    covered = _platform(tmp_path / "a", "DisableUnsafeActionProtection=*ERP_Test*\n")
    assert pr.preflight_note("srv\\ERP_Test", covered) is None      # риска нет — молчим

    bare = _platform(tmp_path / "b", conf_line=None)
    note = pr.preflight_note("srv\\ERP_Test", bare)
    assert note and "Защита от опасных действий" in note
    assert "setup-actions-required" in note


def test_conf_path_derived_from_bin_env(tmp_path, monkeypatch):
    """Корень платформы вычисляется из ONEC_1CV8_BIN — отдельной настройки не нужно."""
    root = _platform(tmp_path, "DisableUnsafeActionProtection=*X*\n")
    exe = root / "8.3.27.2214" / "bin" / "1cv8"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setenv("ONEC_1CV8_BIN", str(exe))
    monkeypatch.delenv("ONEC_PLATFORM_ROOT", raising=False)
    assert pr.conf_cfg_path() == root / "conf" / "conf.cfg"


def test_no_platform_configured_is_unknown_not_crash(monkeypatch):
    monkeypatch.delenv("ONEC_1CV8_BIN", raising=False)
    monkeypatch.delenv("ONEC_PLATFORM_ROOT", raising=False)
    assert pr.conf_cfg_path() is None
    assert pr.masks_from_conf(None) == []
    assert pr.status_for_base("srv\\ERP") == pr.UNKNOWN
