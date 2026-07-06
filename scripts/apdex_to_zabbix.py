# /// script
# dependencies = []
# ///
"""Apdex из штатного экспорта БСП «Оценка производительности» → Zabbix (trapper items).

Цикл: регламентное задание 1С «ЭкспортОценкиПроизводительности» пишет XML в каталог →
этот скрипт по cron/планировщику разбирает свежие файлы, считает Apdex по ключевым
операциям (T = targetValue операции) и шлёт значения через zabbix_sender.
В Zabbix заводятся trapper-item'ы: 1c.apdex[<Операция>], 1c.apdex.worst, 1c.apdex.worst.name.

Примеры:
  python scripts/apdex_to_zabbix.py --source /var/1c/apdex-export --zbx-host vc-1c-app-01 --dry-run
  python scripts/apdex_to_zabbix.py --source "\\\\fs\\apdex$" --zbx-host APP01 --config /etc/zabbix/zabbix_agentd.conf

Без Zabbix тот же разбор доступен как MCP-инструмент bsp_apdex_parse_tool (или --dry-run --json).
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
import onec_ops_mcp as ops  # noqa: E402

# нет переменных в окружении → подхватить .env (текущий каталог, затем корень репо)
ops.load_dotenv_defaults()


def main():
    p = argparse.ArgumentParser(description="Apdex из экспорта БСП → zabbix_sender (trapper)")
    p.add_argument("--source", required=True, help="каталог (или файл) XML-экспорта БСП")
    p.add_argument("--zbx-host", required=True, help="имя хоста, как он заведён в Zabbix")
    p.add_argument("--config", default="", help="путь zabbix_agentd.conf (адрес сервера возьмёт sender)")
    p.add_argument("--server", default="", help="адрес Zabbix-сервера (если без --config)")
    p.add_argument("--sender", default="zabbix_sender", help="путь к бинарю zabbix_sender")
    p.add_argument("--default-t", type=float, default=1.0, help="T для операций без targetValue")
    p.add_argument("--dry-run", action="store_true", help="не отправлять — показать строки")
    p.add_argument("--json", action="store_true", help="вывести разбор Apdex как JSON")
    a = p.parse_args()

    res = ops.bsp_apdex_parse(a.source, default_t=a.default_t)
    if a.json:
        json.dump(res, sys.stdout, ensure_ascii=False, indent=2)
        return
    for w in res["warnings"]:
        print(f"# ⚠ {w}", file=sys.stderr)
    if not res["operations"]:
        sys.exit(f"нет замеров в {a.source} (files={res['files']}) — проверь каталог экспорта и рег. задание")

    lines = ops.apdex_sender_lines(res, a.zbx_host)
    if a.dry_run:
        print("\n".join(lines))
        return
    cmd = [a.sender, "-i", "-"]
    if a.config:
        cmd += ["-c", a.config]
    if a.server:
        cmd += ["-z", a.server]
    r = subprocess.run(cmd, input="\n".join(lines) + "\n", text=True, capture_output=True, timeout=60)
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode != 0:
        sys.exit(f"zabbix_sender завершился с кодом {r.returncode}")


if __name__ == "__main__":
    main()
