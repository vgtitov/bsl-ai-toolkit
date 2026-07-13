"""Фаза 5: регистрация внешних обработок/отчётов в БСП
«Дополнительные отчёты и обработки» — headless, через ENTERPRISE.

Механика (версионно-независимая проверка успеха):
- toolkit поставляет BSL-шаблон обработки-регистратора
  (`onec_metadata/templates/bsp_register_external.bsl`);
- организация один раз собирает из него .epf (build_external);
- `register_external` запускает 1С:Предприятие с /Execute регистратора и
  передаёт в /C три поля: `<целевой .epf>;<раздел>;<файл-результат>`;
- регистратор ПИШЕТ в файл-результат маркер `__ENT_OK__` при успехе или
  `Ошибка: …` при сбое (клиентский запуск кода возврата не даёт, а
  ЗаписьЖурналаРегистрации/Сообщить в /Out не попадают — поэтому именно ФАЙЛ);
- Python читает файл-результат и по нему судит об успехе.
"""
from onec_metadata.apply.dumpload import ApplyError, _wpath
from onec_metadata.apply.runner import Runner, enterprise_cmd, mask_password

_ENT_OK = "__ENT_OK__"


def register_external(r: Runner, ib: str, user: str, pwd: str, *,
                      registrar_epf: str, target_epf: str,
                      section: str = "", log: str | None = None,
                      result_file: str | None = None,
                      timeout: int = 600) -> None:
    """Зарегистрировать target_epf в справочнике «Дополнительные отчёты и
    обработки» через обработку-регистратор. section — необязательный раздел
    командного интерфейса."""
    log = log or _wpath("bsp_register.log")
    result_file = result_file or _wpath("bsp_result.txt")

    # очистить прошлый результат, чтобы «пусто» = «регистратор не отработал»
    r.run(f"del {result_file} 2>nul & echo. >nul", timeout=60)

    c_param = f"{target_epf};{section};{result_file}"
    cmd = enterprise_cmd(ib, user, pwd, registrar_epf, c_param, log)
    r.run(cmd, timeout=timeout)

    _, content = r.run(f"chcp 65001 >nul & type {result_file}", timeout=60)
    if _ENT_OK not in content or "Ошибк" in content:
        _, logtxt = r.run(f"chcp 65001 >nul & type {log}", timeout=60)
        raise ApplyError(
            f"registration failed\ncmd: {mask_password(cmd)}\n"
            f"result: {content.strip()[:500]}\nlog: {logtxt.strip()[:1500]}")
