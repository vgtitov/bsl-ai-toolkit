# DISCIPLINE_ALLOW_TEST_EDIT: новый тест по TDD (модуль smoke ещё не написан)
"""Ступень 2 лестницы тестирования: smoke-раннер (apply.smoke).

deploy_smoke_runner: шаблон templates/smoke_runner → сервер → build_external.
run_smoke: сценарий BSL → сервер, ENTERPRISE /Execute раннера, вердикт ТОЛЬКО
по файлу-результату (__SMOKE_OK__/__SMOKE_FAIL__).
"""
import pytest

from onec_metadata.apply import smoke
from onec_metadata.apply.dumpload import ApplyError


class FakeRunner:
    # DISCIPLINE_ALLOW_TEST_EDIT: перенос файлов теперь через r.put() (SSH/локально),
    # а не через пропатченный subprocess — FakeRunner получает свой put()
    host = "fake-host"

    def __init__(self, result_text="__SMOKE_OK__\nотчёт: всё хорошо"):
        self.commands = []
        self.puts = []
        self.result_text = result_text

    def run(self, cmd, timeout=600):
        self.commands.append(cmd)
        if "type" in cmd and "smoke_result" in cmd:
            return 0, self.result_text
        return 0, "EXIT=0"

    def put(self, local, remote, timeout=300):
        self.puts.append((str(local), remote))


def test_run_smoke_ok_returns_report(tmp_path):
    r = FakeRunner()
    report = smoke.run_smoke(r, "localhost\\ib", "u", "p",
                             scenario_text="Отчет = \"ок\";",
                             epf=r"D:\smoke\СмоукРаннер.epf",
                             workdir=r"D:\smoke")
    assert "всё хорошо" in report
    joined = "\n".join(r.commands)
    assert "ENTERPRISE" in joined
    assert "/Execute" in joined
    assert "smoke_scenario" in joined and "smoke_result" in joined


def test_run_smoke_fail_raises(tmp_path):
    r = FakeRunner(result_text="__SMOKE_FAIL__\nОшибка: нет проведённых РТУ")
    with pytest.raises(ApplyError) as e:
        smoke.run_smoke(r, "localhost\\ib", "u", "p",
                        scenario_text="ВызватьИсключение \"нет проведённых РТУ\";",
                        epf=r"D:\smoke\СмоукРаннер.epf",
                        workdir=r"D:\smoke")
    assert "нет проведённых РТУ" in str(e.value)


def test_run_smoke_no_result_file_raises():
    r = FakeRunner(result_text="")
    with pytest.raises(ApplyError):
        smoke.run_smoke(r, "localhost\\ib", "u", "p",
                        scenario_text="Отчет = \"x\";",
                        epf=r"D:\smoke\СмоукРаннер.epf",
                        workdir=r"D:\smoke")


def test_template_dir_exists_and_complete():
    files = {p.relative_to(smoke.TEMPLATE_DIR).as_posix()
             for p in smoke.TEMPLATE_DIR.rglob("*") if p.is_file()}
    assert "СмоукРаннер.xml" in files
    assert "СмоукРаннер/Forms/Форма/Ext/Form/Module.bsl" in files
    module = (smoke.TEMPLATE_DIR /
              "СмоукРаннер/Forms/Форма/Ext/Form/Module.bsl").read_text("utf-8-sig")
    assert "__SMOKE_OK__" in module and "__SMOKE_FAIL__" in module
    assert "ЗавершитьРаботуСистемы" in module
