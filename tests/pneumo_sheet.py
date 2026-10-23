"""Лист A1 со всеми условными обозначениями пневмосхем — для сверки с таблицей обозначений.

Запуск:  python tests/pneumo_sheet.py
Картинки — в tests/out/pneumo_*.png. Документ — новый, во временной папке; после проверки закрывается (--keep — оставить открытым).
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_test as s  # noqa: E402

COLS, CELL_W, CELL_H = 11, 72, 82
X0, Y_TOP = 60, 545
# параметры для символов, у которых они есть, — чтобы на листе был виден типичный вариант
PARAMS = {"instrument": {"code": "TIC", "position": "101", "panel": True, "marks": ["H", "L"]},
          "line_break": {"text": "на факел"}}


async def main():
    d = await s.TempDrawing("kmcp_pneumo_sheet").create()
    cat = json.loads(await s.call("kompas_symbols_catalog"))["symbols"]
    items, captions = [], []
    for i, sym in enumerate(cat):
        cx = X0 + (i % COLS) * CELL_W
        cy = Y_TOP - (i // COLS) * CELL_H
        items.append({"symbol": sym["symbol"], "x": cx, "y": cy, "leads": 5, **PARAMS.get(sym["symbol"], {})})
        captions.append({"type": "text", "x": cx, "y": cy - 36, "text": sym["symbol"], "height": 3.5, "align": 1})
    placed = json.loads(await d.call("kompas_place_symbols", items=items))
    await d.call("kompas_draw", items=captions)
    print("вставлено:", len(placed))

    # мини-схема справа от таблицы: компрессор → обратный → запорный → ресивер (+ предохранительный)
    # → фильтр с конденсатоотводом → потребитель; регулятор давления — штриховой линией управления
    y = 110
    chain = json.loads(await d.call("kompas_place_symbols", items=[
        {"symbol": "compressor", "x": 470, "y": y, "label": "К1"},
        {"symbol": "check_valve", "x": 530, "y": y, "rotate": 180},      # поток слева направо
        {"symbol": "valve", "x": 580, "y": y},
        {"symbol": "receiver", "x": 640, "y": y - 5, "label": "Р"},
        {"symbol": "safety_valve_angle", "x": 640, "y": y + 40},
        {"symbol": "filter_drain", "x": 710, "y": y + 3, "label": "Ф"},
        {"symbol": "consumer", "x": 780, "y": y + 40},
    ]))
    p = [c["ports"] for c in chain]
    pt = lambda port: [port["x"], port["y"]]  # noqa: E731
    await d.call("kompas_pipes", items=[
        {"points": [pt(p[0]["out"]), pt(p[1]["in"])]},
        {"points": [pt(p[1]["out"]), pt(p[2]["in"])]},
        {"points": [pt(p[2]["out"]), pt(p[3]["in"])], "route": "hv"},
        {"points": [pt(p[3]["top"]), pt(p[4]["out"])]},
        {"points": [pt(p[3]["out"]), pt(p[5]["in"])], "route": "vh"},
        {"points": [pt(p[5]["out"]), pt(p[6]["in"])], "route": "hv", "arrow": "end"},
        {"points": [[pt(p[3]["out"])[0] + 8, pt(p[3]["out"])[1]], [pt(p[3]["out"])[0] + 8, y - 45],
                    [470, y - 45], [470, y - 15]], "kind": "control", "arrow": "end"},
    ])
    await d.call("kompas_place_symbols", items=[{"symbol": "junction", "x": pt(p[3]["out"])[0] + 8,
                                                  "y": pt(p[3]["out"])[1]}])
    await d.call("kompas_preview", max_px=2000)
    await d.call("kompas_preview", region=[20, 170, 430, 590], max_px=2000)
    await d.call("kompas_preview", region=[410, 170, 830, 590], max_px=2000)
    await d.call("kompas_preview", region=[20, 30, 430, 300], max_px=1600)
    await d.call("kompas_preview", region=[430, 40, 830, 200], max_px=1600)
    if "--keep" not in sys.argv:
        await d.close()


asyncio.run(main())
