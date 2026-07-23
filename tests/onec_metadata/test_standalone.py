# DISCIPLINE_ALLOW_TEST_EDIT: новый тест — автономный сервер (HTTP к файловой базе)
"""Автономный сервер `ibsrv`: сборка конфигурации и запись её в UTF-8.

Оба требования выстраданы боевым прогоном 23.07 на ERP WE 2.5.14.82:
- путь к базе с кириллицей нельзя передать аргументом командной строки (платформа
  обрезает строку на первом не-ASCII символе) — значит конфиг обязан быть файлом в UTF-8;
- в YAML путь обязан быть в кавычках, иначе `C:\\dev\\tvg` разбирается с escape-
  последовательностями и `\\t` превращается в табуляцию, а база «не находится».

Пути берутся из tmp_path: буквы дисков есть не на всякой ОС.
"""
import pytest

from onec_metadata.apply import standalone as sa


def test_config_quotes_path_and_uses_forward_slashes():
    cfg = sa.build_config(r"C:\dev\tvg\db\Франчайзи (файловая прод)")
    assert "path: 'C:/dev/tvg/db/Франчайзи (файловая прод)'" in cfg
    assert "\\t" not in cfg and "\\d" not in cfg      # backslash-escape'ов не осталось


def test_config_publishes_odata_and_http_services_by_default():
    cfg = sa.build_config("/opt/1c/bases/erp")
    assert "odata: yes" in cfg
    assert "publish-by-default: yes" in cfg
    assert "publish-extensions-by-default: yes" in cfg   # HTTP-сервисы РАСШИРЕНИЙ тоже


def test_config_can_disable_publications():
    cfg = sa.build_config("/opt/1c/bases/erp", odata=False, http_services=False)
    assert "odata: no" in cfg
    assert "publish-extensions-by-default: no" in cfg


def test_config_scheduled_jobs_denied_by_default():
    """Регламентные задания на копии базы включать не надо — обмены и рассылки живые."""
    assert "schedule-jobs: deny" in sa.build_config("/base")
    assert "schedule-jobs: allow" in sa.build_config("/base", schedule_jobs=True)


def test_config_port_and_name():
    cfg = sa.build_config("/base", port=8321, name="franchise")
    assert "port: 8321" in cfg and "name: franchise" in cfg


def test_write_config_is_utf8(tmp_path):
    """Кириллица в пути обязана пережить запись — ради этого конфиг и существует."""
    p = sa.write_config(tmp_path / "sub" / "ibsrv.yaml",
                        sa.build_config(r"C:\базы\Франчайзи"))
    assert p.read_bytes().decode("utf-8")                    # именно UTF-8
    assert "Франчайзи" in p.read_text(encoding="utf-8")


def test_ibsrv_bin_derived_from_1cv8(monkeypatch, tmp_path):
    """ibsrv лежит рядом с 1cv8 — отдельной настройки не требуется."""
    binp = tmp_path / "bin"
    binp.mkdir()
    v8 = binp / ("1cv8.exe" if __import__("os").name == "nt" else "1cv8")
    v8.write_text("", encoding="utf-8")
    monkeypatch.delenv("ONEC_IBSRV_BIN", raising=False)
    monkeypatch.setenv("ONEC_1CV8_BIN", str(v8))
    assert str(binp) in sa.ibsrv_bin()


def test_ibsrv_bin_explicit_wins(monkeypatch):
    monkeypatch.setenv("ONEC_IBSRV_BIN", "env-ibsrv")
    assert sa.ibsrv_bin("явный") == "явный"
    assert sa.ibsrv_bin() == "env-ibsrv"


def test_ibsrv_bin_without_platform_raises(monkeypatch):
    monkeypatch.delenv("ONEC_IBSRV_BIN", raising=False)
    monkeypatch.delenv("ONEC_1CV8_BIN", raising=False)
    with pytest.raises(RuntimeError):
        sa.ibsrv_bin()


def test_start_reports_platform_error_instead_of_hanging(monkeypatch, tmp_path):
    """Если ibsrv упал на старте — отдаём его текст, а не ждём порт до таймаута."""
    class Dead:
        stdout = __import__("io").StringIO("[FATAL] Отсутствует файл базы данных")

        def poll(self):
            return 1

    monkeypatch.setattr(sa.subprocess, "Popen", lambda *a, **kw: Dead())
    monkeypatch.setattr(sa, "port_is_open", lambda *a, **kw: False)
    with pytest.raises(RuntimeError) as e:
        sa.start(tmp_path / "c.yaml", data_dir=tmp_path / "data", bin_path="ibsrv", wait=5)
    assert "Отсутствует файл базы данных" in str(e.value)


def test_base_url():
    assert sa.base_url(8315) == "http://localhost:8315"
