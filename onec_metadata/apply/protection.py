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

import os
import re
import shutil
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


# Кодировки, в которых установщик платформы оставляет conf.cfg. Файл каталога версии
# встречается в UTF-8 с BOM, общий `<корень>/conf/conf.cfg` — в UTF-16LE с BOM. Читать всё
# как UTF-8 нельзя: из UTF-16 получается мусор и маска «пропадает», а запись в другой
# кодировке делает файл нечитаемым уже для самой платформы.
_BOMS = (
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
)


def read_cfg(cfg: Path) -> tuple[str, str]:
    """Текст conf.cfg БЕЗ BOM и имя его кодировки — чтобы записать обратно так же."""
    raw = Path(cfg).read_bytes()
    for bom, enc in _BOMS:
        if raw.startswith(bom):
            return raw[len(bom):].decode(enc, errors="replace"), enc
    return raw.decode("utf-8", errors="replace"), "utf-8"


def masks_from_conf(cfg: Path | None) -> list[str]:
    """Маски строк соединения из `DisableUnsafeActionProtection` (через `;`).
    Файла нет / параметра нет → пусто."""
    if cfg is None or not Path(cfg).is_file():
        return []
    text = read_cfg(Path(cfg))[0]
    out = []
    for line in text.splitlines():
        m = re.match(r"\s*DisableUnsafeActionProtection\s*=\s*(.*)$", line, re.IGNORECASE)
        if m:
            out += [p.strip() for p in m.group(1).split(";") if p.strip()]
    return out


def _matches(base: str, mask: str) -> bool:
    """Маска — РЕГУЛЯРНОЕ выражение, а не подстановочный шаблон.

    Проверено на 8.3.27.1606 (18.09.2026): маска `.*_PP.*` снимает предупреждения,
    а glob-вид `*_PP*` не даёт ничего — как regex он невалиден (строка начинается с
    квантификатора), и платформа его молча игнорирует. В документации 1С пример
    записан так же: `DisableUnsafeActionProtection=.*`.

    Невалидное выражение здесь не ошибка вызывающего: такую маску платформа не применит,
    и честный ответ — «не покрыта».
    """
    try:
        return re.fullmatch(mask, base, re.IGNORECASE) is not None
    except re.error:
        return False


def invalid_masks(masks: list[str]) -> list[str]:
    """Маски, которые не являются допустимыми регулярными выражениями: платформа их
    игнорирует, а человек считает, что защита снята. Такое стоит показывать явно."""
    bad = []
    for mask in masks:
        if mask in UNIVERSAL_MASKS:
            continue
        try:
            re.compile(mask)
        except re.error:
            bad.append(mask)
    return bad


def status_for_base(base: str, platform_root: str | Path | None = None) -> str:
    """OFF_BY_MASK, если строка соединения базы попадает под маску conf.cfg; иначе UNKNOWN."""
    for mask in masks_from_conf(conf_cfg_path(platform_root)):
        if mask in UNIVERSAL_MASKS or _matches(base, mask):
            return OFF_BY_MASK
    return UNKNOWN


UNIVERSAL_MASKS = (".*", "*", "*.*")
""" Маски «все базы». Рабочая — `.*` (regex). Формы `*` и `*.*` ходят по статьям и
встречаются в чужих конфигурациях: как регулярные выражения они невалидны, но считать
их «не покрывающими ничего» было бы хуже — человек, написавший `*.*`, имел в виду именно
«все базы»."""


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
    text, encoding = read_cfg(cfg) if cfg.is_file() else ("", "utf-8")
    backup = cfg.with_suffix(cfg.suffix + ".toolkit-backup")
    if cfg.is_file() and not backup.exists():
        # Бэкап копируем БАЙТАМИ: пересохранение текстом уже меняло бы кодировку, а бэкап
        # должен воспроизводить файл в точности.
        shutil.copy2(cfg, backup)

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
    _write(cfg, "\n".join(lines).rstrip("\n") + "\n", encoding)
    return cfg


def clear_mask(platform_root: str | Path | None = None,
               cfg_path: str | Path | None = None) -> Path:
    """Убрать параметр из conf.cfg — вернуть защиту от опасных действий."""
    cfg = Path(cfg_path) if cfg_path else conf_cfg_path(platform_root)
    if cfg is None or not Path(cfg).is_file():
        raise RuntimeError("conf.cfg платформы не найден")
    cfg = Path(cfg)
    text, encoding = read_cfg(cfg)
    kept = [l for l in text.splitlines()
            if not re.match(r"\s*DisableUnsafeActionProtection\s*=", l, re.IGNORECASE)]
    _write(cfg, "\n".join(kept).rstrip("\n") + "\n", encoding)
    return cfg


def _write(cfg: Path, text: str, encoding: str = "utf-8") -> None:
    """Запись в ТОЙ ЖЕ кодировке и с тем же BOM, что были у файла: платформа читает
    conf.cfg по BOM, и перезапись UTF-16LE файла в UTF-8 делает его для неё бессмысленным.

    Ошибка прав здесь ожидаема: conf.cfg лежит в каталоге программы."""
    # utf-8-sig добавляет BOM сам, остальным кодировкам его дописываем мы
    bom = b"" if encoding == "utf-8-sig" else next((b for b, e in _BOMS if e == encoding), b"")
    data = bom + text.encode(encoding)
    try:
        cfg.write_bytes(data)
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
            "conf.cfg ЭТОЙ машины. Если сеанс не вернётся — это модальное окно «Защита от "
            "опасных действий» ждёт человека: снимите флаг у пользователя ИБ либо добавьте "
            "маску (docs/setup-actions-required.md §1). Для клиент-серверной базы маску надо "
            "ставить в conf.cfg СЕРВЕРА 1С — открытие внешней обработки проверяет он, и по "
            "нему эта локальная проверка ничего сказать не может. Подключений не делает.")
