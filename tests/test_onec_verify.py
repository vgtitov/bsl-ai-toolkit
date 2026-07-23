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

# DISCIPLINE_ALLOW_TEST_EDIT: общая константа вместо литералов с диском D:
# Строка соединения серверной базы — не путь, к ФС и к дискам отношения не имеет.
# Реальные каталоги в тестах берём ТОЛЬКО из tmp_path: диска D: на машине может не быть,
# как и вообще букв дисков (Linux/macOS).
SERVER_BASE = "srv-1c\\ERP_Test"
BIN_STUB = "1cv8-stub"           # значение env: resolve_bin его только возвращает, не проверяет


def _load(monkeypatch):
    monkeypatch.setenv("ONEC_1CV8_BIN", BIN_STUB)   # без авто-поиска платформы
    sys.path.insert(0, str(SCRIPTS_DIR))
    return importlib.reload(importlib.import_module("onec_verify"))


def test_resolve_bin_priority(monkeypatch):
    m = _load(monkeypatch)
    assert m.resolve_bin("1cv8-explicit") == "1cv8-explicit"   # аргумент важнее env
    assert m.resolve_bin() == BIN_STUB                              # затем env


# DISCIPLINE_ALLOW_TEST_EDIT: маркер вместо литерала D:\src (диска может не быть)
def test_default_workdir_local_is_not_server_path(monkeypatch):
    """Серверный рабочий каталог локально брать нельзя — его на машине разработчика нет
    (у сервера это обычно D:\\src, но диска D: у разработчика может не быть вовсе)."""
    m = _load(monkeypatch)
    remote = "SRV_WORKDIR_MARKER"
    monkeypatch.setenv("ONEC_REMOTE_WORKDIR", remote)
    assert m.default_workdir("srv-1c").startswith(remote)   # с --host — путь сервера
    local = m.default_workdir(None)
    assert remote not in local
    assert Path(local).parent.is_dir()          # родитель существует (временный каталог)


# DISCIPLINE_ALLOW_TEST_EDIT: убираю привязку к диску D: и реальный вызов ssh из теста
def test_check_uses_local_runner_without_host(monkeypatch, tmp_path):
    m = _load(monkeypatch)
    seen = {}

    import onec_metadata.apply.external as ext
    from onec_metadata.apply.runner import LocalRunner

    def fake_check(r, ib, user, pwd, *, ext=None, modes=None, log=None):
        seen.update(runner=r, ib=ib, ext=ext, log=log)

    monkeypatch.setattr(ext, "check_config", fake_check)
    monkeypatch.setenv("ONEC_IB_USER", "ivanov")
    monkeypatch.setenv("ONEC_IB_PASS_ERP_TEST", "secret")
    m.main(["check", "--base", SERVER_BASE, "--ext", "ai_debug",
            "--contour", "ERP_TEST", "--workdir", str(tmp_path / "wd")])

    assert isinstance(seen["runner"], LocalRunner)        # без --host — эта машина
    assert seen["ib"] == SERVER_BASE
    assert seen["ext"] == "ai_debug"
    assert "check_config.log" in seen["log"]


def test_check_with_host_uses_ssh_runner(monkeypatch, tmp_path):
    """С --host берётся SSH-раннер. Сам ssh при этом НЕ вызывается: подготовка каталога
    на сервере замокана — тест не должен зависеть ни от ssh, ни от сети."""
    m = _load(monkeypatch)
    seen = {}

    import onec_metadata.apply.external as ext
    from onec_metadata.apply.runner import Runner

    monkeypatch.setattr(Runner, "makedirs", lambda self, path, timeout=60: None)
    monkeypatch.setattr(ext, "check_config",
                        lambda r, *args, **kw: seen.update(runner=r))
    m.main(["check", "--base", SERVER_BASE, "--host", "onec-srv",
            "--workdir", str(tmp_path / "wd")])
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
                        lambda *a, **kw: (order.append("deploy"), "epf-stub")[1])
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
