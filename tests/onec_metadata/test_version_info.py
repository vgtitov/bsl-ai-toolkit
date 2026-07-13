# DISCIPLINE_ALLOW_TEST_EDIT: red-тест версии/совместимости + версия платформы из пути
"""Фиксация версии применения: чтение CompatibilityMode / расширения / версии
конфигурации из Configuration.xml и сверка с ожидаемым (предупреждения)."""
from pathlib import Path

from onec_metadata.version_info import (
    VersionInfo,
    read_config_version_info,
    compare_expected,
    platform_version_from_bin,
)

FIX = Path(__file__).parent / "fixtures"


def test_read_config_version_info():
    info = read_config_version_info(FIX / "Configuration.xml")
    assert info.config_version == "1.2.3.4"
    assert info.compatibility_mode == "Version8_3_17"
    assert info.extension_compatibility_mode == "Version8_3_25"
    assert info.interface_compatibility_mode == "TaxiEnableVersion8_2"


def test_read_accepts_dump_dir():
    # передан каталог дампа — берём Configuration.xml внутри
    info = read_config_version_info(FIX)
    assert info.compatibility_mode == "Version8_3_17"


def test_compare_no_expected_is_empty():
    info = read_config_version_info(FIX / "Configuration.xml")
    assert compare_expected(info, {}) == []


def test_compare_match_is_empty():
    info = read_config_version_info(FIX / "Configuration.xml")
    warnings = compare_expected(info, {
        "compatibility_mode": "Version8_3_17",
        "extension_compatibility_mode": "Version8_3_25",
    })
    assert warnings == []


def test_compare_mismatch_warns():
    info = read_config_version_info(FIX / "Configuration.xml")
    warnings = compare_expected(info, {
        "compatibility_mode": "Version8_3_20",
    })
    assert len(warnings) == 1
    assert "compatibility_mode" in warnings[0]
    assert "Version8_3_20" in warnings[0]
    assert "Version8_3_17" in warnings[0]


def test_version_info_summary_line():
    info = VersionInfo(
        config_version="1.2.3.4",
        compatibility_mode="Version8_3_17",
        extension_compatibility_mode="Version8_3_25",
        interface_compatibility_mode="TaxiEnableVersion8_2",
    )
    line = info.summary()
    assert "Version8_3_17" in line
    assert "Version8_3_25" in line
    assert "1.2.3.4" in line


# DISCIPLINE_ALLOW_TEST_EDIT: тесты платформы из пути + аудит применения
def test_audit_records_and_no_warnings_on_match():
    from onec_metadata.version_info import audit
    a = audit(FIX, bin_path=r"C:\Program Files\1cv8\8.3.25.1445\bin\1cv8.exe",
              expected={"compatibility_mode": "Version8_3_17"})
    assert a.platform_version == "8.3.25.1445"
    assert a.info.compatibility_mode == "Version8_3_17"
    assert a.warnings == []
    rep = a.report()
    assert "8.3.25.1445" in rep and "Version8_3_17" in rep


def test_audit_warns_on_mismatch_but_returns():
    from onec_metadata.version_info import audit
    a = audit(FIX, expected={"compatibility_mode": "Version8_3_22"})
    assert len(a.warnings) == 1
    assert "[!]" in a.report()


def test_platform_version_from_bin_windows():
    assert platform_version_from_bin(
        r"C:\Program Files\1cv8\8.3.25.1445\bin\1cv8.exe") == "8.3.25.1445"


def test_platform_version_from_bin_posix():
    assert platform_version_from_bin(
        "/opt/1cv8/x86_64/8.3.23.2040/1cv8") == "8.3.23.2040"


def test_platform_version_from_bin_absent():
    assert platform_version_from_bin("/usr/local/bin/1cv8") is None
    assert platform_version_from_bin("") is None
