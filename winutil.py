"""Окна КОМПАСа без COM: снимок окна и работа с модальными диалогами.

Работает из любого потока и не зависит от того, занят ли COM-поток.
"""
from __future__ import annotations

import contextlib
import ctypes
import io
import os
import sys

import win32api
import win32con
import win32gui
import win32process
import win32ui
from PIL import Image as PILImage

from kompas_com import KOMPAS_PROCESS_NAMES, KompasError

PW_RENDERFULLCONTENT = 2


def _process_name(pid: int) -> str:
    try:
        h = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        try:
            return os.path.basename(win32process.GetModuleFileNameEx(h, 0)).lower()
        finally:
            win32api.CloseHandle(h)
    except Exception:  # noqa: BLE001
        return ""


def kompas_windows() -> list[dict]:
    """Все видимые окна верхнего уровня процессов КОМПАСа."""
    names: dict[int, str] = {}
    out: list[dict] = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid not in names:
            names[pid] = _process_name(pid)
        if names[pid] not in KOMPAS_PROCESS_NAMES:
            return True
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        if r - l < 20 or b - t < 20:
            return True
        title = win32gui.GetWindowText(hwnd)
        ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if title == "RoamingWindow" or (ex & win32con.WS_EX_LAYERED and not title):
            return True  # служебные прозрачные окна WPF-интерфейса, не диалоги
        owner = win32gui.GetWindow(hwnd, win32con.GW_OWNER)
        out.append({
            "hwnd": hwnd, "pid": pid, "title": title,
            "class": win32gui.GetClassName(hwnd), "rect": [l, t, r, b],
            "is_dialog": bool(owner) or win32gui.GetClassName(hwnd) == "#32770",
            "enabled": bool(win32gui.IsWindowEnabled(hwnd)),
        })
        return True

    win32gui.EnumWindows(cb, None)
    return out


def main_window() -> dict:
    wins = [w for w in kompas_windows() if not w["is_dialog"]]
    if not wins:
        raise KompasError("Окно КОМПАС-3D не найдено (программа не запущена или свёрнута в трей).")
    return max(wins, key=lambda w: (w["rect"][2] - w["rect"][0]) * (w["rect"][3] - w["rect"][1]))


# --- UI Automation: кнопки WPF-диалогов КОМПАСа не являются Win32-контролами ---
_UIA_BUTTON_TYPE = 50000
_UIA_TEXT_TYPE = 50020
_UIA_CONTROL_TYPE_PROP = 30003
_UIA_INVOKE_PATTERN = 10000
_TREE_SCOPE_DESCENDANTS = 4


def _load_uia_module():
    # Генерация обёрток печатает в stdout (а он занят протоколом MCP), и redirect_stdout меняет
    # sys.stdout для всего процесса — поэтому делаем это один раз при импорте, до старта сервера.
    try:
        import comtypes.client
        with contextlib.redirect_stdout(sys.stderr):
            return comtypes.client.GetModule("UIAutomationCore.dll")
    except Exception:  # noqa: BLE001 — без UIA останутся только Win32-кнопки
        return None


_UIA_MOD = _load_uia_module()


def _uia():
    import comtypes
    import comtypes.client
    if _UIA_MOD is None:
        raise RuntimeError("UI Automation недоступна")
    try:
        # каждый поток asyncio.to_thread инициализирует COM сам; UIA-клиенту рекомендуется MTA
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except OSError:
        pass  # поток уже инициализирован
    auto = comtypes.client.CreateObject("{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=_UIA_MOD.IUIAutomation)
    return _UIA_MOD, auto


def _uia_window(hwnd: int):
    try:
        mod, auto = _uia()
        return mod, auto, auto.ElementFromHandle(hwnd)
    except Exception:  # noqa: BLE001 — UIA недоступна, остаёмся на Win32
        return None, None, None


def _uia_buttons(hwnd: int) -> list[dict]:
    mod, auto, el = _uia_window(hwnd)
    if el is None:
        return []
    cond = auto.CreatePropertyCondition(_UIA_CONTROL_TYPE_PROP, _UIA_BUTTON_TYPE)
    found = el.FindAll(_TREE_SCOPE_DESCENDANTS, cond)
    res = []
    for i in range(found.Length):
        b = found.GetElement(i)
        name = (b.CurrentName or "").strip()
        if not any(ch.isalnum() for ch in name) or b.CurrentIsOffscreen:  # крестик без подписи и т.п.
            continue

        def click(b=b):
            pat = b.GetCurrentPattern(_UIA_INVOKE_PATTERN)
            pat.QueryInterface(mod.IUIAutomationInvokePattern).Invoke()
        res.append({"text": name, "click": click})
    return res


def _uia_texts(hwnd: int) -> list[str]:
    """Надписи диалога (заголовок WPF-окна, вопрос, имя документа) через UI Automation."""
    _, auto, el = _uia_window(hwnd)
    if el is None:
        return []
    try:
        found = el.FindAll(_TREE_SCOPE_DESCENDANTS,
                           auto.CreatePropertyCondition(_UIA_CONTROL_TYPE_PROP, _UIA_TEXT_TYPE))
        texts = []
        for i in range(found.Length):
            t = (found.GetElement(i).CurrentName or "").strip()
            if any(ch.isalnum() for ch in t) and t not in texts:
                texts.append(t)
        return texts
    except Exception:  # noqa: BLE001
        return []


def _buttons(hwnd: int) -> list[dict]:
    """Кнопки диалога: стандартные Win32 и, если их нет, WPF-кнопки через UI Automation."""
    res = []

    def cb(child, _):
        if win32gui.GetClassName(child) == "Button" and win32gui.IsWindowVisible(child):
            text = win32gui.GetWindowText(child).replace("&", "")
            if text:
                res.append({"text": text,
                            "click": lambda c=child: win32gui.PostMessage(c, win32con.BM_CLICK, 0, 0)})
        return True

    try:
        win32gui.EnumChildWindows(hwnd, cb, None)
    except win32gui.error:
        pass
    return res or _uia_buttons(hwnd)


def dialogs() -> list[dict]:
    """Диалоги поверх главного окна (то, что может блокировать COM-вызовы)."""
    main = None
    try:
        main = main_window()["hwnd"]
    except KompasError:
        pass
    out = []
    for w in kompas_windows():
        if w["hwnd"] == main or not w["is_dialog"]:
            continue
        w["buttons"] = [b["text"] for b in _buttons(w["hwnd"])]
        texts = [t for t in _uia_texts(w["hwnd"]) if t not in w["buttons"]]
        w["texts"] = texts
        w["title"] = w["title"] or (texts[0] if texts else "(диалог без заголовка — см. kompas_window_screenshot)")
        out.append(w)
    return out


def modal_dialog_summary() -> str | None:
    """Короткое описание модального диалога, если главное окно КОМПАСа им заблокировано."""
    try:
        if main_window()["enabled"]:
            return None
        dl = dialogs()
    except Exception:  # noqa: BLE001 — проверка не должна ломать основной вызов
        return None
    if not dl:
        return "главное окно заблокировано, но диалог не найден (см. kompas_window_screenshot)"
    d = dl[-1]
    text = " / ".join(d.get("texts") or [d["title"]])
    return f"«{text}», кнопки: {', '.join(d['buttons']) or 'не распознаны'} (hwnd={d['hwnd']})"


def dialog_action(action: str, hwnd: int | None = None, button: str | None = None) -> str:
    dl = dialogs()
    if not dl:
        return "Открытых диалогов КОМПАСа нет."
    target = next((d for d in dl if d["hwnd"] == hwnd), None) if hwnd else dl[-1]
    if target is None:
        raise KompasError(f"Диалог с hwnd={hwnd} не найден.")
    h = target["hwnd"]
    if action == "click":
        if not button:
            raise KompasError("Для action='click' укажите button — текст кнопки.")
        btns = _buttons(h)
        want = button.lower()
        match = next((b for b in btns if b["text"].lower() == want), None) or \
            next((b for b in btns if want in b["text"].lower()), None)
        if match is None:
            raise KompasError(f"Кнопка «{button}» не найдена. Кнопки диалога: {target['buttons'] or 'не найдены'}; "
                              "попробуйте action='enter' или 'escape'.")
        match["click"]()
        return f"Нажата кнопка «{match['text']}» в «{target['title']}»."
    key = {"enter": win32con.VK_RETURN, "escape": win32con.VK_ESCAPE}.get(action)
    if key:
        _force_foreground(h)
        win32api.keybd_event(key, 0, 0, 0)
        win32api.keybd_event(key, 0, win32con.KEYEVENTF_KEYUP, 0)
        return f"Отправлена клавиша {action} в «{target['title']}»."
    if action == "close":
        win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
        return f"Диалог «{target['title']}» закрыт (WM_CLOSE)."
    raise KompasError("action: click, enter, escape или close.")


def _force_foreground(hwnd: int) -> None:
    # Windows запрещает SetForegroundWindow из фона; Alt-трюк снимает ограничение
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    try:
        win32gui.SetForegroundWindow(hwnd)
    finally:
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)


def screenshot(hwnd: int | None = None, max_px: int = 1600) -> bytes:
    """Снимок окна через PrintWindow — работает, даже если окно перекрыто другими."""
    if hwnd is None:
        hwnd = main_window()["hwnd"]
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    hdc = win32gui.GetWindowDC(hwnd)
    src = win32ui.CreateDCFromHandle(hdc)
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, w, h)
    mem.SelectObject(bmp)
    try:
        ctypes.windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(), PW_RENDERFULLCONTENT)
        info = bmp.GetInfo()
        img = PILImage.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                                  bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        mem.DeleteDC()
        src.DeleteDC()
        win32gui.ReleaseDC(hwnd, hdc)
    img.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
