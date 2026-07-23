"""Внешние обработки/отчёты (.epf/.erf) и бинарные расширения (.cfe).

Обёртки над пакетным режимом DESIGNER (через тот же гейт `_run_designer`,
что и load/dump расширений):

- dump_external:  .epf/.erf → исходники XML
  (/DumpExternalDataProcessorOrReportToFiles)
- build_external: исходники XML → .epf/.erf
  (/LoadExternalDataProcessorOrReportFromFiles)
- dump_cfe / load_cfe: расширение конфигурации как бинарный .cfe
  (/DumpCfg | /LoadCfg -Extension) + обязательное применение
  UpdateDBCfg -Extension после загрузки.

Пути — на СЕРВЕРЕ 1С (Windows-строки). Перенос файлов сервер↔рабочая машина —
upload_tree/fetch_tree/scp из dumpload.
"""
from onec_metadata.apply.dumpload import _run_designer, _wpath
from onec_metadata.apply.runner import Runner


def dump_external(r: Runner, ib: str, user: str, pwd: str, *, epf: str,
                  dst_dir: str, log: str | None = None) -> None:
    """Разобрать внешнюю обработку/отчёт в каталог XML-исходников."""
    log = log or _wpath("external_dump.log")
    _run_designer(r, ib, user, pwd,
                  "/DumpExternalDataProcessorOrReportToFiles",
                  [dst_dir, epf], log)


def build_external(r: Runner, ib: str, user: str, pwd: str, *, root_xml: str,
                   out_epf: str, log: str | None = None) -> None:
    """Собрать .epf/.erf из XML-исходников (root_xml — корневой файл)."""
    log = log or _wpath("external_build.log")
    _run_designer(r, ib, user, pwd,
                  "/LoadExternalDataProcessorOrReportFromFiles",
                  [root_xml, out_epf], log)


def dump_cfe(r: Runner, ib: str, user: str, pwd: str, *, ext: str,
             out_cfe: str, log: str | None = None) -> None:
    """Выгрузить расширение конфигурации в бинарный .cfe."""
    log = log or _wpath("cfe_dump.log")
    _run_designer(r, ib, user, pwd, "/DumpCfg",
                  [out_cfe, "-Extension", ext], log)


def load_cfe(r: Runner, ib: str, user: str, pwd: str, *, cfe: str,
             ext: str, log: str | None = None) -> None:
    """Загрузить бинарный .cfe как расширение и ПРИМЕНИТЬ (UpdateDBCfg)."""
    log = log or _wpath("cfe_load.log")
    _run_designer(r, ib, user, pwd, "/LoadCfg",
                  [cfe, "-Extension", ext], log)
    _run_designer(r, ib, user, pwd, "/UpdateDBCfg",
                  ["-Extension", ext], log)


# Контексты синтакс-контроля: модули расширения компилируются под каждый из них.
# Серверное расширение (HTTP-сервисы, общие модули) обязано проходить весь набор —
# ошибка «метод недоступен в контексте клиента» ловится только так.
CHECK_MODES = ("-ThinClient", "-WebClient", "-Server", "-ExternalConnection",
               "-ThickClientManagedApplication", "-DistributiveModules",
               "-IncorrectReferences", "-ConfigLogIntegrity")


def check_config(r: Runner, ib: str, user: str, pwd: str, *,
                 ext: str | None = None, modes: tuple | None = None,
                 log: str | None = None) -> None:
    """Синтакс-контроль платформой (ступень 1 лестницы тестирования).

    Это ЕДИНСТВЕННАЯ проверка, которую не заменяют BSL LS и статика: платформа
    компилирует модули во всех контекстах и видит несуществующие методы/типы,
    недоступные в контексте вызовы и битые ссылки метаданных.
    `ext=None` — основная конфигурация, иначе расширение по имени.
    """
    log = log or _wpath("check_config.log")
    args = list(modes or CHECK_MODES)
    if ext:
        args += ["-Extension", ext]
    _run_designer(r, ib, user, pwd, "/CheckConfig", args, log)


def delete_extension(r: Runner, ib: str, user: str, pwd: str, *, ext: str,
                     log: str | None = None) -> None:
    """Удалить расширение из ИБ — убрать за собой после проверки в чужой базе."""
    log = log or _wpath("ext_delete.log")
    _run_designer(r, ib, user, pwd, "/DeleteCfg", ["-Extension", ext], log)
