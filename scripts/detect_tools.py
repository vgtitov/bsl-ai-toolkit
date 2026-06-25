# /// script
# dependencies = []
# ///
"""Авто-поиск и авто-УСТАНОВКА инструментов 1С/BSL + прописывание системных параметров (env, User-level).

Зачем: чтобы разработчику не задавать пути и не качать jar'ы руками. Скрипт сам:
  1) находит то, что уже есть (платформа 1С, JDK от EDT, jar'ы, мост);
  2) с флагом --install СКАЧИВАЕТ бесплатные инструменты из официальных релизов (см. реестр INSTALLABLE);
  3) прописывает env-переменные, которые читает `.mcp.json`.
Чего нет и что не качается автоматически — честно сообщает, куда положить.

Никаких захардкоженных путей конкретного разработчика. Идемпотентно, кросс-платформенно, только стандартная библиотека.

Бесплатные инструменты (качаются при --install, по своим лицензиям — соблюдаются: код не перепаковываем, тянем из
первоисточника на машину пользователя):
  • mcp-bsl-context (alkoleft/mcp-bsl-platform-context) — MIT       -> BSL_PLATFORM_JAR
  • bsl-language-server (1c-syntax/bsl-language-server) — LGPL-3.0  -> BSL_JAR
Мост bsl_ls_mcp.py (для bsl-ls) ВЕНДОРЕН в репозитории (mcp/bsl_ls_mcp.py) и приезжает с git clone — качать его
не надо: скрипт находит его в mcp/ и сам прописывает BSL_LS_MCP (или подхватывает уже заданный валидный путь).

Запуск:
  uv run scripts/detect_tools.py            # найти и прописать то, что есть локально
  uv run scripts/detect_tools.py --install  # + скачать недостающие бесплатные инструменты
  uv run scripts/detect_tools.py --dry-run  # только показать, ничего не менять и не качать

Версии можно переопределить env: BSL_LS_VERSION (дефолт 0.28.5), MCP_BSL_CONTEXT_VERSION (дефолт 0.3.2).
"""
import argparse, os, re, ssl, subprocess, sys, tempfile, urllib.request
from pathlib import Path

# Windows-консоль по умолчанию cp1251 — печать '→'/кириллицы роняет скрипт. Принудительно UTF-8.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

IS_WIN = os.name == "nt"
HOME = Path.home()
REPO_ROOT = Path(__file__).resolve().parent.parent
VER_RE = re.compile(r"^8\.3\.\d+\.\d+$")
TOOLS_MCP = HOME / "tools" / "mcp"
TOOLS_BSL = HOME / "tools" / "bsl"

# Реестр бесплатных, официально публикуемых инструментов (скачиваются при --install).
_BSL_LS_V = os.environ.get("BSL_LS_VERSION", "0.28.5")
_CTX_V = os.environ.get("MCP_BSL_CONTEXT_VERSION", "0.3.2")
INSTALLABLE = {
    "BSL_PLATFORM_JAR": {
        "label": "mcp-bsl-context (bsl-platform)", "license": "MIT",
        "url": f"https://github.com/alkoleft/mcp-bsl-platform-context/releases/download/v{_CTX_V}/mcp-bsl-context-{_CTX_V}.jar",
        "dest": TOOLS_MCP / f"mcp-bsl-context-{_CTX_V}.jar",
    },
    "BSL_JAR": {
        "label": "bsl-language-server (bsl-ls)", "license": "LGPL-3.0",
        "url": f"https://github.com/1c-syntax/bsl-language-server/releases/download/v{_BSL_LS_V}/bsl-language-server-{_BSL_LS_V}-exec.jar",
        "dest": TOOLS_BSL / f"bsl-language-server-{_BSL_LS_V}-exec.jar",
    },
}


def ok(m): print(f"  [ok] {m}")
def warn(m): print(f"  [нет] {m}")
def info(m): print(f"  [i] {m}")


def _version_key(name):
    return tuple(int(x) for x in name.split("."))


def platform_roots():
    if IS_WIN:
        return [Path(r"C:\Program Files\1cv8"), Path(r"C:\Program Files (x86)\1cv8"),
                Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\1cv8"))]
    return [Path("/opt/1cv8"), Path("/opt/1C/1cv8"), HOME / ".1cv8"]


def find_platform():
    """Каталог платформы 1С — наибольшая версия 8.3.x.y, у которой есть bin/1cv8(.exe)."""
    exe = "1cv8.exe" if IS_WIN else "1cv8"
    best = None
    for root in platform_roots():
        if not root.is_dir():
            continue
        for d in root.iterdir():
            if d.is_dir() and VER_RE.match(d.name) and (d / "bin" / exe).exists():
                if best is None or _version_key(d.name) > _version_key(best.name):
                    best = d
    return best


def edt_components():
    if IS_WIN:
        roots = [Path(r"C:\Program Files\1C\1CE\components")]
    else:
        roots = [HOME / "1C" / "1CE" / "components", Path("/opt/1C/1CE/components")]
    return [r for r in roots if r.is_dir()]


def find_jdk_java():
    """java из PATH; иначе — bundled JDK от EDT (axiom-jdk-*)."""
    from shutil import which
    j = which("java")
    if j:
        return Path(j), "PATH"
    exe = "java.exe" if IS_WIN else "java"
    for comp in edt_components():
        for d in sorted(comp.glob("*jdk*"), reverse=True):
            cand = d / "bin" / exe
            if cand.exists():
                return cand, "EDT"
    return None, None


def _prune(dirnames):
    skip = {".git", "node_modules", ".venv", "venv", "__pycache__", "src", "target", "build", ".idea"}
    dirnames[:] = [d for d in dirnames if d.lower() not in skip and not d.startswith(".")]


def search_roots():
    """Лёгкие каталоги, где разумно искать jar'ы/мост (без полного обхода диска)."""
    cands = [REPO_ROOT, REPO_ROOT.parent, TOOLS_MCP, TOOLS_BSL,
             HOME / "tools", HOME / ".bsl", HOME / "Downloads"]
    src = os.environ.get("ONEC_SRC_DIR")
    if src:
        cands.append(Path(src))
    for base in [REPO_ROOT.parent, HOME / "dev", Path("C:/dev") if IS_WIN else HOME]:
        if base.is_dir():
            try:
                for d in base.iterdir():
                    if d.is_dir() and "toolkit" in d.name.lower():
                        cands.append(d)
            except OSError:
                pass
    seen, out = set(), []
    for c in cands:
        try:
            rc = c.resolve()
        except OSError:
            continue
        if rc.is_dir() and rc not in seen:
            seen.add(rc)
            out.append(rc)
    return out


def find_file(patterns, prefer=None, max_depth=4):
    hits = []
    for root in search_roots():
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            if len(Path(dirpath).parts) - base_depth >= max_depth:
                dirnames[:] = []
                continue
            _prune(dirnames)
            for pat in patterns:
                for fn in filenames:
                    if Path(fn).match(pat):
                        hits.append(Path(dirpath) / fn)
    if not hits:
        return None
    if prefer:
        pref = [h for h in hits if prefer in h.name]
        if pref:
            hits = pref
    return sorted(hits, key=lambda p: p.name)[-1]


def env_valid(name):
    v = os.environ.get(name)
    if v and Path(v).exists():
        return v
    return None


def _download_once(url, dest):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "claude-1c-detect-tools"})
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), suffix=".part")
    os.close(fd)
    tmp = Path(tmp)
    try:
        with urllib.request.urlopen(req, timeout=180, context=ctx) as r, open(tmp, "wb") as f:
            total = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                total += len(chunk)
        if total < 10_000:  # явно не jar — что-то пошло не так (html/редирект-ошибка)
            raise IOError(f"подозрительно маленький файл ({total} байт)")
        tmp.replace(dest)
        return dest, total
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def download(url, dest, attempts=3):
    """Скачать url в dest (атомарно, с повтором при транзиентных сбоях сети/SSL)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for i in range(1, attempts + 1):
        try:
            return _download_once(url, dest)
        except Exception as e:
            last = e
            if i < attempts:
                info(f"  попытка {i} не удалась ({e}); повторяю…")
    raise last


def persist(name, value, dry, posix_exports):
    os.environ[name] = value
    if dry:
        return
    if IS_WIN:
        subprocess.run(["setx", name, value], capture_output=True, text=True)
    else:
        posix_exports.append(f'export {name}="{value}"')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true", help="скачать недостающие бесплатные инструменты")
    ap.add_argument("--dry-run", action="store_true", help="только показать, ничего не менять и не качать")
    args = ap.parse_args()
    dry, do_install = args.dry_run, args.install and not args.dry_run

    print("== Авто-поиск/установка инструментов 1С/BSL ==")
    posix_exports = []
    found, missing = [], []

    # java / JDK
    java, src = find_jdk_java()
    if java:
        ok(f"java: {java} (источник: {src})")
    else:
        warn("java не найден ни в PATH, ни в EDT — bsl-platform/bsl-ls не стартуют. Поставь JDK 17+ или добавь EDT-java в PATH.")

    # ONEC_PLATFORM_PATH — только поиск (установка платформы не автоматизируется)
    pre = env_valid("ONEC_PLATFORM_PATH")
    if pre:
        ok(f"ONEC_PLATFORM_PATH={pre} (уже задано, оставляю)"); found.append("ONEC_PLATFORM_PATH")
    else:
        plat = find_platform()
        if plat:
            persist("ONEC_PLATFORM_PATH", str(plat), dry, posix_exports)
            ok(f"ONEC_PLATFORM_PATH={plat} (платформа 1С){'  [dry-run]' if dry else ''}"); found.append("ONEC_PLATFORM_PATH")
        else:
            warn("платформа 1С не найдена — поставь 8.3.x или задай ONEC_PLATFORM_PATH вручную"); missing.append("ONEC_PLATFORM_PATH")

    # BSL_PLATFORM_JAR, BSL_JAR — поиск, затем (при --install) скачивание из официального релиза
    for name, spec in INSTALLABLE.items():
        pre = env_valid(name)
        if pre:
            ok(f"{name}={pre} (уже задано, оставляю)"); found.append(name); continue
        hit = find_file([Path(spec["dest"]).name])  # точное имя ассета
        if not hit:  # запасной поиск по обобщённой маске (любая версия)
            hit = find_file(["mcp-bsl-context*.jar"]) if name == "BSL_PLATFORM_JAR" else find_file(["bsl-language-server*.jar"], prefer="exec")
        if hit:
            persist(name, str(hit), dry, posix_exports)
            ok(f"{name}={hit} ({spec['label']}){'  [dry-run]' if dry else ''}"); found.append(name); continue
        if do_install:
            try:
                info(f"скачиваю {spec['label']} ({spec['license']}) ← {spec['url']}")
                dest, size = download(spec["url"], Path(spec["dest"]))
                persist(name, str(dest), dry, posix_exports)
                ok(f"{name}={dest} (скачано {size // 1024} КБ, {spec['license']})"); found.append(name)
            except Exception as e:
                warn(f"{spec['label']}: скачать не удалось ({e}). Проверь сеть/прокси или скачай вручную: {spec['url']}")
                missing.append(name)
        else:
            warn(f"{spec['label']}: не найден. Запусти с --install — скачаю из релиза ({spec['license']}): {spec['url']}")
            missing.append(name)

    # BSL_LS_MCP — мост; вендорится в репозитории (mcp/bsl_ls_mcp.py), приезжает с git clone
    pre = env_valid("BSL_LS_MCP")
    vendored = REPO_ROOT / "mcp" / "bsl_ls_mcp.py"
    if pre:
        ok(f"BSL_LS_MCP={pre} (уже задано, оставляю)"); found.append("BSL_LS_MCP")
    else:
        hit = vendored if vendored.is_file() else find_file(["bsl_ls_mcp.py"])
        if hit:
            persist("BSL_LS_MCP", str(hit), dry, posix_exports)
            ok(f"BSL_LS_MCP={hit} (мост из репозитория){'  [dry-run]' if dry else ''}"); found.append("BSL_LS_MCP")
        else:
            warn(f"мост bsl_ls_mcp.py: не найден. Он вендорится в репо ({vendored}) — обнови репозиторий (git pull) "
                 "или задай BSL_LS_MCP. Без него работают onec-code и bsl-platform, но не bsl-ls.")
            missing.append("BSL_LS_MCP")

    print(f"\n== Итог: готово {len(found)}, не хватает {len(missing)} ==")
    if posix_exports:
        print("Добавь в ~/.zshrc или ~/.bashrc и сделай source:")
        for e in posix_exports:
            print(f"  {e}")
    elif IS_WIN and found and not dry:
        info("Переменные прописаны (User). Перезапусти терминал и Claude Code, чтобы они подхватились.")
    if missing and not do_install and any(m in INSTALLABLE for m in missing):
        info("Подсказка: повтори с --install — бесплатные jar'ы (MIT/LGPL) скачаются и пропишутся сами.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
