# DISCIPLINE_ALLOW_TEST_EDIT — первичное создание тестов контракта onec-data (не подгонка под код)
"""Тесты MCP слоя данных 1С (onec-data) против МОК-сервера 1С (stdlib http.server).
Запуск:  uv run --with mcp --with pytest pytest -q tests/test_onec_data_mcp.py
Мок эмулирует: OData ($metadata, чтение с RLS по Basic-auth, виртуальные функции) и
отладочный сервис ai_debug (query/report/settings/call/health, серверный гейт
привилегированного режима, whitelist методов) — контракт extensions/ai_debug.
"""
import base64
import importlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pytest

MCP_DIR = Path(__file__).resolve().parents[1] / "mcp"


def _load():
    sys.path.insert(0, str(MCP_DIR))
    mod = importlib.import_module("onec_data_mcp")
    return importlib.reload(mod)


# ── мок-сервер 1С ─────────────────────────────────────────────────────────────

METADATA_XML = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx" Version="1.0">
 <edmx:DataServices>
  <Schema xmlns="http://schemas.microsoft.com/ado/2009/11/edm" Namespace="StandardODATA">
   <EntityType Name="Catalog_Номенклатура">
    <Property Name="Ref_Key" Type="Edm.Guid" Nullable="false"/>
    <Property Name="Description" Type="Edm.String"/>
    <Property Name="ИНН" Type="Edm.String"/>
   </EntityType>
   <EntityType Name="Document_РеализацияТоваровУслуг">
    <Property Name="Ref_Key" Type="Edm.Guid"/>
   </EntityType>
  </Schema>
 </edmx:DataServices>
</edmx:Edmx>"""

# «Данные» базы: admin видит всё, manager — только первую строку (эмуляция RLS)
ROWS = [
    {"Ref_Key": "11111111-1111-1111-1111-111111111111", "Description": "Диван", "ИНН": "7701"},
    {"Ref_Key": "22222222-2222-2222-2222-222222222222", "Description": "Кровать", "ИНН": "7702"},
    {"Ref_Key": "33333333-3333-3333-3333-333333333333", "Description": "Матрас", "ИНН": "7703"},
]


class Mock1C(BaseHTTPRequestHandler):
    server_version = "Mock1C/1.0"
    # управляется тестами через атрибуты класса
    # DISCIPLINE_ALLOW_TEST_EDIT — расширение мока и тестов под фиксы adversarial-ревью
    privileged_allowed = True
    whitelist = ["ЦеныСервер.ЦенаНоменклатуры", "КонтрагентыСервер.Реквизиты"]
    last_path = ""
    last_body = None

    def log_message(self, *a):  # тишина в тестах
        pass

    def _user(self):
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return None
        try:
            return base64.b64decode(auth[6:]).decode("utf-8").split(":", 1)[0]
        except Exception:
            return None

    def _send(self, code, obj, ctype="application/json"):
        body = (obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _rls_rows(self, user, privileged=False):
        if privileged or user == "admin":
            return ROWS
        if user == "manager":
            return ROWS[:1]
        return []

    def do_GET(self):
        Mock1C.last_path = self.path
        user = self._user()
        if user is None:
            return self._send(401, {"error": "нет аутентификации"})
        path = unquote(self.path)
        if "/odata/standard.odata/$metadata" in path:
            return self._send(200, METADATA_XML, ctype="application/xml")
        if "/odata/standard.odata/Catalog_Номенклатура" in path:
            rows = self._rls_rows(user)
            q = parse_qs(urlparse(path).query)
            top = int(q.get("$top", [0])[0] or 0)
            if top:
                rows = rows[:top]
            return self._send(200, {"value": rows})
        if "_Balance(" in path:
            return self._send(200, {"value": [{"Склад_Key": "aaaa", "КоличествоBalance": 5}]})
        if path.endswith("/hs/aidbg/health"):
            return self._send(200, {"version": "0.1.0", "session_user": user,
                                    "privileged_allowed": Mock1C.privileged_allowed,
                                    "methods_whitelist": Mock1C.whitelist})
        return self._send(404, {"error": "нет маршрута " + path})

    def do_POST(self):
        Mock1C.last_path = self.path
        user = self._user()
        if user is None:
            return self._send(401, {"error": "нет аутентификации"})
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        Mock1C.last_body = body  # DISCIPLINE_ALLOW_TEST_EDIT — для проверки mode=user в rls_probe
        mode = body.get("mode", "user")
        privileged = mode == "privileged"
        if privileged and not Mock1C.privileged_allowed:
            return self._send(403, {"error": "привилегированный режим запрещён настройками ai_debug"})
        if self.path.endswith("/hs/aidbg/query"):
            rows = self._rls_rows(user, privileged)
            top = int(body.get("top", 0) or 0)
            return self._send(200, {"rows": rows[:top] if top else rows,
                                    "fetched": len(rows), "query_params": body.get("params", {})})
        if self.path.endswith("/hs/aidbg/report"):
            if not body.get("report"):
                return self._send(400, {"error": "не указан отчёт"})
            return self._send(200, {"rows": [{"Номенклатура": "Диван", "Количество": 2}],
                                    "fetched": 1, "variant": body.get("variant", ""),
                                    "filters_applied": len(body.get("filters", []))})
        if self.path.endswith("/hs/aidbg/settings"):
            what = body.get("what")
            if what == "constants":
                return self._send(200, {"constants": {n: "значение_" + n for n in body.get("names", [])}})
            if what == "functional_options":
                return self._send(200, {"functional_options": {n: True for n in body.get("names", [])}})
            if what == "access":
                return self._send(200, {"target_user": body.get("target_user"),
                                        "roles": ["Менеджер"], "access_groups": ["Продажи"]})
            if what == "user_settings":
                # DISCIPLINE_ALLOW_TEST_EDIT — блоб настроек с ПДн (проверка маскирования L3)
                return self._send(200, {"user_settings": {"Почта": "ivan@example.com",
                                                          "Тема": "Тёмная"}})
            return self._send(400, {"error": "неизвестный what"})
        if self.path.endswith("/hs/aidbg/call"):
            method = body.get("method", "")
            if method not in Mock1C.whitelist:
                return self._send(403, {"error": "метод '%s' не в whitelist" % method})
            if method == "КонтрагентыСервер.Реквизиты":
                # результат метода получения данных содержит ПДн — должен маскироваться
                return self._send(200, {"result": {"Наименование": "ООО Ромашка",
                                                   "ИНН": "7707083893"}, "method": method})
            return self._send(200, {"result": 990.0, "method": method, "args": body.get("args", [])})
        return self._send(404, {"error": "нет маршрута " + self.path})


@pytest.fixture()
def base(tmp_path, monkeypatch):
    """Поднять мок-1С, настроить env (учётки admin/manager/stranger, лимиты, пресеты)."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Mock1C)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    Mock1C.privileged_allowed = True
    users = tmp_path / "users.json"
    users.write_text(json.dumps({
        "admin": {"login": "admin", "password": "a"},
        "manager": {"login": "manager", "password": "m"},
        "stranger": {"login": "stranger", "password": "s"},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("ONEC_DATA_BASE_URL", "http://127.0.0.1:%d/base" % srv.server_address[1])
    monkeypatch.setenv("ONEC_DATA_USERS", str(users))
    monkeypatch.setenv("ONEC_DATA_DEFAULT_USER", "admin")
    monkeypatch.delenv("ONEC_DATA_FORBID_PRIVILEGED", raising=False)
    monkeypatch.delenv("ONEC_DATA_MASK_OFF", raising=False)
    monkeypatch.delenv("ONEC_DATA_PRESETS", raising=False)
    monkeypatch.setenv("ONEC_DATA_MAX_ROWS", "200")
    yield srv
    srv.shutdown()


# ── L2: OData ─────────────────────────────────────────────────────────────────

def test_odata_read_rls_and_masking(base):
    mod = _load()
    # admin видит все 3 строки; поле ИНН замаскировано по умолчанию (ПДн)
    res = mod.odata_read("Catalog_Номенклатура")
    assert res["fetched"] == 3 and res["shown"] == 3
    assert all(r["ИНН"] == "***" for r in res["rows"])
    assert res["rows"][0]["Description"] == "Диван"  # не-ПДн поля не тронуты
    # manager (RLS) видит одну
    res_m = mod.odata_read("Catalog_Номенклатура", user="manager")
    assert res_m["fetched"] == 1 and res_m["user"] == "manager"


def test_odata_read_mask_off_and_top(base, monkeypatch):
    monkeypatch.setenv("ONEC_DATA_MASK_OFF", "1")
    mod = _load()
    res = mod.odata_read("Catalog_Номенклатура", top=2)
    assert res["shown"] == 2
    assert res["rows"][0]["ИНН"] == "7701"  # маскирование выключено (тест-база)
    assert "$top=2" in unquote(Mock1C.last_path)  # top передан серверу, а не отрезан локально


def test_odata_metadata_list_and_entity(base):
    mod = _load()
    lst = mod.odata_metadata(mask="номенклат")
    assert lst["entities"] == ["Catalog_Номенклатура"]
    ent = mod.odata_metadata(entity="Catalog_Номенклатура")
    names = [p["name"] for p in ent["properties"]]
    assert "Ref_Key" in names and "Description" in names


def test_odata_virtual_literals(base):
    mod = _load()
    res = mod.odata_virtual("AccumulationRegister_Остатки", "Balance",
                            params={"Period": "2026-07-01",
                                    "Condition": "Склад_Key eq guid'11111111-1111-1111-1111-111111111111'"})
    assert res["rows"][0]["КоличествоBalance"] == 5
    # литералы OData: дата обёрнута в datetime'…'
    assert "Period=datetime'2026-07-01T00:00:00'" in unquote(Mock1C.last_path)


def test_unknown_user_alias(base):
    mod = _load()
    with pytest.raises(RuntimeError) as e:
        mod.odata_read("Catalog_Номенклатура", user="нет_такого")
    assert "не найдена" in str(e.value)


# ── L3: ai_debug ──────────────────────────────────────────────────────────────

def test_query_user_vs_privileged(base):
    mod = _load()
    under_user = mod.debug_query("ВЫБРАТЬ * ИЗ Справочник.Номенклатура", user="manager")
    assert under_user["fetched"] == 1 and under_user["mode"] == "user"
    etalon = mod.debug_query("ВЫБРАТЬ * ИЗ Справочник.Номенклатура",
                             user="manager", mode="privileged")
    assert etalon["fetched"] == 3 and etalon["mode"] == "privileged"


def test_privileged_denied_by_server(base):
    Mock1C.privileged_allowed = False  # прод-профиль базы: гейт расширения закрыт
    mod = _load()
    res = mod.debug_query("ВЫБРАТЬ 1", mode="privileged")
    assert "error" in res and "403" in res["error"]


def test_privileged_forbidden_locally(base, monkeypatch):
    monkeypatch.setenv("ONEC_DATA_FORBID_PRIVILEGED", "1")
    mod = _load()
    res = mod.debug_query("ВЫБРАТЬ 1", mode="privileged")
    assert "error" in res and "ONEC_DATA_FORBID_PRIVILEGED" in res["error"]
    # обычный режим при этом работает
    assert "rows" in mod.debug_query("ВЫБРАТЬ 1", mode="user")


def test_query_params_passthrough_and_masking(base):
    mod = _load()
    res = mod.debug_query("ВЫБРАТЬ …", params={"Дата": "2026-07-01T00:00:00"})
    assert res["query_params"] == {"Дата": "2026-07-01T00:00:00"}
    assert all(r["ИНН"] == "***" for r in res["rows"])  # маскирование и на L3


def test_report(base):
    mod = _load()
    res = mod.debug_report("ВедомостьПоТоварамНаСкладах", variant="Основной",
                           filters=[{"field": "Склад", "kind": "Равно", "value": "Главный"}],
                           user="manager")
    assert res["rows"][0]["Номенклатура"] == "Диван"
    assert res["variant"] == "Основной" and res["filters_applied"] == 1
    # отчёт без имени — ошибка сервера отдана наружу, не проглочена
    bad = mod.debug_report("")
    assert "error" in bad and "400" in bad["error"]


def test_settings(base):
    mod = _load()
    consts = mod.debug_settings("constants", names=["ВалютаУчета"])
    assert consts["constants"]["ВалютаУчета"] == "значение_ВалютаУчета"
    acc = mod.debug_settings("access", target_user="Иванов")
    assert acc["roles"] == ["Менеджер"] and acc["access_groups"] == ["Продажи"]


def test_call_whitelist(base):
    mod = _load()
    ok = mod.debug_call("ЦеныСервер.ЦенаНоменклатуры", args=["Диван"])
    assert ok["result"] == 990.0
    denied = mod.debug_call("ОпасныйМодуль.УдалитьВсё")
    assert "error" in denied and "403" in denied["error"]


def test_health(base):
    mod = _load()
    res = mod.debug_health(user="manager")
    assert res["session_user"] == "manager" and res["privileged_allowed"] is True


# ── RLS-матрица ───────────────────────────────────────────────────────────────

def test_rls_probe_query(base):
    mod = _load()
    res = mod.rls_probe(["admin", "manager"], query_text="ВЫБРАТЬ * ИЗ Справочник.Номенклатура")
    assert res["counts"] == {"admin": 3, "manager": 1}
    assert "РАЗЛИЧАЕТСЯ" in res["verdict"]
    assert len(res["matrix"]["manager"]["sample"]) == 1


def test_rls_probe_odata_no_diff(base):
    mod = _load()
    res = mod.rls_probe(["admin", "admin"], entity="Catalog_Номенклатура")
    assert "не видно" in res["verdict"]


# ── пресеты ───────────────────────────────────────────────────────────────────

def _write_presets(tmp_path, monkeypatch):
    presets = {
        "остатки_проблемного_склада": {
            "kind": "query", "description": "S1: остатки по складу под пользователем",
            "mode": "user", "user": "manager",
            "args": {"text": "ВЫБРАТЬ * ИЗ РегистрНакопления.Остатки", "params": {}},
            "params_allowed": ["Склад"],
        },
        "эталон_всех_строк": {
            "kind": "query", "description": "S7: эталон privileged",
            "mode": "privileged", "user": "admin",
            "args": {"text": "ВЫБРАТЬ * ИЗ Справочник.Номенклатура"},
            "params_allowed": [],
        },
        "цена_дивана": {
            "kind": "call", "mode": "user", "user": "admin",
            "args": {"method": "ЦеныСервер.ЦенаНоменклатуры", "args": ["{Номенклатура}"]},
            "params_allowed": ["Номенклатура"],
        },
    }
    f = tmp_path / "presets.json"
    f.write_text(json.dumps(presets, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("ONEC_DATA_PRESETS", str(f))


def test_preset_list_and_run(base, tmp_path, monkeypatch):
    _write_presets(tmp_path, monkeypatch)
    mod = _load()
    lst = mod.run_preset()
    assert {p["name"] for p in lst["presets"]} == {"остатки_проблемного_склада",
                                                  "эталон_всех_строк", "цена_дивана"}
    # пин пользователя и режима из пресета работает: manager+user → 1 строка (RLS)
    res = mod.run_preset("остатки_проблемного_склада", params={"Склад": "Главный"})
    assert res["fetched"] == 1 and res["query_params"] == {"Склад": "Главный"}
    # пин privileged из пресета: admin видит всё
    assert mod.run_preset("эталон_всех_строк")["fetched"] == 3
    # подстановка аргумента в call
    call = mod.run_preset("цена_дивана", params={"Номенклатура": "Диван"})
    assert call["args"] == ["Диван"] and call["result"] == 990.0


def test_preset_illegal_param_and_unknown(base, tmp_path, monkeypatch):
    _write_presets(tmp_path, monkeypatch)
    mod = _load()
    bad = mod.run_preset("эталон_всех_строк", params={"Взлом": 1})
    assert "error" in bad and "не разрешены" in bad["error"]
    assert "не найден" in mod.run_preset("нет_такого")["error"]


# ── фиксы adversarial-ревью (DISCIPLINE_ALLOW_TEST_EDIT) ──────────────────────

def test_masking_key_first_nested(base):
    """_mask_value: поле с ПДн-именем маскируется ЦЕЛИКОМ, даже если значение — структура."""
    mod = _load()
    row = {"Паспорт": {"Серия": "1234", "Номер": "567890"}, "Имя": "Иван", "Код": 7}
    masked = mod.mask_rows([row])[0]
    assert masked["Паспорт"] == "***"      # составное значение под ПДн-ключом скрыто целиком
    assert masked["Имя"] == "***"          # имя — тоже ПДн-токен
    assert masked["Код"] == 7              # не-ПДн скаляр не тронут


def test_masking_by_value_defeats_alias(base):
    """Обход маски алиасом колонки (ВЫБРАТЬ ИНН КАК X) закрывается маскированием по значению."""
    mod = _load()
    # значение похоже на ИНН(10)/email/телефон — маскируется независимо от имени поля
    rows = mod.mask_rows([{"X": "7707083893", "Y": "ivan@example.com", "Z": "обычный текст"}])
    assert rows[0]["X"] == "***"
    assert "***" in rows[0]["Y"]
    assert rows[0]["Z"] == "обычный текст"


def test_call_result_masked(base):
    """data_call: result метода получения данных с ПДн маскируется (не только rows)."""
    mod = _load()
    res = mod.debug_call("КонтрагентыСервер.Реквизиты")
    assert res["result"]["ИНН"] == "***"          # ключ ИНН
    assert res["result"]["Наименование"] == "ООО Ромашка"


def test_settings_user_settings_masked(base):
    """data_settings user_settings: блоб с email маскируется по значению."""
    mod = _load()
    res = mod.debug_settings("user_settings", names=["Ключ", "Настройка"], target_user="Иванов")
    assert "***" in res["user_settings"]["Почта"]
    assert res["user_settings"]["Тема"] == "Тёмная"


def test_rls_probe_sends_mode_user(base):
    """Инвариант 5: rls_probe ходит строго mode=user (иначе вывод про RLS ложный)."""
    mod = _load()
    mod.rls_probe(["admin", "manager"], query_text="ВЫБРАТЬ 1")
    assert Mock1C.last_body is not None and Mock1C.last_body.get("mode") == "user"


def test_rls_probe_all_errors_verdict(base):
    """rls_probe: если все запросы упали — вердикт «невозможен», не «RLS не влияет»."""
    mod = _load()
    res = mod.rls_probe(["нет1", "нет2"], query_text="ВЫБРАТЬ 1")
    assert "невозможен" in res["verdict"]


def test_odata_literal_escapes_quote(base):
    """Инвариант 6: одинарная кавычка в значении удваивается (защита OData-литерала)."""
    mod = _load()
    assert mod._odata_literal("O'Brien") == "'O''Brien'"


def test_preset_read_filter_injection_escaped(base, tmp_path, monkeypatch):
    """Инвариант 7: подстановка в $filter пресета kind=read экранируется как литерал —
    значение не может расширить условие отбора."""
    presets = {
        "поиск": {
            "kind": "read", "mode": "user", "user": "admin",
            "args": {"entity": "Catalog_Номенклатура", "filter": "Description eq {Имя}"},
            "params_allowed": ["Имя"],
        }
    }
    f = tmp_path / "p.json"
    f.write_text(json.dumps(presets, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("ONEC_DATA_PRESETS", str(f))
    mod = _load()
    mod.run_preset("поиск", params={"Имя": "Диван' or ИНН ne '"})
    from urllib.parse import unquote
    sent = unquote(Mock1C.last_path)
    # инъекция обезврежена: кавычка в значении удвоена, весь ввод — один строковый литерал
    assert "Description eq 'Диван'' or ИНН ne '''" in sent


def test_preset_cannot_override_mode_user(base, tmp_path, monkeypatch):
    """Пресетом запинены mode/user; попытка передать их параметром — отклонена (не в allowed)."""
    _write_presets(tmp_path, monkeypatch)
    mod = _load()
    bad = mod.run_preset("остатки_проблемного_склада", params={"mode": "privileged"})
    assert "error" in bad and "не разрешены" in bad["error"]


def test_invalid_mode_rejected(base):
    mod = _load()
    res = mod.debug_query("ВЫБРАТЬ 1", mode="admin")
    assert "error" in res and "user" in res["error"]


def test_forbid_privileged_fail_closed(base, monkeypatch):
    """Запретительный флаг fail-closed: любое непустое значение запрещает privileged."""
    monkeypatch.setenv("ONEC_DATA_FORBID_PRIVILEGED", "true")
    mod = _load()
    res = mod.debug_query("ВЫБРАТЬ 1", mode="privileged")
    assert "error" in res and "запрещён" in res["error"]


def test_negative_top_clamped(base):
    """Отрицательный top не уходит серверу как $top=-5."""
    mod = _load()
    mod.odata_read("Catalog_Номенклатура", top=-5)
    from urllib.parse import unquote
    assert "$top=-" not in unquote(Mock1C.last_path)


def test_broken_users_file_keeps_env_login(base, tmp_path, monkeypatch):
    """Битый ONEC_DATA_USERS не блокирует рабочий default из ONEC_DATA_LOGIN."""
    bad = tmp_path / "bad.json"
    bad.write_text("{ не json", encoding="utf-8")
    monkeypatch.setenv("ONEC_DATA_USERS", str(bad))
    monkeypatch.setenv("ONEC_DATA_LOGIN", "admin")
    monkeypatch.setenv("ONEC_DATA_PASSWORD", "a")
    monkeypatch.setenv("ONEC_DATA_DEFAULT_USER", "default")
    mod = _load()
    res = mod.odata_read("Catalog_Номенклатура")  # default доступен, несмотря на битый файл
    assert res.get("fetched") == 3
    # но запрос несуществующего alias всё же сообщает про ошибку файла
    import pytest as _pt
    with _pt.raises(RuntimeError):
        mod.odata_read("Catalog_Номенклатура", user="manager")


def test_garbage_max_rows_env_no_crash(base, monkeypatch):
    monkeypatch.setenv("ONEC_DATA_MAX_ROWS", "не число")
    mod = _load()
    res = mod.odata_read("Catalog_Номенклатура")
    assert res.get("fetched") == 3  # фолбэк на дефолт, без ValueError
