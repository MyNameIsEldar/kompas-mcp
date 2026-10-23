"""Тест оформления листа на живом КОМПАСе: текст, слои, таблицы, сборка сцены и пересборка раздела.

Запуск:  python tests/sheet_tools.py
Создаёт свой чертёж A1, сразу сохраняет его во временную папку и дальше обращается к нему только по пути
(document=...): открытые документы пользователя и других клиентов не трогаются, даже если активным
станет другой документ. Картинки — в tests/out/. В конце документ закрывается без сохранения.
"""
import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_test as s  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DOC = os.path.join(tempfile.gettempdir(), f"kompas_mcp_sheet_test_{os.getpid()}.cdw")


async def main():
    # создать и сразу сохранить под уникальным путём — в одном COM-вызове, чтобы не спутать с чужим
    await s.call("kompas_run_python", code=(
        f"d = app7.Documents.Add(1, True)\nkompas.doc2d(d)\n"
        f"import drawing2d\nwith kompas.pinned(d):\n    drawing2d.sheet(kompas, 'A1', False)\n"
        f"d.SaveAs({DOC!r})\nprint(d.PathName)"))
    try:
        # текст: выравнивание, шаг строк, сужение, глифы
        out = json.loads((await s.call("kompas_run_python", code=(
            "import drawing2d, json\n"
            f"d = kompas.resolve_doc({DOC!r})\n"
            "with kompas.pinned(d):\n"
            "    r = drawing2d.draw(kompas, [\n"
            "        {'type':'text','x':300,'y':560,'text':'По центру\\nвторая строка','align':'center','line_step':8},\n"
            "        {'type':'text','x':300,'y':530,'text':'Длинная надпись, которую надо сузить','max_width':40},\n"
            "        {'type':'text','x':300,'y':515,'text':'p ≤ 1,6 МПа, CO₂'},\n"
            "    ], layer='Тексты')\n"
            "print(json.dumps(r, ensure_ascii=False))"))).split("\n")[0])
        assert out[1]["width"] <= 40.5, out[1]
        assert out[2].get("warnings"), "нет предупреждения о символах вне шрифта"

        build = json.loads(await s.call("kompas_build", source=os.path.join(HERE, "scene_example.py"),
                                        params={"n_mku": 2}, document=DOC))
        layers = {x["name"]: x["objects"] for x in build["layers"]}
        assert layers["МКУ-1"] and layers["Перечень элементов"], layers

        await s.call("kompas_table", source=os.path.join(HERE, "regimes_example.csv"), x=480, y=575,
                     title="Таблица 1 — Режимы работы", total_width=240, document=DOC)

        # пересборка: три МКУ; узел входа не должен измениться
        before = layers["Узел входа"]
        rb = json.loads(await s.call("kompas_build", source=os.path.join(HERE, "scene_example.py"),
                                     params={"n_mku": 3}, sections=["МКУ-1", "МКУ-2", "МКУ-3", "Перечень элементов"],
                                     document=DOC))
        after = {x["name"]: x["objects"] for x in rb["layers"]}
        assert after["Узел входа"] == before and after["МКУ-3"], after
        assert rb["transform"] == build["transform"], (rb["transform"], build["transform"])

        await s.call("kompas_layers", action="hide", name="КИП", document=DOC)
        await s.call("kompas_layers", action="show", name="КИП", document=DOC)
        await s.call("kompas_preview", max_px=2000, document=DOC)
        await s.call("kompas_preview", region=[640, 60, 841, 300], document=DOC)
        print("OK")
    finally:
        await s.call("kompas_close", document=DOC)
        if os.path.exists(DOC):
            os.remove(DOC)


asyncio.run(main())
