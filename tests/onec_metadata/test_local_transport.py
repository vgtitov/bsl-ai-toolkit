# DISCIPLINE_ALLOW_TEST_EDIT: новый тест — регресс на локальный транспорт (без SSH)
"""Локальный транспорт (LocalRunner) — оболочки на том конце НЕТ.

Боевой прогон на живой базе (Work PC, 22.07) показал: код apply.* был написан в
расчёте на cmd.exe удалённого сеанса SSH, и локально ломался:
- `chcp 65001 & type <лог>` уходил в CreateProcess как отдельная программа `chcp`
  с мусорными аргументами → падение при чтении лога Конфигуратора;
- гейт вердикта `& if errorlevel 1 (echo …)` уезжал АРГУМЕНТАМИ в 1cv8.exe →
  маркер не появлялся никогда, вердикт был бы вечным ложным FAIL.

Эти тесты фиксируют разделение: файловые операции — методами раннера, гейт —
только там, где оболочка есть (`shell=True`).
"""
import pytest

from onec_metadata.apply.dumpload import ApplyError, _run_designer
from onec_metadata.apply.runner import LocalRunner


class ShelllessRunner:
    """Двойник LocalRunner: команда исполняется без оболочки, вердикт — по коду возврата."""
    host = ""
    shell = False

    def __init__(self, code=0, log_text=""):
        self.code = code
        self.log_text = log_text
        self.commands = []

    def run(self, cmd, timeout=600):
        self.commands.append(cmd)
        return self.code, ""

    def read_text(self, path, timeout=60):
        return self.log_text

    def makedirs(self, path, timeout=60):
        pass

    def remove(self, path, timeout=60):
        pass


class ShellRunner(ShelllessRunner):
    """Двойник SSH-Runner: на том конце cmd.exe, вердикт — по маркеру из echo."""
    host = "srv"
    shell = True

    def run(self, cmd, timeout=600):
        self.commands.append(cmd)
        return 0, "__DSGN_OK__" if self.code == 0 else "__DSGN_FAIL__"


# ── LocalRunner: файловые операции реальные, оболочка не нужна ────────────────

def test_local_runner_file_ops(tmp_path):
    r = LocalRunner()
    d = tmp_path / "wd" / "sub"
    r.makedirs(str(d))
    assert d.is_dir()

    src = tmp_path / "src.txt"
    src.write_text("привет", encoding="utf-8")
    r.put(src, str(d / "dst.txt"))
    assert r.read_text(str(d / "dst.txt")) == "привет"

    r.remove(str(d / "dst.txt"))
    assert not (d / "dst.txt").exists()
    r.remove(str(d / "dst.txt"))          # повторное удаление не падает


def test_local_runner_read_text_missing_is_empty(tmp_path):
    assert LocalRunner().read_text(str(tmp_path / "нет.log")) == ""


def test_local_runner_read_text_cp1251(tmp_path):
    """Лог платформы бывает в cp1251 — кириллица не должна теряться."""
    p = tmp_path / "designer.log"
    p.write_bytes("Ошибка загрузки".encode("cp1251"))
    assert "Ошибка загрузки" in LocalRunner().read_text(str(p))


def test_local_runner_runs_real_process():
    """Без оболочки, но реальный процесс запускается (CreateProcess/exec)."""
    import sys
    code, out = LocalRunner().run(f'"{sys.executable}" -c "print(42)"')
    assert code == 0 and "42" in out


# DISCIPLINE_ALLOW_TEST_EDIT: новые тесты на утечку пароля (боевой инцидент 23.07)
# ── пароль ИБ не утекает через исключения subprocess ─────────────────────────

@pytest.mark.parametrize("cls_name", ["LocalRunner", "Runner"])
def test_timeout_does_not_leak_password(monkeypatch, cls_name):
    """subprocess.TimeoutExpired кладёт в текст всю командную строку — с /P<пароль>.
    Боевой случай 23.07: таймаут ENTERPRISE напечатал пароль в консоль."""
    import subprocess as sp

    import onec_metadata.apply.runner as rn

    def fake_run(args, **kw):
        raise sp.TimeoutExpired(args, kw.get("timeout", 1))

    monkeypatch.setattr(rn.subprocess, "run", fake_run)
    r = rn.LocalRunner() if cls_name == "LocalRunner" else rn.Runner("srv")
    cmd = '"1cv8.exe" ENTERPRISE /S srv\\base /N"Петров" /PсекретноеСлово /Execute x.epf'
    with pytest.raises(sp.TimeoutExpired) as e:
        r.run(cmd, timeout=1)
    text = str(e.value) + repr(e.value.cmd)
    assert "секретноеСлово" not in text
    assert "/P***" in text


def test_called_process_error_does_not_leak_password(monkeypatch):
    import subprocess as sp

    import onec_metadata.apply.runner as rn

    def fake_run(args, **kw):
        raise sp.CalledProcessError(1, args)

    monkeypatch.setattr(rn.subprocess, "run", fake_run)
    with pytest.raises(sp.CalledProcessError) as e:
        rn.LocalRunner().run('"1cv8.exe" DESIGNER /N"У" /Pпароль123')
    assert "пароль123" not in str(e.value) + repr(e.value.cmd)


# ── _run_designer: вердикт по транспорту ─────────────────────────────────────

def test_run_designer_local_verdict_by_returncode():
    r = ShelllessRunner(code=0)
    _run_designer(r, r"D:\base", "u", "p", "/DumpCfg", [r"D:\x.cfe"], r"D:\d.log")
    cmd = r.commands[0]
    # оболочечного гейта в локальной команде быть не должно — он уехал бы в 1cv8.exe
    assert "if errorlevel" not in cmd and "__DSGN_OK__" not in cmd
    assert "/DumpCfg" in cmd


def test_run_designer_local_nonzero_code_raises():
    with pytest.raises(ApplyError):
        _run_designer(ShelllessRunner(code=1), r"D:\base", "u", "p",
                      "/DumpCfg", [r"D:\x.cfe"], r"D:\d.log")


def test_run_designer_local_error_in_log_raises():
    """Код 0, но в логе платформы «Ошибка» — это провал (лог главнее кода)."""
    with pytest.raises(ApplyError):
        _run_designer(ShelllessRunner(code=0, log_text="Ошибка синтаксиса в модуле"),
                      r"D:\base", "u", "p", "/DumpCfg", [r"D:\x.cfe"], r"D:\d.log")


def test_run_designer_ssh_keeps_shell_gate():
    """SSH-ветка не тронута: гейт по errorlevel остаётся (регресс сервера)."""
    r = ShellRunner(code=0)
    _run_designer(r, r"srv\base", "u", "p", "/DumpCfg", [r"D:\x.cfe"], r"D:\d.log")
    assert "if errorlevel 1" in r.commands[0]


def test_run_designer_ssh_fail_marker_raises():
    with pytest.raises(ApplyError):
        _run_designer(ShellRunner(code=1), r"srv\base", "u", "p",
                      "/DumpCfg", [r"D:\x.cfe"], r"D:\d.log")


# ── smoke: чтение результата и уборка — методами раннера, не `type`/`del` ─────

def test_run_smoke_reads_result_via_runner(monkeypatch):
    from onec_metadata.apply import smoke

    class R(ShelllessRunner):
        def __init__(self):
            super().__init__()
            self.puts = []
            self.removed = []

        def put(self, local, remote, timeout=300):
            self.puts.append(remote)

        def read_text(self, path, timeout=60):
            return "__SMOKE_OK__\nстрок в регистре: 17"

        def remove(self, path, timeout=60):
            self.removed.append(path)

    r = R()
    report = smoke.run_smoke(r, r"D:\base", "u", "p", scenario_text='Отчет = "x";',
                             epf=r"D:\wd\СмоукРаннер.epf", workdir=r"D:\wd")
    assert "строк в регистре: 17" in report
    joined = "\n".join(r.commands)
    assert "type " not in joined and "del " not in joined     # без оболочечных команд
    assert r.removed == [r"D:\wd\smoke_result.txt"]           # результат чистится до прогона
    assert "ENTERPRISE" in joined and "/Execute" in joined
