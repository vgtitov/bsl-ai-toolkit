# DISCIPLINE_ALLOW_TEST_EDIT: обезличенный apply-тест (значения generic)
"""apply-слой: построение команд DESIGNER и round-trip verify (без SSH)."""
import unicodedata
from pathlib import Path

import pytest


def test_designer_cmd_load_extension():
    from onec_metadata.apply.runner import designer_cmd

    cmd = designer_cmd(
        ib=r"localhost\testbase", user="Разработчик", pwd="XXX",
        action="/LoadConfigFromFiles",
        args=[r"D:\src\ext_apply\Расширение", "-Extension", "Расширение"],
        log=r"D:\src\load.log")
    assert "DESIGNER" in cmd
    assert r"/S localhost\testbase" in cmd
    assert '/N"Разработчик"' in cmd
    assert "/LoadConfigFromFiles" in cmd
    assert "-Extension" in cmd
    assert "/DisableStartupDialogs" in cmd
    assert cmd.endswith(r"/Out D:\src\load.log")
    assert "XXX" in cmd  # пароль подставлен (в лог не пишем, см. runner)


def test_run_designer_gate_uses_runtime_errorlevel(monkeypatch):
    """Барьер провала DESIGNER не должен зависеть от `%errorlevel%`,
    раскрываемого cmd'ом ДО запуска exe. Провал (ненулевой код) обязан
    приводить к ApplyError; успех — нет. Лог пуст в обоих случаях."""
    from onec_metadata.apply import dumpload

    monkeypatch.setattr(dumpload, "_read_log", lambda r, log: "")

    class FakeRunner:
        host = "f"

        def __init__(self, out):
            self.out = out
            self.last_cmd = None

        def run(self, cmd, timeout=600):
            self.last_cmd = cmd
            return 0, self.out

    ok_runner = FakeRunner("__DSGN_OK__")
    dumpload._run_designer(ok_runner, "ib", "u", "p", "/X", [], "log")
    assert ok_runner.last_cmd is not None
    assert "%errorlevel%" not in ok_runner.last_cmd
    assert "if errorlevel 1" in ok_runner.last_cmd

    with pytest.raises(dumpload.ApplyError):
        dumpload._run_designer(FakeRunner("bla __DSGN_FAIL__ bla"),
                               "ib", "u", "p", "/X", [], "log")


def test_load_extension_updates_dbcfg_for_extension(monkeypatch):
    """UpdateDBCfg после загрузки расширения ОБЯЗАН идти с -Extension <имя>:
    без него загруженная версия расширения не применяется к ИБ."""
    from onec_metadata.apply import dumpload

    calls = []

    class FakeRunner:
        host = "fake"

        def run(self, cmd, timeout=600):
            calls.append(cmd)
            return 0, "__DSGN_OK__"

    monkeypatch.setattr(dumpload, "_read_log", lambda r, log: "")
    dumpload.load_extension(FakeRunner(), r"localhost\testbase", "u", "p",
                            "Расширение", r"D:\src\ext")
    update_calls = [c for c in calls if "/UpdateDBCfg" in c]
    assert update_calls, "UpdateDBCfg не вызван"
    assert all("-Extension Расширение" in c for c in update_calls), update_calls


def test_roundtrip_verify(tmp_path):
    from onec_metadata.apply.dumpload import ApplyError, roundtrip_verify

    a = tmp_path / "a"
    b = tmp_path / "b"
    (a / "sub").mkdir(parents=True)
    (b / "sub").mkdir(parents=True)
    (a / "same.txt").write_text("x")
    (b / "same.txt").write_text("x")
    (a / "sub/ch.txt").write_text("1")
    (b / "sub/ch.txt").write_text("2")

    roundtrip_verify(a, b, expect_changed={"sub/ch.txt"})

    with pytest.raises(ApplyError):
        roundtrip_verify(a, b, expect_changed=set())
    with pytest.raises(ApplyError):
        roundtrip_verify(a, b, expect_changed={"sub/ch.txt", "нет.txt"})


def test_roundtrip_verify_ignores_dumpinfo(tmp_path):
    """ConfigDumpInfo.xml — служебный файл выгрузки; не считается изменением."""
    from onec_metadata.apply.dumpload import roundtrip_verify

    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "ConfigDumpInfo.xml").write_text("v1")
    (b / "ConfigDumpInfo.xml").write_text("v2")
    roundtrip_verify(a, b, expect_changed=set())


def test_roundtrip_verify_unicode_nfc(tmp_path):
    """macOS отдаёт кириллические имена в NFD; ожидания пишутся в NFC —
    сравнение обязано быть нормализованным."""
    from onec_metadata.apply.dumpload import roundtrip_verify

    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    name_nfd = unicodedata.normalize("NFD", "Справочник.xml")
    (b / name_nfd).write_text("x")
    roundtrip_verify(a, b, expect_changed={"Справочник.xml"})


def test_roundtrip_verify_new_file(tmp_path):
    from onec_metadata.apply.dumpload import ApplyError, roundtrip_verify

    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (b / "new.txt").write_text("n")

    roundtrip_verify(a, b, expect_changed={"new.txt"})
    with pytest.raises(ApplyError):
        roundtrip_verify(a, b, expect_changed=set())
