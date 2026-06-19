# /// script
# dependencies = ["mcp"]
# ///
"""Smoke-проверка центрального erp-1c по HTTP (SSE): подключиться, перечислить инструменты,
вызвать list_modules/find_object/search_1c на реальном наборе репозиториев. Не часть pytest —
ручная обкатка HTTP-транспорта. Запуск: uv run --with mcp tests/smoke_http_client.py [URL]."""
import sys, asyncio
from mcp import ClientSession
from mcp.client.sse import sse_client

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/sse"


async def main():
    async with sse_client(URL) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("TOOLS:", [t.name for t in tools.tools])

            mods = await s.call_tool("list_modules", {})
            txt = mods.content[0].text
            tags = sorted({ln.split("[")[1].split("]")[0]
                           for ln in txt.splitlines() if ln.startswith("Общие модули")})
            print("LAYERS:", tags)

            fo = await s.call_tool("find_object", {"name": "ВалютыДокументов"})
            print("FIND head:", fo.content[0].text.splitlines()[:2])

            sr = await s.call_tool("search_1c", {"query": "Процедура ПриСозданииНаСервере", "max_results": 3})
            print("SEARCH head:", sr.content[0].text.splitlines()[:2])
            print("OK")


asyncio.run(main())
