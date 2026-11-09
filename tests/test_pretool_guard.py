"""PreToolUse-гард (scripts/pretool_guard.py), env_keys.py, proc_list.mask."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(rel):
    spec = importlib.util.spec_from_file_location(Path(rel).stem, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


guard = load("scripts/pretool_guard.py")
env_keys = load("scripts/env_keys.py")
proc_list = load("scripts/proc_list.py")


def sh(cmd, tool="Bash"):
    return guard.check(tool, {"command": cmd})


# ---------- .env ----------
@pytest.mark.parametrize("tool,ti", [
    ("Read", {"file_path": r"C:\work\repo\.env"}),
    ("Read", {"file_path": "/home/u/repo/.env.local"}),
    ("Grep", {"pattern": "GITLAB", "path": r"C:\repo\.env", "output_mode": "content"}),
    ("Grep", {"pattern": "GITLAB", "path": ".env", "-B": 1}),
    ("Grep", {"pattern": "GITLAB", "glob": ".env", "output_mode": "content"}),
])
def test_env_tools_blocked(tool, ti):
    assert guard.check(tool, ti) == guard.ENV_HINT


@pytest.mark.parametrize("tool,ti", [
    ("Read", {"file_path": r"C:\repo\.env.example"}),
    ("Read", {"file_path": r"C:\repo\.envrc"}),
    ("Read", {"file_path": r"C:\repo\scripts\env_keys.py"}),
    ("Grep", {"pattern": "GITLAB", "path": ".env"}),  # files_with_matches - только имя файла
    ("Grep", {"pattern": "GITLAB", "path": ".env", "output_mode": "count"}),
    ("Grep", {"pattern": "GITLAB", "path": ".env.example", "output_mode": "content"}),
    ("Grep", {"pattern": "x", "path": "scripts", "output_mode": "content"}),
])
def test_env_tools_allowed(tool, ti):
    assert guard.check(tool, ti) is None


@pytest.mark.parametrize("cmd,tool", [
    ("cat .env", "Bash"),
    ("cat ./.env | grep GITLAB", "Bash"),
    ("grep -n TOKEN .env", "Bash"),
    ("head -5 ~/repo/.env", "Bash"),
    ("sed -n 1,5p .env.local", "Bash"),
    ("export $(cat .env | xargs)", "Bash"),
    ("Get-Content .env", "PowerShell"),
    (r"gc C:\work\repo\.env | Select-Object -First 3", "PowerShell"),
    ("Select-String .env -Pattern GITLAB -Context 1,0", "PowerShell"),
    (r'Select-String -Path "C:\repo\.env" -Pattern "^GITLAB"', "PowerShell"),
    ("$lines = Get-Content .env; $lines", "PowerShell"),
    ("type .env", "PowerShell"),
    ("findstr TOKEN .env", "PowerShell"),
    ('ssh host1 "cat /opt/app/.env"', "Bash"),
    ("bash -c 'grep TOKEN .env'", "Bash"),
    # обёртки перед глаголом
    ("sudo -u u cat .env", "Bash"),
    ("sudo -E cat .env", "Bash"),
    ("command cat .env", "Bash"),
    ("env LC_ALL=C cat .env", "Bash"),
    ("env -i PATH=/bin cat .env", "Bash"),
    ("LC_ALL=C cat .env", "Bash"),
    ("nohup cat .env", "Bash"),
    ("time cat .env", "Bash"),
    ("xargs -n1 cat .env", "Bash"),
    ('powershell -Command "Get-Content .env"', "PowerShell"),
])
def test_env_shell_blocked(cmd, tool):
    assert sh(cmd, tool) == guard.ENV_HINT


@pytest.mark.parametrize("cmd,tool", [
    ("python scripts/mr_review.py status 5", "PowerShell"),
    ("uv run scripts/atlassian.py --env-file .env jira issue X", "Bash"),
    ("cat .env.example", "Bash"),
    ("Get-Content .env.example", "PowerShell"),
    ("git status .env", "Bash"),
    ('git commit -m "docs: cat .env запрещён"', "Bash"),
    ("Test-Path .env", "PowerShell"),
    ("grep -q GITLAB_TOKEN .env && echo yes", "Bash"),
    ("Select-String -Path .env -Pattern '^GITLAB_TOKEN=' -Quiet", "PowerShell"),
    ("$env:GITLAB_TOKEN | Out-Null; Get-Content README.md", "PowerShell"),
    ("python -c 'import os; os.environ'", "Bash"),
    ("set -a; . ./.env; set +a; python x.py", "Bash"),
    ("LC_ALL=C python scripts/env_keys.py .env", "Bash"),
    ("sudo -u u python x.py --env .env", "Bash"),
    ("env X=1 git status .env", "Bash"),
    ("cat <<'EOF' | git commit -F -\nfix: cat .env больше не печатаем\nEOF", "Bash"),
])
def test_env_shell_allowed(cmd, tool):
    assert sh(cmd, tool) is None


# ---------- процессы ----------
@pytest.mark.parametrize("cmd,tool", [
    ("Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine", "PowerShell"),
    ("Get-CimInstance Win32_Process -Filter \"Name='1cv8.exe'\" | fl *", "PowerShell"),
    ("Get-WmiObject Win32_Process | % { $_.CommandLine.Substring(0,80) }", "PowerShell"),
    ("gcim Win32_Process | ConvertTo-Json", "PowerShell"),
    ("wmic process where name='1cv8.exe' get processid,commandline", "PowerShell"),
    ("wmic process list full", "PowerShell"),
    ("wmic process", "PowerShell"),
    ("ps -ef | grep 1cv8", "Bash"),
    ("ps aux", "Bash"),
    ("ps -eo pid,args", "Bash"),
    ("ps -o pid -o args", "Bash"),
    ('ssh host1 "ps -ef"', "Bash"),
    ("pgrep -a ragent", "Bash"),
    ("tasklist /v", "PowerShell"),
    ("Get-Process 1cv8 | Select-Object Id,CommandLine", "PowerShell"),
    ("Get-Process -IncludeUserName", "PowerShell"),
    ("Get-Process | Select-Object *", "PowerShell"),
])
def test_process_blocked(cmd, tool):
    assert sh(cmd, tool) == guard.PROC_HINT


@pytest.mark.parametrize("cmd,tool", [
    ("Get-CimInstance Win32_Process -Filter \"Name='1cv8.exe'\" | "
     "Where-Object { $_.CommandLine -like '*MyBase*' } | Select-Object ProcessId,CreationDate", "PowerShell"),
    ("Get-CimInstance Win32_Process -Filter \"CommandLine LIKE '%MyBase%'\" | Select ProcessId", "PowerShell"),
    ("Get-CimInstance Win32_Process | Select-Object ProcessId,Name,"
     "@{n='c';e={$_.CommandLine -replace '(?i)/P(\"[^\"]*\"|\\S+)','/P***'}}", "PowerShell"),
    ("wmic process where name='1cv8.exe' get processid,name", "PowerShell"),
    ("ps", "Bash"),
    ("ps -e", "Bash"),
    ("ps -eo pid,comm", "Bash"),
    ("pgrep -l ragent", "Bash"),
    ("tasklist /fi \"imagename eq 1cv8.exe\"", "PowerShell"),
    ("Get-Process 1cv8 | Select-Object Id,CPU,StartTime", "PowerShell"),
    ("python scripts/proc_list.py --name 1cv8", "PowerShell"),
    ("git log --oneline -5", "Bash"),
])
def test_process_allowed(cmd, tool):
    assert sh(cmd, tool) is None


# ---------- here-doc ----------
@pytest.mark.parametrize("cmd,tool", [
    ("cat > scripts/x.py <<'EOF'\nprint(1)\nEOF", "Bash"),
    ("cat <<EOF > notes.md\nтекст\nEOF", "Bash"),
    ("cat <<-EOF >> log.txt\nx\nEOF", "Bash"),
    ("tee docs/a.md <<'EOF'\nx\nEOF", "Bash"),
    ("cat <<'EOF' | tee -a out.txt\nx\nEOF", "Bash"),
    ("sed 's/a/b/' <<'EOF' > out.txt\na\nEOF", "Bash"),
    ("awk '{print}' <<EOF >> out.txt\na\nEOF", "Bash"),
    ("printf '%s\\n' <<EOF > f.txt\na\nEOF", "Bash"),
    ("echo <<EOF > f.txt\na\nEOF", "Bash"),
    ("@'\nprint(1)\n'@ | Set-Content -Encoding utf8 x.py", "PowerShell"),
    ("$s = @\"\nтекст\n\"@\nOut-File -FilePath a.md -InputObject $s", "PowerShell"),
    ("@'\nx\n'@ > a.txt", "PowerShell"),
])
def test_heredoc_blocked(cmd, tool):
    assert sh(cmd, tool) == guard.HEREDOC_HINT


@pytest.mark.parametrize("cmd,tool", [
    ("git commit -F - <<'EOF'\nfeat: x\n\ncat > f <<x\nEOF", "Bash"),
    ("cat <<'EOF' | git commit -F -\nmsg\nEOF", "Bash"),
    ("python - <<'EOF'\nprint(1)\nEOF", "Bash"),
    # интерпретатор исполняет here-doc; в файл - результат программы
    ("python - <<'EOF' > out.json\nprint(1)\nEOF", "Bash"),
    ("python3 - <<'EOF' >> log.txt\nprint(1)\nEOF", "Bash"),
    ("bash -s <<'EOF' > out.txt\nls\nEOF", "Bash"),
    ("sh -s <<'EOF' > out.txt\nls\nEOF", "Bash"),
    ("node - <<'EOF' > out.txt\nconsole.log(1)\nEOF", "Bash"),
    ("uv run --with lxml python - <<'EOF' > out.txt\nprint(1)\nEOF", "Bash"),
    ("python - <<'EOF' | tee out.txt\nprint(1)\nEOF", "Bash"),
    ("sed 's/a/b/' <<'EOF'\na\nEOF", "Bash"),
    ("ssh host1 'bash -s' <<'EOF'\nls\nEOF", "Bash"),
    ("cat <<EOF\nhello\nEOF", "Bash"),
    ("cat <<EOF 2>&1\nx\nEOF", "Bash"),
    ("grep x <<< \"$var\" > out.txt", "Bash"),
    ("git commit -m @'\nfeat: x\n'@", "PowerShell"),
    ("Set-Content -Path a.txt -Value 'one line'", "PowerShell"),
])
def test_heredoc_allowed(cmd, tool):
    assert sh(cmd, tool) is None


def test_other_tools_ignored():
    assert guard.check("Write", {"file_path": ".env", "content": "X="}) is None
    assert guard.check("Edit", {"file_path": ".env"}) is None


def run_hook(payload: str):
    return subprocess.run([sys.executable, str(ROOT / "scripts/pretool_guard.py")],
                          input=payload.encode("utf-8"), capture_output=True)


def test_hook_exit_codes():
    blocked = run_hook(json.dumps({"tool_name": "Read", "tool_input": {"file_path": ".env"}}))
    assert blocked.returncode == 2
    assert "env_keys.py" in blocked.stderr.decode("utf-8")
    assert run_hook(json.dumps({"tool_name": "Read", "tool_input": {"file_path": "a.py"}})).returncode == 0
    assert run_hook("не json").returncode == 0  # сломанный вход не блокирует работу


def test_settings_registers_hook():
    raw = (ROOT / "adapters/claude/settings.json").read_text(encoding="utf-8")
    assert raw == (ROOT / ".claude/settings.json").read_text(encoding="utf-8")
    settings = json.loads(raw)
    entry = settings["hooks"]["PreToolUse"][0]
    assert set(entry["matcher"].split("|")) == {"Bash", "PowerShell", "Read", "Grep"}
    cmd = entry["hooks"][0]["command"]
    assert "scripts/pretool_guard.py" in cmd and "exit 2" in cmd and "uv run" in cmd
    assert "PostToolUse" in settings["hooks"] and "SessionStart" in settings["hooks"]


# ---------- env_keys ----------
def test_env_keys_no_values(tmp_path):
    f = tmp_path / ".env"
    f.write_text("\ufeff# комментарий\nGITLAB_TOKEN=glpat-SECRET\nEMPTY=\nexport JIRA_PAT='p@ss'\n"
                 "SD_CONFIG=config/x.toml   # путь\nQUOTED=\"\"\n", encoding="utf-8")
    assert env_keys.parse_keys(f.read_text(encoding="utf-8")) == [
        ("GITLAB_TOKEN", True), ("EMPTY", False), ("JIRA_PAT", True), ("SD_CONFIG", True), ("QUOTED", False)]
    out = subprocess.run([sys.executable, str(ROOT / "scripts/env_keys.py"), "--filled", str(f)],
                         capture_output=True).stdout.decode("utf-8")
    assert "GITLAB_TOKEN" in out and "SECRET" not in out and "p@ss" not in out


# ---------- маска процессов ----------
@pytest.mark.parametrize("raw,secret", [
    (r'"C:\Program Files\1cv8\bin\1cv8.exe" DESIGNER /S srv\base /N user /PSecr3t2026 /Out x', "Secr3t2026"),
    (r'1cv8.exe DESIGNER /N user /P "my pass" /DisableStartupDialogs', "my pass"),
    (r'1cv8.exe DESIGNER /N user /P"q w"', "q w"),
    (r"1cv8.exe /P secret123 /N u", "secret123"),
    (r"1cv8.exe /ConfigurationRepositoryF tcp://x /ConfigurationRepositoryN u /ConfigurationRepositoryPhunter2", "hunter2"),
    ("mysql -u root -pS3cr3t db", "S3cr3t"),
    ("psql --password=abc123 -h x", "abc123"),
    ("tool --pwd zzz9", "zzz9"),
    ("Connect-X -Password 'Pa55' -User u", "Pa55"),
    ('Srvr="x";Ref="b";Usr="u";Pwd="topsecret";', "topsecret"),
    ("dsn password=qwerty host=x", "qwerty"),
])
def test_mask_hides_password(raw, secret):
    masked = proc_list.mask(raw)
    assert secret not in masked
    assert "***" in masked


def test_mask_keeps_harmless_parts():
    masked = proc_list.mask(r'"C:\Program Files\1cv8\bin\1cv8.exe" ENTERPRISE /S srv\MyBase /N user /Ppw')
    assert r"C:\Program Files\1cv8" in masked and "MyBase" in masked and "/N user" in masked
