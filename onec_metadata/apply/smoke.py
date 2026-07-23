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
import sys
import tempfile
from pathlib import Path

from onec_metadata.apply.dumpload import ApplyError
from onec_metadata.apply.external import build_external
from onec_metadata.apply.protection import preflight_note
from onec_metadata.apply.runner import RunnerLike, enterprise_cmd, mask_password

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates" / "smoke_runner"

_OK = "__SMOKE_OK__"
_FAIL = "__SMOKE_FAIL__"

# Самая частая причина «зависшего» батч-прогона: раннер пишет файл, платформа спрашивает
# подтверждение («Защита от опасных действий»), модальное окно всплывает на экране
# ПОЛЬЗОВАТЕЛЯ и ждёт вечно. Скрипт окна не видит — снаружи это неотличимо от зависания,
# поэтому подсказку даём прямо в тексте ошибки, а не оставляем гадать.
_HANG_HINT = (
    "Вероятная причина: платформа ждёт ответа в модальном окне «Защита от опасных "
    "действий» (раннер обращается к файлам). Ключа командной строки для её отключения у "
    "платформы нет. Снимите флаг «Защита от опасных действий» у пользователя ИБ ЛИБО "
    "добавьте маску базы в DisableUnsafeActionProtection в <каталог 1С>/conf/conf.cfg. "
    "Пошагово — docs/setup-actions-required.md §1.\n"
    "Как отличить от проблем со связью: прогнать на той же базе `onec_verify.py check` — "
    "если DESIGNER отвечает за секунды, сеть, кластер и учётка исправны, и дело в старте "
    "СЕАНСА, а не в доступе. Признаки зависшего сеанса, замеренные в бою: файл `/Out` не "
    "переписывается вовсе, кэш конфигурации не растёт, процесс держит около нуля CPU. "
    "Тогда единственный надёжный способ увидеть причину — запустить команду при человеке "
    "и посмотреть, не появилось ли окно на экране.")


# перенос файлов идёт через r.put() (SSH→scp, локально→копирование) — работает
# и удалённым Runner, и LocalRunner (платформа на этой машине)


def deploy_smoke_runner(r: RunnerLike, ib: str, user: str, pwd: str, *,
                        workdir: str) -> str:
    """Шаблон → сервер → сборка .epf платформой. Возврат: путь к .epf."""
    workdir_fs = workdir.replace("\\", "/")
    r.makedirs(workdir)
    for p in sorted(TEMPLATE_DIR.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(TEMPLATE_DIR)
        remote_dir = "/".join([workdir_fs, *rel.parts[:-1]])
        if rel.parts[:-1]:
            r.makedirs(remote_dir)
        r.put(p, f"{remote_dir}/{rel.name}")
    root_xml = f"{workdir}\\СмоукРаннер.xml"
    out_epf = f"{workdir}\\СмоукРаннер.epf"
    # лог сборки — В workdir, а не в дефолтный ONEC_REMOTE_WORKDIR (D:\src):
    # на машине разработчика такого каталога обычно нет, и диагностика терялась
    build_external(r, ib, user, pwd, root_xml=root_xml, out_epf=out_epf,
                   log=f"{workdir}\\external_build.log")
    return out_epf


def run_smoke(r: RunnerLike, ib: str, user: str, pwd: str, *,
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
        r.put(local, scenario.replace("\\", "/"))
    finally:
        local.unlink(missing_ok=True)

    # предполёт по КОНКРЕТНОЙ базе: локально, без подключения (баз в контуре бывают
    # десятки — опрашивать их все сеансами дороже проблемы, которую это ловит)
    note = preflight_note(ib)
    if note:
        print(note, file=sys.stderr)

    r.remove(result)
    cmd = enterprise_cmd(ib, user, pwd, epf, f"{scenario};{result}", log)
    try:
        r.run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ApplyError(f"smoke: сеанс не завершился за {timeout} с.\n{_HANG_HINT}\n"
                         f"cmd: {mask_password(cmd)}") from None
    content = r.read_text(result, timeout=60)

    if _OK in content:
        return content.split(_OK, 1)[1].strip()
    if _FAIL in content:
        raise ApplyError("smoke: файл-результат содержит FAIL: {}\ncmd: {}".format(
            content.strip()[:2000], mask_password(cmd)))
    raise ApplyError(
        "smoke: файл-результат отсутствует/пуст — сценарий до записи итога не дошёл.\n"
        f"{_HANG_HINT}\nлог сеанса: {r.read_text(log, timeout=60).strip()[:1000]}\n"
        f"cmd: {mask_password(cmd)}")
