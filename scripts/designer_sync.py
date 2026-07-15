# /// script
# dependencies = []
# ///
"""Точечный цикл «Claude правит код разработчику, работающему в Конфигураторе»:
выгрузить из базы ТОЛЬКО нужные объекты → AI правит .bsl (+BSL LS) → загрузить обратно
изменённые файлы. Полная выгрузка базы не нужна. Проверено на 8.3.27 (см. план
SNG_REPOS_ACTUALIZATION_PLAN.md в operkontur-work): listFile принимает ИМЕНА ОБЪЕКТОВ
в дот-нотации («ОбщийМодуль.Имя»), -files — ПУТИ файлов выгрузки через запятую.

Гейты процесса (обеспечивает платформа + дисциплина):
- в базе, подключённой к хранилищу, полная загрузка запрещена платформой — только -files;
- незахваченный в хранилище объект платформа НЕ даст загрузить (гейт срабатывает сам);
- правим только модули (.bsl); метаданные/формы — человек в Конфигураторе.

Учётки — env (логин един, пароль по базе, фолбэк без суффикса):
  ONEC_IB_USER / ONEC_IB_PASS[_<КЛЮЧ>]          — пользователь ИБ (/N /P); КЛЮЧ = имя базы UPPER
  ONEC_V8_BIN                                    — путь к 1cv8(.exe); иначе авто-поиск
.env подхватывается из CWD и корня репо (не перетирая окружение).

Команды:
  dump --base <каталог|сервер\\база> --objects "ОбщийМодуль.X,Справочник.Y"
       [--ext Имя] [--out DIR] [--contour <КЛЮЧ>]  — точечная выгрузка (без --objects — полная)
  load --base ... [--files "a,b" | --changed] [--ext Имя] [--out DIR] [--contour <КЛЮЧ>]
       — точечная загрузка (--changed = изменённые по git-статусу каталога выгрузки)
  status --out DIR                                — что изменено в каталоге выгрузки

База: существующий каталог = файловая (/F), иначе «сервер\\база» (/S).
Интерактивный Конфигуратор на той же базе должен быть закрыт. Кроссплатформенно (stdlib).
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Windows-консоль по умолчанию cp1251 — печать '→'/'—'/кириллицы роняет скрипт. Принудительно UTF-8.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))                      # detect_tools рядом
sys.path.insert(0, str(SCRIPTS_DIR.parent / "mcp"))       # load_dotenv_defaults


def resolve_cred(kind: str, keys, env=None):
    """(user, pass) для kind IB|STORAGE: логин един (ONEC_<kind>_USER); пароль — первый
    заданный ONEC_<kind>_PASS_<КЛЮЧ> по списку ключей (имя хранилища, контур, ...),
    фолбэк ONEC_<kind>_PASS. Пароли бывают разные и по контурам, и по хранилищам."""
    env = os.environ if env is None else env
    user = env.get(f"ONEC_{kind}_USER") or None
    password = None
    for key in ([keys] if isinstance(keys, str) else (keys or [])):
        password = env.get(f"ONEC_{kind}_PASS_{key.upper()}") or None
        if password:
            break
    password = password or env.get(f"ONEC_{kind}_PASS") or None
    return user, password


def base_args(base: str):
    """Существующий каталог → файловая ИБ (/F), иначе строка «сервер\\база» (/S)."""
    return ["/F", base] if Path(base).is_dir() else ["/S", base]


def _designer(v8, base, user, password, log):
    cmd = [v8, "DESIGNER", *base_args(base)]
    if user:
        cmd += ["/N", user]
        if password:
            cmd += ["/P", password]
    tail = ["/Out", log, "/DisableStartupDialogs", "/DisableStartupMessages"]
    return cmd, tail


def build_dump_cmd(v8, base, out_dir, objects, user, password, extension, log):
    """Команда точечной (objects → listFile, дот-нотация) или полной выгрузки."""
    cmd, tail = _designer(v8, base, user, password, log)
    cmd += ["/DumpConfigToFiles", out_dir]
    listfile = None
    if objects:
        fd, listfile = tempfile.mkstemp(suffix=".txt", prefix="ds_objects_")
        with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
            f.write("\n".join(objects))
        cmd += ["-listFile", listfile]
    if extension:
        cmd += ["-Extension", extension]
    return cmd + tail, listfile


def build_load_cmd(v8, base, src_dir, files, user, password, extension, log):
    """Команда точечной загрузки: -files = пути от каталога выгрузки, / как разделитель."""
    cmd, tail = _designer(v8, base, user, password, log)
    cmd += ["/LoadConfigFromFiles", src_dir]
    if files:
        cmd += ["-files", ",".join(p.replace("\\", "/") for p in files)]
    if extension:
        cmd += ["-Extension", extension]
    return cmd + tail


def changed_files(dump_dir: str):
    """Изменённые/новые файлы каталога выгрузки по git-статусу (пути с /).
    Каталог не под git → None (вызывающий требует явный --files)."""
    if not (Path(dump_dir) / ".git").is_dir():
        return None
    r = subprocess.run(["git", "-C", dump_dir, "status", "--porcelain"],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        return None
    out = []
    for line in r.stdout.splitlines():
        path = line[3:].strip().strip('"')
        if path:
            out.append(path.replace("\\", "/"))
    return sorted(out)


def _find_v8():
    v8 = os.environ.get("ONEC_V8_BIN")
    if v8:
        return v8
    import detect_tools
    root = detect_tools.find_platform()
    if not root:
        raise SystemExit("Платформа 1С не найдена: задай ONEC_V8_BIN или установи 1cv8")
    return str(root / "bin" / ("1cv8.exe" if os.name == "nt" else "1cv8"))


def _run(cmd, log):
    r = subprocess.run(cmd)
    text = ""
    if Path(log).is_file():
        text = Path(log).read_text(encoding="utf-8-sig", errors="replace").strip()
    if r.returncode != 0:
        hint = text or "лог пуст — типично при неверной учётке ИБ (/N /P) или занятой базе"
        raise SystemExit(f"Конфигуратор exit={r.returncode}: {hint}")
    if text:
        print(text)
    return r.returncode


def main(argv=None):
    try:
        from onec_ops_mcp import load_dotenv_defaults
        load_dotenv_defaults()
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Точечный dump/load объектов 1С пакетным Конфигуратором")
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base", required=True, help="каталог файловой ИБ или сервер\\база")
    common.add_argument("--out", default="designer_src", help="каталог выгрузки (по умолчанию ./designer_src)")
    common.add_argument("--ext", help="имя расширения (иначе основная конфигурация)")
    common.add_argument("--contour", help="ключ пароля ONEC_IB_PASS_<КЛЮЧ>; КЛЮЧ = имя базы UPPER (см. bases.*.toml), напр. SLEEPMARKETRB_ERP_TEST")
    p = sub.add_parser("dump", parents=[common], help="точечная выгрузка объектов")
    p.add_argument("--objects", help="объекты через запятую в дот-нотации (пусто = полная выгрузка)")
    p = sub.add_parser("load", parents=[common], help="точечная загрузка файлов")
    p.add_argument("--files", help="пути файлов от каталога выгрузки, через запятую")
    p.add_argument("--changed", action="store_true", help="взять изменённые по git-статусу каталога выгрузки")
    p = sub.add_parser("status", help="изменённые файлы каталога выгрузки")
    p.add_argument("--out", default="designer_src")
    a = ap.parse_args(argv)

    if a.cmd == "status":
        got = changed_files(a.out)
        print("\n".join(got) if got else ("(каталог не под git)" if got is None else "(нет изменений)"))
        return

    v8 = _find_v8()
    user, password = resolve_cred("IB", a.contour)
    log = str(Path(a.out).resolve().with_suffix(".log")) if a.out else "designer_sync.log"
    Path(a.out).mkdir(parents=True, exist_ok=True)

    if a.cmd == "dump":
        objects = [o.strip() for o in (a.objects or "").split(",") if o.strip()] or None
        cmd, listfile = build_dump_cmd(v8, a.base, str(Path(a.out).resolve()), objects,
                                       user, password, a.ext, log)
        try:
            _run(cmd, log)
        finally:
            if listfile:
                os.unlink(listfile)
        print(f"[ok] выгружено в {a.out}" + (f" (объекты: {len(objects)})" if objects else " (полная)"))
    elif a.cmd == "load":
        files = [f.strip() for f in (a.files or "").split(",") if f.strip()]
        if a.changed:
            got = changed_files(a.out)
            if got is None:
                raise SystemExit("--changed требует git в каталоге выгрузки (git init + commit после dump)")
            files = got
        if not files:
            raise SystemExit("Нечего грузить: укажи --files или --changed (полная загрузка в базу с хранилищем запрещена платформой)")
        cmd = build_load_cmd(v8, a.base, str(Path(a.out).resolve()), files, user, password, a.ext, log)
        _run(cmd, log)
        print(f"[ok] загружено файлов: {len(files)} — дальше в Конфигураторе: синтакс-контроль, тест, помещение в хранилище")


if __name__ == "__main__":
    main()
