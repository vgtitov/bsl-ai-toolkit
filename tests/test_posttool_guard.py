"""posttool_guard: хук Claude Code берёт путь из JSON на STDIN и отдаёт находки через additionalContext."""
import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "scripts" / "posttool_guard.py"

LOOP = ("Процедура П()\n"
        "    Для Каждого Стр Из Таблица Цикл\n"
        "        Запрос = Новый Запрос;\n"
        "        Результат = Запрос.Выполнить();\n"
        "    КонецЦикла;\n"
        "КонецПроцедуры\n")


def _hook(payload) -> str:
    return subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True,
                          text=True, encoding="utf-8").stdout


def test_query_in_loop_reaches_agent_context(tmp_path):
    f = tmp_path / "Module.bsl"; f.write_text(LOOP, encoding="utf-8")
    out = _hook(json.dumps({"tool_input": {"file_path": str(f)}}))
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "bsl_guard" in ctx


def test_clean_file_and_foreign_input_are_silent(tmp_path):
    f = tmp_path / "Module.bsl"; f.write_text("Процедура П()\nКонецПроцедуры\n", encoding="utf-8")
    assert _hook(json.dumps({"tool_input": {"file_path": str(f)}})) == ""
    assert _hook("не json") == ""
    assert _hook(json.dumps({"tool_input": {"file_path": str(tmp_path / "нет.bsl")}})) == ""


def test_cyrillic_path_survives_non_utf8_stdio(tmp_path):
    # Windows: stdio в cp1251/cp1252, путь с кириллицей (имена объектов 1С) декодировался неверно,
    # файл «не находился», хук молча ничего не проверял.
    import os
    d = tmp_path / "Справочник"; d.mkdir()
    f = d / "Модуль.bsl"; f.write_text(LOOP, encoding="utf-8")
    payload = json.dumps({"tool_input": {"file_path": str(f)}}, ensure_ascii=False).encode("utf-8")
    for enc in ("cp1251", "cp1252"):
        env = dict(os.environ, PYTHONUTF8="0", PYTHONIOENCODING=enc)
        r = subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, env=env)
        assert r.returncode == 0
        ctx = json.loads(r.stdout.decode("utf-8"))["hookSpecificOutput"]["additionalContext"]
        assert "bsl_guard" in ctx, enc


def test_child_timeout_fits_hook_timeout(monkeypatch):
    # settings.json даёт хуку 60 с; дочерние проверки не вправе просить больше, чем осталось.
    import importlib.util
    spec = importlib.util.spec_from_file_location("posttool_guard", HOOK)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    seen = []

    def fake(cmd, **kw):
        seen.append(kw["timeout"])
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])
    monkeypatch.setattr(m.subprocess, "run", fake)
    assert m._run("bsl_guard.py", "x.bsl") == (0, "")
    assert seen and seen[0] <= m.BUDGET <= 55
    m._T0 -= m.BUDGET                       # бюджет исчерпан: дочерний процесс не запускается
    seen.clear()
    assert m._run("bsl_guard.py", "x.bsl") == (0, "") and not seen


def test_settings_copies_match_and_posttool_never_errors_without_interpreter():
    root = HOOK.parents[1]
    a = (root / "adapters/claude/settings.json").read_text(encoding="utf-8")
    assert a == (root / ".claude/settings.json").read_text(encoding="utf-8")
    cmd = json.loads(a)["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    assert cmd.rstrip().endswith("|| true")      # нет ни python, ни uv: не шум «exit 127» после каждой правки
