r"""Запуск команд 1С (DESIGNER/ENTERPRISE) на сервере по SSH ИЛИ ЛОКАЛЬНО.

Транспорт и построение командной строки разделены:
- `Runner`      — выполняет команду на удалённом сервере 1С по SSH (host из ~/.ssh/config);
- `LocalRunner` — выполняет ту же команду ЛОКАЛЬНО (у кого платформа 1С стоит на своей
  машине: Windows-ПК разработчика, файловая ИЛИ клиент-серверная база с сетевым доступом);
- `make_runner(host)` — фабрика: пустой/"local" host → LocalRunner, иначе Runner(host).

Командные строки (`designer_cmd`/`enterprise_cmd`) от транспорта НЕ зависят — их можно
гонять любым раннером. Строка соединения выбирается автоматически (`conn_arg`): серверная
`сервер\база` → `/S`, путь к каталогу файловой базы → `/F`.

Параметризовано (обезличено): SSH-хост и путь к платформе — из настроек, не захардкожены
под организацию (см. `config/onec_metadata.example.toml`).

Пароль ИБ попадает в командную строку (локальная сессия ОС) — осознанно; но НЕ должен
попадать в логи/коммиты: логирование команд всегда маскирует /P-аргумент (`mask_password`).
"""
import os
import re
import shlex
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol


@contextmanager
def _masked(cmd: str, timeout: int):
    """Не дать паролю ИБ утечь через исключение subprocess.

    `TimeoutExpired`/`CalledProcessError` кладут в текст ПОЛНУЮ командную строку, а
    в ней `/P<пароль>` — и она попадает в трейсбек, лог CI, вывод агента. Боевой
    случай 23.07: таймаут ENTERPRISE-сеанса напечатал пароль в консоль.
    """
    try:
        yield
    except subprocess.TimeoutExpired as e:
        raise subprocess.TimeoutExpired(mask_password(cmd), timeout) from None
    except subprocess.CalledProcessError as e:
        raise subprocess.CalledProcessError(
            e.returncode, mask_password(cmd)) from None


class RunnerLike(Protocol):
    """Структурный тип раннера: и SSH-`Runner`, и `LocalRunner` (и тестовые двойники).
    Позволяет apply.* не зависеть от способа запуска (по ssh / локально).

    `shell` — есть ли на том конце командная оболочка. У SSH-раннера сеанс открывает
    cmd.exe сервера, поэтому допустимы `&`, `type`, `if errorlevel`. LocalRunner
    исполняет команду через CreateProcess БЕЗ оболочки (сознательно: нет инъекции),
    поэтому оболочечный синтаксис туда передавать нельзя — вызывающий код обязан
    выбирать ветку по `shell`, а файловые операции делать методами ниже.
    """
    host: str
    shell: bool

    def run(self, cmd: str, timeout: int = ...) -> tuple[int, str]: ...
    def put(self, local, remote: str, timeout: int = ...) -> None: ...
    def read_text(self, path: str, timeout: int = ...) -> str: ...
    def makedirs(self, path: str, timeout: int = ...) -> None: ...
    def remove(self, path: str, timeout: int = ...) -> None: ...

# Путь к 1cv8.exe. Настраивается через env ONEC_1CV8_BIN или аргумент bin=... командных
# функций. Дефолт — типовой путь; конкретную версию платформы задаёт организация.
DEFAULT_BIN = os.environ.get(
    "ONEC_1CV8_BIN",
    r"C:\Program Files\1cv8\8.3.25.1445\bin\1cv8.exe")


class Runner:
    """SSH-раннер. host — алиас из ~/.ssh/config (обязателен; в toolkit дефолта под
    конкретного клиента нет). Команда выполняется на удалённом сервере 1С."""

    host = ""
    shell = True                      # на том конце cmd.exe сервера

    def __init__(self, host: str):
        if not host:
            raise ValueError("Runner требует host; для локального запуска — LocalRunner/make_runner()")
        self.host = host

    def run(self, cmd: str, timeout: int = 600):
        with _masked(cmd, timeout):
            p = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", self.host, cmd],
                capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)

    def put(self, local, remote: str, timeout: int = 300):
        """Скопировать локальный файл на удалённый сервер (scp). remote — путь на сервере."""
        p = subprocess.run(["scp", "-o", "BatchMode=yes", str(local),
                            f"{self.host}:{remote}"], capture_output=True, text=True,
                           timeout=timeout)
        if p.returncode != 0:
            raise RuntimeError("scp %s -> %s:%s не удался: %s"
                               % (local, self.host, remote, p.stderr.strip()))

    def read_text(self, path: str, timeout: int = 60) -> str:
        """Прочитать текстовый файл НА СЕРВЕРЕ. chcp 65001 — иначе кириллица в
        логах платформы приходит битой (боевой урок)."""
        _, out = self.run(f"chcp 65001 >nul & type {path}", timeout=timeout)
        return out

    def makedirs(self, path: str, timeout: int = 60) -> None:
        p = path.replace("/", "\\")
        self.run(f"cmd /c if not exist {p} mkdir {p}", timeout=timeout)

    def remove(self, path: str, timeout: int = 60) -> None:
        self.run(f"cmd /c del {path} 2>nul & echo.>nul", timeout=timeout)


class LocalRunner:
    """Локальный раннер — та же команда выполняется на ТЕКУЩЕЙ машине (платформа 1С
    установлена здесь; практически это Windows-ПК). host отсутствует.

    БЕЗ `shell=True` (нет инъекции через оболочку): на Windows строка передаётся как есть —
    её разбирает CreateProcess (не cmd.exe), метасимволы оболочки не действуют; на POSIX
    (для тестов/редких кейсов) — `shlex.split`."""

    host = ""
    shell = False                     # CreateProcess без оболочки: `&`/`type` не сработают

    def run(self, cmd: str, timeout: int = 600):
        args = cmd if os.name == "nt" else shlex.split(cmd)
        with _masked(cmd, timeout):
            p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + (p.stderr or ""))

    def put(self, local, remote: str, timeout: int = 300):
        """Локальное копирование: и источник, и приёмник — на этой же машине (нет scp)."""
        import shutil
        dst = remote.replace("/", os.sep)
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        shutil.copyfile(str(local), dst)

    def read_text(self, path: str, timeout: int = 60) -> str:
        """Прочитать файл напрямую — оболочка не нужна. Отсутствующий файл = пусто
        (то же, что `type` несуществующего на сервере: диагностика в вызывающем коде).
        Логи платформы бывают и UTF-8, и cp1251 — пробуем по очереди."""
        p = Path(path.replace("/", os.sep))
        if not p.is_file():
            return ""
        raw = p.read_bytes()
        for enc in ("utf-8-sig", "cp1251"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    def makedirs(self, path: str, timeout: int = 60) -> None:
        os.makedirs(path.replace("/", os.sep), exist_ok=True)

    def remove(self, path: str, timeout: int = 60) -> None:
        Path(path.replace("/", os.sep)).unlink(missing_ok=True)


def make_runner(host: str | None = None):
    """Фабрика раннера. host пустой / None / 'local' → LocalRunner (платформа на этой
    машине), иначе Runner(host) (SSH). Один код apply.* работает обоими способами."""
    if not host or host.strip().lower() == "local":
        return LocalRunner()
    return Runner(host)


def mask_password(cmd: str) -> str:
    return re.sub(r"/P\S+", "/P***", cmd)


def conn_arg(ib: str) -> str:
    """Аргумент строки соединения по виду `ib`:
    - уже готовый флаг (`/S…`/`/F…`/`/IBConnectionString…`) — как есть;
    - `File=…`/путь (диск `X:\\…` или UNC `\\\\…`) — файловая база `/F"путь"`;
    - иначе — клиент-серверная `сервер\\база` → `/S сервер\\база`."""
    s = ib.strip()
    if s.startswith("/S") or s.startswith("/F") or s.startswith("/IBConnectionString"):
        return s
    if s.startswith("File="):
        return '/F"%s"' % s[len("File="):]
    is_path = bool(re.match(r"^[A-Za-z]:[\\/]", s)) or s.startswith("\\\\") or s.startswith("/")
    if is_path:
        return '/F"%s"' % s
    return "/S %s" % s


def enterprise_cmd(ib: str, user: str, pwd: str, execute_epf: str,
                   c_param: str, log: str, bin: str | None = None) -> str:
    """Запуск 1С:Предприятие с исполнением внешней обработки.

    Обработка получает строку из /C через `ПараметрЗапуска` и обязана сама записать итог
    в файл-результат: клиентский запуск кода возврата не даёт, а Сообщить/ЖР в /Out не
    попадают (см. apply.bsp). `ib` — серверная `сервер\\база` или путь к файловой базе.
    """
    return " ".join([
        f'"{bin or DEFAULT_BIN}" ENTERPRISE',
        conn_arg(ib),
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
        conn_arg(ib),
        f'/N"{user}"',
        f"/P{pwd}",
        "/DisableStartupDialogs /DisableStartupMessages",
        action,
    ]
    if quoted:
        parts.append(quoted)
    parts.append(f"/Out {log}")
    return " ".join(parts)
