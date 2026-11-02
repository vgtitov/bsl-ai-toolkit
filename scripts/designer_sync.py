# /// script
# dependencies = []
# ///
"""Точечный цикл «Claude правит код разработчику, работающему в Конфигураторе»:
выгрузить из базы ТОЛЬКО нужные объекты → AI правит .bsl (+BSL LS) → загрузить обратно
изменённые файлы. Полная выгрузка базы не нужна. Проверено на 8.3.27: listFile принимает
ИМЕНА ОБЪЕКТОВ в дот-нотации («ОбщийМодуль.Имя»), -files — ПУТИ файлов выгрузки через запятую.

Гейты процесса (обеспечивает платформа + дисциплина):
- в базе, подключённой к хранилищу, полная загрузка запрещена платформой — только -files;
- незахваченный в хранилище объект платформа НЕ даст загрузить (гейт срабатывает сам);
- этот скрипт правит модули (.bsl); метаданные/формы — человек в IDE или инструмент 1c-metadata (в пределах карты покрытия).

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
  deploy-extension --base ... --host <алиас> --ext Имя --src <каталог исходников>
       — полный деплой расширения (Load+UpdateDBCfg) через SSH; для доступа к данным
         (onec-data/ai_debug) см. docs/data-access-verification-runbook.md
  repo {update|lock|unlock|commit} --base ... [--host] [--contour] [--repo-key КЛЮЧ]
       [--objects "ОбщийМодуль.X"] [--comment "TICKET-1"] [--force] [--ext Имя]
       — хранилище; креды ONEC_REPO_PATH_/USER_/PASS_<КЛЮЧ>. lock/commit — только с явного
         разрешения разработчика (захват блокирует коллег).
  updatedb --base ... [--host] [--contour] [--ext Имя]  — UpdateDBCfg (с разрешения)

Учётка ИБ: логин ONEC_IB_USER_<КЛЮЧ> (фолбэк ONEC_IB_USER). Цикл правки модуля в базе с
хранилищем: repo lock → dump → правка → load → ПОВТОРНЫЙ dump и сверка (load exit=0 бывает
ложным) → updatedb → smoke → repo commit.

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
sys.path.insert(0, str(SCRIPTS_DIR.parent))               # onec_metadata (--host: Runner/dumpload)


def resolve_cred(kind: str, keys, env=None):
    """(user, pass) для kind IB|STORAGE|REPO: пароль — первый заданный ONEC_<kind>_PASS_<КЛЮЧ>
    по списку ключей (имя хранилища, контур, ...), фолбэк ONEC_<kind>_PASS. Логин — так же
    ONEC_<kind>_USER_<КЛЮЧ>, фолбэк общий ONEC_<kind>_USER: в dev-базах логин бывает свой
    (с пробелом, кириллицей), и общий логин давал ложное «Пользователь ИБ не идентифицирован»."""
    env = os.environ if env is None else env
    user = password = None
    for key in ([keys] if isinstance(keys, str) else (keys or [])):
        user = user or env.get(f"ONEC_{kind}_USER_{key.upper()}") or None
        password = password or env.get(f"ONEC_{kind}_PASS_{key.upper()}") or None
    user = user or env.get(f"ONEC_{kind}_USER") or None
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


def _point_sync_workdir() -> str:
    return os.environ.get("ONEC_REMOTE_WORKDIR", r"D:\src") + r"\point_sync"


def _upload_dir_plain(r, local_dir: Path, remote_dir: str) -> None:
    """Перенос каталога выгрузки НА СЕРВЕР — простой tar+scp, без гейтов версии/BSL
    (`onec_metadata.apply.dumpload.upload_tree`): это ad hoc точечный цикл на паре
    файлов, не публикация целого дерева расширения в git."""
    tmp_tar_remote = remote_dir + "_upload.tar"
    local_tar = Path(tempfile.gettempdir()) / f"ds_upload_{os.getpid()}.tar"
    subprocess.run(["tar", "-cf", str(local_tar), "-C", str(local_dir), "."], check=True)
    subprocess.run(["scp", "-o", "BatchMode=yes", str(local_tar),
                    f"{r.host}:{tmp_tar_remote}"], check=True)
    local_tar.unlink()
    code, out = r.run(
        f"chcp 65001 >nul & (if exist {remote_dir} rmdir /s /q {remote_dir}) & "
        f"mkdir {remote_dir} & tar -xf {tmp_tar_remote} -C {remote_dir} & echo EXIT=%errorlevel%")
    if "EXIT=0" not in out:
        raise SystemExit(f"выгрузка каталога на сервер не удалась: {out.strip()[:500]}")


def remote_dump(r, base, user, password, ext, objects, out_dir, workdir=None):
    """Точечная (или полная) выгрузка ЧЕРЕЗ RUNNER (SSH — платформа рядом с
    кластером, не на Work PC) + возврат результата локально (fetch_tree)."""
    from onec_metadata.apply import dumpload

    workdir = workdir or _point_sync_workdir()
    r.makedirs(workdir)
    remote_dst = workdir + r"\dump"
    log = workdir + r"\dump.log"
    dumpload.dump_extension(r, base, user, password, ext, remote_dst, log=log, objects=objects)
    dumpload.fetch_tree(r, remote_dst, Path(out_dir).resolve())


def remote_load(r, base, user, password, ext, files, out_dir, workdir=None):
    """Точечная загрузка ЧЕРЕЗ RUNNER: сперва локальный каталог выгрузки (уже
    правленный) уходит на сервер, потом -files грузится в базу. UpdateDBCfg —
    НЕ вызывается (ad hoc цикл, дальше — Конфигуратор/EDT человека)."""
    from onec_metadata.apply import dumpload

    workdir = workdir or _point_sync_workdir()
    r.makedirs(workdir)
    remote_src = workdir + r"\dump"
    log = workdir + r"\load.log"
    _upload_dir_plain(r, Path(out_dir).resolve(), remote_src)
    dumpload.load_extension(r, base, user, password, ext, remote_src, log=log,
                            files=files, update_dbcfg=False)


def remote_deploy_extension(r, base, user, password, ext, src_dir, workdir=None):
    """Полный деплой расширения ЧЕРЕЗ RUNNER (SSH — платформа рядом с кластером):
    весь src/ уходит на сервер, затем LoadConfigFromFiles + UpdateDBCfg (в отличие
    от remote_load точечного цикла — это полное применение расширения к базе,
    UpdateDBCfg обязателен)."""
    from onec_metadata.apply import dumpload

    workdir = workdir or _point_sync_workdir()
    r.makedirs(workdir)
    remote_src = workdir + r"\ext_src"
    log = workdir + r"\ext_load.log"
    _upload_dir_plain(r, Path(src_dir).resolve(), remote_src)
    dumpload.load_extension(r, base, user, password, ext, remote_src, log=log,
                            update_dbcfg=True)


def objects_xml(objects) -> str:
    """Файл -Objects для захвата/отпускания/помещения: полные имена в дот-нотации."""
    from xml.sax.saxutils import quoteattr
    rows = "".join(f"\t<Object fullName={quoteattr(o)} includeChildObjects=\"false\"/>\n"
                   for o in objects)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Objects xmlns="http://v8.1c.ru/8.3/config/objects" version="1.0">\n'
            f"{rows}</Objects>\n")


def repo_action(op, ext=None, objects_file=None, comment=None, force=False):
    """(действие, аргументы) пакетного Конфигуратора для операции с хранилищем."""
    if op == "update":
        action, args = "/ConfigurationRepositoryUpdateCfg", ["-force"]
    elif op == "lock":
        action, args = "/ConfigurationRepositoryLock", ["-Objects", objects_file, "-revised"]
    elif op == "unlock":
        action, args = "/ConfigurationRepositoryUnLock", ["-Objects", objects_file] + (["-force"] if force else [])
    elif op == "commit":
        action, args = "/ConfigurationRepositoryCommit", ["-Objects", objects_file, "-comment", comment]
    else:
        raise SystemExit(f"неизвестная операция с хранилищем: {op}")
    return action, args + (["-Extension", ext] if ext else [])


def repo_conn(path, user, password) -> str:
    """Параметры соединения с хранилищем — ставятся ПЕРЕД действием, как в рабочих прогонах."""
    from onec_metadata.apply.runner import quote_arg
    return " ".join(["/ConfigurationRepositoryF", quote_arg(path),
                     "/ConfigurationRepositoryN", f'"{user}"',
                     "/ConfigurationRepositoryP", password])


def run_repo(r, base, user, password, repo, op, ext=None, objects=None, comment=None,
             force=False, workdir=None):
    """Захват/отпускание/помещение/обновление из хранилища ЧЕРЕЗ RUNNER. `repo` = (путь, логин, пароль).
    Успех платформы — не вердикт правки: после lock/load сверять повторной выгрузкой."""
    from onec_metadata.apply import dumpload

    workdir = workdir or _point_sync_workdir()
    r.makedirs(workdir)
    objects_file = None
    if op in ("lock", "unlock", "commit"):
        if not objects:
            raise SystemExit(f"repo {op}: нужен --objects (полные имена через запятую)")
        objects_file = workdir + r"\repo_objects.xml"
        dumpload._put_text(r, objects_file, objects_xml(objects))
    if op == "commit" and not comment:
        raise SystemExit("repo commit: нужен --comment")
    action, args = repo_action(op, ext, objects_file, comment, force)
    dumpload._run_designer(r, base, user, password, repo_conn(*repo) + " " + action, args,
                           workdir + rf"\repo_{op}.log")


def update_db(r, base, user, password, ext=None, workdir=None):
    """UpdateDBCfg ЧЕРЕЗ RUNNER. Порядок ключей важен: действие последним перед /Out, без chcp
    (иначе «Пользователь ИБ не идентифицирован» при верных кредах) — так собирает designer_cmd."""
    from onec_metadata.apply import dumpload

    workdir = workdir or _point_sync_workdir()
    r.makedirs(workdir)
    dumpload._run_designer(r, base, user, password, "/UpdateDBCfg",
                           ["-Extension", ext] if ext else [], workdir + r"\updatedb.log",
                           timeout=3600)


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
    common.add_argument("--contour", help="ключ пароля ONEC_IB_PASS_<КЛЮЧ>; КЛЮЧ = имя базы UPPER (см. bases.*.toml), напр. MYBASE_ERP_TEST")
    common.add_argument("--host", help="алиас ssh хоста рядом с кластером (~/.ssh/config); "
                        "без него — платформа этой машины (сеть до сервера напрямую)")
    p = sub.add_parser("dump", parents=[common], help="точечная выгрузка объектов")
    p.add_argument("--objects", help="объекты через запятую в дот-нотации (пусто = полная выгрузка)")
    p = sub.add_parser("load", parents=[common], help="точечная загрузка файлов")
    p.add_argument("--files", help="пути файлов от каталога выгрузки, через запятую")
    p.add_argument("--changed", action="store_true", help="взять изменённые по git-статусу каталога выгрузки")
    p = sub.add_parser("status", help="изменённые файлы каталога выгрузки")
    p.add_argument("--out", default="designer_src")
    p = sub.add_parser("deploy-extension", help="полный деплой расширения из исходников (--host обязателен)")
    p.add_argument("--base", required=True, help="каталог файловой ИБ или сервер\\база")
    p.add_argument("--host", required=True, help="алиас ssh хоста рядом с кластером (~/.ssh/config)")
    p.add_argument("--ext", required=True, help="имя расширения (создаётся, если ещё не существует)")
    p.add_argument("--src", required=True, help="каталог исходников расширения (Configuration.xml внутри)")
    p.add_argument("--contour", help="ключ пароля ONEC_IB_PASS_<КЛЮЧ>")
    p = sub.add_parser("repo", parents=[common], help="хранилище: update | lock | unlock | commit")
    p.add_argument("op", choices=["update", "lock", "unlock", "commit"])
    p.add_argument("--repo-key", help="ключ ONEC_REPO_PATH_/USER_/PASS_<КЛЮЧ> (по умолчанию --contour)")
    p.add_argument("--objects", help="полные имена через запятую: ОбщийМодуль.X,Справочник.Y (lock/unlock/commit)")
    p.add_argument("--comment", help="комментарий помещения (commit)")
    p.add_argument("--force", action="store_true", help="unlock: отменить локальные изменения захваченного")
    p = sub.add_parser("updatedb", parents=[common], help="UpdateDBCfg (основная или --ext)")
    a = ap.parse_args(argv)

    if a.cmd in ("repo", "updatedb"):
        from onec_metadata.apply.runner import make_runner
        user, password = resolve_cred("IB", a.contour)
        r = make_runner(a.host)
        if a.cmd == "updatedb":
            update_db(r, a.base, user, password, a.ext)
            print(f"[ok] UpdateDBCfg {a.base}" + (f" -Extension {a.ext}" if a.ext else ""))
            return
        key = a.repo_key or a.contour
        path = os.environ.get(f"ONEC_REPO_PATH_{(key or '').upper()}") if key else None
        ruser, rpass = resolve_cred("REPO", key)
        if not (path and ruser and rpass):
            raise SystemExit(f"нет кредов хранилища: ONEC_REPO_PATH_/USER_/PASS_<{key}> в .env")
        # shortcut: кириллица в командной строке ssh ломается; комментарий — латиницей/номером тикета.
        # Нужна кириллица — передавать через файл-сценарий на хосте.
        if a.host and a.comment and not a.comment.isascii():
            raise SystemExit("repo commit --host: комментарий только ASCII (номер тикета), кириллица по ssh бьётся")
        objects = [o.strip() for o in (a.objects or "").split(",") if o.strip()] or None
        run_repo(r, a.base, user, password, (path, ruser, rpass), a.op, a.ext, objects,
                 a.comment, a.force)
        print(f"[ok] repo {a.op} {a.base}" + (f" (объекты: {len(objects)})" if objects else ""))
        return

    if a.cmd == "status":
        got = changed_files(a.out)
        print("\n".join(got) if got else ("(каталог не под git)" if got is None else "(нет изменений)"))
        return

    if a.cmd == "deploy-extension":
        from onec_metadata.apply.runner import make_runner
        user, password = resolve_cred("IB", a.contour)
        r = make_runner(a.host)
        remote_deploy_extension(r, a.base, user, password, a.ext, a.src)
        print(f"[ok] расширение {a.ext} задеплоено на {a.base} через {a.host}")
        return

    v8 = None if a.host else _find_v8()   # --host: платформа НА СЕРВЕРЕ, локальная не нужна
    user, password = resolve_cred("IB", a.contour)
    log = str(Path(a.out).resolve().with_suffix(".log")) if a.out else "designer_sync.log"
    Path(a.out).mkdir(parents=True, exist_ok=True)

    if a.cmd == "dump":
        objects = [o.strip() for o in (a.objects or "").split(",") if o.strip()] or None
        if a.host:
            from onec_metadata.apply.runner import make_runner
            r = make_runner(a.host)
            remote_dump(r, a.base, user, password, a.ext, objects, a.out)
            print(f"[ok] выгружено в {a.out} через {a.host}"
                  + (f" (объекты: {len(objects)})" if objects else " (полная)"))
            return
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
        if a.host:
            from onec_metadata.apply.runner import make_runner
            r = make_runner(a.host)
            remote_load(r, a.base, user, password, a.ext, files, a.out)
            print(f"[ok] загружено файлов через {a.host}: {len(files)} — дальше в Конфигураторе: "
                  "синтакс-контроль, тест, помещение в хранилище")
            return
        cmd = build_load_cmd(v8, a.base, str(Path(a.out).resolve()), files, user, password, a.ext, log)
        _run(cmd, log)
        print(f"[ok] загружено файлов: {len(files)} — дальше в Конфигураторе: синтакс-контроль, тест, помещение в хранилище")


if __name__ == "__main__":
    main()
