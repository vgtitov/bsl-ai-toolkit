#!/usr/bin/env python3
"""ponytail_setup: не затирает чужие настройки, идемпотентен, без CLI ничего не ставит.

Запуск:  python3 tests/test_ponytail_setup.py   или   pytest tests/test_ponytail_setup.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ponytail_setup as ps  # noqa: E402


def test_generated_off_is_not_opt_out():
    saved = dict(os.environ)
    try:
        with tempfile.TemporaryDirectory() as t:
            os.environ["XDG_CONFIG_HOME"] = t
            cfg = ps.ponytail_config_path()
            ps.set_default_mode("off", dry=False)        # аналитик: off пишет установщик
            assert not ps.user_turned_off()
            os.utime(cfg, ns=(cfg.stat().st_atime_ns, cfg.stat().st_mtime_ns + 10**9))
            assert ps.user_turned_off()                  # конфиг трогал человек: его off — отказ
            ps.marker_path().unlink()
            cfg.write_text(json.dumps({"defaultMode": "off"}))
            assert ps.user_turned_off()                  # off без метки — выбор человека
            cfg.write_text(json.dumps({"defaultMode": "lite"}))
            assert not ps.user_turned_off()
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_rule_block_rejects_broken_markers():
    saved_src = ps.RULE_SRC
    try:
        with tempfile.TemporaryDirectory() as t:
            src = Path(t) / "AGENTS.md"
            src.write_text("x\n" + ps.BEGIN + "\nправило\nхвост без конца\n")
            ps.RULE_SRC = src
            try:
                ps.rule_block()
                assert False, "блок без END не должен приниматься"
            except ValueError:
                pass
            proj = Path(t) / "proj"
            proj.mkdir()
            assert "пропуск" in ps.write_rule(proj, dry=True)
            assert not (proj / "AGENTS.md").exists()
    finally:
        ps.RULE_SRC = saved_src


def test_claude_disabled_odd_settings():
    saved = dict(os.environ)
    try:
        with tempfile.TemporaryDirectory() as t:
            os.environ["CLAUDE_CONFIG_DIR"] = t
            for body in ("[]", '"str"', '{"enabledPlugins": ["a"]}', "null"):
                (Path(t) / "settings.json").write_text(body)
                assert not ps.claude_disabled(), body
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _fake_plugin(root, ver):
    h = Path(root) / "plugins" / "cache" / "ponytail" / "ponytail" / ver / "hooks"
    h.mkdir(parents=True)
    (h / "ponytail-mode-tracker.js").write_text("process.stdin.resume();setTimeout(()=>process.exit(0),SLEEP)")
    return h


def test_check_hook_reports_version_and_slow_start():
    import shutil
    if not shutil.which("node"):
        return
    saved = dict(os.environ)
    saved_limit = ps.SLOW_HOOK_SEC
    try:
        with tempfile.TemporaryDirectory() as t:
            os.environ["CLAUDE_CONFIG_DIR"] = t
            assert ps.installed_plugin() is None and ps.check_hook() == []
            _fake_plugin(t, "5.3.2").joinpath("ponytail-mode-tracker.js").write_text("process.exit(0)")
            h = _fake_plugin(t, "10.0.1")                     # 10 > 5 числом, а не строкой
            h.joinpath("ponytail-mode-tracker.js").write_text("setTimeout(()=>process.exit(0),700)")
            assert ps.installed_plugin()[0] == "10.0.1"
            ps.SLOW_HOOK_SEC = 0.3                            # хук 0.7 с дольше порога
            text = "\n".join(ps.check_hook())
            assert "версия плагина Ponytail: 10.0.1" in text and "проверена" in text
            assert "[!] медленно" in text and "Что сделать" in text and "версию плагина" in text
            ps.SLOW_HOOK_SEC = 30
            assert "[!]" not in "\n".join(ps.check_hook())
    finally:
        ps.SLOW_HOOK_SEC = saved_limit
        os.environ.clear()
        os.environ.update(saved)


def test_slow_shell_is_reported():
    import shutil
    if not shutil.which("node"):
        return
    saved_probes, saved_limit, saved_env = ps.shell_probes, ps.SLOW_HOOK_SEC, dict(os.environ)
    try:
        with tempfile.TemporaryDirectory() as t:
            os.environ["CLAUDE_CONFIG_DIR"] = t
            _fake_plugin(t, "5.3.2").joinpath("ponytail-mode-tracker.js").write_text("process.exit(0)")
            ps.SLOW_HOOK_SEC = 0.3
            ps.shell_probes = lambda: [("медленная", [sys.executable, "-c", "import time; time.sleep(0.6)"]),
                                       ("быстрая", [sys.executable, "-c", "pass"])]
            ps.SLOW_HOOK_SEC = 0.4
            text = "\n".join(ps.check_hook())
            assert "оболочка медленная:" in text and "[!] медленная оболочка (медленная)" in text and "профил" in text
            assert "быстрая)" not in text
        assert saved_probes() == [] or os.name == "nt"
    finally:
        ps.shell_probes, ps.SLOW_HOOK_SEC = saved_probes, saved_limit
        os.environ.clear()
        os.environ.update(saved_env)


def test_all():
    saved = dict(os.environ)
    try:
        _run()
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _run():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        os.environ.pop("BITRIX_ROLE", None)
        os.environ.pop("ONEC_ACTIVE_PROFILE", None)
        os.environ.update(XDG_CONFIG_HOME=str(t / "xdg"), CLAUDE_CONFIG_DIR=str(t / "claude"),
                          CODEX_HOME=str(t / "codex"), PATH=str(t / "empty"))

        # выключенный руками плагин распознаётся
        (t / "claude").mkdir()
        (t / "claude" / "settings.json").write_text('{"enabledPlugins": {"ponytail@ponytail": false}}')
        assert ps.claude_disabled()
        (t / "codex").mkdir()
        (t / "codex" / "config.toml").write_text('[plugins."ponytail@ponytail"]\nenabled = false\n\n[x]\ny = 1\n')
        assert ps.codex_disabled()
        (t / "codex" / "config.toml").write_text('[plugins."ponytail@ponytail"]\nenabled = true\n[x]\nenabled = false\n')
        assert not ps.codex_disabled()

        # без установленного плагина режим не пишется
        cfg = t / "xdg" / "ponytail" / "config.json"
        ps.main(["--mode", "lite"])
        assert not cfg.exists()

        # фальшивый claude: «установка» успешна; codex по-прежнему выключен руками
        (t / "empty").mkdir()
        if os.name == "nt":                                  # на Windows запускаемый файл — .cmd
            fake = t / "empty" / "claude.cmd"
            fake.write_text(f"@echo off\r\necho %* >> {t}\\calls\r\nexit /b 0\r\n")
        else:
            fake = t / "empty" / "claude"
            fake.write_text(f"#!/bin/sh\necho \"$@\" >> {t}/calls\nexit 0\n")
            fake.chmod(0o755)
        (t / "claude" / "settings.json").write_text("{}")

        # режим пишется один раз, чужие ключи и выбор пользователя сохраняются
        cfg.parent.mkdir(parents=True)
        cfg.write_text('{"hideStatus": true}')
        ps.main(["--mode", "lite"])
        assert json.loads(cfg.read_text()) == {"hideStatus": True, "defaultMode": "lite"}
        ps.main(["--mode", "full"])
        assert json.loads(cfg.read_text())["defaultMode"] == "lite"

        # режим по роли: аналитик -> off, остальные и без роли -> lite; явный --mode главнее
        assert ps.mode_for_role("analyst") == "off"
        assert ps.mode_for_role("franchise") == ps.mode_for_role("") == ps.mode_for_role("developer") == "lite"

        # выключил через /ponytail default off: обновление плагин не трогает
        cfg.write_text('{"defaultMode": "off"}')
        (t / "calls").unlink()
        ps.main([])
        assert not (t / "calls").exists()

        # правило: блок дописывается, повторный запуск его заменяет, а не дублирует
        proj = t / "proj"
        proj.mkdir()
        (proj / "AGENTS.md").write_text("# мои правила\n")
        ps.main(["--no-plugin", "--project", str(proj)])
        ps.main(["--no-plugin", "--project", str(proj)])
        text = (proj / "AGENTS.md").read_text()
        assert text.startswith("# мои правила\n") and text.count(ps.BEGIN) == 1
        assert "Ponytail" in text

        # роль аналитика: его документ в AGENTS.md проекта не сокращается, правило только дописывается
        passport = "# Паспорт задачи\n\n" + "".join(f"## Раздел {i}\nТребование {i}: подробное описание.\n\n" for i in range(50))
        (proj / "AGENTS.md").write_text(passport)
        ps.main(["--no-plugin", "--project", str(proj)])
        assert (proj / "AGENTS.md").read_text().startswith(passport)
        block = ps.rule_block()
        assert "касаются только кода" in block and "Документы не сокращаются" in block
        assert "Для аналитика" in block
        for role in ("analyst", "tester"):
            skill = next((ps.ROOT / "core" / "skills").glob(f"*-{role}")) / "SKILL.md"
            assert "Ponytail здесь не сокращает" in skill.read_text(encoding="utf-8"), skill

        # повреждённые метки не трогаем, несуществующий каталог не валит
        (proj / "AGENTS.md").write_text(ps.BEGIN + "\nстарое\n")
        ps.main(["--no-plugin", "--project", str(proj)])
        assert (proj / "AGENTS.md").read_text() == ps.BEGIN + "\nстарое\n"
        ps.main(["--no-plugin", "--project", str(t / "nope")])

        # каталог самого набора не трогаем
        before = (ps.ROOT / "AGENTS.md").read_bytes()
        ps.main(["--no-plugin", "--project", str(ps.ROOT)])
        assert (ps.ROOT / "AGENTS.md").read_bytes() == before


if __name__ == "__main__":
    test_claude_disabled_odd_settings()
    test_generated_off_is_not_opt_out()
    test_rule_block_rejects_broken_markers()
    test_check_hook_reports_version_and_slow_start()
    test_slow_shell_is_reported()
    test_all()
    print("OK: ponytail_setup")
