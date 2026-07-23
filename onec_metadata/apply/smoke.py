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

import os
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
    "Что проверить по порядку.\n"
    "1) Первый сеанс к УДАЛЁННОЙ клиент-серверной базе строит клиентский кэш "
    "конфигурации и по медленному каналу идёт ЧАСАМИ: платформа тянет его вызовами "
    "open/read примерно по 110 мс каждый, а их десятки тысяч (разбор по ТЖ 23.07). "
    "Кэш накапливается между прогонами, так что это разовая цена на базу и пользователя. "
    "Растущий каталог кэша ниже — признак того, что сеанс работает, а не висит.\n"
    "2) Модальное окно «Защита от опасных действий»: платформа ждёт человека, а скрипт "
    "окна не видит. Ключа командной строки нет — снимите флаг у пользователя ИБ либо "
    "добавьте маску в DisableUnsafeActionProtection (docs/setup-actions-required.md §1).\n"
    "3) Связь и учётка: прогоните `onec_verify.py check` на той же базе. DESIGNER "
    "отвечает за секунды — значит сеть, кластер и учётка исправны, дело в старте сеанса.\n"
    "Радикальное лекарство для удалённых баз — гонять раннер ПРЯМО НА СЕРВЕРЕ через "
    "`--host <алиас ssh>`: тогда обращений по медленному каналу нет вовсе.")

# Клиентский кэш конфигурации: по нему видно, ползёт сеанс или действительно стоит.
_CACHE_DIRS = (
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "1C", "1cv8"),
    os.path.join(os.path.expanduser("~"), ".1cv8"),
)


def client_cache_bytes() -> int:
    """Суммарный размер каталога клиентского кэша 1С. 0 — каталога нет."""
    total = 0
    for root in _CACHE_DIRS:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
    return total


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
    cache_before = client_cache_bytes()
    try:
        r.run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        grew = client_cache_bytes() - cache_before
        progress = ""
        if grew > 0:
            progress = ("\nСеанс НЕ завис: за это время клиентский кэш конфигурации вырос "
                        f"на {grew // (1024 * 1024)} МБ. Он строится и накапливается между "
                        "прогонами — дайте больше времени либо гоняйте раннер на сервере "
                        "через --host.")
        raise ApplyError(f"smoke: сеанс не завершился за {timeout} с.{progress}\n"
                         f"{_HANG_HINT}\ncmd: {mask_password(cmd)}") from None
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
