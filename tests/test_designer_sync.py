"""Тесты designer_sync — точечный цикл dump/load пакетным Конфигуратором:
резолв учёток по контурам (логин един, пароль с суффиксом), файловая vs серверная база,
сборка командной строки (listFile = дот-нотация объектов, -files = пути), список
изменённых файлов по git-статусу каталога выгрузки.
Запуск:  python -m pytest -q tests/test_designer_sync.py
"""
import importlib
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


def _load():
    sys.path.insert(0, str(SCRIPTS_DIR))
    return importlib.reload(importlib.import_module("designer_sync"))


def test_resolve_cred_contour_and_fallback():
    m = _load()
    env = {"ONEC_IB_USER": "ivanov", "ONEC_IB_PASS": "common", "ONEC_IB_PASS_KZ": "kzpass"}
    assert m.resolve_cred("IB", "kz", env) == ("ivanov", "kzpass")     # пароль контура
    assert m.resolve_cred("IB", "rb", env) == ("ivanov", "common")     # фолбэк без суффикса
    assert m.resolve_cred("IB", None, env) == ("ivanov", "common")
    assert m.resolve_cred("STORAGE", "kz", {}) == (None, None)         # не задано — без /N /P


def test_resolve_cred_priority_storage_name_over_contour():
    # пароли бывают разные ПО ХРАНИЛИЩАМ одного контура: первый найденный из списка ключей
    m = _load()
    env = {"ONEC_STORAGE_USER": "u", "ONEC_STORAGE_PASS": "base",
           "ONEC_STORAGE_PASS_RB": "rb", "ONEC_STORAGE_PASS_ERP_RB_PROD": "erp-rb"}
    assert m.resolve_cred("STORAGE", ["ERP_RB_PROD", "RB"], env) == ("u", "erp-rb")  # имя хранилища первично
    assert m.resolve_cred("STORAGE", ["UT_RB_PROD", "RB"], env) == ("u", "rb")       # нет имени → контур
    assert m.resolve_cred("STORAGE", ["ERP_KZ_PROD", "KZ"], env) == ("u", "base")    # нет обоих → общий


def test_base_args_file_vs_server(tmp_path):
    m = _load()
    assert m.base_args(str(tmp_path)) == ["/F", str(tmp_path)]         # каталог существует → файловая
    assert m.base_args(r"srv-1c\ERP_KZ_Dev") == ["/S", r"srv-1c\ERP_KZ_Dev"]  # иначе сервер\база


def test_build_dump_cmd_objects_listfile_and_extension(tmp_path):
    m = _load()
    cmd, listfile = m.build_dump_cmd(
        v8="1cv8", base=str(tmp_path), out_dir=str(tmp_path / "out"),
        objects=["ОбщийМодуль.Тест", "Справочник.Товары"],
        user="u", password="p", extension="askЯдро", log=str(tmp_path / "o.log"))
    text = Path(listfile).read_text(encoding="utf-8-sig")
    assert text.splitlines() == ["ОбщийМодуль.Тест", "Справочник.Товары"]
    assert cmd[:2] == ["1cv8", "DESIGNER"]
    for part in ("/DumpConfigToFiles", "-listFile", "-Extension", "askЯдро",
                 "/N", "u", "/P", "p", "/DisableStartupDialogs"):
        assert part in cmd
    # без objects — полная выгрузка, listFile не пишется
    cmd2, lf2 = m.build_dump_cmd(v8="1cv8", base=str(tmp_path), out_dir=str(tmp_path / "out"),
                                 objects=None, user=None, password=None, extension=None,
                                 log=str(tmp_path / "o.log"))
    assert lf2 is None and "-listFile" not in cmd2 and "/N" not in cmd2


def test_build_load_cmd_files_normalized(tmp_path):
    m = _load()
    cmd = m.build_load_cmd(
        v8="1cv8", base=str(tmp_path), src_dir=str(tmp_path / "out"),
        files=[r"CommonModules\Тест\Ext\Module.bsl", "Configuration.xml"],
        user=None, password=None, extension=None, log=str(tmp_path / "o.log"))
    i = cmd.index("-files")
    assert cmd[i + 1] == "CommonModules/Тест/Ext/Module.bsl,Configuration.xml"  # слэши и запятая
    assert "/LoadConfigFromFiles" in cmd and "/N" not in cmd


def test_changed_files_via_git(tmp_path):
    m = _load()
    d = tmp_path / "out"
    (d / "CommonModules").mkdir(parents=True)
    keep = d / "CommonModules" / "A.bsl"
    keep.write_text("х", encoding="utf-8")
    run = lambda *a: subprocess.run(["git", "-C", str(d), *a], capture_output=True, check=True)
    run("init"); run("add", "-A")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "base")
    keep.write_text("правка", encoding="utf-8")                    # изменённый
    (d / "CommonModules" / "B.bsl").write_text("новый", encoding="utf-8")  # новый
    got = m.changed_files(str(d))
    assert sorted(got) == ["CommonModules/A.bsl", "CommonModules/B.bsl"]
    assert m.changed_files(str(tmp_path)) is None                  # нет git — None, не падение
