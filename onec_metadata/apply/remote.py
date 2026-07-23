"""Готовность удалённого хоста к пакетным прогонам 1С.

Прогон с машины, далёкой от кластера, платит задержкой канала за каждое из десятков тысяч
обращений клиента к серверу, поэтому раннер гоняют на хосте рядом с кластером (`--host`).
Но «SSH поднят» и «прогоны пойдут» — разные вещи. Этот набор проверок ловит типовые
грабли разом и ДО первого прогона, вместо того чтобы разбирать их по одной.

Все проверки только читают. Учётку информационной базы они не трогают вовсе, поэтому
пароль в командах не появляется даже теоретически.
"""
from __future__ import annotations

OK, WARN, BAD = "OK", "WARN", "BAD"


def check_host(r, *, bin_path: str, workdir: str) -> list[tuple[str, str, str]]:
    """Список (статус, что проверяли, подробности). Первая же мёртвая связь обрывает
    проверку: остальные пункты без неё бессмысленны и только зашумят вывод."""
    if not bin_path:
        raise ValueError("нужен путь к 1cv8 на удалённом хосте (bin_path)")

    code, out = r.run("cmd /c echo ready", timeout=60)
    if "ready" not in out:
        return [(BAD, "связь с хостом",
                 f"{getattr(r, 'host', '?')} не отвечает на простую команду. Проверь ssh-алиас, "
                 "службу sshd на хосте и вход по ключу")]

    res = [(OK, "связь с хостом", f"{getattr(r, 'host', '?')} отвечает")]

    # Оболочка по умолчанию. PowerShell не раскрывает %COMSPEC%, и тогда команды раннера,
    # написанные в синтаксисе cmd, молча ломаются.
    _, shell_out = r.run("cmd /c echo %COMSPEC%", timeout=60)
    if "cmd.exe" in shell_out.lower():
        res.append((OK, "оболочка по умолчанию", "cmd.exe"))
    else:
        res.append((BAD, "оболочка по умолчанию",
                    "похоже, оболочка по умолчанию НЕ cmd.exe (ответ: "
                    f"{shell_out.strip()[:60]!r}). Раннер строит команды в синтаксисе cmd — "
                    "верни DefaultShell в cmd.exe в HKLM:\\SOFTWARE\\OpenSSH"))

    _, bin_out = r.run(f'cmd /c if exist "{bin_path}" echo FOUND', timeout=60)
    if "FOUND" in bin_out:
        res.append((OK, "платформа 1С", bin_path))
    else:
        res.append((BAD, "платформа 1С",
                    f"не найден {bin_path}. Задай ONEC_1CV8_BIN под путь на ЭТОМ хосте"))

    probe = f"{workdir}\\_probe.txt"
    _, wd_out = r.run(
        f'cmd /c (if not exist {workdir} mkdir {workdir}) & echo probe-ok> {probe}'
        f' & type {probe} & del {probe}', timeout=60)
    if "probe-ok" in wd_out:
        res.append((OK, "рабочий каталог", f"{workdir} доступен на запись"))
    else:
        res.append((BAD, "рабочий каталог",
                    f"не удалось записать в {workdir}. Задай ONEC_REMOTE_WORKDIR под "
                    "каталог, куда учётке можно писать"))

    _, tar_out = r.run("cmd /c where tar", timeout=60)
    if "tar" in tar_out.lower():
        res.append((OK, "tar", tar_out.strip().splitlines()[0] if tar_out.strip() else "есть"))
    else:
        res.append((WARN, "tar",
                    "не найден. Смоук-прогоны работают и без него, но перенос деревьев "
                    "исходников (upload_tree/fetch_tree) — нет"))
    return res


def format_report(results: list[tuple[str, str, str]]) -> str:
    mark = {OK: "[ OK ]", WARN: "[ !  ]", BAD: "[FAIL]"}
    lines = [f"{mark[s]} {name}: {detail}" for s, name, detail in results]
    bad = sum(1 for s, _, _ in results if s == BAD)
    warn = sum(1 for s, _, _ in results if s == WARN)
    lines.append(f"--- итог: {len(results) - bad - warn} ok, {warn} предупреждений, "
                 f"{bad} проблем ---")
    return "\n".join(lines)
