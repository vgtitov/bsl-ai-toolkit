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
