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
        if mask in UNIVERSAL_MASKS or _matches(base, mask):
            return OFF_BY_MASK
    return UNKNOWN


UNIVERSAL_MASKS = ("*", "*.*")
"""Маски, которые платформа трактует как «все базы». `*.*` документирована именно так,
хотя по правилам подстановки требовала бы точку в строке соединения — поэтому обе
обрабатываются явно, а не через fnmatch."""


def set_mask(mask: str = "*.*", platform_root: str | Path | None = None,
             cfg_path: str | Path | None = None) -> Path:
    """Прописать `DisableUnsafeActionProtection` в conf.cfg платформы.

    Идемпотентно: существующая строка заменяется, остальное содержимое сохраняется.
    Перед первой правкой рядом кладётся `conf.cfg.toolkit-backup`.
    `*.*` снимает защиту для ВСЕХ баз машины — на рабочей машине лучше маску поуже.
    """
    cfg = Path(cfg_path) if cfg_path else conf_cfg_path(platform_root)
    if cfg is None:
        raise RuntimeError("conf.cfg платформы не найден: задай ONEC_1CV8_BIN или путь явно")
    cfg = Path(cfg)
    text = cfg.read_text(encoding="utf-8-sig", errors="replace") if cfg.is_file() else ""
    backup = cfg.with_suffix(cfg.suffix + ".toolkit-backup")
    if cfg.is_file() and not backup.exists():
        _write(backup, text)        # бэкап рядом с conf.cfg — те же права, тот же диагноз

    line = f"DisableUnsafeActionProtection={mask}"
    lines, replaced = [], False
    for raw in text.splitlines():
        if re.match(r"\s*DisableUnsafeActionProtection\s*=", raw, re.IGNORECASE):
            if not replaced:
                lines.append(line)
                replaced = True
            continue
        lines.append(raw)
    if not replaced:
        lines.append(line)
    _write(cfg, "\n".join(lines).rstrip("\n") + "\n")
    return cfg


def clear_mask(platform_root: str | Path | None = None,
               cfg_path: str | Path | None = None) -> Path:
    """Убрать параметр из conf.cfg — вернуть защиту от опасных действий."""
    cfg = Path(cfg_path) if cfg_path else conf_cfg_path(platform_root)
    if cfg is None or not Path(cfg).is_file():
        raise RuntimeError("conf.cfg платформы не найден")
    cfg = Path(cfg)
    kept = [l for l in cfg.read_text(encoding="utf-8-sig", errors="replace").splitlines()
            if not re.match(r"\s*DisableUnsafeActionProtection\s*=", l, re.IGNORECASE)]
    _write(cfg, "\n".join(kept).rstrip("\n") + "\n")
    return cfg


def _write(cfg: Path, text: str) -> None:
    """Запись с внятной ошибкой: conf.cfg лежит в каталоге программы, и без прав
    администратора он не пишется — это не баг, а нормальная ситуация."""
    try:
        cfg.write_text(text, encoding="utf-8")
    except PermissionError:
        raise PermissionError(
            f"нет прав на запись {cfg} — это каталог установки платформы. Запусти команду "
            "от имени администратора либо снимите флаг «Защита от опасных действий» у "
            "пользователя ИБ (docs/setup-actions-required.md §1)") from None


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
