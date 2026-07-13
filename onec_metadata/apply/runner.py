"""Запуск команд на сервере 1С по SSH и построение командных строк DESIGNER.

Параметризовано (обезличено): SSH-хост и путь к платформе — из настроек, не
захардкожены под конкретную организацию. См. `config/onec_metadata.example.toml`.

Пароль ИБ попадает в командную строку на сервере (локальная сессия) — это
осознанно; но он НЕ должен попадать в логи/коммиты: логирование команд здесь
всегда маскирует /P-аргумент.
"""
import os
import re
import subprocess

# Путь к 1cv8.exe на сервере. Настраивается через env ONEC_1CV8_BIN или
# аргумент designer_cmd(bin=...). Значение по умолчанию — типовой путь;
# конкретную версию платформы задаёт организация в своих настройках.
DEFAULT_BIN = os.environ.get(
    "ONEC_1CV8_BIN",
    r"C:\Program Files\1cv8\8.3.25.1445\bin\1cv8.exe")


class Runner:
    """SSH-раннер. host — алиас из ~/.ssh/config (обязателен, задаётся
    организацией; в toolkit дефолта под конкретного клиента нет)."""

    def __init__(self, host: str):
        self.host = host

    def run(self, cmd: str, timeout: int = 600):
        p = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", self.host, cmd],
            capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)


def mask_password(cmd: str) -> str:
    return re.sub(r"/P\S+", "/P***", cmd)


def enterprise_cmd(ib: str, user: str, pwd: str, execute_epf: str,
                   c_param: str, log: str, bin: str | None = None) -> str:
    """Запуск 1С:Предприятие с исполнением внешней обработки.

    Обработка получает строку из /C через `ПараметрЗапуска` и обязана сама
    записать итог в файл-результат: клиентский запуск кода возврата не
    даёт, а Сообщить/ЖР в /Out не попадают (см. apply.bsp).
    """
    return " ".join([
        f'"{bin or DEFAULT_BIN}" ENTERPRISE',
        f"/S {ib}",
        f'/N"{user}"',
        f"/P{pwd}",
        f'/Execute "{execute_epf}"',
        f'/C"{c_param}"',
        "/DisableStartupDialogs /DisableStartupMessages",
        f"/Out {log}",
    ])


def designer_cmd(ib: str, user: str, pwd: str, action: str,
                 args: list, log: str, bin: str | None = None) -> str:
    quoted = " ".join(args)
    parts = [
        f'"{bin or DEFAULT_BIN}" DESIGNER',
        f"/S {ib}",
        f'/N"{user}"',
        f"/P{pwd}",
        "/DisableStartupDialogs /DisableStartupMessages",
        action,
    ]
    if quoted:
        parts.append(quoted)
    parts.append(f"/Out {log}")
    return " ".join(parts)
