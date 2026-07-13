# DISCIPLINE_ALLOW_TEST_EDIT: контракт БСП через ФАЙЛ-РЕЗУЛЬТАТ (фикс ревью Important #1)
"""Фаза 5: регистрация внешней обработки в БСП «Дополнительные отчёты и
обработки». Клиентский запуск ENTERPRISE кода возврата не даёт и Сообщить/ЖР
в /Out не кладёт, поэтому успех определяется по СОДЕРЖИМОМУ файла-результата,
который регистратор пишет сам."""
from pathlib import Path

import pytest


class FakeRunner:
    """result_content — что вернёт `type <result_file>`; enterprise-запуск даёт
    пустой stdout (как настоящий клиент)."""

    host = "f"

    def __init__(self, result_content="__ENT_OK__"):
        self.result_content = result_content
        self.calls = []

    def run(self, cmd, timeout=600):
        self.calls.append(cmd)
        if cmd.strip().lower().startswith("chcp") and "type" in cmd.lower():
            return 0, self.result_content        # чтение файла-результата
        return 0, ""                             # сам ENTERPRISE — пустой stdout


def test_enterprise_cmd_builds():
    from onec_metadata.apply.runner import enterprise_cmd

    cmd = enterprise_cmd(
        ib=r"localhost\testbase", user="Разработчик", pwd="XXX",
        execute_epf=r"D:\src\Регистратор.epf",
        c_param=r"D:\src\Обработка.epf;;D:\src\res.txt",
        log=r"D:\src\ent.log")
    assert "ENTERPRISE" in cmd
    assert r'/Execute "D:\src\Регистратор.epf"' in cmd
    assert '/C"D:\\src\\Обработка.epf;;D:\\src\\res.txt"' in cmd
    assert cmd.endswith(r"/Out D:\src\ent.log")


def test_register_external_success_from_result_file(monkeypatch):
    from onec_metadata.apply import bsp

    r = FakeRunner(result_content="__ENT_OK__")
    bsp.register_external(
        r, r"localhost\testbase", "u", "p",
        registrar_epf=r"D:\src\Регистратор.epf",
        target_epf=r"D:\src\Обработка.epf")
    joined = " || ".join(r.calls)
    assert "ENTERPRISE" in joined
    assert "Регистратор.epf" in joined
    # цель и путь файла-результата переданы регистратору в /C
    assert "Обработка.epf" in joined
    # файл-результат прочитан (а не /Out-лог)
    assert any("type" in c.lower() for c in r.calls)


def test_register_external_failure_when_no_marker():
    from onec_metadata.apply import bsp
    from onec_metadata.apply.dumpload import ApplyError

    # регистратор записал ошибку (нет маркера) — обязан упасть
    with pytest.raises(ApplyError):
        bsp.register_external(
            FakeRunner(result_content="Ошибка регистрации: ..."),
            "ib", "u", "p",
            registrar_epf=r"D:\r.epf", target_epf=r"D:\t.epf")


def test_register_external_failure_when_result_empty():
    from onec_metadata.apply import bsp
    from onec_metadata.apply.dumpload import ApplyError

    # файл-результат пуст (регистратор не отработал) — падаем, не считаем успехом
    with pytest.raises(ApplyError):
        bsp.register_external(
            FakeRunner(result_content=""),
            "ib", "u", "p",
            registrar_epf=r"D:\r.epf", target_epf=r"D:\t.epf")


def test_registrar_template_writes_marker_to_result_file():
    """BSL-шаблон обязан писать маркер в ФАЙЛ-результат (3-е поле /C),
    а не только в журнал регистрации."""
    tpl = (Path(__file__).parents[2] / "onec_metadata" / "templates" /
           "bsp_register_external.bsl")
    text = tpl.read_text(encoding="utf-8")
    assert "ДополнительныеОтчетыИОбработки" in text
    assert "ПараметрЗапуска" in text
    assert "__ENT_OK__" in text
    # маркер идёт в файл-результат (запись текста), не только в ЖР
    assert "ЗаписатьРезультат" in text or "ЗаписьТекста" in text or "ТекстовыйДокумент" in text
