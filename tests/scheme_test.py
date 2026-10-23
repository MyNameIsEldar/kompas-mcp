"""Среды трубопроводов, легенда и автопроверка листа на живом КОМПАСе (временный A1).

Запуск:  python tests/scheme_test.py
Документ сохраняется во временную папку и закрывается в конце; документы пользователя не трогаются.
Картинки — в tests/out/.
"""
import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_test as s  # noqa: E402

PATH = os.path.join(tempfile.gettempdir(), f"kmcp_scheme_test_{os.getpid()}.cdw")
NAME = os.path.basename(PATH)


async def call(name, **args):
    # другие клиенты могут переключать активный документ — перед каждым шагом возвращаем свой
    if name != "kompas_check_sheet":
        await s.call("kompas_activate", document=NAME)
    return json.loads(await s.call(name, **args))


async def main():
    # создать и сохранить одним вызовом: между вызовами активным может стать чужой документ
    await s.call("kompas_run_python", code=(
        "d = app7.Documents.Add(1, True)\n"
        "f = API7.IKompasDocument2D(d).LayoutSheets.Item(0)\n"
        "f.Format.Format = 1; f.Format.VerticalOrientation = False; f.Update()\n"
        f"d.SaveAs({PATH!r}); print(d.Name)"))
    await call("kompas_activate", document=NAME)

    y = 400
    placed = await call("kompas_place_symbols", items=[
        {"symbol": "compressor", "x": 150, "y": y, "label": "К1"},
        {"symbol": "valve", "x": 230, "y": y},
        {"symbol": "cooler", "x": 330, "y": y, "label": "ВО"},
        {"symbol": "filter_drain", "x": 450, "y": y, "label": "Ф"},
    ])
    p = [c["ports"] for c in placed]
    pt = lambda port: [port["x"], port["y"]]  # noqa: E731
    # прибор КИП — кружок Ø10, как рисуют вручную по ГОСТ 21.208
    await call("kompas_draw", items=[{"type": "circle", "xc": 390, "yc": 470, "r": 5, "style": "main"}])

    pipes = await call("kompas_pipes", items=[
        {"points": [pt(p[0]["out"]), pt(p[1]["in"])], "kind": "gas"},                          # 0 — верно
        {"points": [pt(p[1]["out"]), [p[2]["in"]["x"] - 2.5, p[2]["in"]["y"]]], "kind": "gas"},  # 1 — недотянута
        {"points": [pt(p[2]["out"]), pt(p[3]["in"])], "kind": "gas", "arrow": "end"},
        {"points": [[60, 300], [520, 300]], "kind": "oil"},
        {"points": [pt(p[3]["drain"]), [450, 320]], "kind": "drain"},
        {"points": [[150, 470], [150, 540], [600, 540]], "kind": "flare", "arrow": "end"},
        {"points": [[390, 465], [390, y]], "kind": "impulse"},                                 # от кружка к трубе
        {"points": [[360, 470], [420, 470]], "kind": "control"},                               # сквозь кружок
    ])
    assert pipes[3]["code"] == "14" and pipes[4]["code"] == "Д" and pipes[5]["code"] == "С", pipes
    assert pipes[5]["kind"] == "vent"
    # подпись поверх маслопровода
    await call("kompas_draw", items=[{"type": "text", "x": 180, "y": 299, "text": "Наложение", "height": 5}])

    rep = await call("kompas_check_sheet", document=NAME)
    probs = rep["problems"]
    print(json.dumps(rep["by_type"], ensure_ascii=False), rep["checked"])
    texts = [x for x in probs if x["type"] == "text_overlap"]
    ends = [x for x in probs if x["type"] == "pipe_end_not_at_port"]
    inside = [x for x in probs if x["type"] == "line_in_instrument"]
    assert any(t["text"] == "Наложение" for t in texts), texts
    assert all(t["text"] == "Наложение" for t in texts), f"лишние наложения текста: {texts}"
    bad = {(e["reference"], e["end"]) for e in ends}
    assert (pipes[1]["reference"], "end") in bad, ends
    assert next(e for e in ends if e["reference"] == pipes[1]["reference"])["nearest_port"]["distance_mm"] == 2.5
    # ожидаемые свободные концы: обрыв маслопровода, свеча, дренаж, линия управления, импульсная у кружка
    allowed = {(pipes[1]["reference"], "end"), (pipes[3]["reference"], "start"), (pipes[3]["reference"], "end"),
               (pipes[4]["reference"], "end"), (pipes[5]["reference"], "start"), (pipes[5]["reference"], "end"),
               (pipes[6]["reference"], "start"),
               (pipes[7]["reference"], "start"), (pipes[7]["reference"], "end")}
    assert bad <= allowed, bad - allowed
    assert (pipes[0]["reference"], "start") not in bad and (pipes[2]["reference"], "end") not in bad
    assert [x["line_reference"] for x in inside] == [pipes[7]["reference"]], inside

    leg = await call("kompas_legend", x=600, y=250, symbols=True)
    kinds = [r.get("kind") for r in leg["rows"] if r["row"] == "pipe"]
    assert kinds == ["gas", "oil", "drain", "vent", "impulse", "control"], kinds
    assert {r["symbol"] for r in leg["rows"] if r["row"] == "sym"} == {"compressor", "valve", "cooler", "filter_drain"}
    # повторный вызов заменяет блок, а не добавляет второй
    leg2 = await call("kompas_legend", x=600, y=250, symbols=["compressor"])
    assert leg2["replaced"] == 1 and len(leg2["rows"]) == 7

    rep2 = await call("kompas_check_sheet", document=NAME)
    lx1, ly1, lx2, ly2 = leg2["bbox"]
    in_legend = [x for x in rep2["problems"] if lx1 - 1 <= x["at"][0] <= lx2 + 1 and ly1 - 1 <= x["at"][1] <= ly2 + 1]
    assert not in_legend, f"легенда сама даёт замечания: {in_legend}"
    assert rep2["problems_total"] == rep["problems_total"], (rep["by_type"], rep2["by_type"])

    await s.call("kompas_preview", max_px=2000)
    await s.call("kompas_preview", region=leg2["bbox"], max_px=1400)
    await s.call("kompas_preview", region=[40, 280, 540, 560], max_px=1600)
    for pr in probs[:3]:
        await s.call("kompas_preview", region=pr["region"], max_px=600)
    await s.call("kompas_close", document=NAME, save=False)
    os.remove(PATH)
    print("scheme_test: ok")


asyncio.run(main())
