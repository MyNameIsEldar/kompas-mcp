"""Смоук-тест сервера на живом КОМПАСе: вызывает инструменты так же, как MCP-клиент.

Запуск:  python tests/smoke_test.py [имя_теста ...]
Картинки предпросмотра сохраняются в tests/out/ для ручной проверки.
"""
import asyncio
import base64
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "tests", "out")
os.makedirs(OUT, exist_ok=True)

import server  # noqa: E402


async def call(name, /, **args):  # name позиционный: у инструментов бывает аргумент name
    res = await server.mcp.call_tool(name, args)
    content = getattr(res, "content", res)
    if isinstance(content, tuple):
        content = content[0]
    texts, n_img = [], 0
    for c in content:
        if getattr(c, "type", "") == "image":
            n_img += 1
            path = os.path.join(OUT, f"{name}_{len(os.listdir(OUT))}.png")
            with open(path, "wb") as f:
                f.write(base64.b64decode(c.data))
            texts.append(f"<image -> {path}>")
        else:
            texts.append(getattr(c, "text", str(c)))
    if getattr(res, "isError", False) or getattr(res, "is_error", False):
        raise RuntimeError(f"{name} -> ERROR: " + "\n".join(texts))
    out = "\n".join(texts)
    print(f"--- {name} {json.dumps(args, ensure_ascii=False)[:200]}\n{out[:1500]}\n")
    return out


async def t_status():
    await call("kompas_status")


async def t_drawing():
    await call("kompas_new", doc_type="drawing", sheet_format="A4", vertical=True)
    created = json.loads(await call("kompas_draw", items=[
        {"type": "rect", "x": 60, "y": 150, "w": 100, "h": 60},
        {"type": "circle", "xc": 85, "yc": 180, "r": 8},
        {"type": "circle", "xc": 135, "yc": 180, "r": 8},
        {"type": "line", "x1": 70, "y1": 180, "x2": 150, "y2": 180, "style": "axial"},
        {"type": "arc", "xc": 110, "yc": 210, "r": 15, "a1": 180, "a2": 0, "ccw": False},
        {"type": "polyline", "points": [[60, 120], [90, 120], [100, 135], [60, 135]], "closed": True},
        {"type": "text", "x": 60, "y": 100, "text": "Тестовая надпись", "height": 5},
    ]))
    arc_ref = next(c["reference"] for c in created if c["type"] == "arc")
    await call("kompas_dimensions", items=[
        {"type": "linear", "x1": 60, "y1": 150, "x2": 160, "y2": 150, "x3": 110, "y3": 140, "orientation": "horizontal"},
        {"type": "linear", "x1": 160, "y1": 150, "x2": 160, "y2": 210, "x3": 172, "y3": 180, "orientation": "vertical"},
        {"type": "diameter", "xc": 85, "yc": 180, "r": 8, "angle": 135, "prefix": "2 отв. "},
        {"type": "radius", "object": arc_ref},  # стрелка должна попасть в середину дуги (270°)
    ])
    await call("kompas_stamp", values={"1": "Тестовая пластина", "2": "ТП.001", "3": "Сталь 45", "110": "Claude"})
    await call("kompas_list_objects", limit=50)
    await call("kompas_preview")
    await call("kompas_preview", region=[50, 110, 180, 230])


async def t_delete():
    lst = json.loads(await call("kompas_list_objects"))
    texts = [o["reference"] for o in lst["objects"] if o["type"] == "text"]
    await call("kompas_delete", references=texts)


async def t_views():
    await call("kompas_views", create={"x": 40, "y": 60, "scale": 0.5, "name": "Вид М1:2"})
    await call("kompas_draw", items=[{"type": "rect", "x": 0, "y": 0, "w": 100, "h": 40}])
    await call("kompas_views")
    await call("kompas_sheet")


async def t_part():
    await call("kompas_new", doc_type="part")
    await call("kompas_3d_feature", plane="XOY", sketch=[{"type": "rect", "x": -50, "y": -30, "w": 100, "h": 60}],
               depth=20)
    await call("kompas_3d_feature", plane="XOY", sketch=[{"type": "circle", "xc": 0, "yc": 0, "r": 10}],
               mode="cut", through_all=True)
    await call("kompas_3d_feature", plane="XOY", plane_offset=20,
               sketch=[{"type": "circle", "xc": 30, "yc": 0, "r": 8}], depth=15)
    # полукруглый выступ: дуга ccw от 0° до 180° должна выпирать в +Y
    await call("kompas_3d_feature", plane="XOY", plane_offset=20, depth=5, sketch=[
        {"type": "arc", "xc": -25, "yc": 30, "r": 12, "a1": 0, "a2": 180, "ccw": True},
        {"type": "line", "x1": -37, "y1": 30, "x2": -13, "y2": 30},
    ])
    await call("kompas_preview")
    await call("kompas_preview", orientation="Сверху")


async def t_revolve():
    await call("kompas_new", doc_type="part")
    await call("kompas_3d_feature", plane="XOY", operation="revolve", sketch=[
        {"type": "line", "x1": 0, "y1": 0, "x2": 60, "y2": 0, "style": "axial"},
        {"type": "polyline", "points": [[0, 0], [0, 15], [25, 15], [25, 10], [60, 10], [60, 0]], "closed": True},
    ])
    await call("kompas_preview")


async def t_misc():
    await call("kompas_api_search", query="ksLinD")
    await call("kompas_api_search", query="", interface="IArc")
    await call("kompas_run_python", code="print(app7.ApplicationName(True)); result = doc.Name")
    await call("kompas_dialogs")
    await call("kompas_window_screenshot", max_px=1000)


TESTS = {n[2:]: f for n, f in globals().items() if n.startswith("t_")}


async def main(names):
    for n in names or TESTS:
        print(f"=========== {n}")
        await TESTS[n]()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
