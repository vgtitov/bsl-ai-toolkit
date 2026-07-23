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

from onec_metadata.apply import standalone as sa  # noqa: E402  (после настройки sys.path)

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

    v = sub.add_parser("serve", help="автономный сервер: HTTP к ФАЙЛОВОЙ базе без веб-сервера")
    v.add_argument("--base", required=True, help="каталог файловой ИБ")
    v.add_argument("--port", type=int, default=sa.DEFAULT_PORT, help="порт (умолч. 8314)")
    v.add_argument("--name", default="ib", help="имя базы в публикации")
    v.add_argument("--address", default="localhost", help="localhost | any | IP")
    v.add_argument("--data", help="каталог данных сервера")
    v.add_argument("--config-out", help="куда положить YAML-конфигурацию")
    v.add_argument("--no-odata", action="store_true", help="не публиковать OData")
    v.add_argument("--no-http-services", action="store_true", help="не публиковать HTTP-сервисы")
    v.add_argument("--service", action="append", metavar="ИМЯ[:КОРЕНЬ]", default=[],
                   help="HTTP-сервис РАСШИРЕНИЯ, опубликовать поимённо; иначе он отдаёт 503. "
                        "Можно повторять: --service aidbg_API:aidbg")
    v.add_argument("--schedule-jobs", action="store_true",
                   help="включить регламентные задания (на копии базы обычно НЕ надо)")
    v.add_argument("--wait", type=int, default=300, help="сколько ждать готовности, сек")

    rm = sub.add_parser("remote",
                        help="готов ли удалённый хост к прогонам: связь, оболочка, платформа")
    rm.add_argument("--host", required=True, help="алиас ssh хоста рядом с кластером")
    rm.add_argument("--bin", help="путь к 1cv8 НА ЭТОМ хосте (иначе ONEC_1CV8_BIN)")
    rm.add_argument("--workdir", help="рабочий каталог на хосте (иначе ONEC_REMOTE_WORKDIR)")

    p = sub.add_parser("protection",
                       help="защита от опасных действий: посмотреть/снять/вернуть по conf.cfg")
    p.add_argument("--status", action="store_true", help="показать текущие маски")
    p.add_argument("--disable", action="store_true", help="прописать маску (снять защиту)")
    p.add_argument("--enable", action="store_true", help="убрать маску (вернуть защиту)")
    p.add_argument("--mask", default="*.*",
                   help="маска строк соединения; *.* — все базы машины (умолч.)")
    p.add_argument("--base", help="проверить конкретную базу против масок")
    return ap


def _remote(a):
    """Готовность удалённого хоста. Учётку ИБ не трогает — только чтение окружения."""
    import os as _os

    from onec_metadata.apply import remote
    from onec_metadata.apply.runner import make_runner

    bin_path = a.bin or _os.environ.get("ONEC_1CV8_BIN", "")
    workdir = a.workdir or _os.environ.get("ONEC_REMOTE_WORKDIR", r"D:\src")
    if not bin_path:
        raise SystemExit("укажи --bin или задай ONEC_1CV8_BIN: это путь к 1cv8 НА УДАЛЁННОМ "
                         "хосте, локальный сюда не годится")
    results = remote.check_host(make_runner(a.host), bin_path=bin_path, workdir=workdir)
    print(remote.format_report(results))
    if any(s == remote.BAD for s, _, _ in results):
        raise SystemExit(1)


def _protection(a):
    """Защита от опасных действий — единственная машинная настройка, которую агент может
    поправить сам. Правит conf.cfg платформы, поэтому требует прав администратора."""
    from onec_metadata.apply import protection as pr

    os.environ["ONEC_1CV8_BIN"] = resolve_bin(a.bin if hasattr(a, "bin") else None)
    if a.disable and a.enable:
        raise SystemExit("--disable и --enable взаимоисключающие")
    if a.disable:
        cfg = pr.set_mask(a.mask)
        print(f"[ok] защита снята по маске {a.mask} в {cfg}")
    elif a.enable:
        cfg = pr.clear_mask()
        print(f"[ok] защита возвращена, параметр убран из {cfg}")

    cfg = pr.conf_cfg_path()
    masks = pr.masks_from_conf(cfg)
    print(f"conf.cfg: {cfg or 'не найден'}")
    print("маски: " + ("; ".join(masks) if masks else "нет — защита включена"))
    if a.base:
        covered = pr.status_for_base(a.base) == pr.OFF_BY_MASK
        print(f"база {a.base}: " + ("под маской, окна не будет" if covered
                                    else "маска не покрывает"))


def _serve(a):
    """Автономный сервер: HTTP-доступ к файловой базе без веб-сервера и без админа.

    Держит сервер в переднем плане — вызывающий запускает команду фоновой задачей,
    работает по HTTP и гасит её. Ctrl+C завершает сервер штатно.
    """
    os.environ["ONEC_1CV8_BIN"] = resolve_bin(a.bin if hasattr(a, "bin") else None)
    import tempfile

    workdir = Path(a.data) if a.data else Path(tempfile.gettempdir()) / f"ibsrv-{a.port}"
    cfg_path = Path(a.config_out) if a.config_out else workdir / "ibsrv.yaml"
    services = []
    for item in a.service:
        srv_name, _, root = item.partition(":")
        services.append({"name": srv_name, "root": root or srv_name})
    text = sa.build_config(a.base, port=a.port, name=a.name, address=a.address,
                           odata=not a.no_odata, http_services=not a.no_http_services,
                           schedule_jobs=a.schedule_jobs, services=services)
    sa.write_config(cfg_path, text)
    print(f"[ok] конфигурация: {cfg_path}")
    proc = sa.start(cfg_path, data_dir=workdir, port=a.port, wait=a.wait)
    url = sa.base_url(a.port, host="localhost")
    print(f"[ok] автономный сервер готов: {url}")
    print(f"     веб-клиент     {url}/")
    print(f"     OData          {url}/odata/standard.odata/")
    print(f"     HTTP-сервисы   {url}/hs/<RootURL>/...")
    print("     Ctrl+C или остановка задачи — завершить сервер")
    try:
        proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        sa.stop(proc)


def main(argv=None):
    try:
        from onec_ops_mcp import load_dotenv_defaults
        load_dotenv_defaults()
    except Exception:
        pass

    a = build_parser().parse_args(argv)

    if a.cmd == "remote":
        return _remote(a)
    if a.cmd == "protection":
        return _protection(a)
    if a.cmd == "serve":
        return _serve(a)

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
