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
        user="u", password="p", extension="Расширение", log=str(tmp_path / "o.log"))
    text = Path(listfile).read_text(encoding="utf-8-sig")
    assert text.splitlines() == ["ОбщийМодуль.Тест", "Справочник.Товары"]
    assert cmd[:2] == ["1cv8", "DESIGNER"]
    for part in ("/DumpConfigToFiles", "-listFile", "-Extension", "Расширение",
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


class _FakeRunner:
    """Двойник Runner — без реального SSH/scp, только запись вызовов."""
    def __init__(self):
        self.host = "term-01"
        self.made_dirs = []
        self.run_calls = []

    def makedirs(self, path, timeout=60):
        self.made_dirs.append(path)

    def run(self, cmd, timeout=600):
        self.run_calls.append(cmd)
        return 0, "__DSGN_OK__"

    def put(self, local, remote, timeout=300):
        pass

    def remove(self, path, timeout=60):
        pass


def test_remote_dump_uses_runner_and_fetches_result(monkeypatch, tmp_path):
    """--host: точечная выгрузка идёт через Runner (не локальный subprocess), а итог
    возвращается локально через fetch_tree — иначе результата на диске не окажется."""
    m = _load()
    r = _FakeRunner()
    calls = {}

    def fake_dump_extension(runner, base, user, pwd, ext, remote_dst, log=None, objects=None):
        calls["dump"] = (runner, base, ext, remote_dst, objects)

    def fake_fetch_tree(runner, remote_dir, local_dir):
        calls["fetch"] = (runner, remote_dir, local_dir)

    monkeypatch.setattr("onec_metadata.apply.dumpload.dump_extension", fake_dump_extension)
    monkeypatch.setattr("onec_metadata.apply.dumpload.fetch_tree", fake_fetch_tree)

    out_dir = tmp_path / "out"
    m.remote_dump(r, r"srv-1c\ERP_Test", "u", "p", "Расширение",
                 ["ОбщийМодуль.Тест"], str(out_dir), workdir=r"C:\work\point_sync")

    assert r.made_dirs == [r"C:\work\point_sync"]
    dump_runner, dump_base, dump_ext, remote_dst, objects = calls["dump"]
    assert dump_runner is r
    assert dump_base == r"srv-1c\ERP_Test"
    assert dump_ext == "Расширение"
    assert objects == ["ОбщийМодуль.Тест"]
    fetch_runner, fetch_remote_dir, fetch_local_dir = calls["fetch"]
    assert fetch_runner is r
    assert fetch_remote_dir == remote_dst          # тот же удалённый каталог, что дампили
    assert fetch_local_dir == out_dir.resolve()


def test_remote_load_uploads_then_loads_without_update_dbcfg(monkeypatch, tmp_path):
    """--host: точечная загрузка сперва отправляет локальный каталог на сервер (иначе
    файлов, которые правил AI локально, там просто нет), затем -files; UpdateDBCfg
    в ad hoc цикле НЕ вызывается — это шаг человека в Конфигураторе."""
    m = _load()
    r = _FakeRunner()
    calls = {}

    def fake_upload(runner, local_dir, remote_dir):
        calls["upload"] = (runner, local_dir, remote_dir)

    def fake_load_extension(runner, base, user, pwd, ext, remote_src, log=None,
                            files=None, update_dbcfg=True):
        calls["load"] = (runner, base, ext, remote_src, files, update_dbcfg)

    monkeypatch.setattr(m, "_upload_dir_plain", fake_upload)
    monkeypatch.setattr("onec_metadata.apply.dumpload.load_extension", fake_load_extension)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    m.remote_load(r, r"srv-1c\ERP_Test", "u", "p", "Расширение",
                 [r"CommonModules\X\Ext\Module.bsl"], str(out_dir), workdir=r"C:\work\point_sync")

    assert r.made_dirs == [r"C:\work\point_sync"]
    up_runner, up_local, up_remote = calls["upload"]
    assert up_runner is r
    assert up_local == out_dir.resolve()
    load_runner, load_base, load_ext, remote_src, files, update_dbcfg = calls["load"]
    assert load_runner is r
    assert remote_src == up_remote                 # грузим ровно то, что залили
    assert files == [r"CommonModules\X\Ext\Module.bsl"]
    assert update_dbcfg is False


def test_remote_deploy_extension_uploads_then_loads_with_update_dbcfg(monkeypatch, tmp_path):
    """--host: деплой ЦЕЛОГО расширения (не точечных объектов) — сперва весь src/
    уходит на сервер (иначе LoadConfigFromFiles там нечего читать), потом
    LoadConfigFromFiles + ОБЯЗАТЕЛЬНО UpdateDBCfg (в отличие от remote_load точечного
    цикла — здесь это полный цикл применения расширения, не ad hoc правка)."""
    m = _load()
    r = _FakeRunner()
    calls = {}

    def fake_upload(runner, local_dir, remote_dir):
        calls["upload"] = (runner, local_dir, remote_dir)

    def fake_load_extension(runner, base, user, pwd, ext, remote_src, log=None,
                            files=None, update_dbcfg=True):
        calls["load"] = (runner, base, ext, remote_src, update_dbcfg)

    monkeypatch.setattr(m, "_upload_dir_plain", fake_upload)
    monkeypatch.setattr("onec_metadata.apply.dumpload.load_extension", fake_load_extension)

    src_dir = tmp_path / "ai_debug_src"
    src_dir.mkdir()
    m.remote_deploy_extension(r, r"srv-1c\ERP_Test", "u", "p",
                              "ai_debug", str(src_dir), workdir=r"C:\work\ext_deploy")

    assert r.made_dirs == [r"C:\work\ext_deploy"]
    up_runner, up_local, up_remote = calls["upload"]
    assert up_runner is r
    assert up_local == src_dir.resolve()
    load_runner, load_base, load_ext, remote_src, update_dbcfg = calls["load"]
    assert load_runner is r
    assert load_ext == "ai_debug"
    assert remote_src == up_remote
    assert update_dbcfg is True


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
