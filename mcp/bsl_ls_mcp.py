# /// script
# dependencies = ["mcp"]
# ///
"""MCP-сервер bsl-ls — диагностики BSL Language Server (синтаксис + стандарты ИТС) поверх
официального jar 1c-syntax/bsl-language-server (MIT). Тонкая аудируемая обёртка в духе onec-code:
запускает `analyze -r json` по каталогу/файлу 1С и возвращает диагностики. Кроссплатформенно
(Windows/macOS/Linux): java из PATH, пути через os.path, temp-выгрузка отчёта.

Назначение: ПОСЛЕ правки кода 1С прогнать проверку до загрузки в базу (шаг 6-7 схемы
AI_DEV_MCP_SCHEME). НЕ проверяет сигнатуры платформы — это делает bsl-platform (mcp-bsl-context).

ENV:
  BSL_JAR        — путь к bsl-language-server-*-exec.jar (обязательно)
  BSL_MEMORY_MB  — -Xmx для JVM (по умолчанию 4096)
  BSL_CONFIG     — путь к .bsl-language-server.json (опционально)
  JAVA           — путь к java (по умолчанию из PATH)
"""
import os, json, glob, tempfile, subprocess, shutil
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("bsl-ls")
JAR = os.environ.get("BSL_JAR", "")
JAVA = os.environ.get("JAVA") or shutil.which("java") or "java"
MEM = os.environ.get("BSL_MEMORY_MB", "4096")
CONFIG = os.environ.get("BSL_CONFIG", "")


def _analyze(src, timeout=300):
    if not JAR or not os.path.isfile(JAR):
        return {"error": f"BSL_JAR не найден: {JAR!r} — задай env BSL_JAR на bsl-language-server-*-exec.jar"}
    src = os.path.abspath(src)
    if not os.path.exists(src):
        return {"error": f"путь не существует: {src}"}
    out = tempfile.mkdtemp(prefix="bsl_ls_")
    cmd = [JAVA, f"-Xmx{MEM}m", "-Dfile.encoding=UTF-8", "-jar", JAR,
           "analyze", "-s", src, "-o", out, "-r", "json", "-q"]
    if CONFIG and os.path.isfile(CONFIG):
        cmd += ["-c", CONFIG]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        shutil.rmtree(out, ignore_errors=True)
        return {"error": f"таймаут анализа ({timeout}s)"}
    reports = glob.glob(os.path.join(out, "*.json"))
    data = None
    if reports:
        try:
            with open(reports[0], encoding="utf-8", errors="replace") as f:
                data = json.load(f)
        except Exception as e:
            data = {"error": f"не разобрал отчёт: {e}"}
    shutil.rmtree(out, ignore_errors=True)
    if data is None:
        return {"error": "BSL LS не создал отчёт",
                "stderr": (p.stderr or "")[-1500:], "stdout": (p.stdout or "")[-800:]}
    return data


@mcp.tool()
def bsl_analyze(src: str, max_issues: int = 100, severities: str = "", timeout_s: int = 300) -> str:
    """Прогнать BSL Language Server по каталогу/файлу 1С (.bsl/.os) и вернуть диагностики
    (синтаксис + стандарты ИТС). Путь — АБСОЛЮТНЫЙ. Использовать ПОСЛЕ правки кода до загрузки
    в базу — нацеливать на изменённые файлы/каталог, не на всю конфигурацию. severities — фильтр
    через запятую (напр. 'Error,Warning'); пусто = все. timeout_s — лимит анализа (для больших
    каталогов увеличить)."""
    res = _analyze(src, timeout=timeout_s)
    if "error" in res:
        msg = f"[bsl-ls] ошибка: {res['error']}"
        if res.get("stderr"):
            msg += f"\nstderr: {res['stderr']}"
        return msg
    want = {s.strip().lower() for s in severities.split(",") if s.strip()}
    issues, summary = [], {}
    for fi in res.get("fileinfos", []):
        fp = fi.get("path", "?")
        for d in fi.get("diagnostics", []):
            sev = d.get("severity", "?")
            summary[sev] = summary.get(sev, 0) + 1
            if want and sev.lower() not in want:
                continue
            ln = d.get("range", {}).get("start", {}).get("line", 0)
            issues.append((sev, fp, ln + 1, d.get("code", ""), (d.get("message") or "").strip()))
    nfiles = len(res.get("fileinfos", []))
    if not summary:
        return f"[bsl-ls] чисто: диагностик нет ({nfiles} файлов) — {src}"
    sev_order = {"Error": 0, "Warning": 1, "Info": 2, "Hint": 3}
    issues.sort(key=lambda x: sev_order.get(x[0], 9))
    head = issues[:max_issues]
    lines = [f"[bsl-ls] файлов: {nfiles}; по важности: {summary}"
             + (f"; фильтр={sorted(want)}" if want else "")]
    for sev, fp, ln, code, msg in head:
        lines.append(f"  {sev} {os.path.basename(fp)}:{ln} [{code}] {msg}")
    if len(issues) > max_issues:
        lines.append(f"  ... ещё {len(issues) - max_issues}")
    return "\n".join(lines)


@mcp.tool()
def bsl_version() -> str:
    """Версия используемого BSL Language Server (jar) и путь к нему."""
    if not JAR or not os.path.isfile(JAR):
        return f"BSL_JAR не найден: {JAR!r}"
    try:
        p = subprocess.run([JAVA, "-jar", JAR, "--version"],
                           capture_output=True, text=True, timeout=60)
        return f"{(p.stdout or p.stderr or '').strip()} | jar={JAR}"
    except Exception as e:
        return f"ошибка: {e}"


if __name__ == "__main__":
    mcp.run()
