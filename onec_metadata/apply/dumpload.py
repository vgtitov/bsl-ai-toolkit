"""Загрузка/выгрузка расширений через DESIGNER + верификация round-trip.

Транспорт кириллических деревьев Windows→Mac: chcp 65001 + tar —
zip/Compress-Archive ломают кодировку имён (боевой урок эксплуатации).

Удалённый рабочий каталог на сервере — из env ONEC_REMOTE_WORKDIR
(по умолчанию D:\\src). Логи/tar-файлы кладутся внутрь него.
"""
import filecmp
import os
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


def _run_designer(r: Runner, ib: str, user: str, pwd: str, action: str,
                  args: list, log: str, timeout: int = 900) -> None:
    cmd = designer_cmd(ib, user, pwd, action, args, log)
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
            f"out: {out.strip()[:500]}\nlog: {logtxt.strip()[:2000]}")


def load_extension(r: Runner, ib: str, user: str, pwd: str, ext: str,
                   remote_src: str, log: str | None = None) -> None:
    log = log or _wpath("load.log")
    _run_designer(r, ib, user, pwd, "/LoadConfigFromFiles",
                  [remote_src, "-Extension", ext], log)
    # ВАЖНО: без -Extension обновится только основная конфигурация, а
    # загруженная версия расширения останется НЕ применённой к ИБ —
    # исполняться будет старая (боевой урок эксплуатации).
    _run_designer(r, ib, user, pwd, "/UpdateDBCfg",
                  ["-Extension", ext], log)


def dump_extension(r: Runner, ib: str, user: str, pwd: str, ext: str,
                   remote_dst: str, log: str | None = None) -> None:
    """Перевыгрузка расширения платформой → ПЛАТФОРМЕННО-КАНОНИЧЕСКОЕ дерево.

    Это дерево (`after` в roundtrip_verify) и есть артефакт для коммита в git:
    ConfigDumpInfo, порядок свойств и версии — пересчитаны платформой, а не нашей
    правкой. Наша byte-perfect правка (`before`) годится как источник изменения,
    но канонический результат даёт именно эта перевыгрузка (архитектурный аудит,
    canonization). См. docs/architecture-review-2026-07.md.
    """
    log = log or _wpath("dump.log")
    _run_designer(r, ib, user, pwd, "/DumpConfigToFiles",
                  [remote_dst, "-Extension", ext], log)


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
