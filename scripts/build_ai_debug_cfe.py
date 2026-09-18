# /// script
# dependencies = []
# ///
"""Собрать `extensions/ai_debug/src` → `dist/ai_debug.cfe` и (опционально) приложить к релизу.

Закрывает ручной шаг из docs/RELEASING.md «Сборка расширения ai_debug»: CI платформы
1С не имеет, поэтому .cfe собирается на машине с платформой (обычно Windows) одной командой.

Контейнер для сборки — пустая файловая база во временном каталоге (создаётся сама,
переиспользуется): расширение грузится в неё из XML (`/LoadConfigFromFiles -Extension`)
и выгружается бинарником (`/DumpCfg -Extension`). UpdateDBCfg не нужен и в пустой базе
не пройдёт (нет языка «Русский» в основной конфигурации) — для выгрузки .cfe это не важно.

Примеры:
  scripts/build_ai_debug_cfe.py                      # → dist/ai_debug.cfe + sha256
  scripts/build_ai_debug_cfe.py --upload v2.3.5      # + gh release upload
  ONEC_1CV8_BIN="C:\\Program Files\\1cv8\\8.3.27.1606\\bin\\1cv8.exe" scripts/build_ai_debug_cfe.py
"""
import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ext", default="ai_debug", help="имя расширения в extensions/")
    p.add_argument("--bin", help="1cv8(.exe); иначе ONEC_1CV8_BIN или авто-поиск")
    p.add_argument("--out", help="куда положить .cfe (по умолчанию dist/<ext>.cfe)")
    p.add_argument("--work", help="каталог базы-контейнера (по умолчанию %%TEMP%%/onec_cfe_build)")
    p.add_argument("--upload", metavar="TAG", help="приложить к GitHub Release этого тега (gh)")
    a = p.parse_args(argv)

    from onec_verify import resolve_bin
    bin_ = resolve_bin(a.bin)
    # runner фиксирует DEFAULT_BIN в момент импорта — env выставляем ДО импорта apply.*
    os.environ["ONEC_1CV8_BIN"] = bin_
    work = Path(a.work) if a.work else Path(tempfile.gettempdir()) / "onec_cfe_build"
    os.environ["ONEC_REMOTE_WORKDIR"] = str(work)
    work.mkdir(parents=True, exist_ok=True)

    from onec_metadata.apply.dumpload import load_extension
    from onec_metadata.apply.external import dump_cfe
    from onec_metadata.apply.runner import make_runner

    src = REPO / "extensions" / a.ext / "src"
    if not (src / "Configuration.xml").exists():
        raise SystemExit(f"нет исходников расширения: {src}")

    ib = work / "ib"
    if not (ib / "1Cv8.1CD").exists():
        r = subprocess.run([bin_, "CREATEINFOBASE", f"File={ib}", "/DisableStartupDialogs",
                            "/DisableStartupMessages", "/Out", str(work / "create.log")],
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise SystemExit(f"CREATEINFOBASE rc={r.returncode}: "
                             f"{(work / 'create.log').read_text(encoding='utf-8-sig', errors='replace')}")

    runner = make_runner("local")
    load_extension(runner, str(ib), "", "", a.ext, str(src),
                   log=str(work / "load.log"), update_dbcfg=False)
    out = Path(a.out) if a.out else REPO / "dist" / f"{a.ext}.cfe"
    out.parent.mkdir(parents=True, exist_ok=True)
    dump_cfe(runner, str(ib), "", "", ext=a.ext, out_cfe=str(out), log=str(work / "dump.log"))

    data = out.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    commit = subprocess.run(["git", "-C", str(REPO), "log", "-1", "--format=%h",
                             "--", f"extensions/{a.ext}"], capture_output=True, text=True).stdout.strip()
    print(f"[ok] {out} ({len(data)} байт)")
    print(f"Сборка: платформа {Path(bin_).parts[-3]}, исходники extensions/{a.ext}@{commit}")
    print(f"SHA-256: {sha}")

    if a.upload:
        subprocess.run(["gh", "release", "upload", a.upload, str(out), "--clobber"], check=True)
        print(f"[ok] приложено к релизу {a.upload} — добавь строки выше в описание релиза")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
