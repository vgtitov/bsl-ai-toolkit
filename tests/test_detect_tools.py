# DISCIPLINE_ALLOW_TEST_EDIT — первичное создание тестов на JDK-версию и дефолт BSL_LS_VERSION
"""Тесты `scripts/detect_tools.py`: реальная проверка версии JDK для bsl-ls (issue #6).

bsl-language-server >=1.0 требует JDK 21+ (class file version 65) — простого наличия `java`
недостаточно, скрипт обязан распознать РЕАЛЬНУЮ мажорную версию по выводу `java -version` и не
молчать про несовместимость. Проверено живьём на Zulu 17 (падает: UnsupportedClassVersionError)
и Zulu 21.0.8 (работает) — см. docs/design/2026-08-12-bsl-ls-mcp-mode.md.
"""
import importlib
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
detect_tools = importlib.import_module("detect_tools")


def test_default_bsl_ls_version_is_1_0_x(monkeypatch):
    """Дефолт пинится на релиз >=1.0 (MCP-режим появился в 1.0.0) — не старый 0.28.5 без MCP."""
    monkeypatch.delenv("BSL_LS_VERSION", raising=False)
    mod = importlib.reload(detect_tools)
    assert mod._BSL_LS_V.startswith("1."), f"ожидали ветку 1.x, получили {mod._BSL_LS_V!r}"


def _stub_version_output(text, on_stdout=True):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, text if on_stdout else "", "" if on_stdout else text)
    return run


def test_java_major_version_new_format_21(monkeypatch):
    monkeypatch.setattr(
        detect_tools.subprocess, "run",
        _stub_version_output('openjdk version "21.0.8" 2025-07-15 LTS\n', on_stdout=False))
    assert detect_tools.java_major_version("java") == 21


def test_java_major_version_new_format_17_insufficient(monkeypatch):
    monkeypatch.setattr(
        detect_tools.subprocess, "run",
        _stub_version_output('openjdk version "17.0.18" 2026-01-20 LTS\n', on_stdout=False))
    v = detect_tools.java_major_version("java")
    assert v == 17
    assert v < 21  # порог, ниже которого bsl-ls >=1.0 не стартует


def test_java_major_version_old_format_1_8(monkeypatch):
    """До Java 9 версия писалась как 1.X.0_yyy — реальная мажорная версия в ВТОРОМ числе."""
    monkeypatch.setattr(
        detect_tools.subprocess, "run",
        _stub_version_output('java version "1.8.0_392"\n', on_stdout=False))
    assert detect_tools.java_major_version("java") == 8


def test_java_major_version_unparseable_returns_none(monkeypatch):
    monkeypatch.setattr(
        detect_tools.subprocess, "run",
        _stub_version_output("не java вовсе, случайный текст\n", on_stdout=False))
    assert detect_tools.java_major_version("java") is None


def test_java_major_version_process_error_returns_none(monkeypatch):
    def raise_run(cmd, **kwargs):
        raise FileNotFoundError("no such file")
    monkeypatch.setattr(detect_tools.subprocess, "run", raise_run)
    assert detect_tools.java_major_version("no-such-java-binary") is None
