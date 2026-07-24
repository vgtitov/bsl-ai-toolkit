"""Автономный сервер 1С (`ibsrv`) — HTTP-доступ к ФАЙЛОВОЙ базе без веб-сервера.

Зачем это здесь. Слой данных (`docs/data-access-architecture.md`, L2–L4) ходит по HTTP:
OData и HTTP-сервисы. Раньше это упиралось в публикацию на Apache/IIS — действие
администратора, которого разработчик ждёт днями. Автономный сервер снимает зависимость
для файловых баз целиком: он сам поднимает веб-клиент, стандартный интерфейс OData и
HTTP-сервисы, ставить и настраивать веб-сервер не нужно.

Практический порядок: сперва проверяем всё на файловой копии через автономный сервер,
и только потом просим публиковать боевую/тестовую базу на веб-сервере.

Проверено на 8.3.27.2214 (ERP WE 2.5.14.82, файловая копия):
- `/` отдаёт веб-клиент, `/odata/standard.odata/` — стандартный интерфейс OData,
  `/hs/<RootURL>/...` — HTTP-сервисы, в том числе из расширений;
- ОДНА база на один экземпляр сервера (ограничение платформы) — отсюда порт на базу;
- ⚠ путь с кириллицей НЕЛЬЗЯ передать аргументом командной строки: платформа обрезает
  строку на первом не-ASCII символе. Поэтому конфигурация всегда пишется файлом в UTF-8;
- ⚠ в YAML путь обязателен в кавычках и с прямыми слэшами: `C:\\dev\\tvg` разбирается как
  escape-последовательность и `\\t` превращается в табуляцию (боевая ошибка 23.07).
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_PORT = 8314


def ibsrv_bin(bin_path: str | None = None) -> str:
    """Путь к `ibsrv`. Рядом с `1cv8` в каталоге bin платформы."""
    if bin_path:
        return bin_path
    env = os.environ.get("ONEC_IBSRV_BIN")
    if env:
        return env
    v8 = os.environ.get("ONEC_1CV8_BIN")
    if not v8:
        raise RuntimeError(
            "не найден ibsrv: задай ONEC_IBSRV_BIN или ONEC_1CV8_BIN (ibsrv лежит рядом с 1cv8)")
    exe = "ibsrv.exe" if os.name == "nt" else "ibsrv"
    return str(Path(v8).resolve().parent / exe)


def build_config(base_dir: str, *, port: int = DEFAULT_PORT, name: str = "ib",
                 address: str = "localhost", http_base: str = "/",
                 odata: bool = True, http_services: bool = True,
                 web_services: bool = False, schedule_jobs: bool = False,
                 services: list | None = None) -> str:
    """YAML-конфигурация автономного сервера.

    Путь к базе — в ОДИНАРНЫХ кавычках и с прямыми слэшами: разбор YAML в платформе
    обрабатывает escape-последовательности, и `\\t` в `C:\\tmp` стал бы табуляцией.

    `services` — HTTP-сервисы РАСШИРЕНИЙ, которые надо опубликовать поимённо:
    `[{"name": "aidbg_API", "root": "aidbg"}]`. Сами по себе они не публикуются ни на
    автономном сервере, ни на веб-сервере, и обращение к неопубликованному сервису
    отдаёт 503 (проверено на живой базе; типовые сервисы конфигурации при этом отвечают
    404/400, то есть слой сервисов исправен и дело именно в публикации).
    Имена свойств повторяют атрибуты дескриптора публикации `default.vrd`.
    """
    path = str(base_dir).replace("\\", "/").replace("'", "''")
    tf = lambda flag: "true" if flag else "false"
    cfg = (
        "server:\n"
        f"  address: {address}\n"
        f"  port: {port}\n"
        "database:\n"
        f"  path: '{path}'\n"
        "infobase:\n"
        f"  name: {name}\n"
        "  distribute-licenses: yes\n"
        f"  schedule-jobs: {'allow' if schedule_jobs else 'deny'}\n"
        # Раздел публикации ОДИН и называется `http` — это список точек публикации.
        # Двух разделов быть не должно: второй перекрывает первый, и на живой базе при
        # добавлении сервиса расширения отваливался OData.
        # `odata` — ОБЪЕКТ с `publish`, а не флаг: форма `odata: yes` молча не работает
        # и отдаёт 404. Имена полей подтверждены схемой платформы и живым прогоном.
        "http:\n"
        f"  - base: {http_base}\n"
        "    odata:\n"
        f"      publish: {tf(odata)}\n"
        "    http-services:\n"
        f"      publish-by-default: {tf(http_services)}\n"
        f"      publish-extensions-by-default: {tf(http_services)}\n")
    if services:
        cfg += "      service:\n"
        for s in services:
            cfg += (f"        - name: {s['name']}\n"
                    f"          root: {s.get('root', s['name'])}\n"
                    "          publish: true\n")
    cfg += ("    web-services:\n"
            f"      publish-by-default: {tf(web_services)}\n")
    return cfg


def write_config(path: Path, text: str) -> Path:
    """Конфигурация ВСЕГДА пишется в UTF-8 — иначе кириллица в пути к базе теряется."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def port_is_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def start(config: Path, *, data_dir: str | Path, bin_path: str | None = None,
          port: int = DEFAULT_PORT, wait: int = 240) -> subprocess.Popen:
    """Запустить автономный сервер отдельным процессом и дождаться готовности порта.

    Держится в фоне: агент запускает сервер, работает с базой по HTTP и гасит его.
    """
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    cmd = [ibsrv_bin(bin_path), f"--config={config}", f"--data={data_dir}"]
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, **kwargs)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = (proc.stdout.read() if proc.stdout else "") or ""
            raise RuntimeError(f"ibsrv завершился при старте: {out.strip()[:800]}")
        if port_is_open(port):
            return proc
        time.sleep(2)
    proc.terminate()
    raise TimeoutError(f"автономный сервер не открыл порт {port} за {wait} с")


def stop(proc: subprocess.Popen | None = None, *, port: int | None = None) -> None:
    """Погасить сервер. Без процесса (например, запускали не мы) — по имени процесса."""
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/IM", "ibsrv.exe", "/F"],
                       capture_output=True, text=True)
    else:
        subprocess.run(["pkill", "-f", "ibsrv"], capture_output=True, text=True)


def base_url(port: int = DEFAULT_PORT, host: str = "localhost", http_base: str = "/") -> str:
    return f"http://{host}:{port}{http_base}".rstrip("/")
