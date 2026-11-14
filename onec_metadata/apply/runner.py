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
        # encoding+errors ОБЯЗАТЕЛЬНЫ: text=True без encoding декодирует вывод ssh
        # локальной кодировкой Windows (cp1251), а файл-результат/лог платформа отдаёт
        # в UTF-8 (read_text тянет его через `chcp 65001 & type`). На кириллице это
        # роняло `UnicodeDecodeError: 'charmap' codec` уже ПОСЛЕ успешного прогона.
        # errors="replace" — служебные команды (whoami/ver) редко, но бывают не-UTF-8.
        with _masked(cmd, timeout):
            p = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", self.host, cmd],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout)
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


def ps_encoded(script: str) -> str:
    """Сценарий PowerShell одной командой без кавычек: -EncodedCommand (UTF-16LE base64).
    Ни cmd.exe на том конце ssh, ни CreateProcess локально ничего в нём не разберут —
    кириллица в путях, `|`, `&` и кавычки доезжают как есть."""
    import base64
    return ("powershell -NoProfile -NonInteractive -EncodedCommand "
            + base64.b64encode(script.encode("utf-16-le")).decode("ascii"))


def mask_password(cmd: str) -> str:
    """Скрыть пароли ИБ и хранилища во всех формах записи: `/Pxxx`, `/P"x y"`, `/P xxx`,
    `/ConfigurationRepositoryP xxx`. Пароль отдельным аргументом уже уходил в чат."""
    cmd = re.sub(r'(/ConfigurationRepositoryP)\s*("[^"]*"|\S+)', r"\1 ***", cmd)
    cmd = re.sub(r'/P\s*"[^"]*"', "/P***", cmd)
    return re.sub(r"/P(?!\*\*\*)\s*\S+", "/P***", cmd)


# Символы, которые ломают строку для cmd.exe или кавычки CreateProcess: значение с ними
# рвёт команду, а маска скрывает пароль не целиком (ab"cd ef → в логе остаётся хвост).
_CMD_UNSAFE = set('"&|<>^%\r\n\t')


def check_cmd_safe(value, what: str = "значение") -> None:
    """Отказать на входе, пока Runner собирает команду строкой.
    shortcut: запрет вместо экранирования; снять, когда Runner перейдёт на argv без оболочки."""
    bad = sorted(_CMD_UNSAFE & set(value or ""))
    if bad:
        shown = " ".join("перевод строки" if c in "\r\n" else c for c in dict.fromkeys(bad))
        raise ValueError(f"{what} содержит недопустимые для командной строки символы: {shown}")


def quote_arg(a: str) -> str:
    """Аргумент с пробелом — в кавычки (уже закавыченный не трогаем): командная строка
    склеивается через пробел, и путь с `Program Files` иначе рвётся на два."""
    quoted = len(a) > 1 and a.startswith('"') and a.endswith('"')
    check_cmd_safe(a[1:-1] if quoted else a, f"аргумент {a[:20]!r}")
    return f'"{a}"' if " " in a and not quoted else a


def pass_arg(pwd: str) -> str:
    """`/P<пароль>` одним аргументом: пароль с пробелом или пустой — в кавычках, иначе он
    рвётся на два токена, и маска скрыла бы только первый."""
    check_cmd_safe(pwd, "пароль")
    return f'/P"{pwd}"' if pwd == "" or " " in (pwd or "") else f"/P{pwd}"


def conn_arg(ib: str) -> str:
    """Аргумент строки соединения по виду `ib`:
    - уже готовый флаг (`/S…`/`/F…`/`/IBConnectionString…`) — как есть;
    - `File=…`/путь (диск `X:\\…` или UNC `\\\\…`) — файловая база `/F"путь"`;
    - иначе — клиент-серверная `сервер\\база` → `/S сервер\\база`."""
    s = ib.strip()
    check_cmd_safe(ib[len(ib.rstrip()):] + ib[:len(ib) - len(ib.lstrip())], "база")   # края до strip: \t, \r, \n
    if re.match(r"(?i)/(S|F|IBConnectionString)", s):
        # готовый флаг - ровно один аргумент: /S сервер\база, /F"путь", /IBConnectionString"..."
        if not re.fullmatch(r'(?i)/S ?[^\s"]+|/F[^\s"]+|/F"[^"]+"|/S ?"[^"]+"|/IBConnectionString[^\s"]+|/IBConnectionString"[^"]+"', s):
            raise ValueError("база: готовый флаг должен быть одним аргументом, без дополнительных параметров")
        body = s[s.index('"'):].strip('"') if '"' in s else s
        _check_base(body if '"' in s else s, quoted='"' in s)
        return s
    m = re.fullmatch(r'(?i)File="?([^"]+?)"?;?', s)
    path = m.group(1) if m else s
    is_path = bool(m) or bool(re.match(r"^[A-Za-z]:[\\/]", s)) or s.startswith("\\\\") or s.startswith("/")
    if '"' in path:
        raise ValueError("база содержит недопустимые для командной строки символы: \"")
    if is_path:
        _check_base(path, quoted=True)               # пробелы в пути ок: он уйдёт в кавычках
        return '/F"%s"' % path
    _check_base(s, quoted=False)
    if re.search(r"\s", s):
        raise ValueError("база: имя сервера и базы без пробелов (иначе дополнительный параметр)")
    return "/S %s" % s


def _check_base(value: str, quoted: bool) -> None:
    """Вне кавычек - полный запрет метасимволов; внутри & ^ | < > литералы, % раскрывается, управляющие нельзя."""
    if quoted:
        for c in "%\r\n\t":
            if c in value:
                raise ValueError("база содержит недопустимые для командной строки символы")
    else:
        check_cmd_safe(value, "база")


def enterprise_cmd(ib: str, user: str, pwd: str, execute_epf: str,
                   c_param: str, log: str, bin: str | None = None) -> str:
    """Запуск 1С:Предприятие с исполнением внешней обработки.

    Обработка получает строку из /C через `ПараметрЗапуска` и обязана сама записать итог
    в файл-результат: клиентский запуск кода возврата не даёт, а Сообщить/ЖР в /Out не
    попадают (см. apply.bsp). `ib` — серверная `сервер\\база` или путь к файловой базе.
    """
    check_cmd_safe(user, "логин")
    check_cmd_safe(c_param, "параметр /C")
    return " ".join([
        f'"{bin or DEFAULT_BIN}" ENTERPRISE',
        conn_arg(ib),
        f'/N"{user}"',
        pass_arg(pwd),
        f'/Execute "{execute_epf}"',
        f'/C"{c_param}"',
        "/DisableStartupDialogs /DisableStartupMessages",
        f"/Out {log}",
    ])


def designer_cmd(ib: str, user: str, pwd: str, action: str,
                 args: list, log: str, bin: str | None = None) -> str:
    check_cmd_safe(user, "логин")
    quoted = " ".join(quote_arg(a) for a in args)
    parts = [
        f'"{bin or DEFAULT_BIN}" DESIGNER',
        conn_arg(ib),
        f'/N"{user}"',
        pass_arg(pwd),
        "/DisableStartupDialogs /DisableStartupMessages",
        action,
    ]
    if quoted:
        parts.append(quoted)
    parts.append(f"/Out {log}")
    return " ".join(parts)
