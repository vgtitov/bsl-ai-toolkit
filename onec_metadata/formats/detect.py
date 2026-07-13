"""Автоопределение формата дерева исходников 1С.

Конфигуратор (DumpConfigToFiles): в корне лежит Configuration.xml.
EDT: в корне проекта .project (+ каталог src с исходниками).
"""
from enum import Enum, auto
from pathlib import Path


class SourceFormat(Enum):
    CONFIGURATOR = auto()
    EDT = auto()


class UnknownFormatError(Exception):
    pass


def detect_format(root: Path) -> SourceFormat:
    if (root / "Configuration.xml").is_file():
        return SourceFormat.CONFIGURATOR
    if (root / ".project").is_file() and (root / "src").is_dir():
        return SourceFormat.EDT
    raise UnknownFormatError(f"Не распознан формат исходников: {root}")
