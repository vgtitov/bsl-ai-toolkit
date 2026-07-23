# /// script
# dependencies = []
# ///
"""Точка входа в лестницу тестирования (docs/testing-ladder.md), ступени 1–2.

Механизмы `apply.smoke` / `apply.external` существовали только как библиотека —
прогнать проверку на базе можно было лишь своим драйвером. Этот CLI закрывает
разрыв: одна команда от исходников до вердикта.

  check  — ступень 1: синтакс-контроль платформой (/CheckConfig во всех контекстах)
  smoke  — ступень 2: серверный BSL-сценарий на реальных данных, вердикт по файлу

Транспорт выбирается сам: без `--host` платформа берётся на ЭТОЙ машине
(`LocalRunner`), с `--host <алиас ssh>` — на сервере 1С. База: существующий
каталог → файловая (/F), иначе «сервер\\база» (/S).

Учётки — env (логин един, пароль по контуру, фолбэк без суффикса):
  ONEC_IB_USER / ONEC_IB_PASS[_<КЛЮЧ>]   — пользователь ИБ (/N /P)
  ONEC_1CV8_BIN                           — путь к 1cv8(.exe); иначе авто-поиск
.env подхватывается из CWD и корня репозитория (окружение главнее файла).

Примеры:
  onec_verify.py check --base "srv\\ERP_Test" --ext ai_debug --contour ERP_TEST
  onec_verify.py smoke --base "D:\\bases\\erp" --scenario scen.bsl --deploy
"""
import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "mcp"))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def resolve_bin(explicit: str | None = None) -> str:
    """1cv8 из аргумента → env ONEC_1CV8_BIN → авто-поиск установленной платформы."""
    if explicit:
        return explicit
    env = os.environ.get("ONEC_1CV8_BIN")
    if env:
        return env
    import detect_tools
    root = detect_tools.find_platform()
    if not root:
        raise SystemExit("Платформа 1С не найдена: задай ONEC_1CV8_BIN или установи 1cv8")
    return str(root / "bin" / ("1cv8.exe" if os.name == "nt" else "1cv8"))


def default_workdir(host: str | None) -> str:
    """Рабочий каталог: на сервере — ONEC_REMOTE_WORKDIR, локально — временный каталог
    ЭТОЙ машины (серверный D:\\src у разработчика обычно не существует)."""
    if host:
        return os.environ.get("ONEC_REMOTE_WORKDIR", r"D:\src") + r"\smoke"
    import tempfile
    return str(Path(tempfile.gettempdir()) / "onec_smoke")


def build_parser():
    ap = argparse.ArgumentParser(
        description="Лестница тестирования 1С: синтакс-контроль (ступень 1) и смоук (ступень 2)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base", required=True, help="каталог файловой ИБ или сервер\\база")
    common.add_argument("--host", help="алиас ssh сервера 1С; без него — платформа этой машины")
    common.add_argument("--contour", help="ключ пароля ONEC_IB_PASS_<КЛЮЧ> (имя базы UPPER)")
    common.add_argument("--bin", help="путь к 1cv8(.exe)")
    common.add_argument("--workdir", help="рабочий каталог для .epf/логов/результата")

    c = sub.add_parser("check", parents=[common], help="ступень 1: синтакс-контроль платформой")
    c.add_argument("--ext", help="имя расширения; без него — основная конфигурация")

    s = sub.add_parser("smoke", parents=[common], help="ступень 2: серверный сценарий на данных")
    s.add_argument("--scenario", required=True,
                   help="файл серверного BSL; итог — в строковой переменной Отчет")
    s.add_argument("--deploy", action="store_true", help="пересобрать раннер .epf перед прогоном")
    s.add_argument("--timeout", type=int, default=900, help="таймаут сеанса, сек (умолч. 900)")
    return ap


def main(argv=None):
    try:
        from onec_ops_mcp import load_dotenv_defaults
        load_dotenv_defaults()
    except Exception:
        pass

    a = build_parser().parse_args(argv)

    os.environ["ONEC_1CV8_BIN"] = resolve_bin(a.bin)
    from designer_sync import resolve_cred
    from onec_metadata.apply import smoke as smoke_mod
    from onec_metadata.apply.external import check_config
    from onec_metadata.apply.runner import make_runner

    user, pwd = resolve_cred("IB", a.contour)
    r = make_runner(a.host)
    workdir = a.workdir or default_workdir(a.host)
    r.makedirs(workdir)

    if a.cmd == "check":
        check_config(r, a.base, user, pwd, ext=a.ext,
                     log=f"{workdir}\\check_config.log")
        print("[ok] синтакс-контроль платформой пройден: "
              + (f"расширение {a.ext}" if a.ext else "основная конфигурация"))
        return

    epf = f"{workdir}\\СмоукРаннер.epf"
    if a.deploy:
        epf = smoke_mod.deploy_smoke_runner(r, a.base, user, pwd, workdir=workdir)
        print(f"[ok] раннер собран: {epf}")
    report = smoke_mod.run_smoke(
        r, a.base, user, pwd,
        scenario_text=Path(a.scenario).read_text(encoding="utf-8-sig"),
        epf=epf, workdir=workdir, timeout=a.timeout)
    print("[ok] __SMOKE_OK__, отчёт сценария:")
    print(report)


if __name__ == "__main__":
    main()
