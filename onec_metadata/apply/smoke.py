"""Смоук-раннер — ступень 2 лестницы тестирования (docs/testing-ladder.md).

Механика: обезличенная внешняя обработка `templates/smoke_runner` (форма с
ПриОткрытии → серверный `Выполнить(<сценарий>)` → файл-результат →
ЗавершитьРаботуСистемы) собирается платформой в .epf и запускается headless:
`1cv8 ENTERPRISE /Execute СмоукРаннер.epf /C"<сценарий.bsl>;<результат.txt>"`.

Контракт сценария (серверный BSL): переменная `Отчет` (строка) — то, что
сценарий хочет показать; при успехе она попадает в файл-результат после
маркера `__SMOKE_OK__`, при исключении пишется `__SMOKE_FAIL__` + подробное
представление ошибки. Вердикт — ТОЛЬКО по файлу-результату: клиентский запуск
кода возврата не даёт, а Сообщить/ЖР в /Out не попадают (боевой урок про
ложную верификацию: «зелёный» без артефакта не засчитывается).

Зачем это командам: дымовая проверка «печатная форма/отчёт реально
формируется на реальных данных» без установки фреймворков в базу клиента —
раннер это .epf (данные, не конфигурация).
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from onec_metadata.apply.dumpload import ApplyError
from onec_metadata.apply.external import build_external
from onec_metadata.apply.runner import Runner, enterprise_cmd, mask_password

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates" / "smoke_runner"

_OK = "__SMOKE_OK__"
_FAIL = "__SMOKE_FAIL__"


def _scp_to(host: str, local: Path, remote: str) -> None:
    p = subprocess.run(["scp", "-o", "BatchMode=yes", str(local),
                        f"{host}:{remote}"])
    if p.returncode != 0:
        raise ApplyError(f"scp {local} -> {host}:{remote} failed")


def deploy_smoke_runner(r: Runner, ib: str, user: str, pwd: str, *,
                        workdir: str) -> str:
    """Шаблон → сервер → сборка .epf платформой. Возврат: путь к .epf."""
    workdir_fs = workdir.replace("\\", "/")
    r.run(f"cmd /c if not exist {workdir} mkdir {workdir}", timeout=60)
    for p in sorted(TEMPLATE_DIR.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(TEMPLATE_DIR)
        remote_dir = "/".join([workdir_fs, *rel.parts[:-1]])
        if rel.parts[:-1]:
            r.run("cmd /c if not exist {} mkdir {}".format(
                remote_dir.replace('/', '\\'), remote_dir.replace('/', '\\')),
                timeout=60)
        _scp_to(r.host, p, f"{remote_dir}/{rel.name}")
    root_xml = f"{workdir}\\СмоукРаннер.xml"
    out_epf = f"{workdir}\\СмоукРаннер.epf"
    build_external(r, ib, user, pwd, root_xml=root_xml, out_epf=out_epf)
    return out_epf


def run_smoke(r: Runner, ib: str, user: str, pwd: str, *,
              scenario_text: str, epf: str, workdir: str,
              timeout: int = 900) -> str:
    """Выполнить серверный BSL-сценарий, вернуть текст отчёта.

    ApplyError, если файл-результат отсутствует или содержит __SMOKE_FAIL__.
    """
    scenario = f"{workdir}\\smoke_scenario.bsl"
    result = f"{workdir}\\smoke_result.txt"
    log = f"{workdir}\\smoke_out.log"

    with tempfile.NamedTemporaryFile("w", encoding="utf-8-sig",
                                     suffix=".bsl", delete=False) as f:
        f.write(scenario_text)
        local = Path(f.name)
    try:
        _scp_to(r.host, local, scenario.replace("\\", "/"))
    finally:
        local.unlink(missing_ok=True)

    r.run(f"cmd /c del {result} 2>nul & echo.>nul", timeout=60)
    cmd = enterprise_cmd(ib, user, pwd, epf, f"{scenario};{result}", log)
    r.run(cmd, timeout=timeout)
    _, content = r.run(f"cmd /c chcp 65001 >nul & type {result}", timeout=60)

    if _OK in content:
        return content.split(_OK, 1)[1].strip()
    raise ApplyError(
        "smoke: файл-результат {}: {}\ncmd: {}".format(
            "содержит FAIL" if _FAIL in content else "отсутствует/пуст",
            content.strip()[:2000], mask_password(cmd)))
