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
LOCAL_ONLY = "local_only"        # локально покрыта, но база клиент-серверная: решает conf.cfg СЕРВЕРА
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


class ConfEncodingError(RuntimeError):
    """conf.cfg не читается ни в одной известной кодировке.

    Отдельное исключение, потому что реакция особая: файл НЕЛЬЗЯ переписывать. Раньше здесь
    стоял `errors="replace"`, и нечитаемые байты молча превращались в «?», а следующая запись
    сохраняла этот мусор уже навсегда.
    """


def read_cfg(cfg: Path) -> tuple[str, str, str]:
    """Текст conf.cfg БЕЗ BOM, имя кодировки и перевод строки — чтобы записать обратно так же.

    Кодировку определяем по BOM; без BOM пробуем UTF-8 строго. UTF-16 без BOM отличим по
    нулевым байтам — такой файл мы читать не беремся и говорим об этом вслух.
    """
    raw = Path(cfg).read_bytes()
    text = None
    encoding = "utf-8"
    for bom, enc in _BOMS:
        if raw.startswith(bom):
            body, encoding = raw[len(bom):], enc
            try:
                text = body.decode(enc)
            except UnicodeDecodeError as e:
                raise ConfEncodingError(f"{cfg}: не читается как {enc}: {e}") from None
            break
    else:
        if b"\x00" in raw:
            raise ConfEncodingError(
                f"{cfg}: похоже на UTF-16 без BOM — кодировку не распознать надёжно, "
                "правь файл вручную, сохраняя исходную кодировку")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ConfEncodingError(f"{cfg}: не читается как UTF-8: {e}") from None

    newline = "\r\n" if "\r\n" in text else "\n"
    return text, encoding, newline


def masks_from_conf(cfg: Path | None) -> list[str]:
    """Маски строк соединения из `DisableUnsafeActionProtection` (через `;`).
    Файла нет / параметра нет → пусто."""
    if cfg is None or not Path(cfg).is_file():
        return []
    # Нечитаемый файл — ConfEncodingError наружу: «масок нет» и «файл не читается» — разные
    # диагнозы, и второй нельзя прятать за первым (doctor/предполёт ловят его сами).
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


def is_client_server(base: str) -> bool:
    """Похожа ли строка соединения на клиент-серверную базу (`сервер\база`, `Srvr=...`).

    Нужно не ради красоты: для такой базы решение о предупреждении принимает conf.cfg СЕРВЕРА,
    и локальная проверка про него ничего не знает.
    """
    b = base.strip()
    low = b.casefold()
    if re.match(r"/s[\s\"]", low) or "srvr=" in low:      # флаг /S или строка соединения
        return True
    if re.match(r"/f[\s\"]", low) or "file=" in low:
        return False
    if b.startswith("\\\\") or b.startswith("/") or ":" in b.split("\\")[0][1:]:
        return False                      # UNC, POSIX-путь, диск — файловая база
    return "\\" in b


def status_for_base(base: str, platform_root: str | Path | None = None) -> str:
    """Статус по маскам локального conf.cfg.

    OFF_BY_MASK — база покрыта и локального файла достаточно (файловая база);
    LOCAL_ONLY   — покрыта локально, но база клиент-серверная: решает conf.cfg сервера;
    UNKNOWN      — не покрыта, всё зависит от флага у пользователя ИБ.
    """
    try:
        masks = masks_from_conf(conf_cfg_path(platform_root))
    except ConfEncodingError:
        return UNKNOWN                    # нечитаемый conf.cfg = про маску ничего не известно
    for mask in masks:
        if mask in UNIVERSAL_MASKS or _matches(base, mask):
            return LOCAL_ONLY if is_client_server(base) else OFF_BY_MASK
    return UNKNOWN


UNIVERSAL_MASKS = (".*",)
"""Маска «все базы» — ровно одна, `.*` (регулярное выражение).

Формы `*` и `*.*` ходят по статьям, но как regex невалидны, и платформа их игнорирует.
Считать их универсальными нельзя: тогда инструмент показывал бы зелёный статус там, где
защита на деле включена. Они попадают в `invalid_masks()` и дают предупреждение."""


def set_mask(mask: str = ".*", platform_root: str | Path | None = None,
             cfg_path: str | Path | None = None) -> Path:
    """Прописать `DisableUnsafeActionProtection` в conf.cfg платформы.

    Идемпотентно: существующая строка заменяется, остальное содержимое сохраняется.
    Перед первой правкой рядом кладётся `conf.cfg.toolkit-backup`.
    `.*` снимает защиту для ВСЕХ баз машины — на рабочей машине лучше маску поуже.
    """
    cfg = Path(cfg_path) if cfg_path else conf_cfg_path(platform_root)
    if cfg is None:
        raise RuntimeError("conf.cfg платформы не найден: задай ONEC_1CV8_BIN или путь явно")
    cfg = Path(cfg)
    text, encoding, newline = read_cfg(cfg) if cfg.is_file() else ("", "utf-8", os.linesep)
    backup = cfg.with_suffix(cfg.suffix + ".toolkit-backup")
    if cfg.is_file() and not backup.exists():
        # Бэкап копируем БАЙТАМИ: пересохранение текстом уже меняло бы кодировку, а бэкап
        # должен воспроизводить файл в точности. Права проверяются именно здесь — это первая
        # запись в каталог программы, и отказ должен объяснять причину так же, как при _write.
        try:
            shutil.copy2(cfg, backup)
        except PermissionError:
            raise PermissionError(_no_rights_message(backup)) from None

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
    _write(cfg, newline.join(lines).rstrip(newline) + newline, encoding)
    return cfg


def clear_mask(platform_root: str | Path | None = None,
               cfg_path: str | Path | None = None) -> Path:
    """Убрать параметр из conf.cfg — вернуть защиту от опасных действий."""
    cfg = Path(cfg_path) if cfg_path else conf_cfg_path(platform_root)
    if cfg is None or not Path(cfg).is_file():
        raise RuntimeError("conf.cfg платформы не найден")
    cfg = Path(cfg)
    text, encoding, newline = read_cfg(cfg)
    kept = [l for l in text.splitlines()
            if not re.match(r"\s*DisableUnsafeActionProtection\s*=", l, re.IGNORECASE)]
    _write(cfg, newline.join(kept).rstrip(newline) + newline, encoding)
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
        raise PermissionError(_no_rights_message(cfg)) from None


def _no_rights_message(path: Path) -> str:
    return (f"нет прав на запись {path} — это каталог установки платформы. Запусти команду "
            "от имени администратора либо снимите флаг «Защита от опасных действий» у "
            "пользователя ИБ (docs/setup-actions-required.md §1)")


def preflight_note(base: str, platform_root: str | Path | None = None) -> str | None:
    """Строка-предупреждение перед батч-прогоном, либо None, если рисковать нечем.

    Намеренно ПРЕДУПРЕЖДЕНИЕ, а не отказ: флаг у пользователя ИБ мог быть снят, и тогда
    всё отработает. Задача — чтобы человек, увидев долгий прогон, сразу знал, куда смотреть.
    """
    status = status_for_base(base, platform_root)
    if status == OFF_BY_MASK:
        return None
    if status == LOCAL_ONLY:
        return (f"[предполёт] база {base} покрыта маской в conf.cfg ЭТОЙ машины, но база "
                "клиент-серверная: открытие внешней обработки проверяет СЕРВЕР 1С, и решает "
                "его conf.cfg — локально этого не видно. Если сеанс не вернётся, смотрите "
                "маску на сервере либо флаг у пользователя ИБ "
                "(docs/setup-actions-required.md §1). Подключений не делает.")
    return (f"[предполёт] база {base} не покрыта маской DisableUnsafeActionProtection в "
            "conf.cfg ЭТОЙ машины. Если сеанс не вернётся — это модальное окно «Защита от "
            "опасных действий» ждёт человека: снимите флаг у пользователя ИБ либо добавьте "
            "маску (docs/setup-actions-required.md §1). Для клиент-серверной базы маску надо "
            "ставить в conf.cfg СЕРВЕРА 1С. Подключений не делает.")
