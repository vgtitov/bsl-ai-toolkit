# /// script
# dependencies = []
# ///
"""Переключение источника onec-code между ЛОКАЛЬНЫМ движком и ЦЕНТРАЛЬНЫМ HTTP-сервисом — переписывает
ТОЛЬКО блок сервера `onec-code` в развёрнутом `.mcp.json`, остальные серверы (bsl-platform, bsl-ls и т.п.) не трогает.

Часть ядра toolkit: центральный сервис (docker-compose + Caddy) уже здесь, этот скрипт — клиентская сторона
переключения. Локализации получают его вендорингом и пользуются как есть (профили/конфиги — их данные).

Режимы (--mode или env ONEC_MCP_MODE, по умолчанию auto):
  local   — stdio-движок читает клоны из ONEC_SRC_DIR (блок берётся из mcp/<profile>.mcp.json данного репозитория).
  central — SSE к центральному сервису; URL и токен — через env-плейсхолдеры ${ONEC_MCP_URL}/${ONEC_MCP_TOKEN}
            (секрет в файл НЕ пишется, остаётся в окружении).
  auto    — если задан ONEC_MCP_URL и центр доступен по TCP → central, иначе local (безопасный откат).

Переход на центр = одна команда (её зовёт onboard и может выполнить сам ассистент):
  uv run scripts/switch_source.py --mode auto      # после раздачи ONEC_MCP_URL/ONEC_MCP_TOKEN сам подхватит центр
Кросс-платформенно (только стандартная библиотека). После запуска — перезапустить Claude Code.
"""
import argparse, json, os, socket, sys
from urllib.parse import urlparse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _reachable(url, timeout=1.5):
    """TCP-доступность хоста центра (без HTTP-запроса — быстро и без зависимостей)."""
    if not url:
        return False
    try:
        u = urlparse(url)
        host, port = u.hostname, (u.port or (443 if u.scheme == "https" else 80))
        if not host:
            return False
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _local_block(profile):
    """Канонический локальный блок onec-code — из mcp/<profile>.mcp.json этого репозитория (сохраняет env профиля).
    Если профиль не найден — минимальный generic-блок (движок читает ONEC_SRC_DIR)."""
    src = os.path.join(REPO_ROOT, "mcp", f"{profile}.mcp.json")
    if os.path.isfile(src):
        with open(src, encoding="utf-8") as f:
            blk = json.load(f).get("mcpServers", {}).get("onec-code")
            if blk:
                return blk
    env = {"ONEC_SRC_DIR": "${ONEC_SRC_DIR}"}
    if os.environ.get("ONEC_LAYERS_CONFIG"):
        env["ONEC_LAYERS_CONFIG"] = "${ONEC_LAYERS_CONFIG}"
    return {"type": "stdio", "command": "uv",
            "args": ["run", "--quiet", "${ONEC_SRC_DIR}/onec_mcp.py"], "env": env}


def _central_block():
    """Блок onec-code для центрального HTTP-сервиса. URL и токен — env-плейсхолдеры (секрет не в файле)."""
    return {"type": "sse", "url": "${ONEC_MCP_URL}",
            "headers": {"Authorization": "Bearer ${ONEC_MCP_TOKEN}"}}


def switch(workdir, mode, profile):
    url = os.environ.get("ONEC_MCP_URL", "").strip()
    if mode == "auto":
        mode = "central" if _reachable(url) else "local"
        print(f"[auto] ONEC_MCP_URL={'задан' if url else 'не задан'}, центр "
              f"{'доступен' if mode == 'central' else 'недоступен'} → режим {mode}")
    mcp_path = os.path.join(workdir, ".mcp.json")
    if not os.path.isfile(mcp_path):
        print(f"[!] не найден {mcp_path} — сначала onboard разложит профиль", file=sys.stderr)
        return 1
    with open(mcp_path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("mcpServers", {})
    # вычистить легаси-ключ прежнего имени сервера (erp-1c) из ранее развёрнутого конфига —
    # иначе Claude Code пытается поднять его по несуществующему erp_mcp.py и ругается.
    for legacy in ("erp-1c",):
        cfg["mcpServers"].pop(legacy, None)
    cfg["mcpServers"]["onec-code"] = _central_block() if mode == "central" else _local_block(profile)
    with open(mcp_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    src = "env ONEC_MCP_URL/ONEC_MCP_TOKEN" if mode == "central" else "клоны из ONEC_SRC_DIR"
    print(f"[ok] onec-code → {mode} в {mcp_path} (источник: {src}). Перезапусти Claude Code.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default=os.environ.get("ONEC_MCP_MODE", "auto"),
                    choices=["auto", "local", "central"])
    ap.add_argument("--profile", default=os.environ.get("ONEC_ACTIVE_PROFILE", "dev"))
    ap.add_argument("--workdir", default=os.getcwd(), help="каталог с .mcp.json")
    args = ap.parse_args()
    sys.exit(switch(args.workdir, args.mode, args.profile))


if __name__ == "__main__":
    main()
