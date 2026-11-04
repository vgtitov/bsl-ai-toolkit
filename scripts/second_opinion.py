#!/usr/bin/env python3
"""Второе мнение другой моделью: ревью диффа или проверка документа, решения, гипотезы.

Claude не проверяет сам себя: ревью и проверку выносит модель другого семейства. Цепочка идёт по
порядку и вызывает только то, что у человека настроено:

  1. M365 Copilot — ПО УМОЛЧАНИЮ для команды (лимитов у всех много, данные уже в корпоративном контуре):
     встроенный адаптер scripts/copilot_adapter.py после разового входа (scripts/copilot_setup.py), либо
     своя команда в AI_TOOLKIT_COPILOT_CMD (читает вопрос со STDIN, печатает ответ).
  2. Codex (OpenAI)          — для продвинутого: --risk critical, либо резервом, если Copilot не ответил;
                               нужен CLI `codex` и вход (`codex login`).
  3. Модель без обучения     — только если есть OPENROUTER_API_KEY И явно разрешено AI_TOOLKIT_NOTRAIN=1;
                               запрос уходит лишь провайдерам, которые не хранят и не обучаются
                               (provider.data_collection = deny, zdr = true).
                               Бесплатные модели не вызываются никогда: они обучаются на запросах.

Звено пропускается, если его нет, оно в лимите, недоступно из страны (геоблок OpenAI в РФ, РБ и
других регионах — нужен VPN) или вернуло пустой ответ. Не ответил никто — код 2, и «готово» нельзя.
E-mail и телефоны в запросе, диффе или вложении: уходит только доверенному корпоративному ассистенту
(AI_TOOLKIT_COPILOT_CMD, например M365 Copilot); Codex и внешние модели пропускаются. Нет ассистента — отказ.

  python scripts/second_opinion.py review --base origin/main [--repo .]
  python scripts/second_opinion.py review --commit HEAD
  python scripts/second_opinion.py ask "полный контекст и вопрос"     (или «-» = STDIN)
  python scripts/second_opinion.py status                              # что из цепочки настроено

Отчёт: ~/.ai-toolkit/reviews/<время>-<вид>-<кто>.md; кто проверял — в последней строке вывода.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

REPORTS = Path.home() / ".ai-toolkit" / "reviews"
CHUNK = 24000
TIMEOUT = 1800
DOWN_RX = re.compile(r"(?m)^(ERROR:.*(usage limit|rate.?limit|429|quota|unauthori[sz]ed|not logged in|"
                     r"unsupported.?country|forbidden|403))", re.I)
# e-mail (и с не-ASCII доменом) и телефоны в международном и местном формате; лучше лишний отказ, чем утечка
PII_RX = re.compile(r"[^\s@<>\"']+@[^\s@<>\"']+\.[^\s@<>\"'.]{2,}"           # e-mail
                    r"|(?<![\w+])\+\d[\d\s().-]{7,}\d"                           # +код страны
                    r"|(?<![\w.])\(?\d{2,4}\)?[\s-]\d{2,3}[\s-]?\d{2}[\s-]?\d{2}(?![\w.])"   # местный
                    r"|(?<![\w.])[80]\s?\(?\d{2,4}\)?[\s-]?\d{2,3}[\s-]?\d{2}[\s-]?\d{2}(?![\w.])",  # слитный, 8(0xx)
                    re.I)
REVIEW_PROMPT = ("Ты ревьюер кода. Часть {part} диффа репозитория {repo}. Найди дефекты: ошибки логики, "
                 "безопасность, потерю данных, регрессии, несоответствие описанию. Для каждого: [P0–P3] файл:строка "
                 "— что не так и как исправить. Стиль не обсуждай. Нет дефектов — так и напиши.\n\n{chunk}")
NOTRAIN_MODEL = os.environ.get("AI_TOOLKIT_NOTRAIN_MODEL", "openai/gpt-4.1-mini")


for _s in (sys.stdout, sys.stderr):                  # Windows: консоль и канал не в UTF-8 по умолчанию
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def utf8_env() -> dict:
    """Окружение дочерних процессов: Python внутри них читает и пишет UTF-8 независимо от локали Windows (cp1251, cp866)."""
    return dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")


def read_stdin_utf8() -> str:
    """Текст из STDIN как UTF-8 (байты, а не локаль): вопрос на кириллице не превращается в мусор на Windows."""
    raw = sys.stdin.buffer.read()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        import locale
        return raw.decode(locale.getpreferredencoding(False), errors="replace")


# ---------- звенья цепочки: каждое возвращает текст или None (звено недоступно) ----------

def codex_exec(args: list, cwd: Path, stdin: str | None = None) -> str | None:
    exe = shutil.which("codex")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, *args], cwd=cwd, input=stdin, capture_output=True, text=True, timeout=TIMEOUT,
                           encoding="utf-8", errors="replace", env=utf8_env())
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"⚠ Codex: {e}", flush=True)
        return None
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 or not (r.stdout or "").strip() or DOWN_RX.search(out[-800:]):
        print(f"⚠ Codex не ответил: {(out.strip().splitlines() or [str(r.returncode)])[-1][:160]}", flush=True)
        return None
    return r.stdout                      # только ответ: в stderr ход работы и исходный запрос


ADAPTER = Path(__file__).resolve().parent / "copilot_adapter.py"
COPILOT_PROFILE = Path.home() / ".cache" / "ai_toolkit_copilot_profile"


def copilot_cmd() -> str:
    """Команда доверенного корпоративного ассистента. AI_TOOLKIT_COPILOT_CMD задаёт её явно; иначе по
    умолчанию встроенный адаптер к M365 Copilot, если вход в него уже выполнен (scripts/copilot_setup.py)."""
    explicit = os.environ.get("AI_TOOLKIT_COPILOT_CMD", "").strip()
    if explicit:
        return explicit
    if ADAPTER.is_file() and COPILOT_PROFILE.is_dir() and any(COPILOT_PROFILE.iterdir()) and shutil.which("uv"):
        return f'uv run --quiet --with playwright python "{ADAPTER}" -'
    return ""


EXIT_LIMIT = 75      # copilot_adapter.py: дневной лимит Copilot, ответом это не считается
COPILOT_LAST_WHY = ""
# Общий признак лимита (как в operkontur-work-bundle, copilot_delegate); список дублирует copilot_adapter.LIMIT_PHRASES
LIMIT_PHRASES = ("daily chat limit", "reached your limit", "try again later", "upgrade to keep going", "used your daily",
                 "дневной лимит", "достигли лимита", "достигли дневного лимита", "суточный лимит", "повторите попытку позже")
LIMIT_MAX = 500
PAUSE_DIR = Path(os.environ.get("AI_TOOLKIT_LIMIT_SKIP_DIR") or Path.home() / ".cache" / "ai_toolkit_limit_skip")
PAUSE_HOURS = 6


def is_limit(text: str) -> bool:
    """Короткий ответ с кодом 0, который на деле отказ из-за лимита: ответом не считается, звено пропускается."""
    t = (text or "").strip().casefold()
    return 0 < len(t) < LIMIT_MAX and any(ph in t for ph in LIMIT_PHRASES)


def paused_until(name: str) -> float:
    """Unix-время конца паузы звена или 0. Пока пауза идёт, страницу и сеть не трогаем."""
    try:
        until = float((PAUSE_DIR / name).read_text().strip())
    except (OSError, ValueError):
        return 0.0
    return until if until > time.time() else 0.0


def set_pause(name: str, hours: float = PAUSE_HOURS) -> None:
    try:
        PAUSE_DIR.mkdir(parents=True, exist_ok=True)
        (PAUSE_DIR / name).write_text(str(time.time() + hours * 3600))
    except OSError:
        pass                                            # пауза — оптимизация, без неё просто повторим вызов


def copilot(prompt: str, files=()) -> str | None:
    global COPILOT_LAST_WHY
    COPILOT_LAST_WHY = ""
    cmd = copilot_cmd()
    if not cmd:
        return None
    until = paused_until("copilot")
    if until:
        COPILOT_LAST_WHY = f"лимит Copilot, пауза до {datetime.fromtimestamp(until):%H:%M}"
        print(f"⚠ Copilot пропущен: {COPILOT_LAST_WHY}", flush=True)
        return None
    quote = (lambda f: subprocess.list2cmdline([str(f)])) if os.name == "nt" else (lambda f: shlex.quote(str(f)))
    cmd += "".join(" --file " + quote(f) for f in files)     # вложения читаются целиком, лимит запроса не мешает
    try:
        # команду задаёт сам человек; оболочка разбирает кавычки и на Windows. По таймауту гасим всю группу
        grp = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        proc = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=utf8_env(), **grp)       # байты: кодировку задаём сами
    except OSError as e:
        print(f"⚠ Copilot: {e}", flush=True)
        return None
    try:
        out, _ = proc.communicate(prompt.encode("utf-8"), timeout=900)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            try:
                os.killpg(proc.pid, 9)
            except (ProcessLookupError, PermissionError):
                pass                                   # успел завершиться сам
        try:
            proc.communicate(timeout=10)                   # дочитать, но не ждать вечно
        except subprocess.TimeoutExpired:
            proc.kill()
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                if pipe:
                    pipe.close()
        print("⚠ Copilot: таймаут", flush=True)
        return None
    if proc.returncode == EXIT_LIMIT:
        set_pause("copilot")
        COPILOT_LAST_WHY = "лимит Copilot (дневной), пауза записана"
        print("⚠ Copilot: лимит Copilot (дневной). Это не ответ, перехожу к следующему звену.", flush=True)
        return None
    text = (out or b"").decode("utf-8", errors="replace")
    if proc.returncode == 0 and is_limit(text):        # страница ответила текстом про лимит с кодом 0
        set_pause("copilot")
        COPILOT_LAST_WHY = "лимит Copilot (дневной), пауза записана"
        print("⚠ Copilot: ответ оказался сообщением о лимите, следующее звено.", flush=True)
        return None
    return text if proc.returncode == 0 and text.strip() else None


def notrain(prompt: str, files=()) -> str | None:
    if files:
        return None                                    # вложить файл в запрос к этому звену нельзя
    if os.environ.get("AI_TOOLKIT_CORP") == "1":
        return None                                    # корпоративный код: внешние звенья без договора запрещены
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key or os.environ.get("AI_TOOLKIT_NOTRAIN") != "1":
        return None
    if NOTRAIN_MODEL.endswith(":free") or NOTRAIN_MODEL.startswith("openrouter/free"):
        print("⚠ бесплатная модель запрещена: она обучается на запросах", flush=True)
        return None
    body = json.dumps({"model": NOTRAIN_MODEL, "messages": [{"role": "user", "content": prompt}],
                       "provider": {"data_collection": "deny", "zdr": True}}).encode("utf-8")
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.load(resp)
        return (data.get("choices") or [{}])[0].get("message", {}).get("content") or None
    except Exception as e:                                   # noqa: BLE001 — звено недоступно, идём дальше
        print(f"⚠ модель без обучения: {str(e)[:160]}", flush=True)
        return None


TEXT_CHAIN = (("copilot", copilot), ("notrain", notrain))


# ---------- сценарии ----------

def split_diff(diff: str, limit: int = CHUNK) -> list:
    """Дифф → куски не длиннее limit по границам файлов; длинный файл режется подряд."""
    out, cur = [], ""
    for f in (x for x in re.split(r"(?m)^(?=diff --git )", diff) if x.strip()):
        for p in (f[i:i + limit] for i in range(0, len(f), limit)):
            if cur and len(cur) + len(p) > limit:
                out.append(cur)
                cur = ""
            cur += p
    return out + ([cur] if cur else [])


def _git(repo: Path, *args) -> str:
    r = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=repo, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"git {args[0]}: {(r.stderr or r.stdout).strip()[:200]}")
    return r.stdout


def collect_diff(repo: Path, base: str | None, commit: str | None) -> str:
    if commit:
        parents = _git(repo, "rev-list", "--parents", "-n", "1", commit).split()[1:]
        return _git(repo, "diff", "--no-color", "-U5", f"{commit}^1", commit) if parents else \
            _git(repo, "show", "--format=", "--no-color", "-U5", commit)   # merge — к первому родителю, корень целиком
    mb = _git(repo, "merge-base", base, "HEAD").strip()
    diff = _git(repo, "diff", "--no-color", "-U5", mb)        # с рабочим деревом: и незакоммиченные правки
    for rel in _git(repo, "ls-files", "--others", "--exclude-standard").splitlines():     # новые, ещё не добавленные файлы
        f = repo / rel
        try:
            body = f.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue                                         # двоичный или нечитаемый — пропуск
        diff += f"diff --git a/{rel} b/{rel}\nnew file\n--- /dev/null\n+++ b/{rel}\n" + "".join(f"+{l}\n" for l in body.splitlines())
    return diff


def has_pii(*texts: str) -> bool:
    return any(PII_RX.search(t) for t in texts)


NO_TRUSTED = "в тексте есть e-mail или телефон, а доверенный корпоративный ассистент (AI_TOOLKIT_COPILOT_CMD) не настроен: Codex и внешним моделям такое не отправляю"


def review(repo: Path, base: str | None, commit: str | None, chain=TEXT_CHAIN, codex=codex_exec,
           risk: str = "normal") -> tuple:
    diff = collect_diff(repo, base, commit)
    if not diff.strip():
        return "none", "Дифф пуст — проверять нечего."
    hidden = has_pii(diff)
    if hidden and not copilot_cmd():
        return "", NO_TRUSTED
    if hidden:
        # в диффе ПД: уходит только доверенному корпоративному ассистенту, без редактирования
        print("⚠ в диффе e-mail или телефон: Codex и внешние модели пропущены, только доверенный ассистент", flush=True)
    fns = dict(chain)
    for name in order(risk, chain):
        if hidden and not trusted_only(name):
            continue
        if name == "codex":
            out = codex(["exec", "review"] + (["--commit", commit] if commit else ["--base", base]), repo)
            if out:
                return "codex", out
            continue
        if len(diff) > CHUNK and name == "copilot":          # большой дифф — вложением целиком, без нарезки
            with tempfile.TemporaryDirectory() as tmp:
                f = Path(tmp) / "diff.patch"
                f.write_text(diff, encoding="utf-8")
                ans = fns[name](REVIEW_PROMPT.format(part="1 из 1 (дифф во вложении)", repo=repo.name, chunk=""), [str(f)])
            if ans:
                return name, ans
            continue
        chunks, parts = split_diff(diff), []
        for i, ch in enumerate(chunks, 1):
            ans = fns[name](REVIEW_PROMPT.format(part=f"{i} из {len(chunks)}", repo=repo.name, chunk=ch), ())
            if not ans:
                break
            parts.append(f"## Часть {i}\n\n{ans.strip()}\n")
        if len(parts) == len(chunks):
            return name, "\n".join(parts)
    return "", ""


def order(risk: str, chain) -> list:
    """Порядок звеньев по риску: critical — Codex первым (его лимит маленький, берём на важное);
    normal — корпоративный ассистент первым, Codex резервом; low — без Codex."""
    names = [n for n, _ in chain]
    return {"critical": ["codex"] + names, "normal": names[:1] + ["codex"] + names[1:], "low": names}[risk]


def _check_files(files) -> str:
    return next((f"вложения нет: {f}" for f in files if not Path(f).expanduser().is_file()), "")


def trusted_only(name: str) -> bool:
    return name == "copilot"


def ask(prompt: str, chain=TEXT_CHAIN, codex=codex_exec, files=(), risk: str = "normal") -> tuple:
    bad = _check_files(files)
    if bad:
        return "", bad
    files = [str(Path(f).expanduser().resolve()) for f in files]
    pii = has_pii(prompt, *(Path(f).read_text(encoding="utf-8", errors="replace") for f in files))
    if pii and not copilot_cmd():
        return "", NO_TRUSTED
    if pii:
        print("⚠ e-mail или телефон в запросе или вложении: только доверенный ассистент, Codex и внешние модели пропущены", flush=True)
    fns = dict(chain)
    for name in order(risk, chain):
        if pii and not trusted_only(name):
            continue
        if name == "codex":
            full = prompt + ("\n\nВложения (прочитай по путям перед ответом):\n" + "\n".join(files) if files else "")
            with tempfile.TemporaryDirectory() as tmp:
                out = codex(["exec", "--skip-git-repo-check", "-s", "read-only", "-"], Path(tmp), stdin=full)
            if out and not is_limit(out):
                return "codex", out
        else:
            ans = fns[name](prompt, files)
            if ans and not is_limit(ans):
                return name, ans
    return "", ""


def copilot_why_not() -> str:
    """Почему встроенный адаптер Copilot не считается настроенным (пусто, если настроен)."""
    if os.environ.get("AI_TOOLKIT_COPILOT_CMD", "").strip():
        return ""
    if not ADAPTER.is_file():
        return "нет scripts/copilot_adapter.py"
    if not shutil.which("uv"):
        return "не найден uv (https://astral.sh/uv)"
    if not (COPILOT_PROFILE.is_dir() and any(COPILOT_PROFILE.iterdir())):
        return "вход не выполнен (python scripts/copilot_setup.py --login)"
    return ""


def hints() -> str:
    """Одна строка «Что сделать» по звеньям, которые не настроены или на паузе. Только имена переменных и команды,
    без значений. Формат общий с operkontur-work-bundle (second_opinion.HINTS)."""
    out = []
    if not shutil.which("codex"):
        out.append("Codex: установить и войти (python scripts/codex_setup.py, затем codex login)")
    else:
        out.append("Codex: если лимит, ждать сброса окна 5 ч или недели (не ждать, ревью идёт дальше); если не вошли, codex login")
    until = paused_until("copilot")
    if until:
        out.append(f"Copilot: дневной лимит, пауза до {datetime.fromtimestamp(until):%H:%M}")
    elif copilot_why_not():
        out.append(f"Copilot: {copilot_why_not()}")
    if not (os.environ.get("OPENROUTER_API_KEY") and os.environ.get("AI_TOOLKIT_NOTRAIN") == "1"):
        out.append("OpenRouter: OPENROUTER_API_KEY и AI_TOOLKIT_NOTRAIN=1 в .env (ключ выпускает владелец на openrouter.ai/keys)")
    return "Что сделать: " + "; ".join(out)


def probe_copilot() -> tuple[bool, str]:
    """Живая проба: короткий вопрос на кириллице. Проверяет и вход, и кодировку (в ответе должно быть слово)."""
    ans = copilot("Повтори одним словом, строчными буквами, без других слов: проверка")
    if ans is None:
        return False, "Copilot не ответил (вход, сеть, селекторы страницы)"
    if "проверка" not in ans.casefold():
        return False, f"ответ не похож на ожидаемый, возможна проблема кодировки: {ans.strip()[:80]!r}"
    return True, "ответил, кириллица доходит"


def status(probe: bool = False) -> int:
    print("Codex CLI:", "есть" if shutil.which("codex") else "нет (scripts/codex_setup.py)")
    cmd = copilot_cmd()
    print("M365 Copilot (по умолчанию) или иной ассистент:",
          ("настроен" + (" (своя команда)" if os.environ.get("AI_TOOLKIT_COPILOT_CMD") else " (встроенный адаптер, вход выполнен)")) if cmd
          else f"не настроен: {copilot_why_not()}")
    if probe:
        ok, msg = probe_copilot() if cmd else (False, "пробу нечем делать")
        print(f"Проба Copilot: {'ok' if ok else 'НЕ ПРОЙДЕНА'}, {msg}")
    print("Модель без обучения:", "включена" if os.environ.get("OPENROUTER_API_KEY")
          and os.environ.get("AI_TOOLKIT_NOTRAIN") == "1" else "выключена (OPENROUTER_API_KEY + AI_TOOLKIT_NOTRAIN=1)")
    print(hints())
    return 0


def _report(kind: str, who: str, body: str) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    p = REPORTS / f"{datetime.now():%Y%m%d-%H%M%S-%f}-{os.getpid()}-{kind}-{who}.md"
    p.write_text(body, encoding="utf-8")
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rv = sub.add_parser("review")
    rv.add_argument("--repo", type=Path, default=Path.cwd())
    g = rv.add_mutually_exclusive_group(required=True)
    g.add_argument("--base")
    g.add_argument("--commit")
    ak = sub.add_parser("ask")
    ak.add_argument("text")
    ak.add_argument("--file", action="append", default=[], metavar="ПУТЬ", help="приложить файл (повторять)")
    for sp in (rv, ak):
        sp.add_argument("--risk", choices=["critical", "normal", "low"], default="normal",
                        help="critical: Codex первым; normal: ассистент первым, Codex резервом; low: без Codex")
    st = sub.add_parser("status")
    st.add_argument("--probe", action="store_true", help="живая проба Copilot коротким вопросом (вход и кодировка)")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        return status(a.probe)
    if a.cmd == "review":
        try:
            who, body = review(a.repo.resolve(), a.base, a.commit, risk=a.risk)
        except (RuntimeError, OSError) as e:
            print(f"Второе мнение недоступно: {e}")
            return 2
    else:
        who, body = ask(read_stdin_utf8() if a.text == "-" else a.text, files=a.file, risk=a.risk)
    if not who:
        print(f"Второе мнение недоступно: {body or COPILOT_LAST_WHY or 'ни одно звено цепочки не ответило, проверь `status`'}. Готовым не считать.")
        print(hints())
        return 2
    print(body)
    print(f"\nПроверял: {who}. Отчёт: {_report(a.cmd, who, body)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
