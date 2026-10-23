"""Фрагмент технологической схемы ДКС на A1 с новыми обозначениями: сепаратор, компрессор с двигателем, АВО,
арматура с приводами, трёхходовой терморегулятор, обрывы линий с адресом, приборы КИП (ГОСТ 21.208).

Запуск:  python tests/pneumo_dks.py
Картинки — в tests/out/kompas_preview_*.png. Документ — новый, во временной папке; после проверки закрывается (--keep — оставить открытым).
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_test as s  # noqa: E402


def pt(port):
    return [port["x"], port["y"]]


async def main():
    d = await s.TempDrawing("kmcp_pneumo_dks").create()
    y = 450
    sym = json.loads(await d.call("kompas_place_symbols", items=[
        {"symbol": "line_break", "x": 80, "y": y, "rotate": 180, "incoming": True, "text": "из ГСП"},   # 0
        {"symbol": "valve_electric", "x": 120, "y": y, "label": "ЗД1", "label_pos": "bottom"},          # 1
        {"symbol": "separator", "x": 180, "y": y - 7, "label": "С-1", "label_pos": [-17, -8]},             # 2
        {"symbol": "instrument", "x": 222, "y": y - 12, "code": "LIA", "position": "102",               # 3
         "marks": ["H", "L"]},
        {"symbol": "valve_solenoid", "x": 180, "y": y - 70, "rotate": 270, "label": "КЭ1",              # 4
         "label_pos": "left"},
        {"symbol": "line_break", "x": 180, "y": y - 110, "rotate": 270, "text": "в Е-1"},               # 5
        {"symbol": "compressor", "x": 270, "y": y + 50, "label": "К1", "label_pos": "bottom"},          # 6
        {"symbol": "motor", "x": 270, "y": y + 100, "rotate": 270, "label": "М1", "label_pos": "right"},  # 7
        {"symbol": "instrument", "x": 225, "y": y + 80, "code": "PT", "position": "101"},              # 8
        {"symbol": "check_valve", "x": 315, "y": y + 50, "rotate": 180},                               # 9
        {"symbol": "air_cooler", "x": 400, "y": y + 45, "label": "АВО-1"},                              # 10
        {"symbol": "thermostat_3way", "x": 460, "y": y + 50, "label": "Т", "label_pos": "bottom"},      # 11
        {"symbol": "line_break", "x": 505, "y": y + 50, "text": "в Е-1"},                               # 12
        {"symbol": "valve_diaphragm", "x": 345, "y": y + 80, "rotate": 90},                             # 13
        {"symbol": "line_break", "x": 345, "y": y + 115, "rotate": 90, "text": "на свечу"},             # 14
        {"symbol": "instrument", "x": 520, "y": y + 95, "code": "TIC", "position": "103",              # 15
         "panel": True, "marks": "H"},
    ]))
    p = [c["ports"] for c in sym]
    tee = [345, y + 50]
    await d.call("kompas_pipes", items=[
        {"points": [pt(p[0]["in"]), pt(p[1]["in"])]},
        {"points": [pt(p[1]["out"]), pt(p[2]["in"])]},
        {"points": [pt(p[2]["drain"]), pt(p[4]["in"])]},
        {"points": [pt(p[4]["out"]), pt(p[5]["in"])]},
        {"points": [pt(p[2]["out"]), pt(p[6]["in"])], "route": "vh"},
        {"points": [pt(p[7]["shaft"]), [270, y + 65]]},
        {"points": [pt(p[8]["bottom"]), [225, y + 50]], "kind": "thin"},
        {"points": [pt(p[3]["left"]), [189, y - 12]], "kind": "thin"},
        {"points": [pt(p[6]["out"]), pt(p[9]["in"])]},
        {"points": [pt(p[9]["out"]), pt(p[10]["in"])]},
        {"points": [pt(p[10]["out"]), pt(p[11]["in"])]},
        {"points": [pt(p[11]["out"]), pt(p[12]["in"])]},
        {"points": [tee, pt(p[13]["in"])]},
        {"points": [pt(p[13]["out"]), pt(p[14]["in"])]},
        {"points": [pt(p[15]["left"]), pt(p[11]["sense"])], "route": "hv", "kind": "control"},
    ])
    await d.call("kompas_place_symbols", items=[{"symbol": "junction", "x": tee[0], "y": tee[1]}])

    # ряд вариантов прибора КИП: местный / на щите, длинные коды и позиции
    variants = [("P", ""), ("PI", "1"), ("PT", "101"), ("TIC", "103"), ("PDIA", "104"),
                ("PDIAHL", "105-2"), ("FQIRC", "1101"), ("LT", "")]
    items = []
    for i, (code, pos) in enumerate(variants):
        for j, panel in enumerate((False, True)):
            items.append({"symbol": "instrument", "x": 80 + i * 40, "y": 260 - j * 35, "code": code,
                          "position": pos, "panel": panel, "marks": ["H", "L"] if i % 2 else None})
    await d.call("kompas_place_symbols", items=items)

    # оборудование без обвязки
    await d.call("kompas_place_symbols", items=[
        {"symbol": "oil_separator", "x": 440, "y": 250, "label": "МО"},
        {"symbol": "filter_separator", "x": 490, "y": 250, "label": "ФС"},
        {"symbol": "tank_horizontal", "x": 560, "y": 250, "label": "Е-1"},
        {"symbol": "heater_electric", "x": 640, "y": 250, "label": "ЭН"},
        {"symbol": "motor", "x": 700, "y": 250, "label": "М"},
        {"symbol": "valve_electric", "x": 440, "y": 330, "rotate": 90},
        {"symbol": "line_break", "x": 500, "y": 330, "rotate": 180, "text": "на факел"},
        {"symbol": "line_break", "x": 560, "y": 330, "mirror": True, "incoming": True, "text": "из К-1"},
    ])
    await d.call("kompas_preview", max_px=2000)
    await d.call("kompas_preview", region=[40, 320, 560, 580], max_px=2000)
    await d.call("kompas_preview", region=[40, 190, 400, 290], max_px=2000)
    await d.call("kompas_preview", region=[400, 190, 740, 360], max_px=2000)
    if "--keep" not in sys.argv:
        await d.close()


asyncio.run(main())
