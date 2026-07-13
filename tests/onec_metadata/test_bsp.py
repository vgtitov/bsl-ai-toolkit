# DISCIPLINE_ALLOW_TEST_EDIT: новый тест, TDD red-фаза (Фаза 5: БСП доп. обработки)
"""Фаза 5: запуск ENTERPRISE и регистрация внешней обработки в БСП
«Дополнительные отчёты и обработки» через обработку-регистратор."""
from pathlib import Path

import pytest


class FakeRunner:
    host = "f"

    def __init__(self, out="__ENT_OK__"):
        self.out = out
        self.calls = []

    def run(self, cmd, timeout=600):
        self.calls.append(cmd)
        return 0, self.out


def test_enterprise_cmd_builds():
    from onec_metadata.apply.runner import enterprise_cmd

    cmd = enterprise_cmd(
        ib=r"localhost\testbase", user="Разработчик", pwd="XXX",
        execute_epf=r"D:\src\Регистратор.epf",
        c_param=r"D:\src\Обработка.epf;РаботаСФайлами",
        log=r"D:\src\ent.log")
    assert "ENTERPRISE" in cmd
    assert r"/S localhost\testbase" in cmd
    assert '/N"Разработчик"' in cmd
    assert r'/Execute "D:\src\Регистратор.epf"' in cmd
    assert '/C"D:\\src\\Обработка.epf;РаботаСФайлами"' in cmd
    assert "/DisableStartupDialogs" in cmd
    assert cmd.endswith(r"/Out D:\src\ent.log")


def test_register_external_runs_enterprise(monkeypatch):
    from onec_metadata.apply import bsp

    monkeypatch.setattr(bsp, "_read_log", lambda r, log: "")
    r = FakeRunner(out="__ENT_OK__")
    bsp.register_external(
        r, r"localhost\testbase", "u", "p",
        registrar_epf=r"D:\src\Регистратор.epf",
        target_epf=r"D:\src\Обработка.epf")
    joined = " || ".join(r.calls)
    assert "ENTERPRISE" in joined
    assert "Регистратор.epf" in joined
    assert "Обработка.epf" in joined


def test_register_external_failure_raises(monkeypatch):
    from onec_metadata.apply import bsp
    from onec_metadata.apply.dumpload import ApplyError

    monkeypatch.setattr(bsp, "_read_log", lambda r, log: "")
    with pytest.raises(ApplyError):
        bsp.register_external(
            FakeRunner(out="что-то пошло не так"),  # нет маркера успеха
            "ib", "u", "p",
            registrar_epf=r"D:\r.epf", target_epf=r"D:\t.epf")


def test_registrar_template_exists_and_parametrized():
    """BSL-шаблон обработки-регистратора поставляется с toolkit
    (обезличенный, собирается через build_external)."""
    tpl = (Path(__file__).parents[2] / "onec_metadata" / "templates" /
           "bsp_register_external.bsl")
    text = tpl.read_text(encoding="utf-8")
    assert "ДополнительныеОтчетыИОбработки" in text
    assert "ПараметрЗапуска" in text  # берёт цель из /C
    assert "__ENT_OK__" in text       # печатает маркер успеха в лог
