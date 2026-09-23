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
