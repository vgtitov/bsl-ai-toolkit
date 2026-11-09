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
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from onec_metadata.apply.dumpload import ApplyError
from onec_metadata.apply.external import build_external
from onec_metadata.apply.protection import preflight_note
from onec_metadata.apply.runner import RunnerLike, enterprise_cmd, mask_password, ps_encoded

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates" / "smoke_runner"

_OK = "__SMOKE_OK__"
_FAIL = "__SMOKE_FAIL__"

# Самая частая причина «зависшего» батч-прогона: раннер пишет файл, платформа спрашивает
# подтверждение («Защита от опасных действий»), модальное окно всплывает на экране
# ПОЛЬЗОВАТЕЛЯ и ждёт вечно. Скрипт окна не видит — снаружи это неотличимо от зависания,
# поэтому подсказку даём прямо в тексте ошибки, а не оставляем гадать.
_HANG_HINT = (
    "Что проверить по порядку.\n"
    "0) Если перед прогоном в базу грузили правки: некомпилируемый модуль. Сеанс падает на "
    "ошибке компиляции при старте, в батче это модальное окно — снаружи ровный CPU и пустой "
    "/Out. Прогоните `onec_verify.py check` (CheckConfig) на этой базе и поищите в логе "
    "имя правленого объекта.\n"
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


_RETURN_RE = re.compile(r"(?<![\w.])(Возврат|Return)(?!\w)", re.IGNORECASE)


def _code_only(text: str) -> str:
    """Текст BSL без комментариев `//…` и строковых литералов (`""` внутри строки, многострочные
    строки с `|`): чтобы искать операторы, а не слова в строках и комментариях."""
    out, i, in_str, n = [], 0, False, len(text)
    while i < n:
        c = text[i]
        if in_str:
            if c == '"':
                if i + 1 < n and text[i + 1] == '"':
                    i += 2
                    continue
                in_str = False
            elif c == "\n":
                out.append(c)
            i += 1
            continue
        if c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == '"':
            in_str = True
        else:
            out.append(c)
        i += 1
    return "".join(out)

# Сколько секунд сеанс может жить при неизменном /Out (и неизменном локальном кэше), прежде
# чем сторож его остановит. 0 — сторож выключен. Только для локального транспорта: на удалённом
# хосте кэш клиента отсюда не виден, а одного /Out мало — ложно убить рабочий сеанс хуже, чем
# поздно заметить зависание, поэтому удалённо сторож по умолчанию выключен (включается явным stall).
DEFAULT_STALL = 600


def _is_remote(r: RunnerLike) -> bool:
    return bool(getattr(r, "host", ""))


def resolve_stall(r: RunnerLike, stall: int | None) -> int:
    """None — умолчание по транспорту (локально DEFAULT_STALL, удалённо выкл.); < 0 — отказ."""
    if stall is None:
        return 0 if _is_remote(r) else DEFAULT_STALL
    if stall < 0:
        raise ApplyError(f"smoke: stall не может быть отрицательным ({stall}); 0 — сторож выключен")
    return stall


def _kill_by_log(r: RunnerLike, log: str) -> None:
    """Остановить ТОЛЬКО свой сеанс: 1cv8 с этим уникальным путём /Out в командной строке.
    Командная строка не печатается — в ней пароль."""
    needle = log.replace("'", "''")
    try:
        r.run(ps_encoded(
            "Get-CimInstance Win32_Process -Filter \"Name='1cv8.exe' OR Name='1cv8c.exe'\" | "
            f"Where-Object {{ $_.CommandLine -and $_.CommandLine.Contains('{needle}') }} | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"), timeout=60)
    except Exception:
        pass                    # не смогли остановить — об этом скажет текст ошибки


def _run_watched(r: RunnerLike, cmd: str, *, timeout: int, log: str, stall: int,
                 poll: float = 30) -> None:
    """r.run(cmd) со сторожем: сеанс жив, а /Out и клиентский кэш не меняются `stall` секунд —
    это не работа, а модальное окно (ошибка компиляции, защита от опасных действий)."""
    box = {}

    def work():
        try:
            box["res"] = r.run(cmd, timeout=timeout)
        except BaseException as e:                     # noqa: BLE001 — отдаём в основной поток
            box["err"] = e

    t = threading.Thread(target=work, daemon=True)
    t.start()
    last, since = None, time.monotonic()
    while True:
        t.join(poll)
        if not t.is_alive():
            break
        if not stall:
            continue
        # удалённо прогресс — только /Out: локальный кэш к удалённому сеансу отношения не имеет
        sig = (r.read_text(log, timeout=60), None if _is_remote(r) else client_cache_bytes())
        now = time.monotonic()
        if sig != last:
            last, since = sig, now
        elif now - since >= stall:
            _kill_by_log(r, log)
            t.join(30)
            raise ApplyError(
                f"smoke: сеанс жив, но /Out и кэш конфигурации не менялись {stall} с — остановлен "
                "сторожем. Вероятно, модуль не компилируется: запусти `onec_verify.py check` "
                "(CheckConfig) для этой базы. Если сеанс должен идти дольше молча — `--stall 0`.\n"
                f"{_HANG_HINT}\ncmd: {mask_password(cmd)}")
    if "err" in box:
        raise box["err"]


def run_smoke(r: RunnerLike, ib: str, user: str, pwd: str, *,
              scenario_text: str, epf: str, workdir: str,
              timeout: int = 900, stall: int | None = None) -> str:
    """Выполнить серверный BSL-сценарий, вернуть текст отчёта.

    ApplyError, если файл-результат отсутствует или содержит __SMOKE_FAIL__.
    stall — см. resolve_stall (None: локально 600 с, удалённо сторож выключен).
    """
    stall = resolve_stall(r, stall)
    if _RETURN_RE.search(_code_only(scenario_text)):
        raise ApplyError("smoke: в сценарии оператор Возврат. Сценарий исполняется через "
                         "Выполнить() вне процедуры, Возврат там не компилируется и роняет весь "
                         "сценарий — ранний выход делай флагом и Если … КонецЕсли")
    # свои имена на прогон: параллельный запуск в том же workdir иначе подменял сценарий
    run_id = uuid.uuid4().hex[:8]
    scenario = f"{workdir}\\smoke_scenario_{run_id}.bsl"
    result = f"{workdir}\\smoke_result_{run_id}.txt"
    log = f"{workdir}\\smoke_out_{run_id}.log"

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

    cmd = enterprise_cmd(ib, user, pwd, epf, f"{scenario};{result}", log)
    cache_before = client_cache_bytes()
    try:
        _run_watched(r, cmd, timeout=timeout, log=log, stall=stall)
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
    for done in (scenario, result):
        r.remove(done)

    if _OK in content:
        return content.split(_OK, 1)[1].strip()
    if _FAIL in content:
        raise ApplyError("smoke: файл-результат содержит FAIL: {}\ncmd: {}".format(
            content.strip()[:2000], mask_password(cmd)))
    raise ApplyError(
        "smoke: файл-результат отсутствует/пуст — сценарий до записи итога не дошёл.\n"
        f"{_HANG_HINT}\nлог сеанса: {r.read_text(log, timeout=60).strip()[:1000]}\n"
        f"cmd: {mask_password(cmd)}")
