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
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
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
    # пустой пароль ("") — заданный пароль учётки без пароля, отсутствие — None
    common = (env.get(f"ONEC_{kind}_USER") or None, env.get(f"ONEC_{kind}_PASS"))
    for key in ([keys] if isinstance(keys, str) else (keys or [])):
        # пара берётся с ОДНОГО ключа (недостающее — из общего), чтобы не склеить логин KEY1 с паролем KEY2
        user = env.get(f"ONEC_{kind}_USER_{key.upper()}") or None
        password = env.get(f"ONEC_{kind}_PASS_{key.upper()}")
        if user or password is not None:
            return user or common[0], common[1] if password is None else password
    return common


def base_key(base: str):
    """Имя базы из «сервер\\база» — ключ учётки по умолчанию; для файловой базы None."""
    s = (base or "").strip().strip('"')
    if re.match(r"^[A-Za-z]:[\\/]|^\\\\|^/|^(?i:file=)", s) or Path(s).is_dir():
        return None
    return s.rsplit("\\", 1)[-1] if "\\" in s else None


def ib_creds(base: str, contour, env=None):
    """(логин, пароль) ИБ. Без --contour ключ — имя базы (`сервер\\база` → БАЗА): раньше без
    него перебор ключей не шёл вовсе, уходил на общий ONEC_IB_PASS, и платформа отвечала
    «Пользователь ИБ не идентифицирован». Серверной базе без учётки — отказ до запуска."""
    key = contour or base_key(base)
    user, password = resolve_cred("IB", key, env)
    if key and (not user or password is None):
        k = key.upper()
        raise SystemExit(f"нет учётки ИБ для {base}: ONEC_IB_USER[_{k}] и ONEC_IB_PASS[_{k}] в .env "
                         "(ключ — --contour, по умолчанию имя базы)")
    return user, password


def require_remote_bin(env=None):
    """--host: путь к 1cv8 НА СЕРВЕРЕ берётся только из ONEC_1CV8_BIN. Без него раннер брал
    устаревший дефолт, платформа не находилась, а в /Out оставался лог прошлого запуска."""
    env = os.environ if env is None else env
    if not env.get("ONEC_1CV8_BIN"):
        raise SystemExit("--host: задай ONEC_1CV8_BIN — путь к 1cv8.exe НА СЕРВЕРЕ "
                         r"(например C:\Program Files\1cv8\<версия>\bin\1cv8.exe); "
                         "ONEC_V8_BIN — для платформы этой машины")


SAFE_MODE_NOTE = (
    "[!] безопасный режим расширения: при СОЗДАНИИ расширения пакетной загрузкой платформа "
    "ставит ему «Безопасный режим» = Истина, и перехваты &Вместо/&После/&Перед молча не "
    "исполняются (экспортные методы при этом работают, CheckConfig чист). Повторный деплой флаг "
    "не меняет. Проверь у расширения в базе и сними, если нужны перехваты: Конфигуратор → "
    "Конфигурация → Расширения конфигурации → флажок «Безопасный режим»; либо сценарий ступени 2 "
    "(право «Администрирование»): Расширение.БезопасныйРежим = Ложь; Расширение.Записать(); "
    "либо ibcmd extension update --name <Имя> --safe-mode=no (ключ есть в справке ibcmd 8.3.27, "
    "ibcmd ходит в СУБД напрямую — нужны её параметры) [проверить].")


def post_load_notes(paths) -> list:
    """Подсказки после загрузки по составу файлов (без подключения к базе)."""
    notes = []
    norm = [p.replace("\\", "/") for p in paths]
    jobs = sorted({p.split("/")[1].removesuffix(".xml") for p in norm if p.startswith("ScheduledJobs/")})
    if jobs:
        notes.append("[!] регламентные задания в загрузке: " + ", ".join(jobs) + ". НОВОЕ задание "
                     "после выката бывает «Включено», но планировщик его не запускает: проверь "
                     "фактические запуски, при нуле — открой задание, сними и снова поставь "
                     "«Включено», запиши.")
    return notes


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


def check_partial_load(src_dir: Path, files, allow_missing_ext: bool = False) -> None:
    """Отказ ДО запуска, если xml объекта грузится без его Ext — платформа сотрёт модули."""
    from onec_metadata.apply import dumpload
    gaps = dumpload.partial_load_gaps(src_dir, files, allow_missing_ext)
    if gaps:
        raise SystemExit("точечная загрузка сотрёт модули: xml объекта в списке без его Ext/*. "
                         "Добавь в --files (из свежей выгрузки этой же базы):\n  " + "\n  ".join(gaps)
                         + "\nНовый объект без ConfigDumpInfo.xml в каталоге — --allow-missing-ext "
                         "(риск: если объект в базе есть, его модули будут стёрты)")


def merge_partial_dump(src: Path, dst: Path) -> None:
    """Перенести проверенную точечную выгрузку в рабочий каталог: каталог и xml каждого
    выгруженного объекта заменяются целиком, остальное в dst не трогается. ConfigDumpInfo.xml
    не заменяется: в нём меняются только записи выгруженных объектов (dumpload.merge_dump_info)."""
    from onec_metadata.apply import dumpload
    info_dst, info_src = dst / "ConfigDumpInfo.xml", src / "ConfigDumpInfo.xml"
    old_info = info_dst.read_bytes() if info_dst.is_file() else None
    tops = set()
    for p in src.rglob("*"):
        if p.is_file():
            parts = p.relative_to(src).parts
            if len(parts) >= 2 and parts[0] != "Ext":
                tops.add((parts[0], Path(parts[1]).stem if len(parts) == 2 else parts[1]))
    for kind, name in tops:
        shutil.rmtree(dst / kind / name, ignore_errors=True)
        (dst / kind / f"{name}.xml").unlink(missing_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    if old_info is not None:
        bom = b"\xef\xbb\xbf"
        new_raw = info_src.read_bytes() if info_src.is_file() else b""
        if not new_raw:
            info_dst.write_bytes(old_info)
            return
        merged = dumpload.merge_dump_info(
            old_info.removeprefix(bom).decode("utf-8"), new_raw.removeprefix(bom).decode("utf-8"),
            {f"{dumpload.dir_type(kind)}.{name}" for kind, name in tops})
        info_dst.write_bytes((bom if old_info.startswith(bom) else b"") + merged.encode("utf-8"))


def check_partial_dump_forms(dump_dir: Path, before: dict) -> None:
    """После точечной выгрузки: формы без событий/модуля — ошибка; любые формы — предупреждение."""
    from onec_metadata.apply import dumpload
    problems = dumpload.partial_dump_form_problems(dump_dir, before)
    if problems:
        raise SystemExit("выгрузка формы неполная (точечная выгрузка теряет события и модуль формы) — "
                         "делай полную выгрузку конфигурации/расширения:\n  " + "\n  ".join(problems))
    if dumpload.form_snapshot(dump_dir):
        print("[!] формы из точечной выгрузки бывают без <Events> и Form/Module.bsl — выводы о формах "
              "делай только по полной выгрузке")


def _run(cmd, log):
    Path(log).unlink(missing_ok=True)          # лог прошлого запуска не должен сойти за этот
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
    """Перенос каталога выгрузки НА СЕРВЕР без гейтов версии/BSL
    (`onec_metadata.apply.dumpload.upload_tree`): это ad hoc точечный цикл, не публикация
    целого дерева расширения в git.

    zip, а не tar: на хосте с системной локалью en-US `tar -xf` распаковывал только файлы
    с латинскими именами (34 из 1082), а LoadConfigFromFiles потом падал «Файл объекта не
    существует». Python пишет кириллические имена в UTF-8 с флагом 0x800, .NET
    `ZipFile.ExtractToDirectory` его учитывает. После распаковки сверяется число файлов."""
    import zipfile
    from onec_metadata.apply.runner import ps_encoded

    if "'" in remote_dir:
        raise SystemExit(f"путь на сервере с апострофом не поддержан: {remote_dir}")
    local_dir = Path(local_dir)
    files = [p for p in local_dir.rglob("*")
             if p.is_file() and ".git" not in p.relative_to(local_dir).parts]   # git init каталога выгрузки — не груз
    remote_zip = remote_dir + "_upload.zip"
    local_zip = Path(tempfile.gettempdir()) / f"ds_upload_{os.getpid()}.zip"
    try:
        with zipfile.ZipFile(local_zip, "w", zipfile.ZIP_DEFLATED) as z:
            for p in files:
                z.write(p, p.relative_to(local_dir).as_posix())
        subprocess.run(["scp", "-o", "BatchMode=yes", str(local_zip),
                        f"{r.host}:{remote_zip.replace(chr(92), '/')}"], check=True)
    finally:
        local_zip.unlink(missing_ok=True)
    code, out = r.run(ps_encoded(
        f"$d='{remote_dir}'; $z='{remote_zip}'; "
        "if (Test-Path $d) { Remove-Item -Recurse -Force $d }; "
        "Add-Type -AssemblyName System.IO.Compression.FileSystem; "
        "[IO.Compression.ZipFile]::ExtractToDirectory($z, $d); Remove-Item -Force $z; "
        "'COUNT=' + (Get-ChildItem -Recurse -File $d | Measure-Object).Count"), timeout=600)
    m = re.search(r"COUNT=(\d+)", out)
    if not m or int(m.group(1)) != len(files):
        raise SystemExit(f"выгрузка каталога на сервер не удалась: отправлено файлов {len(files)}, "
                         f"на сервере {m.group(1) if m else '?'}: {out.strip()[:500]}")


def remote_dump(r, base, user, password, ext, objects, out_dir, workdir=None):
    """Точечная (или полная) выгрузка ЧЕРЕЗ RUNNER (SSH — платформа рядом с
    кластером, не на Work PC) + возврат результата локально (fetch_tree)."""
    from onec_metadata.apply import dumpload

    workdir = workdir or _point_sync_workdir()
    r.makedirs(workdir)
    remote_dst = workdir + r"\dump"
    log = workdir + r"\dump.log"
    # остатки прошлой выгрузки в том же каталоге иначе приедут вместе с новой и спрячут то,
    # чего точечная выгрузка не отдала (модуль формы)
    if getattr(r, "shell", True):
        r.run(f"if exist {remote_dst} rmdir /s /q {remote_dst}")
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
        # -force = согласие на получение новых/удаление объектов; без него пакетное обновление на них остановится
        action, args = "/ConfigurationRepositoryUpdateCfg", (["-Objects", objects_file] if objects_file else []) \
            + (["-force"] if force else [])
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
    from onec_metadata.apply.runner import check_cmd_safe, quote_arg
    check_cmd_safe(user, "логин хранилища")
    check_cmd_safe(password, "пароль хранилища")
    return " ".join(["/ConfigurationRepositoryF", quote_arg(path),
                     "/ConfigurationRepositoryN", f'"{user}"',
                     "/ConfigurationRepositoryP", f'"{password}"'])


def run_repo(r, base, user, password, repo, op, ext=None, objects=None, comment=None,
             force=False, workdir=None):
    """Захват/отпускание/помещение/обновление из хранилища ЧЕРЕЗ RUNNER. `repo` = (путь, логин, пароль).
    Успех платформы — не вердикт правки: после lock/load сверять повторной выгрузкой."""
    from onec_metadata.apply import dumpload

    workdir = workdir or _point_sync_workdir()
    r.makedirs(workdir)
    objects_file = None
    if op in ("lock", "unlock", "commit") and not objects:
        raise SystemExit(f"repo {op}: нужен --objects (полные имена через запятую)")
    if op == "commit" and not comment:
        raise SystemExit("repo commit: нужен --comment")
    # своё имя файлов на вызов: параллельные прогоны в одном workdir не подменяют список объектов и журнал
    run_id = uuid.uuid4().hex[:8]
    if objects:
        objects_file = workdir + rf"\repo_objects_{run_id}.xml"
        dumpload._put_text(r, objects_file, objects_xml(objects))
    action, args = repo_action(op, ext, objects_file, comment, force)
    try:
        dumpload._run_designer(r, base, user, password, repo_conn(*repo) + " " + action, args,
                               workdir + rf"\repo_{op}_{run_id}.log")
    finally:
        if objects_file:
            r.remove(objects_file)


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
    p.add_argument("--allow-missing-ext", action="store_true",
                   help="без ConfigDumpInfo.xml пустить xml объекта без Ext (новый объект); "
                        "если объект в базе есть — его модули будут стёрты")
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
    p.add_argument("--force", action="store_true", help="unlock: отменить локальные изменения захваченного; update: согласие на новые/удалённые объекты")
    p = sub.add_parser("updatedb", parents=[common], help="UpdateDBCfg (основная или --ext)")
    a = ap.parse_args(argv)

    if a.host:
        require_remote_bin()

    if a.cmd in ("repo", "updatedb"):
        from onec_metadata.apply.runner import make_runner
        user, password = ib_creds(a.base, a.contour)
        if not user or password is None:
            # иначе в команду уйдут /N"None" /PNone и платформа ответит невнятным «не идентифицирован»
            raise SystemExit(f"нет учётки ИБ: ONEC_IB_USER[_{(a.contour or '<КЛЮЧ>').upper()}] и "
                             f"ONEC_IB_PASS[_{(a.contour or '<КЛЮЧ>').upper()}] в .env")
        r = make_runner(a.host)
        if a.cmd == "updatedb":
            update_db(r, a.base, user, password, a.ext)
            print(f"[ok] UpdateDBCfg {a.base}" + (f" -Extension {a.ext}" if a.ext else ""))
            return
        key = a.repo_key or a.contour
        path = (os.environ.get(f"ONEC_REPO_PATH_{key.upper()}") if key else None) \
            or os.environ.get("ONEC_REPO_PATH")
        ruser, rpass = resolve_cred("REPO", key)
        if not (path and ruser) or rpass is None:
            names = f"ONEC_REPO_PATH_/USER_/PASS_{key.upper()}" if key else "ONEC_REPO_PATH/USER/PASS"
            raise SystemExit(f"нет кредов хранилища: {names} в .env (ключ — --repo-key или --contour)")
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
        user, password = ib_creds(a.base, a.contour)
        r = make_runner(a.host)
        remote_deploy_extension(r, a.base, user, password, a.ext, a.src)
        print(f"[ok] расширение {a.ext} задеплоено на {a.base} через {a.host}")
        src = Path(a.src)
        for note in [SAFE_MODE_NOTE] + post_load_notes(
                p.relative_to(src).as_posix() for p in src.rglob("*.xml")):
            print(note)
        return

    v8 = None if a.host else _find_v8()   # --host: платформа НА СЕРВЕРЕ, локальная не нужна
    user, password = ib_creds(a.base, a.contour)
    log = str(Path(a.out).resolve().with_suffix(".log")) if a.out else "designer_sync.log"
    Path(a.out).mkdir(parents=True, exist_ok=True)

    if a.cmd == "dump":
        from onec_metadata.apply import dumpload
        objects = [o.strip() for o in (a.objects or "").split(",") if o.strip()] or None
        forms_before = dumpload.form_snapshot(Path(a.out))
        # точечная — во временный ПУСТОЙ каталог: старые файлы a.out (Form/Module.bsl) иначе
        # маскируют то, чего выгрузка не отдала; в a.out переносим только проверенное
        target = tempfile.mkdtemp(prefix="ds_dump_") if objects else str(Path(a.out).resolve())
        if a.host:
            from onec_metadata.apply.runner import make_runner
            r = make_runner(a.host)
            remote_dump(r, a.base, user, password, a.ext, objects, target)
        else:
            cmd, listfile = build_dump_cmd(v8, a.base, target, objects, user, password, a.ext, log)
            try:
                _run(cmd, log)
            finally:
                if listfile:
                    os.unlink(listfile)
        if objects:
            try:
                check_partial_dump_forms(Path(target), forms_before)
            except SystemExit as e:
                raise SystemExit(f"{e}\n{a.out} не изменён; выгрузка оставлена для разбора: {target}") from None
            merge_partial_dump(Path(target), Path(a.out))
            shutil.rmtree(target, ignore_errors=True)
        print(f"[ok] выгружено в {a.out}" + (f" через {a.host}" if a.host else "")
              + (f" (объекты: {len(objects)})" if objects else " (полная)"))
    elif a.cmd == "load":
        files = [f.strip() for f in (a.files or "").split(",") if f.strip()]
        if a.changed:
            got = changed_files(a.out)
            if got is None:
                raise SystemExit("--changed требует git в каталоге выгрузки (git init + commit после dump)")
            files = got
        if not files:
            raise SystemExit("Нечего грузить: укажи --files или --changed (полная загрузка в базу с хранилищем запрещена платформой)")
        check_partial_load(Path(a.out), files, a.allow_missing_ext)
        if a.host:
            from onec_metadata.apply.runner import make_runner
            r = make_runner(a.host)
            remote_load(r, a.base, user, password, a.ext, files, a.out)
        else:
            cmd = build_load_cmd(v8, a.base, str(Path(a.out).resolve()), files, user, password, a.ext, log)
            _run(cmd, log)
        # код возврата точечной загрузки — не вердикт: бывало «загружено», а в базе старая версия
        print(f"[загружено, не проверено] файлов: {len(files)}" + (f" через {a.host}" if a.host else "")
              + ". Вердикт — повторная выгрузка этих объектов и сверка с каталогом, затем "
              "синтакс-контроль (onec_verify.py check), тест, помещение в хранилище")
        for note in post_load_notes(files):
            print(note)


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:      # отказ check_cmd_safe: понятное сообщение без трейсбека
        raise SystemExit(f"ошибка: {e}")
