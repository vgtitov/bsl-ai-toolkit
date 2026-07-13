"""Фаза 5: регистрация внешних обработок/отчётов в БСП
«Дополнительные отчёты и обработки» — headless, через ENTERPRISE.

Механика: toolkit поставляет BSL-шаблон обработки-регистратора
(`onec_metadata/templates/bsp_register_external.bsl`). Организация один раз
собирает из него .epf (build_external из apply.external), после чего
`register_external` запускает 1С:Предприятие с /Execute регистратора и
передаёт путь к целевому .epf в /C. Регистратор пишет маркер __ENT_OK__
в /Out-лог — по нему (и по отсутствию слов об ошибке) определяется успех:
клиентский запуск ENTERPRISE кода возврата не даёт.
"""
from onec_metadata.apply.dumpload import ApplyError, _read_log, _wpath
from onec_metadata.apply.runner import Runner, enterprise_cmd, mask_password

_ENT_OK = "__ENT_OK__"


def register_external(r: Runner, ib: str, user: str, pwd: str, *,
                      registrar_epf: str, target_epf: str,
                      section: str = "", log: str | None = None,
                      timeout: int = 600) -> None:
    """Зарегистрировать target_epf в справочнике «Дополнительные отчёты и
    обработки» через обработку-регистратор. section — необязательный раздел
    командного интерфейса (передаётся регистратору вторым полем /C)."""
    log = log or _wpath("bsp_register.log")
    c_param = target_epf if not section else f"{target_epf};{section}"
    cmd = enterprise_cmd(ib, user, pwd, registrar_epf, c_param, log)
    code, out = r.run(cmd, timeout=timeout)
    logtxt = _read_log(r, log)
    combined = out + "\n" + logtxt
    if _ENT_OK not in combined or "Ошибк" in logtxt:
        raise ApplyError(
            f"registration failed\ncmd: {mask_password(cmd)}\n"
            f"out: {out.strip()[:500]}\nlog: {logtxt.strip()[:2000]}")
