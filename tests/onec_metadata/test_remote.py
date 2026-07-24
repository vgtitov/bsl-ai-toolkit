# DISCIPLINE_ALLOW_TEST_EDIT: новый тест — проверка готовности удалённого хоста к прогонам
"""Готовность удалённого хоста (терминала контура) к пакетным прогонам 1С.

Зачем инструмент. Прогон 1С с машины, далёкой от кластера, платит задержкой канала за
каждое из десятков тысяч обращений, поэтому раннер гоняют на хосте рядом с кластером через
SSH. Но «SSH поднят» и «прогоны пойдут» — разные вещи, и разница вылезает по одной грабле
за раз. Этот набор проверок ловит их разом и до первого прогона.

Каждая проверка обязана называть, ЧТО именно сломается, если она красная.
"""
import pytest

from onec_metadata.apply import remote


class FakeRunner:
    """Двойник SSH-раннера: отдаёт заготовленные ответы по подстроке команды."""
    host = "term-01"
    shell = True

    def __init__(self, answers):
        self.answers = answers
        self.commands = []

    def run(self, cmd, timeout=600):
        self.commands.append(cmd)
        for needle, out in self.answers.items():
            if needle in cmd:
                return 0, out
        return 1, ""


HEALTHY = {
    "COMSPEC": r"C:\WINDOWS\system32\cmd.exe",
    "if exist": "FOUND",
    "where tar": r"C:\WINDOWS\system32\tar.exe",
    "_probe": "probe-ok",
    "echo ready": "ready",
}


def _by_name(results):
    return {name: (status, detail) for status, name, detail in results}


def test_all_green_on_healthy_host():
    res = _by_name(remote.check_host(FakeRunner(HEALTHY), bin_path=r"C:\1cv8\bin\1cv8.exe",
                                     workdir=r"D:\src"))
    assert all(status == remote.OK for status, _ in res.values()), res


def test_powershell_default_shell_is_detected():
    """Если оболочка по умолчанию PowerShell, он не раскрывает %COMSPEC% и команды в
    синтаксисе cmd ломаются — раннер строит их именно так."""
    answers = dict(HEALTHY, COMSPEC="%COMSPEC%")
    res = _by_name(remote.check_host(FakeRunner(answers), bin_path="1cv8.exe", workdir="D:\\src"))
    status, detail = res["оболочка по умолчанию"]
    assert status == remote.BAD
    assert "cmd" in detail.lower()


def test_missing_platform_is_reported():
    answers = {k: v for k, v in HEALTHY.items() if k != "if exist"}
    res = _by_name(remote.check_host(FakeRunner(answers), bin_path=r"C:\нет\1cv8.exe",
                                     workdir=r"D:\src"))
    status, detail = res["платформа 1С"]
    assert status == remote.BAD
    assert r"C:\нет\1cv8.exe" in detail


def test_workdir_not_writable_is_reported():
    answers = {k: v for k, v in HEALTHY.items() if k != "_probe"}
    res = _by_name(remote.check_host(FakeRunner(answers), bin_path="1cv8.exe", workdir=r"D:\src"))
    assert res["рабочий каталог"][0] == remote.BAD


def test_missing_tar_is_warning_not_error():
    """tar нужен только для переноса деревьев исходников, смоук без него работает."""
    answers = {k: v for k, v in HEALTHY.items() if k != "where tar"}
    res = _by_name(remote.check_host(FakeRunner(answers), bin_path="1cv8.exe", workdir=r"D:\src"))
    assert res["tar"][0] == remote.WARN


def test_dead_transport_short_circuits():
    """Если хост не отвечает, остальные проверки бессмысленны и не должны шуметь."""
    res = remote.check_host(FakeRunner({}), bin_path="1cv8.exe", workdir=r"D:\src")
    assert len(res) == 1
    status, name, detail = res[0]
    assert status == remote.BAD and "связь" in name


def test_password_never_appears_in_commands():
    r = FakeRunner(HEALTHY)
    remote.check_host(r, bin_path="1cv8.exe", workdir=r"D:\src")
    joined = " ".join(r.commands)
    assert "/P" not in joined          # проверки не трогают учётку ИБ вовсе


@pytest.mark.parametrize("bad", ["", None])
def test_bin_path_required(bad):
    with pytest.raises(ValueError):
        remote.check_host(FakeRunner(HEALTHY), bin_path=bad, workdir=r"D:\src")
