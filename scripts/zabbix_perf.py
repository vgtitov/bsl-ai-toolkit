# /// script
# dependencies = []
# ///
"""CLI анализа производительности по Zabbix (read-only) поверх mcp/onec_ops_mcp.py.
Итог — предложения, ранжированные по вкладу в производительность (значимое первым).

Примеры:
  python scripts/zabbix_perf.py --dashboard 408,410 --hosts "vc-1c-*" --window 24
  python scripts/zabbix_perf.py --hosts "kz-*" --window 1 --json
  ZABBIX_URL=http://zbx.example/api_jsonrpc.php ZABBIX_TOKEN=... python scripts/zabbix_perf.py --dashboard 408

Аутентификация (в порядке приоритета):
  --token / env ZABBIX_TOKEN        — API-токен (создаётся в Zabbix: Пользователь → API tokens; read-only)
  --user (+ пароль интерактивно)    — user.login, сессия только на время запуска
"""
import argparse
import getpass
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
import onec_ops_mcp as ops  # noqa: E402

# нет переменных в окружении → подхватить .env (текущий каталог, затем корень репо)
ops.load_dotenv_defaults()


def main():
    p = argparse.ArgumentParser(description="Zabbix perf-анализ (read-only): находки по вкладу в производительность")
    p.add_argument("--url", default=os.environ.get("ZABBIX_URL", ""),
                   help="URL API (http://host/api_jsonrpc.php); можно дать URL дашборда — обрежется сам")
    p.add_argument("--token", default=os.environ.get("ZABBIX_TOKEN", ""), help="API-токен (или env ZABBIX_TOKEN)")
    p.add_argument("--user", default=os.environ.get("ZABBIX_USER", ""),
                   help="логин для user.login (пароль спросится интерактивно), если нет токена")
    p.add_argument("--dashboard", default="", help="dashboardid (из URL ...dashboardid=408); несколько через запятую: 408,410")
    p.add_argument("--hosts", default="", help="имена/шаблоны хостов через запятую (напр. 'kz-*,SQL*')")
    p.add_argument("--window", type=float, default=1.0, help="окно анализа, часов (default 1; >48ч — тренды)")
    p.add_argument("--licensed-cores", default="", metavar="host=N,host2=M",
                   help="лимит ядер лицензии 1С по хостам (ПРОФ = 12 на рабочий сервер): контроль потолка rphost")
    p.add_argument("--maps-dir", default=os.environ.get("ONEC_STORAGE_MAPS_DIR", ""),
                   help="каталог карт структуры хранения (или env ONEC_STORAGE_MAPS_DIR) — расшифровка таблиц в SQL-уликах")
    p.add_argument("--json", action="store_true", help="вывести сырой JSON отчёта вместо markdown")
    a = p.parse_args()

    licensed = {}
    for part in a.licensed_cores.split(","):
        if "=" in part:
            h, n = part.split("=", 1)
            try:
                licensed[h.strip()] = int(n)
            except ValueError:
                p.error(f"--licensed-cores: не число в '{part.strip()}'")

    url = a.url.strip()
    if not url:
        p.error("нет URL: --url или env ZABBIX_URL")
    if "api_jsonrpc.php" not in url:  # дали URL фронтенда/дашборда — привести к API
        url = url.split("/zabbix.php")[0].split("/index.php")[0].rstrip("/") + "/api_jsonrpc.php"
    if not a.dashboard and not a.hosts:
        p.error("нужен --dashboard и/или --hosts")

    auth = a.token.strip() or None
    if not auth and a.user:
        pwd = getpass.getpass(f"Пароль Zabbix для {a.user}: ")
        auth = ops._zbx_call(url, "user.login", {"username": a.user, "password": pwd})
        print(f"# сессия user.login получена ({a.user})", file=sys.stderr)
    if not auth:
        print("# без токена/логина: большинству инсталляций нужны права (env ZABBIX_TOKEN)", file=sys.stderr)

    try:
        rep = ops.zabbix_perf_report(url, auth=auth,
                                     dashboardid=a.dashboard or None,
                                     hosts=[h.strip() for h in a.hosts.split(",") if h.strip()] or None,
                                     window_hours=a.window,
                                     licensed_cores_by_host=licensed or None,
                                     maps_dir=a.maps_dir or None)
    except RuntimeError as e:
        sys.exit(f"ОШИБКА: {e}\nПодсказка: нужен read-only API-токен (Zabbix: Профиль пользователя → API tokens) "
                 f"в env ZABBIX_TOKEN, либо --user <логин>.")
    if a.json:
        json.dump(rep, sys.stdout, ensure_ascii=False, indent=2)
    else:
        print(ops.render_perf_report(rep))


if __name__ == "__main__":
    main()
