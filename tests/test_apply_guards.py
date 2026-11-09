"""Страховки загрузки/выгрузки и окружения: грабли, превращённые в автоматические проверки.

Без баз и SSH: транспорт — двойники, файловые деревья — во временном каталоге.
Запуск:  python -m pytest -q tests/test_apply_guards.py
"""
import importlib
import json
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from onec_metadata.apply import dumpload, smoke  # noqa: E402
from onec_metadata.apply.dumpload import ApplyError  # noqa: E402


def _ds():
    return importlib.reload(importlib.import_module("designer_sync"))


def _touch(root: Path, rel: str, text: str = "x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# ── 1. xml объекта без его Ext в точечной загрузке ────────────────────────────

def test_partial_load_object_xml_without_ext_is_a_gap(tmp_path):
    _touch(tmp_path, "Catalogs/Товары.xml")
    _touch(tmp_path, "Catalogs/Товары/Ext/ObjectModule.bsl")
    _touch(tmp_path, "Catalogs/Товары/Ext/ManagerModule.bsl")
    gaps = dumpload.partial_load_gaps(tmp_path, ["Catalogs/Товары.xml",
                                                 "Catalogs/Товары/Ext/ManagerModule.bsl"])
    assert gaps == ["Catalogs/Товары.xml → нет Catalogs/Товары/Ext/ObjectModule.bsl"]
    assert dumpload.partial_load_gaps(tmp_path, [
        r"Catalogs\Товары.xml", "Catalogs/Товары/Ext/ObjectModule.bsl",
        "Catalogs/Товары/Ext/ManagerModule.bsl"]) == []
    # только модуль, без xml объекта — модули не стираются, отказа нет
    assert dumpload.partial_load_gaps(tmp_path, ["Catalogs/Товары/Ext/ObjectModule.bsl"]) == []


def test_partial_load_root_needs_whole_ext(tmp_path):
    _touch(tmp_path, "Configuration.xml")
    _touch(tmp_path, "Ext/SessionModule.bsl")
    _touch(tmp_path, "Ext/ClientApplicationInterface.xml")
    gaps = dumpload.partial_load_gaps(tmp_path, ["Configuration.xml", "Ext/SessionModule.bsl"])
    assert gaps == ["Configuration.xml → нет Ext/ClientApplicationInterface.xml"]


DUMP_INFO = """<?xml version="1.0" encoding="UTF-8"?>
<ConfigDumpInfo xmlns="http://v8.1c.ru/8.3/xcf/dumpinfo" format="Hierarchical" version="2.20">
\t<ConfigVersions>
\t\t<Metadata name="Configuration.Тест" id="1" configVersion="a"/>
\t\t<Metadata name="Configuration.Тест.SessionModule" id="1.1" configVersion="a"/>
\t\t<Metadata name="Catalog.Товары" id="2" configVersion="b">
\t\t\t<Metadata name="Catalog.Товары.Attribute.Код" id="3"/>
\t\t</Metadata>
\t\t<Metadata name="Catalog.Товары.ObjectModule" id="2.0" configVersion="c"/>
\t\t<Metadata name="Catalog.Товары.ManagerModule" id="2.3" configVersion="c"/>
\t\t<Metadata name="Catalog.Товары.Command.Открыть.CommandModule" id="4.0" configVersion="d"/>
\t\t<Metadata name="Catalog.Товары.Form.ФормаЭлемента" id="5" configVersion="e"/>
\t\t<Metadata name="Catalog.Товары.Form.ФормаЭлемента.Form" id="5.0" configVersion="e"/>
\t</ConfigVersions>
</ConfigDumpInfo>
"""


# DISCIPLINE_ALLOW_TEST_EDIT: ревью P1 — «нет Ext» больше не безопасно само по себе; новый объект
# определяется по отсутствию в ConfigDumpInfo.xml, без него нужен явный --allow-missing-ext
def test_partial_load_new_object_without_ext_dir_is_fine(tmp_path):
    (tmp_path / "ConfigDumpInfo.xml").write_text(DUMP_INFO, encoding="utf-8")
    _touch(tmp_path, "Catalogs/Новый.xml")
    assert dumpload.partial_load_gaps(tmp_path, ["Catalogs/Новый.xml"]) == []


def test_partial_load_dump_info_requires_modules_even_without_ext_dir(tmp_path):
    """Неполная выгрузка существующего объекта: Ext на диске нет, но ConfigDumpInfo его модули знает."""
    (tmp_path / "ConfigDumpInfo.xml").write_text(DUMP_INFO, encoding="utf-8")
    _touch(tmp_path, "Catalogs/Товары.xml")
    joined = "\n".join(dumpload.partial_load_gaps(tmp_path, ["Catalogs/Товары.xml"]))
    for need in ("Catalogs/Товары/Ext/ObjectModule.bsl", "Catalogs/Товары/Ext/ManagerModule.bsl",
                 "Catalogs/Товары/Commands/Открыть/Ext/CommandModule.bsl"):
        assert need in joined
    assert "выгрузка неполная" in joined
    assert "ФормаЭлемента" not in joined                       # формы объекта при загрузке xml не стираются


def test_partial_load_dump_info_form_and_root(tmp_path):
    (tmp_path / "ConfigDumpInfo.xml").write_text(DUMP_INFO, encoding="utf-8")
    _touch(tmp_path, "Configuration.xml")
    _touch(tmp_path, "Catalogs/Товары/Forms/ФормаЭлемента.xml")
    gaps = dumpload.partial_load_gaps(tmp_path, ["Configuration.xml",
                                                 "Catalogs/Товары/Forms/ФормаЭлемента.xml"])
    assert any("Ext/SessionModule.bsl" in g for g in gaps)
    assert any("Catalogs/Товары/Forms/ФормаЭлемента/Ext/Form.xml" in g for g in gaps)


def test_partial_load_without_dump_info_needs_explicit_flag(tmp_path):
    _touch(tmp_path, "Catalogs/Новый.xml")
    gaps = dumpload.partial_load_gaps(tmp_path, ["Catalogs/Новый.xml"])
    assert gaps and "ConfigDumpInfo" in gaps[0]
    assert dumpload.partial_load_gaps(tmp_path, ["Catalogs/Новый.xml"], allow_missing_ext=True) == []
    _touch(tmp_path, "Catalogs/Новый/Ext/Predefined.xml")   # xml внутри Ext — не определение объекта
    assert dumpload.partial_load_gaps(tmp_path, ["Catalogs/Новый/Ext/Predefined.xml"]) == []


def test_designer_sync_allow_missing_ext_flag(monkeypatch, tmp_path):
    m = _ds()
    out = tmp_path / "out"
    _touch(out, "Catalogs/Новый.xml")
    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.setenv("ONEC_V8_BIN", "1cv8")
    ran = []
    monkeypatch.setattr(m, "_run", lambda *a, **k: ran.append(a))
    with pytest.raises(SystemExit) as e:
        m.main(["load", "--base", str(base), "--out", str(out), "--files", "Catalogs/Новый.xml"])
    assert "--allow-missing-ext" in str(e.value) and not ran
    m.main(["load", "--base", str(base), "--out", str(out), "--files", "Catalogs/Новый.xml",
            "--allow-missing-ext"])
    assert ran


def test_designer_sync_load_refuses_before_running(monkeypatch, tmp_path):
    m = _ds()
    out = tmp_path / "out"
    _touch(out, "Catalogs/Товары.xml")
    _touch(out, "Catalogs/Товары/Ext/ObjectModule.bsl")
    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.setenv("ONEC_V8_BIN", "1cv8")
    monkeypatch.setattr(m, "_run", lambda *a, **k: pytest.fail("загрузка не должна стартовать"))
    with pytest.raises(SystemExit) as e:
        m.main(["load", "--base", str(base), "--out", str(out), "--files", "Catalogs/Товары.xml"])
    assert "ObjectModule.bsl" in str(e.value)


# ── 2. точечная выгрузка теряет события и модуль формы ────────────────────────

FORM = "Catalogs/Товары/Forms/ФормаЭлемента/Ext/Form.xml"
MODULE = "Catalogs/Товары/Forms/ФормаЭлемента/Ext/Form/Module.bsl"


def test_form_lost_events_since_previous_dump(tmp_path):
    _touch(tmp_path, FORM, "<Form><Events><Event name='OnOpen'/></Events></Form>")
    _touch(tmp_path, MODULE)
    before = dumpload.form_snapshot(tmp_path)
    _touch(tmp_path, FORM, "<Form></Form>")                 # события удалили, модуль на месте
    soft = []
    assert dumpload.partial_dump_form_problems(tmp_path, before, warnings=soft) == []
    assert soft and "<Events>" in soft[0]
    (tmp_path / MODULE).unlink()                            # урезанная выгрузка: ни событий, ни модуля
    problems = dumpload.partial_dump_form_problems(tmp_path, before)
    assert problems and "<Events>" in problems[0]


def test_form_events_without_module(tmp_path):
    _touch(tmp_path, FORM, "<Form><Events><Event name='OnOpen'/></Events></Form>")
    problems = dumpload.partial_dump_form_problems(tmp_path, {})
    assert problems and "Module.bsl" in problems[0]


def test_complete_form_passes_with_warning(tmp_path, capsys):
    m = _ds()
    _touch(tmp_path, FORM, "<Form><Events/></Form>")
    _touch(tmp_path, MODULE)
    m.check_partial_dump_forms(tmp_path, dumpload.form_snapshot(tmp_path))
    assert "полной выгрузке" in capsys.readouterr().out
    _touch(tmp_path, FORM, "<Form/>")
    m.check_partial_dump_forms(tmp_path, {FORM: True})      # события убрали, модуль есть — предупреждение
    assert "<Events>" in capsys.readouterr().out
    (tmp_path / MODULE).unlink()
    with pytest.raises(SystemExit):
        m.check_partial_dump_forms(tmp_path, {FORM: True})


def _dump_main(monkeypatch, tmp_path, fresh: dict):
    """designer_sync dump --objects, где «платформа» кладёт в целевой каталог файлы `fresh`."""
    m = _ds()
    base = tmp_path / "base"
    base.mkdir(exist_ok=True)
    monkeypatch.setenv("ONEC_V8_BIN", "1cv8")
    seen = {}

    def fake_run(cmd, log):
        target = Path(cmd[cmd.index("/DumpConfigToFiles") + 1])
        seen["target"] = target
        for rel, text in fresh.items():
            _touch(target, rel, text)

    monkeypatch.setattr(m, "_run", fake_run)
    return m, base, seen


def test_partial_dump_goes_to_empty_dir_and_leaves_out_untouched_on_loss(monkeypatch, tmp_path):
    """Старый Form/Module.bsl в --out не маскирует, что новая выгрузка модуль не отдала."""
    out = tmp_path / "out"
    old_form = "<Form><Events><Event name='OnOpen'/></Events></Form>"
    _touch(out, FORM, old_form)
    _touch(out, MODULE, "старый модуль")
    m, base, seen = _dump_main(monkeypatch, tmp_path, {"Catalogs/Товары.xml": "<x/>", FORM: old_form})
    with pytest.raises(SystemExit) as e:
        m.main(["dump", "--base", str(base), "--out", str(out), "--objects", "Справочник.Товары"])
    assert "Module.bsl" in str(e.value) and str(seen["target"]) in str(e.value)
    assert seen["target"] != out.resolve() and seen["target"].is_dir()   # временный оставлен для разбора
    assert (out / MODULE).read_text(encoding="utf-8") == "старый модуль"
    assert not (out / "Catalogs/Товары.xml").exists()                   # out не тронут


# DISCIPLINE_ALLOW_TEST_EDIT: ревью №3 — перенос ничего не удаляет из --out (rmtree объекта стирал формы
# и модули владельца при выгрузке вложенного объекта); устаревший файл остаётся, об этом — строка в выводе
def test_partial_dump_success_copies_over_and_deletes_nothing(monkeypatch, tmp_path, capsys):
    out = tmp_path / "out"
    _touch(out, "Catalogs/Товары/Ext/Устаревший.bsl")                  # исчез в базе — в --out остаётся
    _touch(out, "Catalogs/Прочее.xml", "не трогать")
    form = "<Form><Events><Event name='OnOpen'/></Events></Form>"
    m, base, seen = _dump_main(monkeypatch, tmp_path, {
        "Catalogs/Товары.xml": "<new/>", FORM: form, MODULE: "новый модуль"})
    m.main(["dump", "--base", str(base), "--out", str(out), "--objects", "Справочник.Товары"])
    assert (out / MODULE).read_text(encoding="utf-8") == "новый модуль"
    assert (out / "Catalogs/Товары/Ext/Устаревший.bsl").exists()
    assert (out / "Catalogs/Прочее.xml").read_text(encoding="utf-8") == "не трогать"
    assert not seen["target"].exists()                                  # временный убран
    assert "в --out не удаляются" in capsys.readouterr().out


def test_partial_dump_of_command_keeps_owner_files(monkeypatch, tmp_path):
    """Точечная выгрузка команды не трогает формы, модули и другие команды справочника в --out."""
    out = tmp_path / "out"
    form = "<Form><Events><Event name='OnOpen'/></Events></Form>"
    keep = {FORM: form, MODULE: "модуль формы", "Catalogs/Товары.xml": "<obj/>",
            "Catalogs/Товары/Ext/ObjectModule.bsl": "модуль объекта",
            "Catalogs/Товары/Commands/Другая/Ext/CommandModule.bsl": "другая команда"}
    for rel, text in keep.items():
        _touch(out, rel, text)
    m, base, seen = _dump_main(monkeypatch, tmp_path, {
        "Catalogs/Товары/Commands/Открыть/Ext/CommandModule.bsl": "новая команда"})
    m.main(["dump", "--base", str(base), "--out", str(out),
            "--objects", "Справочник.Товары.Команда.Открыть"])
    for rel, text in keep.items():
        assert (out / rel).read_text(encoding="utf-8") == text, rel
    assert (out / "Catalogs/Товары/Commands/Открыть/Ext/CommandModule.bsl").read_text(
        encoding="utf-8") == "новая команда"


def test_nested_request_does_not_check_owner_forms(tmp_path):
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    _touch(old_dir, FORM, "<Form/>")
    _touch(new_dir, "Catalogs/Товары/Commands/Открыть/Ext/CommandModule.bsl")
    before = dumpload.form_snapshot(old_dir)
    soft = []
    assert dumpload.partial_dump_form_problems(new_dir, before, ["Справочник.Товары.Команда.Открыть"], soft) == []
    assert soft == []
    assert dumpload.partial_dump_form_problems(new_dir, before, ["Справочник.Товары"], soft) == []
    assert soft                                                  # целиком — проверяем, но предупреждением


# ── лог прошлого запуска и подсказки по ответам платформы ─────────────────────

class _Runner:
    host = "h"
    shell = True

    def __init__(self, out="__DSGN_OK__", log=""):
        self.out, self.log, self.events = out, log, []

    def remove(self, path, timeout=60):
        self.events.append(("remove", path))

    def run(self, cmd, timeout=600):
        self.events.append(("run", cmd))
        return 0, self.out

    def read_text(self, path, timeout=60):
        return self.log


def test_stale_log_removed_before_designer_runs():
    r = _Runner()
    dumpload._run_designer(r, r"srv\b", "u", "p", "/X", [], r"C:\w\x.log")
    assert r.events[0] == ("remove", r"C:\w\x.log") and r.events[1][0] == "run"


def test_designer_sync_run_removes_stale_log(monkeypatch, tmp_path):
    """Лог прошлого запуска не выдаётся за ответ этого: платформа упала, не написав лог."""
    m = _ds()
    log = tmp_path / "x.log"
    log.write_text("старый успешный лог", encoding="utf-8")

    class R:
        returncode = 1
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **kw: R())
    with pytest.raises(SystemExit) as e:
        m._run(["1cv8"], str(log))
    assert "старый" not in str(e.value) and "лог пуст" in str(e.value)


def test_remote_dump_clears_leftovers_before_dump(monkeypatch, tmp_path):
    m = _ds()
    r = _Runner()
    r.makedirs = lambda path: None
    calls = []
    monkeypatch.setattr(dumpload, "dump_extension", lambda *a, **kw: calls.append("dump"))
    monkeypatch.setattr(dumpload, "fetch_tree", lambda *a, **kw: calls.append("fetch"))
    m.remote_dump(r, r"srv\b", "u", "p", None, ["Справочник.Т"], tmp_path / "o", workdir=r"D:\w")
    assert r.events == [("run", r"if exist D:\w\dump rmdir /s /q D:\w\dump")]
    assert calls == ["dump", "fetch"]


def test_changed_files_cyrillic_and_rename(tmp_path):
    import shutil as _sh
    if not _sh.which("git"):
        pytest.skip("нет git")
    m = _ds()
    run = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    _touch(tmp_path, "Catalogs/Старое.xml")
    run("add", "-A")
    run("commit", "-qm", "x")
    run("mv", "Catalogs/Старое.xml", "Catalogs/Новое.xml")
    _touch(tmp_path, "Catalogs/Товары/Ext/ObjectModule.bsl")
    assert m.changed_files(str(tmp_path)) == ["Catalogs/Новое.xml", "Catalogs/Товары/Ext/ObjectModule.bsl"]


def test_dump_log_next_to_out_keeps_dotted_name(monkeypatch, tmp_path):
    out = tmp_path / "src.v2"
    (tmp_path / "src.log").write_text("чужой лог", encoding="utf-8")
    m, base, seen = _dump_main(monkeypatch, tmp_path, {})
    logs = []
    monkeypatch.setattr(m, "_run", lambda cmd, log: logs.append(log))
    m.main(["dump", "--base", str(base), "--out", str(out)])
    assert logs == [str(out.resolve()) + ".log"]
    assert (tmp_path / "src.log").read_text(encoding="utf-8") == "чужой лог"


def test_failed_partial_dump_removes_temp_dir(monkeypatch, tmp_path):
    out = tmp_path / "out"
    m, base, seen = _dump_main(monkeypatch, tmp_path, {})

    def fail(cmd, log):
        seen["target"] = Path(cmd[cmd.index("/DumpConfigToFiles") + 1])
        raise SystemExit("Конфигуратор exit=1")
    monkeypatch.setattr(m, "_run", fail)
    with pytest.raises(SystemExit):
        m.main(["dump", "--base", str(base), "--out", str(out), "--objects", "Справочник.Т"])
    assert seen["target"].parent == out.resolve().parent and not seen["target"].exists()


def test_server_base_without_creds_warns_partial_refuses(capsys):
    m = _ds()
    assert m.ib_creds(r"srv\ERP_Test", None, {}) == (None, None)
    assert "аутентификацией ОС" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        m.ib_creds(r"srv\ERP_Test", None, {"ONEC_IB_USER": "u"})


def test_designer_error_gets_hint():
    r = _Runner(out="__DSGN_FAIL__", log="Ошибка связывания с хранилищем конфигурации")
    with pytest.raises(ApplyError) as e:
        dumpload._run_designer(r, r"srv\b", "u", "p", "/X", [], "l")
    assert "привязана к одной ИБ" in str(e.value)


# ── 4. сторож зависшего пакетного сеанса ──────────────────────────────────────

class _HangingRunner:
    host = "h"
    shell = True

    def __init__(self):
        self.stop = threading.Event()
        self.killed = []

    def run(self, cmd, timeout=600):
        if "-EncodedCommand" in cmd:                         # остановка сторожем
            self.killed.append(cmd)
            self.stop.set()
            return 0, ""
        self.stop.wait(5)
        return 0, ""

    def read_text(self, path, timeout=60):
        return ""                                          # /Out пуст и не растёт


def test_watchdog_stops_silent_session(monkeypatch):
    monkeypatch.setattr(smoke, "client_cache_bytes", lambda: 0)
    r = _HangingRunner()
    with pytest.raises(ApplyError) as e:
        smoke._run_watched(r, "1cv8 ENTERPRISE /Out C:\\w\\o.log", timeout=60,
                           log="C:\\w\\o.log", stall=0.2, poll=0.05)
    assert "onec_verify.py check" in str(e.value)
    assert r.killed                                         # остановлен свой сеанс


def test_watchdog_off_waits_for_session(monkeypatch):
    r = _HangingRunner()
    threading.Timer(0.2, r.stop.set).start()
    smoke._run_watched(r, "1cv8", timeout=60, log="l", stall=0, poll=0.05)
    assert not r.killed


def test_scenario_with_return_refused_before_run():
    class R(_Runner):
        def put(self, *a, **k):
            pytest.fail("сценарий с Возврат не должен уходить в базу")
    with pytest.raises(ApplyError) as e:
        smoke.run_smoke(R(), r"srv\b", "u", "p", scenario_text='Если Истина Тогда\n  Возврат;\nКонецЕсли;',
                        epf="r.epf", workdir="W")
    assert "Возврат" in str(e.value)


# ── 5. учётка, путь к платформе, транспорт с кириллицей ───────────────────────

def test_ib_creds_key_defaults_to_base_name():
    m = _ds()
    env = {"ONEC_IB_USER": "u", "ONEC_IB_PASS_ERP_TEST": "p"}
    assert m.ib_creds(r"srv\ERP_Test", None, env) == ("u", "p")
    with pytest.raises(SystemExit):
        m.ib_creds(r"srv\Other", None, {"ONEC_IB_USER": "u"})


def test_ib_creds_file_base_may_have_no_user(tmp_path):
    m = _ds()
    assert m.ib_creds(str(tmp_path), None, {}) == (None, None)


def test_remote_bin_required_for_host():
    m = _ds()
    with pytest.raises(SystemExit):
        m.require_remote_bin({})
    m.require_remote_bin({"ONEC_1CV8_BIN": r"C:\x\1cv8.exe"})


def test_upload_uses_utf8_zip_and_checks_count(monkeypatch, tmp_path):
    m = _ds()
    src = tmp_path / "src"
    _touch(src, "CommonModules/Модуль/Ext/Module.bsl")
    _touch(src, "Configuration.xml")
    sent = {}

    def fake_scp(args, check=True):
        z = zipfile.ZipFile(args[-2])
        sent["names"] = z.namelist()
        sent["utf8"] = all(i.flag_bits & 0x800 for i in z.infolist() if not i.filename.isascii())

    monkeypatch.setattr(m.subprocess, "run", fake_scp)

    class R:
        host = "h"

        def __init__(self, count):
            self.count, self.cmds = count, []

        def run(self, cmd, timeout=600):
            self.cmds.append(cmd)
            return 0, f"COUNT={self.count}"

    r = R(2)
    m._upload_dir_plain(r, src, r"D:\w\dump")
    assert sorted(sent["names"]) == ["CommonModules/Модуль/Ext/Module.bsl", "Configuration.xml"]
    assert sent["utf8"] and "-EncodedCommand" in r.cmds[0] and "tar" not in r.cmds[0]
    with pytest.raises(SystemExit):
        m._upload_dir_plain(R(1), src, r"D:\w\dump")     # на сервере файлов меньше — отказ


# ── 3, 7. подсказки после загрузки и деплоя ───────────────────────────────────

def test_scheduled_job_note():
    m = _ds()
    notes = m.post_load_notes([r"ScheduledJobs\Обмен.xml", "CommonModules/X/Ext/Module.bsl"])
    assert notes and "Обмен" in notes[0] and "Включено" in notes[0]
    assert m.post_load_notes(["CommonModules/X/Ext/Module.bsl"]) == []


def test_deploy_extension_warns_about_safe_mode(monkeypatch, tmp_path, capsys):
    m = _ds()
    src = tmp_path / "ext"
    _touch(src, "Configuration.xml")
    _touch(src, "ScheduledJobs/Пульс.xml")
    monkeypatch.setenv("ONEC_1CV8_BIN", r"C:\x\1cv8.exe")
    monkeypatch.setenv("ONEC_IB_USER", "u")
    monkeypatch.setenv("ONEC_IB_PASS", "p")
    monkeypatch.setattr(m, "remote_deploy_extension", lambda *a, **k: None)
    m.main(["deploy-extension", "--base", r"srv\b", "--host", "h", "--ext", "Ext1", "--src", str(src)])
    out = capsys.readouterr().out
    assert "Безопасный режим" in out and "Пульс" in out


# ── 6. doctor: зависшие bash.exe Git ──────────────────────────────────────────

def test_stuck_git_shells():
    doctor = importlib.import_module("doctor")
    git = r"C:\Program Files\Git"
    idle = {"Children": 0, "CpuDelta": 0.0}
    procs = [{"Id": 1, "Path": git + r"\usr\bin\bash.exe", "Age": 5.04, **idle},
             {"Id": 2, "Path": git + r"\bin\bash.exe", "Age": 0.5, **idle},
             {"Id": 3, "Path": r"C:\tools\wsl\bash.exe", "Age": 9, **idle},
             {"Id": 4, "Path": r"D:\src\git\tools\bash.exe", "Age": 9, **idle},        # папка «git», не Git
             {"Id": 5, "Path": git + r"\usr\bin\bash.exe", "Age": 9, **idle},           # своя сессия
             {"Id": 6, "Path": git + r"\usr\bin\bash.exe", "Age": 9, "Children": 1, "CpuDelta": 0},
             {"Id": 7, "Path": git + r"\usr\bin\sh.exe", "Age": 9, "Children": 0, "CpuDelta": 0.3}]
    assert doctor.stuck_git_shells(procs, git, ancestors={5, 100}) == [(1, 5.0)]
    assert doctor.stuck_git_shells(procs, None) == []


def test_check_stuck_git_shells_wrapper(monkeypatch):
    """Обёртка: разбор JSON PowerShell, исключение предков, без команды Stop-Process."""
    doctor = importlib.import_module("doctor")
    git = r"C:\Program Files\Git"
    payload = {"procs": [{"Id": 11, "Path": git + r"\usr\bin\bash.exe", "Age": 5, "Children": 0, "CpuDelta": 0},
                         {"Id": 12, "Path": git + r"\usr\bin\bash.exe", "Age": 5, "Children": 0, "CpuDelta": 0}],
               "parents": [[999, 12], [12, 1]]}

    class R:
        stdout = json.dumps(payload)
    monkeypatch.setattr(doctor.os, "name", "nt")
    monkeypatch.setattr(doctor.os, "getpid", lambda: 999)            # 12 — предок текущего процесса
    monkeypatch.setattr(doctor, "_git_root", lambda: Path(git))
    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **kw: R())
    [(level, _, text)] = doctor.check_stuck_git_shells()
    assert level == doctor.WARN and "11 (5 ч)" in text and "12 (" not in text
    assert "Stop-Process" not in text

    payload["procs"] = payload["procs"][1:]
    R.stdout = json.dumps(payload)
    assert doctor.check_stuck_git_shells()[0][0] == doctor.OK

    def boom(*a, **kw):
        raise subprocess.TimeoutExpired("powershell", 30)
    monkeypatch.setattr(doctor.subprocess, "run", boom)
    assert doctor.check_stuck_git_shells()[0][0] == doctor.WARN


# ── ConfigDumpInfo.xml при переносе точечной выгрузки ─────────────────────────

_CDI_HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n<ConfigDumpInfo xmlns="http://v8.1c.ru/8.3/xcf/dumpinfo" '
             'format="Hierarchical" version="2.20">\n\t<ConfigVersions>\n')
_CDI_TAIL = "\t</ConfigVersions>\n</ConfigDumpInfo>"

OLD_CDI = _CDI_HEAD + (
    '\t\t<Metadata name="Configuration.Тест" id="1" configVersion="r1"/>\n'
    '\t\t<Metadata name="Catalog.Прочее" id="7" configVersion="p1"/>\n'
    '\t\t<Metadata name="Catalog.Прочее.ObjectModule" id="7.0" configVersion="p2"/>\n'
    '\t\t<Metadata name="Catalog.Товары" id="2" configVersion="old">\n'
    '\t\t\t<Metadata name="Catalog.Товары.Attribute.Код" id="3"/>\n'
    '\t\t</Metadata>\n'
    '\t\t<Metadata name="Catalog.Товары.ObjectModule" id="2.0" configVersion="old"/>\n'
    '\t\t<Metadata name="Catalog.ТоварыАрхив" id="9" configVersion="z1"/>\n') + _CDI_TAIL

NEW_CDI = _CDI_HEAD + (
    '\t\t<Metadata name="Configuration.Тест" id="1" configVersion="ЧАСТИЧНАЯ"/>\n'
    '\t\t<Metadata name="Catalog.Товары" id="2" configVersion="new"/>\n'
    '\t\t<Metadata name="Catalog.Товары.ManagerModule" id="2.3" configVersion="new"/>\n'
    '\t\t<Metadata name="Catalog.Товары.ObjectModule" id="2.0" configVersion="new"/>\n') + _CDI_TAIL


def test_merge_partial_dump_keeps_full_dump_info(tmp_path):
    """В --out полный реестр на два объекта, точечно выгружен один: записи второго на месте,
    первого — новые; корень, формат, BOM и CRLF старого файла сохранены."""
    m = _ds()
    out, src = tmp_path / "out", tmp_path / "src"
    out.mkdir()
    (out / "ConfigDumpInfo.xml").write_bytes(b"\xef\xbb\xbf" + OLD_CDI.replace("\n", "\r\n").encode("utf-8"))
    _touch(src, "Catalogs/Товары.xml", "<new/>")
    (src / "ConfigDumpInfo.xml").write_text(NEW_CDI, encoding="utf-8")
    m.merge_partial_dump(src, out)
    raw = (out / "ConfigDumpInfo.xml").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    text = raw[3:].decode("utf-8")
    expected = _CDI_HEAD + (
        '\t\t<Metadata name="Configuration.Тест" id="1" configVersion="r1"/>\n'
        '\t\t<Metadata name="Catalog.Прочее" id="7" configVersion="p1"/>\n'
        '\t\t<Metadata name="Catalog.Прочее.ObjectModule" id="7.0" configVersion="p2"/>\n'
        '\t\t<Metadata name="Catalog.Товары" id="2" configVersion="new"/>\n'
        '\t\t<Metadata name="Catalog.Товары.ObjectModule" id="2.0" configVersion="new"/>\n'
        '\t\t<Metadata name="Catalog.ТоварыАрхив" id="9" configVersion="z1"/>\n'
        '\t\t<Metadata name="Catalog.Товары.ManagerModule" id="2.3" configVersion="new"/>\n') + _CDI_TAIL
    # DISCIPLINE_ALLOW_TEST_EDIT: ревью №3 — слияние по имени записи: совпавшие заменяются на месте,
    # новые (ManagerModule) — в конец, ничего не удаляется
    assert text == expected.replace("\n", "\r\n")             # «ТоварыАрхив» — не запись «Товары»


def test_merge_dump_info_nested_dump_keeps_owner_entries():
    """В новом реестре только команда — записи формы и модулей справочника остаются."""
    new = _CDI_HEAD + ('\t\t<Metadata name="Catalog.Товары.Command.Открыть.CommandModule" id="4.0" '
                       'configVersion="n"/>\n') + _CDI_TAIL
    merged = dumpload.merge_dump_info(OLD_CDI, new)
    for name in ("Catalog.Товары.ObjectModule", "Catalog.Товары.Attribute.Код", "Catalog.Прочее"):
        assert f'name="{name}"' in merged
    assert merged.rstrip().endswith('configVersion="n"/>\n' + _CDI_TAIL)


# DISCIPLINE_ALLOW_TEST_EDIT: ревью №5 — частичный реестр в пустой --out больше не пишется: с ним
# существующие объекты, которых в нём нет, при точечной загрузке сошли бы за новые и потеряли модули
def test_merge_partial_dump_without_old_dump_info_does_not_save_partial(tmp_path, capsys):
    m = _ds()
    out, src = tmp_path / "out", tmp_path / "src"
    out.mkdir()
    _touch(src, "Catalogs/Товары.xml")
    (src / "ConfigDumpInfo.xml").write_text(NEW_CDI, encoding="utf-8")
    m.merge_partial_dump(src, out)
    assert not (out / "ConfigDumpInfo.xml").exists()
    assert (out / "Catalogs/Товары.xml").exists()
    assert "реестр неполный" in capsys.readouterr().out
    # без реестра xml без Ext — отказ, пока не передан --allow-missing-ext
    gaps = dumpload.partial_load_gaps(out, ["Catalogs/Товары.xml"])
    assert gaps and "ConfigDumpInfo" in gaps[0]
    assert dumpload.partial_load_gaps(out, ["Catalogs/Товары.xml"], allow_missing_ext=True) == []


# ── повторное ревью ───────────────────────────────────────────────────────────

def test_status_without_host_option(tmp_path, capsys):
    m = _ds()
    m.main(["status", "--out", str(tmp_path)])                    # без AttributeError на a.host
    assert "не под git" in capsys.readouterr().out


def test_form_missing_from_new_dump_of_same_object(tmp_path):
    """Форма выгруженного объекта была в прошлой выгрузке и не пришла — перенос удалил бы её."""
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    _touch(old_dir, FORM, "<Form/>")
    _touch(old_dir, "Catalogs/Прочее/Forms/Ф/Ext/Form.xml", "<Form/>")
    before = dumpload.form_snapshot(old_dir)
    _touch(new_dir, "Catalogs/Товары.xml", "<x/>")                   # выгружен только «Товары», без формы
    soft = []
    assert dumpload.partial_dump_form_problems(new_dir, before, warnings=soft) == []   # могли удалить сознательно
    assert soft == [f"{FORM}: форма была в прошлой выгрузке, в новой её нет (в --out остаётся старая)"]


def test_nested_command_xml_requires_its_module(tmp_path):
    (tmp_path / "ConfigDumpInfo.xml").write_text(DUMP_INFO, encoding="utf-8")
    _touch(tmp_path, "Catalogs/Товары/Commands/Открыть.xml")
    gaps = dumpload.partial_load_gaps(tmp_path, ["Catalogs/Товары/Commands/Открыть.xml"])
    assert gaps and "Catalogs/Товары/Commands/Открыть/Ext/CommandModule.bsl" in gaps[0]
    assert dumpload.partial_load_gaps(tmp_path, [
        "Catalogs/Товары/Commands/Открыть.xml",
        "Catalogs/Товары/Commands/Открыть/Ext/CommandModule.bsl"]) == []


def test_nested_recalculation_module_required_with_register(tmp_path):
    info = DUMP_INFO.replace(
        "\t</ConfigVersions>",
        '\t\t<Metadata name="CalculationRegister.Начисления" id="8"/>\n'
        '\t\t<Metadata name="CalculationRegister.Начисления.Recalculation.Пересчет.RecordSetModule" id="8.1"/>\n'
        "\t</ConfigVersions>")
    (tmp_path / "ConfigDumpInfo.xml").write_text(info, encoding="utf-8")
    _touch(tmp_path, "CalculationRegisters/Начисления.xml")
    gaps = dumpload.partial_load_gaps(tmp_path, ["CalculationRegisters/Начисления.xml"])
    assert any("CalculationRegisters/Начисления/Recalculations/Пересчет/Ext/RecordSetModule.bsl" in g
               for g in gaps)


def test_stall_off_by_default_and_rejects_negative():
    class Local:
        host = ""
    assert smoke.resolve_stall(Local(), None) == 0                    # долгий сценарий не убиваем
    assert smoke.resolve_stall(_HangingRunner(), None) == 0
    assert smoke.resolve_stall(_HangingRunner(), 120) == 120
    with pytest.raises(ApplyError):
        smoke.resolve_stall(Local(), -1)


def test_remote_watchdog_ignores_local_cache(monkeypatch):
    """Удалённый сеанс: прогресс — только /Out; локальный кэш не читается вовсе."""
    monkeypatch.setattr(smoke, "client_cache_bytes",
                        lambda: pytest.fail("локальный кэш к удалённому сеансу не относится"))
    r = _HangingRunner()
    with pytest.raises(ApplyError):
        smoke._run_watched(r, "1cv8 ENTERPRISE", timeout=60, log="C:\\w\\o.log", stall=0.2, poll=0.05)
    assert r.killed


def test_remote_watchdog_growing_out_is_progress(monkeypatch):
    r = _HangingRunner()
    ticks = iter(range(10 ** 6))
    r.read_text = lambda path, timeout=60: "строка\n" * next(ticks)    # /Out растёт
    threading.Timer(0.5, r.stop.set).start()
    smoke._run_watched(r, "1cv8", timeout=60, log="l", stall=0.2, poll=0.05)
    assert not r.killed


# ── ревью №4 ──────────────────────────────────────────────────────────────────

def test_base_key_and_ib_creds_by_base_kind(tmp_path):
    m = _ds()
    assert m.base_key(r"srv\ERP_Test") == "ERP_Test"
    for file_base in (r"C:\bases\erp", r"d:/bases/erp", r"\\srv\share\erp", "/opt/bases/erp",
                      'File="C:\\bases\\erp";', "FILE=C:\\b", str(tmp_path)):
        assert m.base_key(file_base) is None, file_base
        assert m.ib_creds(file_base, None, {}) == (None, None)
    assert m.ib_creds(r"srv\ERP_Test", None, {"ONEC_IB_USER": "u", "ONEC_IB_PASS_ERP_TEST": "p"}) == ("u", "p")
    assert m.ib_creds(r"srv\ERP_Test", "kz", {"ONEC_IB_USER": "u", "ONEC_IB_PASS_KZ": "k"}) == ("u", "k")


def test_broken_new_dump_info_leaves_out_untouched(tmp_path):
    m = _ds()
    out, src = tmp_path / "out", tmp_path / "src"
    out.mkdir()
    (out / "ConfigDumpInfo.xml").write_text(OLD_CDI, encoding="utf-8")
    _touch(out, MODULE, "старый модуль")
    _touch(src, MODULE, "новый модуль")
    (src / "ConfigDumpInfo.xml").write_text(
        _CDI_HEAD + '\t\t<Metadata name="Catalog.Товары" id="2">\n', encoding="utf-8")   # не закрыт
    with pytest.raises(SystemExit) as e:
        m.merge_partial_dump(src, out)
    assert str(src) in str(e.value)
    assert (out / MODULE).read_text(encoding="utf-8") == "старый модуль"
    assert (out / "ConfigDumpInfo.xml").read_text(encoding="utf-8") == OLD_CDI
    assert not (out / "ConfigDumpInfo.xml.tmp").exists()


@pytest.mark.parametrize("text", [
    "Если Условие Тогда Возврат; КонецЕсли;",
    "Если Истина Тогда\n  Возврат;\nКонецЕсли;",
    "If True Then return; EndIf;",
])
def test_return_anywhere_in_code_is_refused(text):
    assert smoke._RETURN_RE.search(smoke._code_only(text))


@pytest.mark.parametrize("text", [
    'Отчет = "текст Возврат";',
    'Отчет = "кавычка "" и Возврат";',
    'Отчет = "строка\n|Возврат в продолжении";',
    "// Возврат в комментарии\nОтчет = 1;",
    "Отчет = Док.Возврат;",                 # свойство, а не оператор
    "ВозвратТоваров = 1;",
])
def test_return_in_strings_comments_and_names_is_allowed(text):
    assert not smoke._RETURN_RE.search(smoke._code_only(text))
