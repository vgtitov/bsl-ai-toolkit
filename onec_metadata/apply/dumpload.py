"""Загрузка/выгрузка расширений через DESIGNER + верификация round-trip.

Транспорт кириллических деревьев Windows→Mac: chcp 65001 + tar —
zip/Compress-Archive ломают кодировку имён (боевой урок эксплуатации).

Удалённый рабочий каталог на сервере — из env ONEC_REMOTE_WORKDIR
(по умолчанию D:\\src). Логи/tar-файлы кладутся внутрь него.
"""
import filecmp
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

from onec_metadata.apply.runner import Runner, designer_cmd, mask_password

REMOTE_WORKDIR = os.environ.get("ONEC_REMOTE_WORKDIR", r"D:\src")


def _audit_version(local_dir: Path, expected: dict | None) -> None:
    """Зафиксировать версию/совместимость дампа перед применением (не фатально).

    Политика «фиксировать + предупреждать»: печатаем сводку и предупреждения о
    расхождении; блокировки нет — несовместимое отвергнет платформа при загрузке.
    """
    from onec_metadata import version_info
    try:
        audit = version_info.audit(
            local_dir, bin_path=os.environ.get("ONEC_1CV8_BIN"),
            expected=expected)
    except Exception as e:  # аудит информативен и НИКОГДА не рушит применение
        print(f"[версия применения] пропущен аудит: {e}", file=sys.stderr)
        return
    print(audit.report(), file=sys.stderr)


def _wpath(*parts: str) -> str:
    return "\\".join([REMOTE_WORKDIR, *parts])


class ApplyError(Exception):
    pass


def _read_log(r: Runner, log: str) -> str:
    return r.read_text(log, timeout=60)


_OK = "__DSGN_OK__"
_FAIL = "__DSGN_FAIL__"

# Частые ответы платформы, по которым причина не очевидна: фрагмент лога → что делать.
_LOG_HINTS = (
    ("помещена в хранилище",
     "конфигурация подключена к хранилищу: полная загрузка запрещена платформой — грузить "
     "точечно (-files / designer_sync load --files) только захваченные объекты"),
    ("связывания с хранилищем",
     "учётка хранилища бывает привязана к одной ИБ: из другой базы пакетный режим даёт эту "
     "ошибку без слова про учётку — работать из базы, к которой привязана учётка"),
    ("не идентифицирован",
     "проверь учётку ИБ (--contour = ключ ONEC_IB_USER_/PASS_<КЛЮЧ>) и порядок ключей: "
     "действие последним перед /Out"),
)


def _log_hints(text: str) -> str:
    low = text.lower()
    return "".join(f"\nподсказка: {h}" for frag, h in _LOG_HINTS if frag in low)


def _run_designer(r: Runner, ib: str, user: str, pwd: str, action: str,
                  args: list, log: str, timeout: int = 900) -> None:
    cmd = designer_cmd(ib, user, pwd, action, args, log)
    # лог прошлого запуска с тем же путём иначе читается как результат этого (ложная
    # ошибка или ложный успех, если платформа не стартовала и свой лог не записала)
    if hasattr(r, "remove"):
        r.remove(log)
    if getattr(r, "shell", True):
        # SSH: на том конце cmd.exe. `if errorlevel 1` вычисляется В МОМЕНТ
        # исполнения (это команда, а не переменная), поэтому корректно ловит код
        # возврата DESIGNER. Инлайновый `%errorlevel%` раскрылся бы cmd'ом ДО
        # запуска exe и всегда давал 0.
        gate = f" & if errorlevel 1 (echo {_FAIL}) else (echo {_OK})"
        code, out = r.run(cmd + gate, timeout=timeout)
        ok_run = (_OK in out) and (_FAIL not in out)
    else:
        # Локально оболочки нет: гейт-строка ушла бы АРГУМЕНТАМИ в 1cv8.exe (и
        # маркер не появился бы никогда → вечный ложный FAIL). Код возврата
        # процесса доступен напрямую — он и есть вердикт.
        code, out = r.run(cmd, timeout=timeout)
        ok_run = (code == 0)
    logtxt = _read_log(r, log)
    ok = ok_run and ("Ошибк" not in logtxt) and \
         ("недостаточно прав" not in logtxt.lower())
    if not ok:
        raise ApplyError(
            f"designer {action} failed\ncmd: {mask_password(cmd)}\n"
            f"out: {out.strip()[:500]}\nlog: {logtxt.strip()[:2000]}"
            + _log_hints(out + "\n" + logtxt))


def _put_text(r: Runner, remote_path: str, text: str) -> None:
    """Положить текст файлом НА СЕРВЕР (Runner.put берёт только локальный файл)."""
    import tempfile
    fd, local_path = tempfile.mkstemp(suffix=".txt", prefix="ds_listfile_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
            f.write(text)
        r.put(local_path, remote_path)
    finally:
        os.unlink(local_path)


def load_extension(r: Runner, ib: str, user: str, pwd: str, ext: str | None,
                   remote_src: str, log: str | None = None,
                   files: list[str] | None = None, update_dbcfg: bool = True) -> None:
    """Полная (files=None) или точечная (`files` — пути от remote_src, `-files`)
    загрузка. `update_dbcfg=False` — для ad hoc точечного цикла: человек сам
    прогоняет UpdateDBCfg/синтакс-контроль в Конфигураторе после правки."""
    log = log or _wpath("load.log")
    args = [remote_src]
    if files:
        args += ["-files", ",".join(p.replace("\\", "/") for p in files)]
    if ext:
        args += ["-Extension", ext]
    _run_designer(r, ib, user, pwd, "/LoadConfigFromFiles", args, log)
    if update_dbcfg and ext:
        # ВАЖНО: без -Extension обновится только основная конфигурация, а
        # загруженная версия расширения останется НЕ применённой к ИБ —
        # исполняться будет старая (боевой урок эксплуатации).
        _run_designer(r, ib, user, pwd, "/UpdateDBCfg",
                      ["-Extension", ext], log)


def dump_extension(r: Runner, ib: str, user: str, pwd: str, ext: str | None,
                   remote_dst: str, log: str | None = None,
                   objects: list[str] | None = None) -> None:
    """Перевыгрузка расширения платформой → ПЛАТФОРМЕННО-КАНОНИЧЕСКОЕ дерево.

    Это дерево (`after` в roundtrip_verify) и есть артефакт для коммита в git:
    ConfigDumpInfo, порядок свойств и версии — пересчитаны платформой, а не нашей
    правкой. Наша byte-perfect правка (`before`) годится как источник изменения,
    но канонический результат даёт именно эта перевыгрузка (архитектурный аудит,
    canonization). См. docs/architecture-review-2026-07.md.

    `objects` (дот-нотация, напр. «ОбщийМодуль.Имя») — точечная выгрузка:
    список уходит listFile'ом НА СЕРВЕР (там же читает платформа), файл
    подчищается после выгрузки. Без objects — полная выгрузка.
    """
    log = log or _wpath("dump.log")
    args = [remote_dst]
    listfile_remote = None
    if objects:
        listfile_remote = _wpath("_objects.txt")
        _put_text(r, listfile_remote, "\n".join(objects))
        args += ["-listFile", listfile_remote]
    if ext:
        args += ["-Extension", ext]
    try:
        _run_designer(r, ib, user, pwd, "/DumpConfigToFiles", args, log)
    finally:
        if listfile_remote:
            r.remove(listfile_remote)


def upload_tree(r: Runner, local_dir: Path, remote_dir: str,
                tmp_tar: str | None = None,
                expected_version: dict | None = None) -> None:
    """Mac → сервер: tar локально → scp → распаковка с chcp 65001.

    Перед отправкой фиксирует версию/совместимость дампа (audit, не фатально).
    ConfigDumpInfo.xml исключается всегда: с ним LoadConfigFromFiles делает
    ЧАСТИЧНУЮ загрузку по его реестру хэшей, молча пропуская файлы, которых
    в реестре нет (новые объекты). Боевой урок эксплуатации.
    """
    _audit_version(local_dir, expected_version)
    # Гейт производительности: не отправлять дерево с НОВЫМИ обращениями к БД
    # в цикле (боевой урок 20.07). Легаси — через baseline, см. apply/bsl_gate.
    from .bsl_gate import BASELINE_NAME, enforce as _bsl_enforce
    _bsl_enforce(local_dir)
    tmp_tar = tmp_tar or _wpath("_upload.tar")
    local_tar = local_dir.parent / "_upload.tar"
    subprocess.run(["tar", "--exclude", "./ConfigDumpInfo.xml",
                    "--exclude", "./" + BASELINE_NAME,
                    "-cf", str(local_tar), "-C", str(local_dir), "."],
                   check=True)
    subprocess.run(["scp", "-o", "BatchMode=yes", str(local_tar),
                    f"{r.host}:{tmp_tar.replace(chr(92), '/')}"], check=True)
    local_tar.unlink()
    code, out = r.run(
        f"chcp 65001 >nul & (if exist {remote_dir} rmdir /s /q {remote_dir}) & "
        f"mkdir {remote_dir} & tar -xf {tmp_tar} -C {remote_dir} & "
        f"echo EXIT=%errorlevel%")
    if "EXIT=0" not in out:
        raise ApplyError(f"upload_tree failed: {out.strip()[:500]}")


def fetch_tree(r: Runner, remote_dir: str, local_dir: Path,
               tmp_tar: str | None = None) -> None:
    """Сервер → Mac: tar с chcp 65001 → scp → распаковка."""
    tmp_tar = tmp_tar or _wpath("_fetch.tar")
    code, out = r.run(
        f"chcp 65001 >nul & del {tmp_tar} 2>nul & "
        f"tar -cf {tmp_tar} -C {remote_dir} . & echo EXIT=%errorlevel%")
    if "EXIT=0" not in out:
        raise ApplyError(f"fetch_tree tar failed: {out.strip()[:500]}")
    local_tar = local_dir.parent / "_fetch.tar"
    local_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["scp", "-o", "BatchMode=yes",
                    f"{r.host}:{tmp_tar.replace(chr(92), '/')}",
                    str(local_tar)], check=True)
    subprocess.run(["tar", "-xf", str(local_tar), "-C", str(local_dir)],
                   check=True)
    local_tar.unlink()


# служебные файлы выгрузки: платформа меняет их при каждом дампе,
# к содержимому конфигурации они не относятся
IGNORE_DUMP_FILES = frozenset({"ConfigDumpInfo.xml"})


def roundtrip_verify(before: Path, after: Path, expect_changed: set,
                     ignore: frozenset = IGNORE_DUMP_FILES) -> None:
    """Изменённые/новые/удалённые файлы == ровно expect_changed.

    Пути сравниваются в NFC: macOS-файловая система отдаёт кириллицу в NFD,
    а ожидания естественно записываются в NFC.
    """
    nfc = lambda s: unicodedata.normalize("NFC", s)
    expected = {nfc(s) for s in expect_changed}
    ignored = {nfc(s) for s in ignore}
    changed = set()
    for f in sorted(p for p in before.rglob("*") if p.is_file()):
        rel = nfc(f.relative_to(before).as_posix())
        if rel in ignored:
            continue
        g = after / f.relative_to(before)
        if not g.exists() or not filecmp.cmp(f, g, shallow=False):
            changed.add(rel)
    for g in sorted(p for p in after.rglob("*") if p.is_file()):
        rel = nfc(g.relative_to(after).as_posix())
        if rel in ignored:
            continue
        if not (before / g.relative_to(after)).exists():
            changed.add(rel)
    if changed != expected:
        unexpected = sorted(changed - expected)
        missing = sorted(expected - changed)
        raise ApplyError(
            f"round-trip diff неожиданный. Лишние изменения: {unexpected}; "
            f"обещанные, но не изменившиеся: {missing}")


# Каталог выгрузки → тип в ConfigDumpInfo.xml: обычно множественное число через «s», кроме этих.
_DIR_TYPE = {"ChartsOfAccounts": "ChartOfAccounts", "ChartsOfCharacteristicTypes": "ChartOfCharacteristicTypes",
             "ChartsOfCalculationTypes": "ChartOfCalculationTypes", "BusinessProcesses": "BusinessProcess",
             "FilterCriteria": "FilterCriterion"}


def dump_info_names(src_dir: Path) -> set | None:
    """Имена `<Metadata name=…>` из ConfigDumpInfo.xml каталога выгрузки; None — файла нет."""
    p = Path(src_dir) / "ConfigDumpInfo.xml"
    if not p.is_file():
        return None
    return {unicodedata.normalize("NFC", n) for n in
            re.findall(r'<Metadata\s+name="([^"]+)"', p.read_text("utf-8-sig", errors="replace"))}


def _full_name(rel: str, names: set) -> str | None:
    """Полное имя объекта в ConfigDumpInfo по пути его xml: Catalogs/X.xml → Catalog.X,
    Catalogs/X/Forms/F.xml → Catalog.X.Form.F, Configuration.xml → Configuration.<Имя>."""
    parts = rel[:-4].split("/")
    if parts == ["Configuration"]:
        return next((n for n in names if n.startswith("Configuration.") and n.count(".") == 1), None)
    if len(parts) not in (2, 4) or (len(parts) == 4 and parts[2] != "Forms"):
        return None
    return f"{dir_type(parts[0])}.{parts[1]}" + (f".Form.{parts[3]}" if len(parts) == 4 else "")


def dir_type(kind_dir: str) -> str:
    """Каталог выгрузки → тип в ConfigDumpInfo.xml: Catalogs → Catalog."""
    return _DIR_TYPE.get(kind_dir, kind_dir[:-1] if kind_dir.endswith("s") else kind_dir)


_NAME_RE = re.compile(r'<Metadata\s+name="([^"]+)"')


def _dump_info_units(lines: list[str]):
    """(голова, [(имя, [строки записи с вложенными])], хвост) — записи верхнего уровня ConfigVersions."""
    start = next(i for i, ln in enumerate(lines) if "<ConfigVersions" in ln) + 1
    end = max(i for i, ln in enumerate(lines) if "</ConfigVersions>" in ln)
    units, i = [], start
    while i < end:
        m = _NAME_RE.search(lines[i])
        if not m:                                   # пустая/служебная строка — сама по себе
            units.append((None, [lines[i]]))
            i += 1
            continue
        j, depth = i, 0
        while True:
            depth += lines[j].count("<Metadata") - lines[j].count("/>") - lines[j].count("</Metadata>")
            j += 1
            if depth <= 0:
                break
        units.append((m.group(1), lines[i:j]))
        i = j
    return lines[:start], units, lines[end:]


def merge_dump_info(old_text: str, new_text: str, objects: set) -> str:
    """ConfigDumpInfo.xml полной выгрузки + точечной: записи объектов `objects` («Тип.Имя») — из
    новой, остальные — как были. Порядок, формат и переводы строк старого файла сохраняются.
    Иначе точечная выгрузка затирала полный реестр, и проверка загрузки теряла чужие модули."""
    eol = "\r\n" if "\r\n" in old_text else "\n"
    owner = lambda name: next((o for o in objects if name == o or name.startswith(o + ".")), None)
    head, old_units, tail = _dump_info_units(old_text.splitlines(keepends=True))
    _, new_units, _ = _dump_info_units(new_text.splitlines(keepends=True))
    fresh = {}
    for name, block in new_units:
        o = name and owner(name)
        if o:
            fresh.setdefault(o, []).extend(ln.rstrip("\r\n") + eol for ln in block)
    out, placed = list(head), set()
    for name, block in old_units:
        o = name and owner(name)
        if not o or o not in fresh:                  # в новой выгрузке объекта нет — старое не теряем
            out += block
        elif o not in placed:
            out += fresh.get(o, [])
            placed.add(o)
    for o in sorted(set(fresh) - placed):                # новые объекты — в конец реестра
        out += fresh[o]
    return "".join(out + tail)


def _required_by_dump_info(rel: str, full: str, names: set) -> list[str]:
    """Файлы модулей объекта, которые ConfigDumpInfo числит за ним (без вложенных форм)."""
    base = "" if rel == "Configuration.xml" else rel[:-4] + "/"
    need = []
    for n in names:
        if not n.startswith(full + "."):
            continue
        tail = n[len(full) + 1:].split(".")
        if len(tail) == 1 and tail[0].endswith("Module"):           # ObjectModule, Module, SessionModule…
            need.append(f"{base}Ext/{tail[0]}.bsl")
        elif len(tail) == 3 and tail[0] == "Command" and tail[2] == "CommandModule":
            need.append(f"{base}Commands/{tail[1]}/Ext/CommandModule.bsl")
        elif tail == ["Form"]:                                       # сама форма
            need.append(f"{base}Ext/Form.xml")
    return need


def partial_load_gaps(src_dir: Path, files: list[str], allow_missing_ext: bool = False) -> list[str]:
    """Файлы, без которых точечная загрузка СОТРЁТ части объекта в базе.

    `<Объект>.xml` в `-files` без его `<Объект>/Ext/*` платформа понимает как «у объекта
    модулей нет»: стирает модуль объекта/менеджера, а у корня (`Configuration.xml`) — модули
    сеанса и приложения, командный интерфейс, рабочую область, поддержку. load при этом
    exit=0, и обратная выгрузка пустой Ext просто не отдаёт.

    Источник истины — ConfigDumpInfo.xml каталога выгрузки: все модули, которые он числит за
    объектом, обязаны быть в списке (объекта там нет — он новый, Ext не нужен). Плюс всё, что
    лежит в `<Объект>/Ext/` на диске. Без ConfigDumpInfo новый объект не отличить от неполной
    выгрузки существующего: xml без Ext — отказ, если не `allow_missing_ext`.
    """
    src_dir = Path(src_dir)
    listed = {unicodedata.normalize("NFC", f.replace("\\", "/").lstrip("./")) for f in files}
    names = dump_info_names(src_dir)
    gaps = []
    for rel in sorted(listed):
        if not rel.lower().endswith(".xml") or "Ext" in rel.split("/") or rel == "ConfigDumpInfo.xml":
            continue
        ext_dir = src_dir / ("Ext" if rel == "Configuration.xml" else rel[:-4] + "/Ext")
        need = []
        if names is not None:
            full = _full_name(rel, names)
            if full in names:
                need += _required_by_dump_info(rel, full, names)
        elif not ext_dir.is_dir() and not allow_missing_ext:
            gaps.append(f"{rel} → нет ни {ext_dir.relative_to(src_dir).as_posix()}/, ни ConfigDumpInfo.xml: "
                        "новый объект не отличить от неполной выгрузки существующего (модули сотрутся)")
            continue
        if ext_dir.is_dir():
            need += [p.relative_to(src_dir).as_posix() for p in ext_dir.rglob("*") if p.is_file()]
        for n in sorted({unicodedata.normalize("NFC", x) for x in need}):
            if n not in listed:
                missing = "" if (src_dir / n).is_file() else " (нет и в каталоге — выгрузка неполная, нужна полная)"
                gaps.append(f"{rel} → нет {n}{missing}")
    return gaps


def form_snapshot(dump_dir: Path) -> dict:
    """{путь Form.xml: есть ли в нём <Events>} — до точечной выгрузки, чтобы заметить потерю."""
    dump_dir = Path(dump_dir)
    return {p.relative_to(dump_dir).as_posix(): "<Events>" in p.read_text("utf-8-sig", errors="replace")
            for p in dump_dir.rglob("Form.xml") if p.parent.name == "Ext"}


def partial_dump_form_problems(dump_dir: Path, before: dict) -> list[str]:
    """Формы, которые точечная выгрузка (-listFile) отдала неполными.

    Боевой случай: форма пришла без `<Events>` в Form.xml и без `Form/Module.bsl`, хотя в базе
    они были (полная выгрузка их показала). Ошибка — если события были в прошлой выгрузке
    этого каталога и пропали, или события есть, а модуля нет.
    """
    dump_dir = Path(dump_dir)
    problems = []
    for rel, had_events in sorted(form_snapshot(dump_dir).items()):
        module = dump_dir / Path(rel).parent / "Form" / "Module.bsl"
        if had_events and not module.is_file():
            problems.append(f"{rel}: события есть, модуля Form/Module.bsl нет")
        elif before.get(rel) and not had_events:
            problems.append(f"{rel}: в прошлой выгрузке были <Events>, теперь нет")
    return problems
