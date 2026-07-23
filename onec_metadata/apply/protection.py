"""Защита от опасных действий — предполётная проверка КОНКРЕТНОЙ базы, без подключения.

Зачем отдельно: модальное окно «Защита от опасных действий» подвешивает батч-сеанс
насмерть (боевой случай 23.07 — 55 минут «зависания» на ровном месте). Узнать заранее
можно ровно одно и совершенно бесплатно: попадает ли строка соединения базы под маску
`DisableUnsafeActionProtection` в `conf.cfg` платформы. Снят ли флаг у конкретного
ПОЛЬЗОВАТЕЛЯ ИБ — снаружи базы не видно никак, поэтому вердикт честно троичный.

Почему не проверять подключением: баз в контуре бывают десятки, а сеанс к каждой — это
минуты (по VPN — десятки минут). Опрос всех баз «на всякий случай» стоит дороже проблемы,
которую ловит. Поэтому проверка ленивая: она делается в момент обращения к базе, локально
по файлу, и не открывает ни одного соединения.
"""
from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

# Итог проверки
OFF_BY_MASK = "off_by_mask"      # база под маской conf.cfg — защита выключена, окна не будет
UNKNOWN = "unknown"              # маска не покрывает: зависит от флага пользователя ИБ


def conf_cfg_path(platform_root: str | Path | None = None) -> Path | None:
    """Путь к `conf/conf.cfg` платформы. `conf` лежит рядом с каталогами версий:
    `C:\\Program Files\\1cv8\\conf\\conf.cfg`, `/opt/1cv8/conf/conf.cfg`."""
    if platform_root is None:
        platform_root = os.environ.get("ONEC_PLATFORM_ROOT")
        if not platform_root:
            bin_path = os.environ.get("ONEC_1CV8_BIN")
            if not bin_path:
                return None
            # <корень>/<версия>/bin/1cv8.exe → <корень>
            platform_root = Path(bin_path).resolve().parents[1]
    p = Path(platform_root)
    cfg = p / "conf" / "conf.cfg"
    if cfg.is_file():
        return cfg
    cfg = p.parent / "conf" / "conf.cfg"        # передали каталог версии
    return cfg if cfg.is_file() else None


def masks_from_conf(cfg: Path | None) -> list[str]:
    """Маски строк соединения из `DisableUnsafeActionProtection` (через `;`).
    Файла нет / параметра нет → пусто."""
    if cfg is None or not Path(cfg).is_file():
        return []
    text = Path(cfg).read_text(encoding="utf-8-sig", errors="replace")
    out = []
    for line in text.splitlines():
        m = re.match(r"\s*DisableUnsafeActionProtection\s*=\s*(.*)$", line, re.IGNORECASE)
        if m:
            out += [p.strip() for p in m.group(1).split(";") if p.strip()]
    return out


def _matches(base: str, mask: str) -> bool:
    """Маска сопоставляется без учёта регистра и разделителя пути: строку соединения
    пишут и с `\\`, и с `/`, а маску — как придётся."""
    norm = lambda s: s.replace("\\", "/").casefold()
    return fnmatch.fnmatchcase(norm(base), norm(mask))


def status_for_base(base: str, platform_root: str | Path | None = None) -> str:
    """OFF_BY_MASK, если строка соединения базы попадает под маску conf.cfg; иначе UNKNOWN."""
    for mask in masks_from_conf(conf_cfg_path(platform_root)):
        if _matches(base, mask):
            return OFF_BY_MASK
    return UNKNOWN


def preflight_note(base: str, platform_root: str | Path | None = None) -> str | None:
    """Строка-предупреждение перед батч-прогоном, либо None, если рисковать нечем.

    Намеренно ПРЕДУПРЕЖДЕНИЕ, а не отказ: флаг у пользователя ИБ мог быть снят, и тогда
    всё отработает. Задача — чтобы человек, увидев долгий прогон, сразу знал, куда смотреть.
    """
    if status_for_base(base, platform_root) == OFF_BY_MASK:
        return None
    return (f"[предполёт] база {base} не покрыта маской DisableUnsafeActionProtection в "
            "conf.cfg. Если сеанс не вернётся — это модальное окно «Защита от опасных "
            "действий» ждёт человека: снимите флаг у пользователя ИБ либо добавьте маску "
            "(docs/setup-actions-required.md §1). Проверка локальная, подключений не делает.")
