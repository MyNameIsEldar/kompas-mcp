"""Проверка восстановления после настоящего модального диалога КОМПАСа.

Вызовы идут строго по очереди — так же, как их делает Claude через MCP. Сценарий: чертёж с
изменениями закрывается с запросом «Сохранить изменения?» (сообщения временно включены).
Во время теста ничего не нажимайте в КОМПАСе.
"""
import asyncio
import faulthandler
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_test as s  # noqa: E402

PROMPT_CLOSE = (
    "app7.HideMessage = 0\n"
    "try:\n"
    "    doc.Close(2)  # kdPromptSaveChanges — модальный запрос\n"
    "finally:\n"
    "    app7.HideMessage = 1\n"
)


async def expect_dialog_error(name, **args):
    t = time.time()
    try:
        await s.call(name, **args)
    except Exception as e:  # noqa: BLE001
        msg = str(e).split(": ", 1)[-1]
        print(f"ОК ({time.time() - t:.1f} с): {name} сообщил о диалоге: {msg[:300]}")
        return msg
    raise AssertionError(f"{name} должен был сообщить о диалоге")


async def scenario(button: str):
    await s.call("kompas_new", doc_type="drawing")
    await s.call("kompas_draw", items=[{"type": "line", "x1": 30, "y1": 30, "x2": 80, "y2": 30}])
    await expect_dialog_error("kompas_run_python", code=PROMPT_CLOSE, timeout=60)
    info = json.loads(await s.call("kompas_dialogs"))
    assert info["main_window_blocked"] is True, info
    assert "Не сохранять" in info["dialogs"][-1]["buttons"], info
    await s.call("kompas_window_screenshot", hwnd=info["dialogs"][-1]["hwnd"], max_px=600)
    await expect_dialog_error("kompas_status")  # следующий вызов тоже сразу видит диалог
    await s.call("kompas_dialogs", action="click", button=button)
    await asyncio.sleep(2)
    info = json.loads(await s.call("kompas_dialogs"))
    assert not info["dialogs"] and info["main_window_blocked"] is False, info
    await s.call("kompas_status")
    print(f"ОК: после «{button}» КОМПАС снова отвечает")


async def main():
    await scenario("Не сохранять")   # документ закрывается без сохранения
    await scenario("Отменить")       # закрытие отменяется — документ остаётся открытым
    await s.call("kompas_close", save=False)
    print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")


faulthandler.dump_traceback_later(120, exit=True)  # при зависании — стеки всех потоков
asyncio.run(main())
