# DISCIPLINE_ALLOW_TEST_EDIT: новый тест, TDD red-фаза (Фаза 4: .epf/.erf/.cfe)
"""Внешние обработки/отчёты (.epf/.erf) и бинарные расширения (.cfe):
построение и гейт команд DESIGNER (без SSH, FakeRunner)."""
import pytest


class FakeRunner:
    host = "f"

    def __init__(self, out="__DSGN_OK__"):
        self.out = out
        self.calls = []

    def run(self, cmd, timeout=600):
        self.calls.append(cmd)
        return 0, self.out


@pytest.fixture(autouse=True)
def _no_log(monkeypatch):
    from onec_metadata.apply import dumpload
    monkeypatch.setattr(dumpload, "_read_log", lambda r, log: "")


def test_dump_external_cmd():
    from onec_metadata.apply.external import dump_external

    r = FakeRunner()
    dump_external(r, r"localhost\testbase", "u", "p",
                  epf=r"D:\src\Обработка.epf", dst_dir=r"D:\src\epf_src")
    cmd = r.calls[0]
    assert "/DumpExternalDataProcessorOrReportToFiles" in cmd
    assert r"D:\src\epf_src" in cmd and r"D:\src\Обработка.epf" in cmd


def test_build_external_cmd():
    from onec_metadata.apply.external import build_external

    r = FakeRunner()
    build_external(r, r"localhost\testbase", "u", "p",
                   root_xml=r"D:\src\epf_src\Обработка.xml",
                   out_epf=r"D:\src\Обработка.epf")
    cmd = r.calls[0]
    # DISCIPLINE_ALLOW_TEST_EDIT: red-фаза, замена бракованного ассерта на порядок аргументов
    assert "/LoadExternalDataProcessorOrReportFromFiles" in cmd
    assert r"D:\src\epf_src\Обработка.xml" in cmd
    assert r"D:\src\Обработка.epf" in cmd
    # порядок: сначала исходники, затем выходной файл
    assert cmd.index(r"D:\src\epf_src\Обработка.xml") < cmd.index(r"D:\src\Обработка.epf")


def test_dump_cfe_cmd():
    from onec_metadata.apply.external import dump_cfe

    r = FakeRunner()
    dump_cfe(r, r"localhost\testbase", "u", "p",
             ext="МоёРасширение", out_cfe=r"D:\src\ext.cfe")
    cmd = r.calls[0]
    assert "/DumpCfg" in cmd
    assert "-Extension МоёРасширение" in cmd
    assert r"D:\src\ext.cfe" in cmd


def test_load_cfe_cmd():
    from onec_metadata.apply.external import load_cfe

    r = FakeRunner()
    load_cfe(r, r"localhost\testbase", "u", "p",
             cfe=r"D:\src\ext.cfe", ext="МоёРасширение")
    joined = " || ".join(r.calls)
    assert "/LoadCfg" in joined
    assert "-Extension МоёРасширение" in joined
    # применение обязательно: UpdateDBCfg -Extension
    assert "/UpdateDBCfg" in joined
    update = [c for c in r.calls if "/UpdateDBCfg" in c]
    assert all("-Extension МоёРасширение" in c for c in update)


def test_failed_designer_raises():
    from onec_metadata.apply.dumpload import ApplyError
    from onec_metadata.apply.external import dump_cfe

    with pytest.raises(ApplyError):
        dump_cfe(FakeRunner(out="__DSGN_FAIL__"), "ib", "u", "p",
                 ext="X", out_cfe=r"D:\x.cfe")


# ── SSH ИЛИ локально: LocalRunner / make_runner / conn_arg ────────────────────
# DISCIPLINE_ALLOW_TEST_EDIT: тесты доработки «работает и по ssh, и без него»

def test_conn_arg_server_vs_file():
    from onec_metadata.apply.runner import conn_arg
    assert conn_arg(r"localhost\testbase") == r"/S localhost\testbase"      # серверная
    assert conn_arg(r"D:\bases\erp_test") == r'/F"D:\bases\erp_test"'        # файловая (диск)
    assert conn_arg(r"\\nas\share\base") == r'/F"\\nas\share\base"'          # файловая (UNC)
    assert conn_arg("File=/opt/1c/base") == '/F"/opt/1c/base"'              # File=… явно
    assert conn_arg(r"/S srv\b") == r"/S srv\b"                              # готовый флаг — как есть


def test_enterprise_cmd_uses_file_base():
    from onec_metadata.apply.runner import enterprise_cmd
    cmd = enterprise_cmd(r"D:\bases\erp_test", "u", "p", r"D:\r.epf",
                         "сцен;рез", "out.log", bin=r"C:\1cv8.exe")
    assert r'/F"D:\bases\erp_test"' in cmd and "/S " not in cmd
    assert "ENTERPRISE" in cmd and "/Execute" in cmd


def test_make_runner_local_vs_ssh():
    from onec_metadata.apply.runner import LocalRunner, Runner, make_runner
    assert isinstance(make_runner(None), LocalRunner)      # локально по умолчанию
    assert isinstance(make_runner("local"), LocalRunner)
    assert isinstance(make_runner(""), LocalRunner)
    assert isinstance(make_runner("workpc"), Runner)       # host → SSH


def test_ssh_runner_requires_host():
    from onec_metadata.apply.runner import Runner
    with pytest.raises(ValueError):
        Runner("")


def test_local_runner_runs_without_ssh(monkeypatch):
    """LocalRunner исполняет команду на текущей машine (без ssh) — проверяем через мок."""
    import onec_metadata.apply.runner as rn
    seen = {}

    class P:
        returncode = 0
        stdout = "__DSGN_OK__"
        stderr = ""

    def fake_run(args, **kw):
        seen["args"] = args
        seen["shell"] = kw.get("shell", False)
        return P()

    monkeypatch.setattr(rn.subprocess, "run", fake_run)
    code, out = rn.LocalRunner().run('"1cv8" DESIGNER /S x')
    assert code == 0 and "OK" in out
    assert seen["shell"] is False           # без shell (нет инъекции через оболочку)
    # ssh НЕ участвует: первый токен — не 'ssh'
    first = seen["args"][0] if isinstance(seen["args"], list) else seen["args"].split()[0]
    assert "ssh" not in str(first).lower()
