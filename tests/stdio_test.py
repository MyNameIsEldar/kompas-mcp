"""Проверка сервера через настоящий MCP-клиент по stdio — так его запускает Claude Code.

Создаёт в КОМПАСе один чертёж, рисует, смотрит превью и закрывает его без сохранения.
"""
import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def text_of(res) -> str:
    return "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")


async def main():
    # необязательный аргумент — другой server.py, например из распакованного .mcpb
    server_py = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "server.py")
    params = StdioServerParameters(command=sys.executable, args=[server_py],
                                   env={**os.environ, "PYTHONUTF8": "1"})
    async with stdio_client(params) as (r, w), ClientSession(r, w) as session:
        init = await session.initialize()
        print("server:", init.server_info.name, "| instructions:", bool(init.instructions))
        tools = (await session.list_tools()).tools
        print(f"tools ({len(tools)}):", ", ".join(t.name for t in tools))

        st = json.loads(text_of(await session.call_tool("kompas_status", {})))
        print("КОМПАС:", st["application"], "| документов:", len(st["documents"]))

        await session.call_tool("kompas_new", {"doc_type": "drawing"})
        res = await session.call_tool("kompas_draw", {"items": [
            {"type": "rect", "x": 50, "y": 120, "w": 110, "h": 70},
            {"type": "circle", "xc": 105, "yc": 155, "r": 20},
        ]})
        print("draw:", text_of(res).replace("\n", " ")[:120])

        res = await session.call_tool("kompas_preview", {"max_px": 600})
        imgs = [c for c in res.content if getattr(c, "type", "") == "image"]
        assert imgs and imgs[0].mime_type == "image/png", res
        print("preview: картинка", imgs[0].mime_type, len(imgs[0].data), "символов base64")

        res = await session.call_tool("kompas_draw", {"items": [{"type": "spline"}]})
        assert res.is_error, "ожидалась ошибка"
        print("ошибка доходит до клиента:", text_of(res)[:120])

        res = await session.call_tool("kompas_close", {"save": False})
        print("close:", json.loads(text_of(res))["closed"] == "")
    print("STDIO OK")


asyncio.run(main())
