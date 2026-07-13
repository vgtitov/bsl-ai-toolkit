"""Фиксация версии применения: чтение версии и режимов совместимости.

Набор свойств объекта метаданных зависит от версии платформы и РЕЖИМА
СОВМЕСТИМОСТИ. Сам движок правок это не моделирует (клонирует реальный образец,
поэтому свойства нового объекта по построению валидны для текущей комбинации), а
корректность применения проверяет платформа при загрузке. Этот модуль добавляет
АУДИТ: читает из `Configuration.xml` дампа версию конфигурации и режимы
совместимости, чтобы зафиксировать, ПРОТИВ ЧЕГО применяем, и предупредить при
расхождении с ожидаемым (жёсткой блокировки нет — несовместимое отвергнет сама
платформа при `UpdateDBCfg`).

Поддержан формат Конфигуратора (`Configuration.xml`, `DumpConfigToFiles`). Для
EDT (`Configuration.mdo`) читаются те же значения по camelCase-тегам.
"""
import re
from dataclasses import dataclass
from pathlib import Path

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError

_PLATFORM_RE = re.compile(r"(\d+\.\d+\.\d+\.\d+)")


def platform_version_from_bin(bin_path: str) -> str | None:
    """Версия платформы из пути к 1cv8 (…/8.3.25.1445/bin/1cv8.exe → 8.3.25.1445).

    Путь к платформе задаётся env `ONEC_1CV8_BIN` и содержит версию каталогом.
    Возвращает None, если версии в пути нет.
    """
    if not bin_path:
        return None
    m = _PLATFORM_RE.search(bin_path.replace("\\", "/"))
    return m.group(1) if m else None

# свойство VersionInfo → (тег Конфигуратора, тег EDT)
_FIELDS = {
    "config_version": ("Version", "version"),
    "compatibility_mode": ("CompatibilityMode", "compatibilityMode"),
    "extension_compatibility_mode": (
        "ConfigurationExtensionCompatibilityMode",
        "configurationExtensionCompatibilityMode"),
    "interface_compatibility_mode": (
        "InterfaceCompatibilityMode", "interfaceCompatibilityMode"),
}


@dataclass
class VersionInfo:
    config_version: str | None = None
    compatibility_mode: str | None = None
    extension_compatibility_mode: str | None = None
    interface_compatibility_mode: str | None = None

    def summary(self) -> str:
        return (
            f"конфигурация {self.config_version}; "
            f"совместимость {self.compatibility_mode}; "
            f"совместимость расширений {self.extension_compatibility_mode}; "
            f"интерфейс {self.interface_compatibility_mode}")


def _configuration_xml(path: Path) -> Path:
    if path.is_dir():
        for name in ("Configuration.xml", "Configuration.mdo"):
            cand = path / name
            if cand.exists():
                return cand
        raise OpPreconditionError(
            f"В каталоге {path} нет Configuration.xml / Configuration.mdo")
    return path


def _first_text(doc, cfg_tag: str, edt_tag: str) -> str | None:
    # Конфигуратор: <CompatibilityMode> в любом месте (в Properties); local-name,
    # чтобы не зависеть от namespace. EDT: camelCase-тег.
    for tag in (cfg_tag, edt_tag):
        hits = doc.xpath(f"//*[local-name()='{tag}']/text()")
        if hits:
            return hits[0]
    return None


def read_config_version_info(path: Path) -> VersionInfo:
    """Прочитать версию и режимы совместимости из Configuration.(xml|mdo).

    `path` — сам файл или каталог дампа (тогда ищем Configuration.* внутри).
    """
    conf = _configuration_xml(path)
    doc = cfg.load(conf)
    values = {field: _first_text(doc, cfg_tag, edt_tag)
              for field, (cfg_tag, edt_tag) in _FIELDS.items()}
    return VersionInfo(**values)


@dataclass
class VersionAudit:
    info: VersionInfo
    platform_version: str | None
    warnings: list[str]

    def report(self) -> str:
        head = f"[версия применения] платформа {self.platform_version}; {self.info.summary()}"
        if not self.warnings:
            return head
        return head + "\n" + "\n".join("[!] " + w for w in self.warnings)


def audit(local_src: Path, bin_path: str | None = None,
          expected: dict | None = None) -> VersionAudit:
    """Зафиксировать версию применения: прочитать версию/совместимость из дампа,
    версию платформы из пути к 1cv8, сверить с ожидаемым (предупреждения).

    Ничего не блокирует — политика «фиксировать + предупреждать»: вызывающая
    сторона печатает `report()`; несовместимое отвергнет платформа при загрузке.
    """
    info = read_config_version_info(local_src)
    platform = platform_version_from_bin(bin_path) if bin_path else None
    warnings = compare_expected(info, expected or {})
    return VersionAudit(info=info, platform_version=platform, warnings=warnings)


def compare_expected(info: VersionInfo, expected: dict) -> list[str]:
    """Сверить фактические значения с ожидаемыми; вернуть список предупреждений.

    `expected` — подмножество полей VersionInfo → ожидаемое значение. Пустой
    список = совпало (или ожиданий не задано). Блокировки нет — вызывающая
    сторона решает, что делать с предупреждениями (политика: предупреждать).
    """
    warnings = []
    for field, want in expected.items():
        got = getattr(info, field, None)
        if got != want:
            warnings.append(
                f"Расхождение версии: {field} = {got!r}, ожидалось {want!r}")
    return warnings
