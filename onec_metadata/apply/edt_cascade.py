"""Делегирование КАСКАДНОГО рефакторинга (rename/delete с починкой ссылок в BSL,
формах и метаданных) внешнему EDT-MCP (DitriXNew/EDT-MCP) — «сервис каскада».

Наш lxml-движок сознательно НЕ делает деструктив с каскадом (scope-граница,
см. docs/architecture-review-2026-07.md): это надёжно умеет только внутренняя
модель EDT. Плагин ставится в EDT p2-директором (README DitriX), поднимается
headless (EDT_MCP_AUTO_START) и отдаёт MCP streamable-HTTP на :8765.

Протокол снят с живого обмена (EDT 2025.2.6, плагин 2.6.1, 2026-07-14):
- POST /mcp, JSON-RPC; ответы SSE-строками `event: message / data: {json}`;
- initialize → заголовок ответа Mcp-Session-Id, дальше он обязателен;
- rename_metadata_object(projectName, objectFqn, newName[, confirm]) —
  ДВУХФАЗНО: без confirm возвращает preview (таблица точек изменения),
  с confirm=True исполняет; delete_metadata — аналогично.

⚠ Боевые нюансы (сняты живыми прогонами 2026-07-14):
- /health ready наступает РАНЬШЕ готовности BM-модели проекта — перед первой
  операцией ждать `wait_project()`, иначе «Could not get configuration for project».
- Ответ «executed» приходит РАНЬШЕ, чем изменения долетают на диск (flush
  асинхронный) — после операции ждать `wait_disk()`. Повторные операции в той же
  сессии флашатся ещё дольше (>2 мин наблюдалось); надёжный барьер перед
  git-операциями — `stop_edt()` и проверка файлов после остановки.
- Для автоматизации деструктива нужен env EDT_MCP_DESTRUCTIVE_CONSENT=allow на
  процессе EDT — включать только для ТЕСТ-копий, не для прод-веток.

Только stdlib (urllib), как и остальной apply-слой.
"""
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8765"


class CascadeError(RuntimeError):
    pass


def _parse_sse(raw: str):
    """Достать последний data:-JSON из SSE-ответа."""
    datas = [l[6:] for l in raw.splitlines() if l.startswith("data: ")]
    if not datas:
        raise CascadeError(f"нет data-строк в ответе MCP: {raw[:200]!r}")
    return json.loads(datas[-1])


def _parse_front_matter(text: str) -> dict:
    """Плагин отвечает markdown-ресурсом с YAML-шапкой (--- key: value ---)."""
    m = re.match(r"---\n(.*?)\n---", text, re.S)
    out = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                v = v.strip()
                out[k.strip()] = int(v) if v.lstrip("-").isdigit() else v
    return out


class CascadeClient:
    """MCP-клиент к работающему EDT-MCP. Жизненным циклом EDT управляет boot_edt()."""

    def __init__(self, url: str = DEFAULT_URL, timeout: int = 300):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.session_id = None

    # ---------- транспорт ----------

    def _post(self, payload: dict, with_session: bool = True):
        req = urllib.request.Request(
            self.url + "/mcp", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream"})
        if with_session and self.session_id:
            req.add_header("Mcp-Session-Id", self.session_id)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid
            raw = resp.read().decode("utf-8", errors="replace")
        if resp.status == 202 or not raw.strip():
            return None
        msg = _parse_sse(raw)
        if "error" in msg:
            raise CascadeError(f"MCP error: {msg['error']}")
        return msg.get("result")

    # ---------- протокол ----------

    def health(self) -> dict:
        with urllib.request.urlopen(self.url + "/health", timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    def initialize(self) -> dict:
        result = self._post({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                       "clientInfo": {"name": "onec-metadata-cascade", "version": "1.0"}}})
        if not self.session_id:
            raise CascadeError("сервер не вернул Mcp-Session-Id")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def call_tool(self, name: str, arguments: dict) -> dict:
        if not self.session_id:
            raise CascadeError("сначала initialize()")
        result = self._post({
            "jsonrpc": "2.0", "id": int(time.time() * 1000) % 10**9,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments}})
        if result is None:
            raise CascadeError(f"пустой ответ tools/call {name}")
        if result.get("isError"):
            raise CascadeError(f"инструмент {name}: {json.dumps(result)[:400]}")
        # текст из content (text | resource.text)
        text = ""
        for c in result.get("content", []):
            if c.get("type") == "text":
                text += c.get("text", "")
            elif c.get("type") == "resource":
                text += c.get("resource", {}).get("text", "")
        parsed = _parse_front_matter(text)
        parsed["_text"] = text
        return parsed

    def wait_project(self, project: str, timeout: int = 300, poll: float = 5.0) -> bool:
        """Дождаться, пока проект в workspace станет ready (проиндексирован).
        Боевой урок: /health ready наступает РАНЬШЕ готовности BM-модели проекта —
        rename сразу после старта падает «Could not get configuration for project»."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                text = self.call_tool("list_projects", {}).get("_text", "")
                if any(project in line and "ready" in line
                       for line in text.splitlines() if line.startswith("|")):
                    return True
            except CascadeError:
                pass
            time.sleep(poll)
        return False

    # ---------- каскадные операции (двухфазные) ----------

    def rename(self, project: str, fqn: str, new_name: str, confirm: bool = False) -> dict:
        args = {"projectName": project, "objectFqn": fqn, "newName": new_name}
        if confirm:
            args["confirm"] = True
        return self.call_tool("rename_metadata_object", args)

    def delete(self, project: str, fqn: str, confirm: bool = False) -> dict:
        args = {"projectName": project, "objectFqn": fqn}
        if confirm:
            args["confirm"] = True
        return self.call_tool("delete_metadata", args)

    # ---------- ожидание flush на диск ----------

    def wait_disk(self, predicate, timeout: int = 120, poll: float = 1.0) -> bool:
        """Ждать, пока predicate() не станет истинным (изменения долетели на диск).
        Пример: lambda: 'НовоеИмя' in path.read_text(...)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(poll)
        return False


def boot_edt(edt_exe: str | Path, workspace: str | Path,
             import_projects: str = "", port: int = 8765,
             destructive_consent: bool = False, wait: int = 600) -> subprocess.Popen:
    """Поднять EDT с плагином EDT-MCP headless-ботом и дождаться /health ready.

    edt_exe — путь к 1cedt.exe (Windows) / eclipse (Linux, под xvfb-run снаружи).
    import_projects — пути EDT-проектов через ':' для авто-импорта в чистый workspace.
    destructive_consent=True — ТОЛЬКО для тест-копий (авто-подтверждение деструктива).
    Возвращает процесс; останавливать stop_edt(proc).
    """
    env = dict(os.environ)
    env["EDT_MCP_AUTO_START"] = "true"
    env["EDT_MCP_PORT"] = str(port)
    if import_projects:
        env["EDT_MCP_IMPORT_PROJECTS"] = import_projects
    if destructive_consent:
        env["EDT_MCP_DESTRUCTIVE_CONSENT"] = "allow"
    proc = subprocess.Popen(
        [str(edt_exe), "-data", str(workspace), "-clean", "-nosplash",
         "--launcher.suppressErrors"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    client = CascadeClient(f"http://127.0.0.1:{port}")
    deadline = time.time() + wait
    while time.time() < deadline:
        if proc.poll() is not None:
            raise CascadeError(f"EDT завершился при старте (код {proc.returncode})")
        try:
            if client.health().get("ready"):
                return proc
        except Exception:
            pass
        time.sleep(5)
    proc.terminate()
    raise CascadeError(f"EDT-MCP не поднялся за {wait} с на :{port}")


def stop_edt(proc: subprocess.Popen, grace: int = 15) -> None:
    proc.terminate()
    try:
        proc.wait(grace)
    except subprocess.TimeoutExpired:
        proc.kill()
