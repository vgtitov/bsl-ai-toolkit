# DISCIPLINE_ALLOW_TEST_EDIT: новый red-набор TDD для doctor.py
"""TDD для doctor.py — чистые хелперы: подстановка ${ENV} и план проверки MCP-сервера по типу."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "doctor", Path(__file__).parent.parent / "scripts" / "doctor.py")
doctor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(doctor)


# DISCIPLINE_ALLOW_TEST_EDIT: новые тесты — защита от опасных действий и текст по 401
# ---------- защита от опасных действий (виснут батч-прогоны ступени 2) ----------

def _platform(monkeypatch, tmp_path, conf_text=None):
    """Подсунуть doctor'у каталог «установленной платформы» с conf.cfg или без него."""
    root = tmp_path / "1cv8" / "8.3.27.2214"
    root.mkdir(parents=True)
    if conf_text is not None:
        conf = tmp_path / "1cv8" / "conf"
        conf.mkdir()
        (conf / "conf.cfg").write_text(conf_text, encoding="utf-8")

    import sys
    import types
    fake = types.ModuleType("detect_tools")
    fake.find_platform = lambda: root
    monkeypatch.setitem(sys.modules, "detect_tools", fake)


def test_unsafe_protection_warns_without_mask(monkeypatch, tmp_path):
    _platform(monkeypatch, tmp_path, conf_text="SystemLanguage=RU\n")
    res = doctor.check_unsafe_action_protection()
    assert len(res) == 1
    status, name, detail = res[0]
    assert status == doctor.WARN                       # мягко: флаг мог быть снят у пользователя
    assert "setup-actions-required" in detail          # человеку сказано, куда идти


def test_unsafe_protection_ok_with_mask(monkeypatch, tmp_path):
    _platform(monkeypatch, tmp_path,
              conf_text="SystemLanguage=RU\nDisableUnsafeActionProtection=*Test*\n")
    assert doctor.check_unsafe_action_protection()[0][0] == doctor.OK


def test_unsafe_protection_warns_when_conf_missing(monkeypatch, tmp_path):
    _platform(monkeypatch, tmp_path, conf_text=None)   # conf.cfg вообще нет
    assert doctor.check_unsafe_action_protection()[0][0] == doctor.WARN


def test_unsafe_protection_silent_without_platform(monkeypatch):
    import sys
    import types
    fake = types.ModuleType("detect_tools")
    fake.find_platform = lambda: None
    monkeypatch.setitem(sys.modules, "detect_tools", fake)
    assert doctor.check_unsafe_action_protection() == []   # платформы нет — вопрос не стоит


# ---------- resolve_env ----------

def test_resolve_plain():
    assert doctor.resolve_env("http://127.0.0.1:8765/mcp", {}) == "http://127.0.0.1:8765/mcp"


def test_resolve_var():
    assert doctor.resolve_env("${FOO}", {"FOO": "bar"}) == "bar"


def test_resolve_missing_is_empty():
    assert doctor.resolve_env("${MISSING}", {}) == ""


def test_resolve_default_when_missing():
    assert doctor.resolve_env("${MISSING:-def}", {}) == "def"


def test_resolve_default_ignored_when_set():
    assert doctor.resolve_env("${X:-def}", {"X": "real"}) == "real"


def test_resolve_mixed():
    assert doctor.resolve_env("${A}/x/${B:-y}", {"A": "p"}) == "p/x/y"


def test_env_refs():
    assert doctor.env_refs("Bearer ${TOK}") == ["TOK"]
    assert set(doctor.env_refs("${A}/${B:-z}")) == {"A", "B"}
    assert doctor.env_refs("нет переменных") == []


# ---------- plan_for_server ----------

def test_plan_http():
    p = doctor.plan_for_server("edt", {"type": "http", "url": "http://127.0.0.1:8765/mcp"}, {})
    assert p["kind"] == "http"
    assert p["target"] == "http://127.0.0.1:8765/mcp"


def test_plan_sse_collects_env():
    spec = {"type": "sse", "url": "${ONEC_MCP_URL}",
            "headers": {"Authorization": "Bearer ${ONEC_MCP_TOKEN}"}}
    p = doctor.plan_for_server("onec-code", spec, {"ONEC_MCP_URL": "https://c/mcp", "ONEC_MCP_TOKEN": "t"})
    assert p["kind"] == "sse"
    assert p["target"] == "https://c/mcp"
    assert set(p["needs_env"]) == {"ONEC_MCP_URL", "ONEC_MCP_TOKEN"}


def test_plan_stdio_file_from_args():
    spec = {"type": "stdio", "command": "uv", "args": ["run", "--quiet", "${BSL_LS_MCP}"]}
    p = doctor.plan_for_server("bsl-ls", spec, {"BSL_LS_MCP": "/x/bsl_ls_mcp.py"})
    assert p["kind"] == "stdio"
    assert p["command"] == "uv"
    assert p["file"] == "/x/bsl_ls_mcp.py"


def test_plan_stdio_jar_after_dash_jar():
    spec = {"type": "stdio", "command": "java",
            "args": ["-Dfile.encoding=UTF-8", "-jar", "${BSL_PLATFORM_JAR}", "--platform-path", "${P}"]}
    p = doctor.plan_for_server("bsl-platform", spec, {"BSL_PLATFORM_JAR": "/x/bp.jar"})
    assert p["file"] == "/x/bp.jar"
