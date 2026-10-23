"""Воспроизведение зависания: создание A1-чертежа и макрообъекта через kompas_run_python по stdio.

Сервер запускается с KOMPAS_MCP_DUMP_STACKS=20 — при зависании стеки потоков попадут в stderr.
"""
import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE = r"""
d = app7.Documents.Add(1, True)
kompas.activate(d)
d2 = API7.IKompasDocument2D(d)
fmt = d2.LayoutSheets.Item(0).Format
fmt.Format = 1; fmt.VerticalOrientation = False
d2.LayoutSheets.Item(0).Update()
k5doc = app5.ActiveDocument2D()
print('fmt', fmt.FormatWidth, fmt.FormatHeight)
k5doc.ksMacro(0)
k5doc.ksLineSeg(100, 300, 140, 300, 1)
k5doc.ksCircle(120, 300, 15, 1)
k5doc.ksArcByAngle(120, 300, 10, 0, 90, 1, 1)
k5doc.ksColouring(0)
k5doc.ksLineSeg(150, 290, 150, 310, 1)
k5doc.ksLineSeg(150, 310, 170, 300, 1)
k5doc.ksLineSeg(170, 300, 150, 290, 1)
ref_fill = k5doc.ksEndObj()
txt = k5doc.ksText(100, 320, 0, 5, 1, 0, 'К1')
ref = k5doc.ksEndObj()
print('macro ref', ref, 'fill', ref_fill, 'text', txt)
dc = API7.IDrawingContainer(view())
print('macros', dc.MacroObjects.Count, 'colourings', dc.Colourings.Count, 'lines', dc.LineSegments.Count)
m = dc.MacroObjects.Item(0)
print('macro reference api7', m.Reference)
"""


async def main():
    params = StdioServerParameters(command=sys.executable, args=[os.path.join(ROOT, "server.py")],
                                   env={**os.environ, "PYTHONUTF8": "1", "KOMPAS_MCP_DUMP_STACKS": "20"})
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        for i in range(3):
            res = await asyncio.wait_for(s.call_tool("kompas_run_python", {"code": CODE}), 90)
            print(i, "->", [c.text for c in res.content if getattr(c, "type", "") == "text"], flush=True)
            await asyncio.wait_for(s.call_tool("kompas_close", {"save": False}), 60)
    print("NO HANG")


asyncio.run(main())
