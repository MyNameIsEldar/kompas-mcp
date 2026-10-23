# Сцена тестовой ДКС: координаты в «сетке» схемы (1 клетка = 10 мм при scale=1).
# Параметр n_mku — число компрессорных установок.
n = int(params.get("n_mku", 2))

entry = {"layer": "Узел входа", "ops": [
    {"op": "symbols", "items": [
        {"id": "КШ1", "symbol": "valve", "x": 0, "y": 0, "label": "КШ1"},
        {"id": "ФС1", "symbol": "filter_drain", "x": 6, "y": 0, "label": "ФС1"},
        {"id": "Т1", "symbol": "junction", "x": 11, "y": 0},
    ]},
    {"op": "pipes", "items": [
        {"points": [[-5, 0], "КШ1.in"], "arrow": "end"},
        {"points": ["КШ1.out", "ФС1.in"]},
        {"points": ["ФС1.out", [11, 0]]},
    ]},
    {"op": "draw", "items": [
        {"type": "text", "x": -5, "y": 1.2, "text": "Газ с промысла\np = 3,2 МПа", "height": 3.5, "line_step": 5},
    ]},
]}

mku = []
for i in range(1, n + 1):
    y = 6 - (i - 1) * 8
    mku.append({"layer": f"МКУ-{i}", "ops": [
        {"op": "symbols", "items": [
            {"id": f"К{i}", "symbol": "compressor_centrifugal", "x": 20, "y": y, "label": f"К{i}", "label_pos": "left"},
            {"id": f"АВО{i}", "symbol": "cooler", "x": 30, "y": y + 3, "label": f"АВО{i}"},
            {"id": f"ОК{i}", "symbol": "check_valve", "x": 37, "y": y + 3, "rotate": 180},
        ]},
        {"op": "pipes", "items": [
            {"points": ["Т1.c", [11, y - 4], [20, y - 4], f"К{i}.in"]},
            {"points": [f"К{i}.out", f"АВО{i}.in"], "route": "vh"},
            {"points": [f"АВО{i}.out", f"ОК{i}.in"]},
            {"points": [f"ОК{i}.out", [42, y + 3], [42, -10]], "route": "hv"},
        ]},
        {"op": "draw", "items": [
            {"type": "text", "x": 25, "y": y - 5.5, "text": f"МКУ-{i}", "height": 5, "align": "center"},
        ]},
    ]})

kip = {"layer": "КИП", "ops": [
    {"op": "draw", "items": [
        {"type": "circle", "xc": 3, "yc": 4, "r": 0.8, "style": "thin"},
        {"type": "text", "x": 3, "y": 3.85, "text": "PI", "height": 2.5, "align": "center"},
        {"type": "line", "x1": 3, "y1": 0, "x2": 3, "y2": 3.2, "style": "thin"},
    ]},
    {"op": "table", "preset": "kip", "x": 40, "y": 575, "rows": [
        ["PI1", "Давление газа на входе ДКС", "0…6 МПа", "Манометр МП4-У", "1", ""],
        ["TI1", "Температура газа после АВО", "≤ 45 °C", "ТСМ-50М", "2", ""],
    ], "fix_glyphs": True},
]}

perechen = {"layer": "Перечень элементов", "ops": [
    {"op": "table", "preset": "perechen", "rows":
        [{"group": "Компрессорные установки"}]
        + [{"pos": f"К{i}", "name": "Компрессор центробежный", "qty": "1"} for i in range(1, n + 1)]
        + [{"pos": f"АВО{i}", "name": "Аппарат воздушного охлаждения газа", "qty": "1"} for i in range(1, n + 1)]
        + [{"group": "Арматура"}, {"pos": "КШ1", "name": "Кран шаровой Ду 300", "qty": "1"},
           {"pos": "ФС1", "name": "Фильтр-сепаратор\nгазовый ФС-1600", "qty": "1", "note": "с конденсатоотводом"}]},
]}

scene = {
    "unit": 5,
    "transform": {"fit": [60, 200, 560, 500], "margin": 10, "max_scale": 12},
    "sections": [entry] + mku + [kip, perechen],
}
