# DISCIPLINE_ALLOW_TEST_EDIT — первичное создание тестов контракта bsl-ls (не подгонка под код)
"""Тесты MCP-моста bsl-ls: два поведения, из-за которых проверка молча «зеленела».

1. `analyze -s` у BSL Language Server принимает КАТАЛОГ: если дать путь к файлу,
   jar отработает с exit 0 и пустым отчётом. Мост обязан подсунуть одиночный файл
   через временный каталог.
2. Отчёт с нулём файлов — это НЕ «чисто», а непроведённая проверка (обычно кривой
   путь). Мост обязан сказать об этом явно, иначе агент рапортует «чисто» вхолостую.

Запуск:  uv run --with mcp --with pytest pytest -q tests/test_bsl_ls_mcp.py
"""
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parents[1] / "mcp"


def _load(monkeypatch, jar):
    monkeypatch.setenv("BSL_JAR", str(jar))
    sys.path.insert(0, str(MCP_DIR))
    return importlib.reload(importlib.import_module("bsl_ls_mcp"))


def _fake_jar(tmp_path):
    jar = tmp_path / "bsl-language-server-exec.jar"
    jar.write_bytes(b"fake")
    return jar


def _stub_run(report, seen):
    """Подменяет запуск java: пишет заданный отчёт в каталог -o, запоминает -s и cwd."""

    def run(cmd, **kwargs):
        seen["cmd"] = cmd
        src = cmd[cmd.index("-s") + 1]
        out = cmd[cmd.index("-o") + 1]
        seen["cwd"] = kwargs.get("cwd")
        seen["src_arg"] = src
        # java запускается из cwd — реальный каталог сканирования отсюда и считаем
        seen["src"] = os.path.abspath(os.path.join(seen["cwd"] or os.getcwd(), src))
        seen["src_files"] = sorted(os.listdir(seen["src"])) if os.path.isdir(seen["src"]) else None
        Path(out, "bsl-json.json").write_text(json.dumps(report), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return run


def test_single_file_analyzed_via_temp_dir(tmp_path, monkeypatch):
    """Одиночный .bsl доходит до jar как каталог, содержащий этот файл."""
    mod = _load(monkeypatch, _fake_jar(tmp_path))
    target = tmp_path / "Module.bsl"
    target.write_text("Процедура П() КонецПроцедуры\n", encoding="utf-8")
    report = {"fileinfos": [{"path": str(target), "diagnostics": []}]}
    seen = {}
    monkeypatch.setattr(mod.subprocess, "run", _stub_run(report, seen))

    out = mod.bsl_analyze(str(target))

    # каталог существовал в момент вызова jar (снимок содержимого сделал стаб)
    assert seen["src"] != str(target), "в jar ушёл путь-файл — отчёт будет пустым"
    assert seen["src_files"] == ["Module.bsl"]
    assert "[bsl-ls] чисто:" in out
    assert not os.path.exists(seen["src"]), "временный каталог не убран"


def test_zero_files_is_not_clean(tmp_path, monkeypatch):
    """Пустой отчёт (0 файлов) — предупреждение, а не «чисто»."""
    mod = _load(monkeypatch, _fake_jar(tmp_path))
    empty_dir = tmp_path / "пусто"
    empty_dir.mkdir()
    seen = {}
    monkeypatch.setattr(mod.subprocess, "run", _stub_run({"fileinfos": []}, seen))

    out = mod.bsl_analyze(str(empty_dir))

    assert "[bsl-ls] чисто:" not in out
    assert "НЕ ВЫПОЛНЕНА" in out


def test_cyrillic_dir_not_passed_as_argument(tmp_path, monkeypatch):
    """Путь с кириллицей НЕ уходит в аргумент java.

    Реальный дефект: java на Windows получает кириллицу в argv как «?», и BSL LS
    молча анализирует ДРУГОЙ каталог той же длины имени — отчёт приходит по чужому
    модулю. Лечится запуском из целевого каталога с `-s .`.
    """
    mod = _load(monkeypatch, _fake_jar(tmp_path))
    target = tmp_path / "askПартнерыСервер"
    target.mkdir()
    (target / "Module.bsl").write_text("Процедура П() КонецПроцедуры\n", encoding="utf-8")
    report = {"fileinfos": [{"path": str(target / "Module.bsl"), "diagnostics": []}]}
    seen = {}
    monkeypatch.setattr(mod.subprocess, "run", _stub_run(report, seen))

    mod.bsl_analyze(str(target))

    assert seen["src_arg"].isascii(), "кириллица ушла в argv java — проанализируется чужой каталог"
    assert os.path.samefile(seen["src"], target), "java запущен не над целевым каталогом"


def test_non_ascii_output_path_reported_clearly(tmp_path, monkeypatch):
    """Не-ASCII путь -o (кириллица в TEMP) java не переживает: java падает, отчёта нет.

    Мост обязан назвать причину, а не отдать глухое «BSL LS не создал отчёт».
    Если ОС даёт короткое 8.3-имя — путь чинится сам и проверка не нужна.
    """
    mod = _load(monkeypatch, _fake_jar(tmp_path))
    src = tmp_path / "src"
    src.mkdir()
    (src / "Module.bsl").write_text("А = 1;\n", encoding="utf-8")
    non_ascii_out = tmp_path / "врЕменные файлы"
    non_ascii_out.mkdir()
    monkeypatch.setattr(mod.tempfile, "mkdtemp", lambda **kw: str(non_ascii_out))
    # том без 8.3-имён: починить путь нечем
    monkeypatch.setattr(mod, "_ascii_safe", lambda p: p)
    # java «падает», как на живой системе: ничего не пишет в -o
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "Exception"))

    out = mod.bsl_analyze(str(src))

    assert "не-ASCII" in out, out


def test_ascii_safe_keeps_ascii_paths_untouched(tmp_path, monkeypatch):
    """ASCII-пути не трогаем (на macOS/Linux — вообще ничего не меняем)."""
    mod = _load(monkeypatch, _fake_jar(tmp_path))
    p = str(tmp_path / "plain")
    assert mod._ascii_safe(p) == p


def test_diagnostics_are_reported(tmp_path, monkeypatch):
    """Диагностики попадают в ответ с важностью, строкой и кодом."""
    mod = _load(monkeypatch, _fake_jar(tmp_path))
    src = tmp_path / "src"
    src.mkdir()
    (src / "Module.bsl").write_text("А = 1;\n", encoding="utf-8")
    report = {"fileinfos": [{"path": str(src / "Module.bsl"), "diagnostics": [
        {"severity": "Error", "code": "CanonicalSpellingKeywords",
         "message": "Ошибка", "range": {"start": {"line": 0}}},
    ]}]}
    monkeypatch.setattr(mod.subprocess, "run", _stub_run(report, {}))

    out = mod.bsl_analyze(str(src))

    assert "Error Module.bsl:1 [CanonicalSpellingKeywords]" in out
