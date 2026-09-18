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


# DISCIPLINE_ALLOW_TEST_EDIT: маска — регулярное выражение, glob-вид платформа игнорирует
def test_mask_matches_server_base(tmp_path):
    """Клиент-серверная база: локального совпадения мало, решает conf.cfg сервера."""
    root = _platform(tmp_path, "DisableUnsafeActionProtection=.*ERP_Test.*\n")
    assert pr.status_for_base("srv-1c\\ERP_Test", root) == pr.LOCAL_ONLY
    assert pr.status_for_base("srv-1c\\ERP_Prod", root) == pr.UNKNOWN


def test_file_base_is_covered_locally(tmp_path):
    """Файловая база живёт на этой же машине — тут локальный conf.cfg и есть решающий."""
    root = _platform(tmp_path, "DisableUnsafeActionProtection=.*bases.erp_test.*\n")
    assert pr.status_for_base("D:\\Bases\\ERP_Test", root) == pr.OFF_BY_MASK


def test_glob_mask_does_not_work(tmp_path):
    """`*ERP_Test*` — привычная, но нерабочая запись: как regex она невалидна."""
    root = _platform(tmp_path, "DisableUnsafeActionProtection=*ERP_Test*\n")
    assert pr.status_for_base("srv-1c\\ERP_Test", root) == pr.UNKNOWN
    assert pr.invalid_masks(pr.masks_from_conf(pr.conf_cfg_path(root))) == ["*ERP_Test*"]


# DISCIPLINE_ALLOW_TEST_EDIT: нормализация разделителей убрана — про неё у платформы
# ничего не проверено, а регистр сопоставляется без учёта
def test_mask_is_case_insensitive(tmp_path):
    """Имена баз пишут в разном регистре; про разделители пути у платформы не проверено."""
    root = _platform(tmp_path, "DisableUnsafeActionProtection=.*erp_test.*\n")
    assert pr.status_for_base("srv\\ERP_Test", root) == pr.LOCAL_ONLY
    assert pr.status_for_base("srv\\Erp_TEST", root) == pr.LOCAL_ONLY


def test_several_masks_separated_by_semicolon(tmp_path):
    root = _platform(tmp_path, "DisableUnsafeActionProtection=.*_Test.*; .*_Dev.*\n")
    assert pr.status_for_base("srv\\ERP_Dev", root) == pr.LOCAL_ONLY
    assert pr.status_for_base("srv\\ERP_Test", root) == pr.LOCAL_ONLY
    assert pr.status_for_base("srv\\ERP", root) == pr.UNKNOWN


# DISCIPLINE_ALLOW_TEST_EDIT: «все базы» — это `.*`; формы `*` и `*.*` платформа игнорирует
def test_wildcard_all_covers_everything(tmp_path):
    """Универсальная маска ровно одна — `.*`."""
    root = _platform(tmp_path, "DisableUnsafeActionProtection=.*\n")
    assert pr.status_for_base("что угодно", root) == pr.OFF_BY_MASK
    assert pr.status_for_base("srv-1c\\ERP_Test", root) == pr.LOCAL_ONLY


def test_star_forms_are_invalid_not_universal(tmp_path):
    """`*` и `*.*` ходят по статьям, но как regex невалидны: считать их «все базы» нельзя,
    иначе инструмент покажет зелёный там, где защита включена."""
    for bad in ("*", "*.*"):
        root = _platform(tmp_path / bad.replace("*", "s").replace(".", "d"),
                         f"DisableUnsafeActionProtection={bad}\n")
        assert pr.status_for_base("srv-1c\\ERP_Test", root) == pr.UNKNOWN
        assert pr.invalid_masks([bad]) == [bad]


def test_set_mask_is_idempotent_and_keeps_other_lines(tmp_path):
    cfg = tmp_path / "conf.cfg"
    cfg.write_text("SystemLanguage=RU\n", encoding="utf-8")

    pr.set_mask(".*Test.*", cfg_path=cfg)
    text = cfg.read_text(encoding="utf-8")
    assert "SystemLanguage=RU" in text                      # чужие настройки не потеряны
    assert "DisableUnsafeActionProtection=.*Test.*" in text
    assert (tmp_path / "conf.cfg.toolkit-backup").is_file()  # бэкап до правки

    pr.set_mask(".*", cfg_path=cfg)                         # повторно — замена, не дубль
    text = cfg.read_text(encoding="utf-8")
    assert text.count("DisableUnsafeActionProtection") == 1
    assert "DisableUnsafeActionProtection=.*" in text


def test_clear_mask_restores_protection(tmp_path):
    cfg = tmp_path / "conf.cfg"
    cfg.write_text("SystemLanguage=RU\nDisableUnsafeActionProtection=.*\n", encoding="utf-8")
    pr.clear_mask(cfg_path=cfg)
    text = cfg.read_text(encoding="utf-8")
    assert "DisableUnsafeActionProtection" not in text
    assert "SystemLanguage=RU" in text


def test_set_mask_creates_file_when_absent(tmp_path):
    cfg = tmp_path / "conf.cfg"
    pr.set_mask(".*", cfg_path=cfg)
    assert "DisableUnsafeActionProtection=.*" in cfg.read_text(encoding="utf-8")


def test_set_mask_without_rights_explains(tmp_path, monkeypatch):
    """conf.cfg лежит в каталоге программы — без прав администратора он не пишется.
    Сообщение обязано это назвать, а не отдать голый PermissionError."""
    cfg = tmp_path / "conf.cfg"
    cfg.write_text("SystemLanguage=RU\n", encoding="utf-8")

    def deny(self, *a, **kw):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(pr.Path, "write_bytes", deny)
    with pytest.raises(PermissionError) as e:
        pr.set_mask(".*", cfg_path=cfg)
    assert "администратор" in str(e.value)


# DISCIPLINE_ALLOW_TEST_EDIT: бэкап делается копированием байтов и падает раньше записи
def test_backup_without_rights_explains(tmp_path, monkeypatch):
    """Бэкап — первая запись в каталог программы. Его отказ обязан объяснять причину так же,
    иначе человек получает голый PermissionError на непонятном пути."""
    cfg = tmp_path / "conf.cfg"
    cfg.write_text("SystemLanguage=RU\n", encoding="utf-8")

    def deny(*a, **kw):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(pr.shutil, "copy2", deny)
    with pytest.raises(PermissionError) as e:
        pr.set_mask(".*", cfg_path=cfg)
    assert "администратор" in str(e.value)


def test_roundtrip_keeps_encoding_and_newlines(tmp_path):
    """Файл возвращается в той же кодировке, с тем же BOM и теми же переводами строк.

    Ради этого всё и затевалось: conf.cfg установщик пишет в UTF-16LE или UTF-8 с BOM,
    и перезапись в другой кодировке делает файл бессмысленным для платформы.
    """
    cases = [
        ("utf-8", b"", "\r\n"),
        ("utf-8", b"\xef\xbb\xbf", "\r\n"),
        ("utf-16-le", b"\xff\xfe", "\r\n"),
        ("utf-16-be", b"\xfe\xff", "\n"),
    ]
    for i, (enc, bom, nl) in enumerate(cases):
        cfg = tmp_path / f"conf{i}.cfg"
        cfg.write_bytes(bom + f"SystemLanguage=RU{nl}".encode(enc))

        pr.set_mask(".*_PP.*", cfg_path=cfg)
        raw = cfg.read_bytes()
        assert raw.startswith(bom), f"{enc}: BOM не сохранён"
        text, got_enc, got_nl = pr.read_cfg(cfg)
        assert "SystemLanguage=RU" in text, f"{enc}: соседняя строка потеряна"
        assert "DisableUnsafeActionProtection=.*_PP.*" in text
        assert got_nl == nl, f"{enc}: перевод строки изменился"

        pr.clear_mask(cfg_path=cfg)
        text = pr.read_cfg(cfg)[0]
        assert "DisableUnsafeActionProtection" not in text
        assert "SystemLanguage=RU" in text


def test_unreadable_conf_is_not_rewritten(tmp_path):
    """UTF-16 без BOM не распознать надёжно — такой файл не трогаем и говорим об этом."""
    cfg = tmp_path / "conf.cfg"
    cfg.write_bytes("SystemLanguage=RU\r\n".encode("utf-16-le"))   # без BOM

    with pytest.raises(pr.ConfEncodingError):
        pr.read_cfg(cfg)
    with pytest.raises(pr.ConfEncodingError):
        pr.set_mask(".*", cfg_path=cfg)
    assert cfg.read_bytes() == "SystemLanguage=RU\r\n".encode("utf-16-le")  # файл цел


def test_preflight_note_only_when_risk(tmp_path):
    # файловая база под маской — локального conf.cfg достаточно, молчим
    covered = _platform(tmp_path / "a", "DisableUnsafeActionProtection=.*erp_test.*\n")
    assert pr.preflight_note("D:\\Bases\\ERP_Test", covered) is None

    bare = _platform(tmp_path / "b", conf_line=None)
    note = pr.preflight_note("srv\\ERP_Test", bare)
    assert note and "Защита от опасных действий" in note
    assert "setup-actions-required" in note


def test_preflight_warns_that_server_decides(tmp_path):
    """Клиент-серверная база под локальной маской: молчать нельзя — решает сервер."""
    root = _platform(tmp_path, "DisableUnsafeActionProtection=.*ERP_Test.*\n")
    note = pr.preflight_note("srv\\ERP_Test", root)
    assert note and "СЕРВЕР" in note


def test_conf_path_derived_from_bin_env(tmp_path, monkeypatch):
    """Корень платформы вычисляется из ONEC_1CV8_BIN — отдельной настройки не нужно."""
    root = _platform(tmp_path, "DisableUnsafeActionProtection=.*X.*\n")
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
