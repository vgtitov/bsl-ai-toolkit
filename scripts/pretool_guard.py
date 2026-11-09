"""PreToolUse-хук Claude Code: частые утечки и сбои агента -> отказ до запуска инструмента.

1. Утечка `.env`: Read/Grep(content) по `.env`, shell-печать файла (cat, Get-Content, Select-String...).
   Замена: `scripts/env_keys.py` (только имена ключей).
2. Пароль в списке процессов: Win32_Process с CommandLine, wmic process, ps -ef/aux, tasklist /v,
   Get-Process с CommandLine. Замена: `scripts/proc_list.py` (строка с маской паролей).
3. Файл через here-doc / here-string (cat > f <<EOF, @'..'@ | Set-Content). Замена: инструмент Write/Edit.

Вход: JSON на stdin (`tool_name`, `tool_input`). Отказ: exit 2 и причина в stderr (её видит модель).
Правила узкие: ложный отказ хуже пропуска. Любая ошибка разбора -> exit 0 (не мешать работе).
Подключение: `adapters/claude/settings.json` (PreToolUse на Bash|PowerShell|Read|Grep); в своём проекте -
та же команда с путём `$ONEC_TOOLKIT_DIR/scripts/pretool_guard.py`.
"""
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
ENV_HINT = ("Вывод содержимого .env запрещён (секреты попадают в журнал сессии). "
            f"Имена ключей: `python \"{_HERE / 'env_keys.py'}\" [--filled] [путь к .env]`; "
            "значения читают сами скрипты, в вывод их не печатать.")
PROC_HINT = ("Командная строка процессов без маски печатает пароли (/P..., -p, --password). "
             f"Используй `python \"{_HERE / 'proc_list.py'}\" [--name 1cv8] [--grep <база>]` - строка с маской; "
             "или выводи только ProcessId/Name, а по CommandLine только фильтруй (-like/-match).")
HEREDOC_HINT = ("Файл через here-doc/here-string не создаём: кавычки, $ и кириллица ломают разбор. "
                "Используй инструмент Write (новый файл) или Edit (правка); многострочный скрипт - "
                "файлом через Write и запуск `python <файл>`.")

# .env, .env.local, путь/.env; не .env.example/.sample/.template, не .envrc, не $env:, не os.environ
_ENV_TOKEN = re.compile(r"""(?:^|(?<=[\s'"=/\\:<>|(,]))\.env((?:\.[\w-]+)*)(?=$|[\s'"|;&)<>,])""")
_SAFE_SUFFIX = {".example", ".sample", ".template"}

_PRINT_VERBS = re.compile(
    r"^(cat|type|more|less|head|tail|bat|nl|tac|strings|od|xxd|gc|get-content|select-string|sls"
    r"|grep|egrep|fgrep|rg|findstr|sed|awk|format-hex|import-csv)$", re.I)
# grep без вывода строк (только код возврата/счётчик/имя файла)
_QUIET_GREP = re.compile(r"(?<!\S)(-[a-zA-Z]*[qcl][a-zA-Z]*|--quiet|--count|--files-with-matches|-Quiet|-List)(?!\S)")


def env_file_mentioned(text: str) -> bool:
    return any(m.group(1).lower() not in _SAFE_SUFFIX for m in _ENV_TOKEN.finditer(text or ""))


def is_env_path(path: str) -> bool:
    name = re.split(r"[\\/]", path or "")[-1]
    m = re.fullmatch(r"\.env((?:\.[\w-]+)*)", name)
    return bool(m) and m.group(1).lower() not in _SAFE_SUFFIX


def strip_heredoc_bodies(cmd: str) -> str:
    """Убрать тела here-doc (bash) и here-string (PowerShell): там текст, а не команды."""
    out, lines, i = [], cmd.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = re.search(r"<<-?\s*(['\"]?)([\w.-]+)\1", line.replace("<<<", "   "))
        if m:
            delim, i = m.group(2), i + 1
            while i < len(lines) and lines[i].strip() != delim:
                i += 1
        i += 1
    text = "\n".join(out)
    return re.sub(r"@(['\"])\r?\n.*?\r?\n\1@", "@''@", text, flags=re.S)


def _segments(cmd: str):
    for seg in re.split(r"\$\(|`|[;\n|&(){}]+", cmd):
        seg = re.sub(r"^\s*(\$[\w:]+\s*=\s*|@)", "", seg).strip()
        if seg:
            yield seg


# обёртки перед настоящей командой и их опции, у которых есть значение (-u user, -n 1 ...)
_WRAPPERS = {
    "sudo": {"-u", "-g", "-C", "-p", "-U", "-h", "-r", "-t", "-D", "-R"},
    "env": {"-u", "-C", "-S"},
    "xargs": {"-I", "-n", "-L", "-d", "-P", "-s", "-E", "-a"},
    "command": set(), "nohup": set(), "time": set(), "exec": set(), "nice": {"-n"},
}


def command_word(seg: str) -> str:
    """Глагол сегмента без обёрток: sudo -u u / env X=Y / command / nohup / time / xargs -n1 / VAR=val."""
    words = [w.strip("'\"") for w in seg.split()]
    i = 0
    while i < len(words):
        w = words[i]
        if re.fullmatch(r"[A-Za-z_]\w*=.*", w):
            i += 1
            continue
        opts = _WRAPPERS.get(w.lower())
        if opts is None:
            return w.lower()
        i += 1
        while i < len(words) and (words[i].startswith("-") or re.fullmatch(r"[A-Za-z_]\w*=.*", words[i])):
            i += 2 if words[i] in opts else 1
    return ""


def check_env_shell(cmd: str):
    cmd = strip_heredoc_bodies(cmd)
    # команда в кавычках для удалённой/вложенной оболочки: ssh host "...", bash -c '...', -Command "..."
    nested = [m.group(2) for m in re.finditer(
        r"(?:\bssh\s+(?:-\S+\s+)*[^\s'\"-]\S*|\s-c|\s-Command)\s+(['\"])(.*?)\1", cmd, re.I | re.S)]
    for seg in [s for part in [cmd, *nested] for s in _segments(part)]:
        verb = command_word(seg)
        if verb == "git" or not _PRINT_VERBS.match(verb) or not env_file_mentioned(seg):
            continue
        if verb in ("grep", "egrep", "fgrep", "rg", "findstr", "select-string", "sls") and _QUIET_GREP.search(seg):
            continue
        return ENV_HINT
    return None


def check_process_list(cmd: str):
    c = strip_heredoc_bodies(cmd)
    if re.search(r"-replace\s*['\"][^'\"]*/P", c, re.I) or "proc_list.py" in c:
        return None  # уже с маской
    low = c.lower()
    # фильтр по CommandLine без вывода - безопасен; убираем сравнения и WQL-условия
    shown = re.sub(r"\$_\.commandline\s+-(?:c|i)?(?:not)?(?:like|match|eq|ne|contains)\b", "", low)
    shown = re.sub(r"commandline\s+(?:not\s+)?like\b", "", shown)
    star = re.search(r"(select(-object)?|fl|format-list|ft|format-table)\s+(-property\s+)?\*"
                     r"|convertto-(json|csv)|export-(csv|clixml)", low)
    if re.search(r"win32_process", low) and ("commandline" in shown or star):
        return PROC_HINT
    if re.search(r"\bwmic\b[^\n]*\bprocess\b", low):
        if ("commandline" in low or re.search(r"\blist\b|\bget\s+\*", low)
                or not re.search(r"\b(get|call|delete)\b", low)):
            return PROC_HINT
    for m in re.finditer(r"(?:^|[\s;&|(\"'])(ps|pgrep)((?:[ \t]+[^\s|;&)\"']+)*)", c):
        args = m.group(2).split()
        for i, a in enumerate(args):
            if m.group(1) == "pgrep":
                if re.fullmatch(r"-\w*a\w*|--list-full", a):
                    return PROC_HINT
            elif (re.fullmatch(r"-\w*[fF]\w*", a) or re.fullmatch(r"[auxwjlr]+", a)
                  or (re.match(r"-[a-zA-Z]*o", a) and re.search(r"args|command|cmd", a + " " + " ".join(args[i + 1:i + 2])))):
                return PROC_HINT
    if re.search(r"\btasklist\b[^\n]*\s/v\b", low):
        return PROC_HINT
    if re.search(r"\b(get-process|gps)\b", low) and (
            "commandline" in shown or "-includeusername" in low or star):
        return PROC_HINT
    return None


_HEREDOC_WRITERS = {"cat", "sed", "awk", "printf", "echo"}
_INTERPRETERS = {"py", "node", "bash", "sh", "zsh", "uv", "pwsh", "powershell", "perl", "ruby", "ssh"}
_REDIRECT = re.compile(r"(?<![0-9&])>>?\s*(?!&|/dev/null)['\"]?[\w./\\~$-]")
_TEE_FILE = re.compile(r"\btee\s+(-a\s+)?(?!/dev/null)['\"]?[\w./\\~$-]")


def check_heredoc_file(cmd: str):
    for line in cmd.split("\n"):
        if not re.search(r"<<(?!<)-?\s*['\"]?[\w.-]+", line):
            continue
        # первая строка here-doc: какая команда получает here-doc и куда уходит её вывод
        segs = [s for s in re.split(r"[|;&]+", line) if s.strip()]
        k = next(i for i, s in enumerate(segs) if "<<" in s)
        verb = command_word(segs[k])
        if verb in _INTERPRETERS or verb.startswith("python"):
            break  # here-doc - это скрипт; в файл пишется результат программы, а не текст
        if verb in _HEREDOC_WRITERS and _REDIRECT.search(segs[k]):
            return HEREDOC_HINT
        if verb == "tee" and _TEE_FILE.search(segs[k]):
            return HEREDOC_HINT
        if verb in _HEREDOC_WRITERS and k + 1 < len(segs) and _TEE_FILE.search(segs[k + 1]) \
                and command_word(segs[k + 1]) == "tee":
            return HEREDOC_HINT
        break  # дальше - тело here-doc
    if re.search(r"@['\"]\s*\r?\n", cmd):
        if re.search(r"\b(set-content|add-content|out-file|writealltext|writealllines)\b|\bnew-item\b[^\n]*-value"
                     r"|['\"]@\s*>>?\s*[\w.\\/$'\"]", cmd, re.I):
            return HEREDOC_HINT
    return None


def check(tool_name: str, tool_input: dict):
    ti = tool_input or {}
    if tool_name == "Read":
        return ENV_HINT if is_env_path(ti.get("file_path", "")) else None
    if tool_name == "Grep":
        target = is_env_path(ti.get("path", "")) or env_file_mentioned(ti.get("glob", ""))
        context = any(ti.get(k) for k in ("-A", "-B", "-C", "context"))
        if target and (ti.get("output_mode") == "content" or context):
            return ENV_HINT
        return None
    if tool_name in ("Bash", "PowerShell"):
        cmd = ti.get("command", "") or ""
        return check_env_shell(cmd) or check_process_list(cmd) or check_heredoc_file(cmd)
    return None


def main() -> int:
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        reason = check(data.get("tool_name", ""), data.get("tool_input") or {})
    except Exception:
        return 0
    if reason:
        print("[guard] " + reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
