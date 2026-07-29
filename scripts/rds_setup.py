#!/usr/bin/env python3
"""Настройка SSH-доступа к терминалу 1С для тех, кто с SSH не работал.

Одна команда делает всё, что нужно на СВОЕЙ машине:
  * проверяет, что установлен OpenSSH-клиент (в Windows 10/11 и macOS он есть из коробки);
  * генерит ключ ~/.ssh/onec_rds, если его ещё нет;
  * прописывает алиас в ~/.ssh/config (с правильным кавычением логина);
  * печатает публичный ключ и готовый текст для администратора.

Дальше администратор кладёт ваш ключ на терминал под вашу учётку, и вы проверяете
готовность командой `onec_verify.py remote`.

Кроссплатформенно (Windows/macOS/Linux), только stdlib. Ничего секретного не пишет в git.

    python scripts/rds_setup.py setup            # спросит алиас/адрес/логин интерактивно
    python scripts/rds_setup.py setup --host rds-01 --ip 10.0.0.10 --login "DOMAIN\\user"
    python scripts/rds_setup.py check --host rds-01
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_IDENTITY = "~/.ssh/onec_rds"


# ── чистое ядро (покрыто тестами) ──────────────────────────────────────────

def build_host_block(alias: str, ip: str, login: str, identity: str) -> str:
    """Собрать блок Host для ~/.ssh/config.

    Логин ВСЕГДА в кавычках: в доменных именах вида ``DOMAIN\\vuser`` без
    кавычек ssh трактует ``\\v`` как escape (вертикальная табуляция) и калечит
    логин — реальная грабля, стоившая пары часов отладки.
    """
    return (
        f"Host {alias}\n"
        f"    HostName {ip}\n"
        f'    User "{login}"\n'
        f"    IdentityFile {identity}\n"
        f"    IdentitiesOnly yes\n"
        f"    BatchMode yes"
    )


def _is_host_line(line: str) -> bool:
    s = line.strip().lower()
    return s == "host" or s.startswith("host ") or s.startswith("host\t")


def _host_aliases(line: str) -> list[str]:
    return line.strip().split()[1:]


def upsert_host_block(config_text: str, alias: str, block: str) -> str:
    """Вставить/заменить блок ``Host <alias>`` идемпотентно, не трогая соседей."""
    lines = config_text.splitlines()
    out: list[str] = []
    i, n = 0, len(lines)
    replaced = False
    while i < n:
        line = lines[i]
        if _is_host_line(line) and alias in _host_aliases(line):
            i += 1
            while i < n and not _is_host_line(lines[i]):
                i += 1
            if not replaced:
                out.append(block.rstrip("\n"))
                out.append("")  # отбивка от следующего блока
                replaced = True
            continue
        out.append(line)
        i += 1
    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append(block.rstrip("\n"))
    result = "\n".join(out)
    result = re.sub(r"\n{3,}", "\n\n", result).rstrip("\n") + "\n"
    return result


# ── работа с файловой системой и ssh ───────────────────────────────────────

def _ssh_dir() -> Path:
    d = Path.home() / ".ssh"
    d.mkdir(mode=0o700, exist_ok=True)
    return d


def _resolve(identity: str) -> Path:
    return Path(os.path.expanduser(identity))


def check_client() -> list[str]:
    """Вернуть список отсутствующих бинарников OpenSSH-клиента."""
    return [b for b in ("ssh", "ssh-keygen") if shutil.which(b) is None]


def ensure_key(identity: str, comment: str) -> str:
    """Сгенерить ключ, если его нет. Вернуть текст публичного ключа."""
    key = _resolve(identity)
    pub = key.with_suffix(key.suffix + ".pub") if key.suffix else Path(str(key) + ".pub")
    if not key.exists():
        key.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", str(key), "-N", "", "-C", comment],
            check=True,
        )
    return pub.read_text(encoding="ascii").strip()


def _config_path() -> Path:
    return _ssh_dir() / "config"


def write_config(alias: str, ip: str, login: str, identity: str) -> Path:
    cfg = _config_path()
    existing = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
    block = build_host_block(alias, ip, login, identity)
    cfg.write_text(upsert_host_block(existing, alias, block), encoding="utf-8")
    return cfg


# ── команды CLI ────────────────────────────────────────────────────────────

def _prompt(label: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    val = input(f"{label}{hint}: ").strip()
    return val or default


def admin_handoff_text(alias: str, ip: str, login: str, pub: str) -> str:
    """Готовый текст для отправки администратору. Покрывает обе учётки:
    обычную (ключ в профиль) и админскую (ключ в administrators_authorized_keys)."""
    return (
        f"Прошу дать SSH-доступ на терминал {alias} ({ip}) под моей учёткой {login}.\n"
        "Нужно: (1) моя учётка в группе доступа SSH; (2) мой публичный ключ на сервере.\n"
        "Мой ключ:\n\n"
        f"{pub}\n\n"
        "Куда класть ключ — зависит от прав моей учётки на терминале:\n\n"
        "• ОБЫЧНАЯ учётка — в профиль (PowerShell под моей учёткой из RDP):\n"
        r'    $k = "<ключ выше>"' + "\n"
        r'    New-Item -ItemType Directory -Force "$env:USERPROFILE\.ssh" | Out-Null' + "\n"
        r'    Set-Content "$env:USERPROFILE\.ssh\authorized_keys" $k -Encoding ascii' + "\n"
        r'    icacls "$env:USERPROFILE\.ssh\authorized_keys" /inheritance:r /grant:r "$env:USERNAME:F" "SYSTEM:F"' + "\n\n"
        "• АДМИНСКАЯ учётка (входит в Администраторы сервера) — sshd читает ключ НЕ из\n"
        "  профиля, а из общего файла; класть туда (PowerShell от администратора):\n"
        r'    $k = "<ключ выше>"' + "\n"
        r'    $f = "C:\ProgramData\ssh\administrators_authorized_keys"' + "\n"
        r'    Add-Content $f $k -Encoding ascii' + "\n"
        r'    icacls $f /inheritance:r /grant "Administrators:F" "SYSTEM:F"' + "\n\n"
        "Если не знаете, какая у меня учётка — админская или обычная — подскажите, "
        "проверю сам."
    )


def cmd_setup(args: argparse.Namespace) -> int:
    missing = check_client()
    if missing:
        print("Не найден OpenSSH-клиент: " + ", ".join(missing))
        print("Windows 10/11: Параметры → Приложения → Дополнительные компоненты → 'Клиент OpenSSH'.")
        print("macOS/Linux: ssh обычно уже установлен; иначе поставьте пакет openssh-client.")
        return 2

    alias = args.host or _prompt("Имя терминала (как в реестре баз)", "rds-01")
    ip = args.ip or _prompt("Адрес терминала (IP)")
    login = args.login or _prompt(r"Ваш логин домена (ДОМЕН\пользователь)")
    identity = args.identity
    if not ip or not login:
        print("Нужны адрес и логин. Прервано.")
        return 2

    user = re.split(r"[\\/]", login)[-1] or "user"
    pub = ensure_key(identity, f"onec-toolkit-rds {user}@{os.environ.get('COMPUTERNAME', 'client')}")
    cfg = write_config(alias, ip, login, identity)

    print()
    print(f"Готово. Алиас '{alias}' прописан в {cfg}.")
    print()
    print("─" * 70)
    print("ОТПРАВЬТЕ АДМИНИСТРАТОРУ (например в Teams) следующий текст и ключ:")
    print("─" * 70)
    print(admin_handoff_text(alias, ip, login, pub))
    print("─" * 70)
    print()
    print("Когда админ подтвердит — проверьте связь:")
    print(f"    python scripts/rds_setup.py check --host {alias}")
    print("и готовность для прогонов 1С:")
    print(f'    python scripts/onec_verify.py remote --host {alias} --bin "C:\\Program Files\\1cv8\\<версия>\\bin\\1cv8.exe"')
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    alias = args.host or _prompt("Имя терминала", "rds-01")
    print(f"Проверяю связь с {alias} …")
    try:
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12", alias, "whoami & hostname"],
            capture_output=True, text=True, timeout=40,
        )
    except FileNotFoundError:
        print("ssh не найден в PATH — сначала запустите setup.")
        return 2
    except subprocess.TimeoutExpired:
        print("Таймаут. Хост не ответил за 12 секунд — вероятно закрыт порт 22 или нет маршрута.")
        return 1
    if r.returncode == 0:
        print("OK, подключение работает. На той стороне:")
        print("  " + "\n  ".join(l for l in r.stdout.splitlines() if l.strip()))
        print(f"\nДальше: python scripts/onec_verify.py remote --host {alias} --bin \"...1cv8.exe\"")
        return 0
    err = (r.stderr or "").strip()
    print("Подключиться не удалось. Ответ сервера:")
    print("  " + err.replace("\n", "\n  "))
    print()
    if "not allowed because not in any group" in err or "Permission denied" in err:
        print("Похоже, ключ ещё не положен на сервер или учётка не в группе доступа —")
        print("это делает администратор. Отправьте ему вывод команды setup, если ещё не отправили.")
    elif "Connection refused" in err or "Connection timed out" in err:
        print("Порт 22 закрыт или нет маршрута до хоста — вопрос к администратору сети.")
    return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Настройка SSH-доступа к терминалу 1С (для не-SSH-пользователей).")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="настроить ключ и ~/.ssh/config, напечатать текст для админа")
    s.add_argument("--host", help="алиас хоста (короткое имя)")
    s.add_argument("--ip", help="адрес терминала")
    s.add_argument("--login", help=r"логин домена, напр. DOMAIN\user")
    s.add_argument("--identity", default=DEFAULT_IDENTITY, help=f"файл ключа (по умолчанию {DEFAULT_IDENTITY})")
    s.set_defaults(func=cmd_setup)

    c = sub.add_parser("check", help="проверить, что подключение по SSH работает")
    c.add_argument("--host", help="алиас хоста")
    c.set_defaults(func=cmd_check)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
