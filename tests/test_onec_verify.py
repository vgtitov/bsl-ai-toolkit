# DISCIPLINE_ALLOW_TEST_EDIT: новый тест — CLI лестницы тестирования (ступени 1–2)
"""onec_verify — CLI ступеней 1–2: разбор аргументов, выбор транспорта, рабочий каталог.

Запуск платформы не мокается «насквозь»: подменяются ровно точки выхода наружу
(check_config / smoke), проверяется, ЧТО им передали.
"""
import importlib
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


def _load(monkeypatch):
    monkeypatch.setenv("ONEC_1CV8_BIN", r"C:\1cv8.exe")   # без авто-поиска платформы
    sys.path.insert(0, str(SCRIPTS_DIR))
    return importlib.reload(importlib.import_module("onec_verify"))


def test_resolve_bin_priority(monkeypatch):
    m = _load(monkeypatch)
    assert m.resolve_bin(r"D:\явный\1cv8.exe") == r"D:\явный\1cv8.exe"   # аргумент важнее env
    assert m.resolve_bin() == r"C:\1cv8.exe"                              # затем env


def test_default_workdir_local_is_not_server_path(monkeypatch):
    """Локально нельзя брать серверный D:\\src — его на машине разработчика нет."""
    m = _load(monkeypatch)
    monkeypatch.setenv("ONEC_REMOTE_WORKDIR", r"D:\src")
    assert m.default_workdir("srv-1c").startswith(r"D:\src")
    local = m.default_workdir(None)
    assert not local.startswith(r"D:\src")
    assert Path(local).parent.is_dir()          # родитель существует (временный каталог)


def test_check_uses_local_runner_without_host(monkeypatch):
    m = _load(monkeypatch)
    seen = {}

    import onec_metadata.apply.external as ext
    from onec_metadata.apply.runner import LocalRunner

    def fake_check(r, ib, user, pwd, *, ext=None, modes=None, log=None):
        seen.update(runner=r, ib=ib, ext=ext, log=log)

    monkeypatch.setattr(ext, "check_config", fake_check)
    monkeypatch.setenv("ONEC_IB_USER", "ivanov")
    monkeypatch.setenv("ONEC_IB_PASS_ERP_TEST", "secret")
    m.main(["check", "--base", r"srv\ERP_Test", "--ext", "ai_debug",
            "--contour", "ERP_TEST"])

    assert isinstance(seen["runner"], LocalRunner)        # без --host — эта машина
    assert seen["ib"] == r"srv\ERP_Test"
    assert seen["ext"] == "ai_debug"
    assert "check_config.log" in seen["log"]


def test_check_with_host_uses_ssh_runner(monkeypatch):
    m = _load(monkeypatch)
    seen = {}

    import onec_metadata.apply.external as ext
    from onec_metadata.apply.runner import Runner

    monkeypatch.setattr(ext, "check_config",
                        lambda r, *args, **kw: seen.update(runner=r))
    m.main(["check", "--base", r"srv\ERP", "--host", "onec-srv",
            "--workdir", r"D:\src\wd"])
    assert isinstance(seen["runner"], Runner) and seen["runner"].host == "onec-srv"


def test_smoke_passes_scenario_text_and_credentials(monkeypatch, tmp_path):
    m = _load(monkeypatch)
    seen = {}

    import onec_metadata.apply.smoke as smoke_mod

    def fake_run(r, ib, user, pwd, *, scenario_text, epf, workdir, timeout=900):
        seen.update(user=user, pwd=pwd, text=scenario_text, epf=epf, timeout=timeout)
        return "строк: 3"

    monkeypatch.setattr(smoke_mod, "run_smoke", fake_run)
    monkeypatch.setattr(smoke_mod, "deploy_smoke_runner",
                        lambda *a, **kw: pytest.fail("без --deploy раннер не пересобирается"))
    monkeypatch.setenv("ONEC_IB_USER", "ivanov")
    monkeypatch.setenv("ONEC_IB_PASS", "common")

    scen = tmp_path / "s.bsl"
    scen.write_text('Отчет = "привет";', encoding="utf-8-sig")
    m.main(["smoke", "--base", str(tmp_path), "--scenario", str(scen),
            "--workdir", str(tmp_path / "wd"), "--timeout", "60"])

    assert seen["user"] == "ivanov" and seen["pwd"] == "common"
    assert seen["text"] == 'Отчет = "привет";'      # BOM снят при чтении сценария
    assert seen["timeout"] == 60
    assert seen["epf"].endswith("СмоукРаннер.epf")


def test_smoke_deploy_builds_runner_first(monkeypatch, tmp_path):
    m = _load(monkeypatch)
    order = []

    import onec_metadata.apply.smoke as smoke_mod

    monkeypatch.setattr(smoke_mod, "deploy_smoke_runner",
                        lambda *a, **kw: (order.append("deploy"), r"D:\wd\СмоукРаннер.epf")[1])
    monkeypatch.setattr(smoke_mod, "run_smoke",
                        lambda *a, **kw: (order.append("run"), "ок")[1])
    scen = tmp_path / "s.bsl"
    scen.write_text('Отчет = "x";', encoding="utf-8")
    m.main(["smoke", "--base", str(tmp_path), "--scenario", str(scen),
            "--deploy", "--workdir", str(tmp_path / "wd")])
    assert order == ["deploy", "run"]


def test_base_is_required(monkeypatch):
    m = _load(monkeypatch)
    with pytest.raises(SystemExit):
        m.main(["check"])
