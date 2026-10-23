"""COM-слой для КОМПАС-3D: отдельный поток, подключение, помощники для документов.

Все обращения к COM идут через один поток (ComWorker): объекты КОМПАСа нельзя
передавать между потоками без маршалинга, а MCP-сервер асинхронный.
"""
from __future__ import annotations

import asyncio
import builtins
import contextlib
import io
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import winreg
from concurrent.futures import Future
from typing import Any, Callable

import pythoncom
import pywintypes
from win32com.client import GetActiveObject, gencache

API7_TLB = "{69AC2981-37C0-4379-84FD-5DD2F3C0A520}"
API5_TLB = "{0422828C-F174-495E-AC5D-D31014DBBE87}"

# --- Константы из SDK (ksConstants.h, ksConstants3D.h, ldefin2d.h) ---------
DOC_TYPES = {
    "drawing": 1, "fragment": 2, "specification": 3,
    "part": 4, "assembly": 5, "text": 6,
}
DOC_TYPE_NAMES = {v: k for k, v in DOC_TYPES.items()}
LINE_STYLES = {
    "main": 1, "thin": 2, "axial": 3, "dashed": 4, "break": 5,
    "construction": 6, "thick": 7,
}
LINE_DIM_ORIENTATION = {"parallel": 0, "horizontal": 1, "vertical": 2}
SHEET_FORMATS = {"A0": 0, "A1": 1, "A2": 2, "A3": 3, "A4": 4, "A5": 5}
HIDE_MESSAGE_YES = 1
FORMAT_PNG = 3
ZOOM_ALL = 2
VIEW_TYPE_NORMAL = 1

PLANES_3D = {"XOY": 1, "XOZ": 2, "YOZ": 3}
O3D_PLANE_OFFSET = 14
O3D_SKETCH = 5
O3D_EXTRUSION = {"base": 24, "boss": 25, "cut": 26}
O3D_ROTATED = {"base": 27, "boss": 28, "cut": 29}
ET_BLIND, ET_THROUGH_ALL = 0, 1
DT_NORMAL, DT_REVERSE, DT_BOTH = 0, 1, 2
PART_TOP = -1

KOMPAS_PROCESS_NAMES = ("kstudy.exe", "kompas.exe")


def _load_type_libraries():
    # gencache может печатать в stdout (занят протоколом MCP), а redirect_stdout действует на весь
    # процесс — поэтому загружаем обёртки один раз при импорте, до старта сервера и других потоков.
    with contextlib.redirect_stdout(sys.stderr):
        return gencache.EnsureModule(API7_TLB, 0, 1, 0), gencache.EnsureModule(API5_TLB, 0, 1, 0)


API7, API5 = _load_type_libraries()


class KompasError(RuntimeError):
    pass


class KompasBusy(KompasError):
    pass


# --- Поток COM ---------------------------------------------------------------
class ComWorker:
    """Один поток, в котором живут все COM-объекты КОМПАСа."""

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        self.busy_since: float | None = None
        self.busy_with: str = ""
        threading.Thread(target=self._loop, daemon=True, name="kompas-com").start()

    def _loop(self) -> None:
        pythoncom.CoInitialize()
        while True:
            fn, fut, label = self._q.get()
            if not fut.set_running_or_notify_cancel():
                continue
            self.busy_since, self.busy_with = time.time(), label
            try:
                fut.set_result(fn())
            except BaseException as e:  # noqa: BLE001 — пробрасываем в вызывающий поток
                fut.set_exception(e)
            finally:
                self.busy_since, self.busy_with = None, ""

    async def run(self, fn: Callable[[], Any], timeout: float, label: str = "",
                  modal_check: Callable[[], str | None] | None = None) -> Any:
        """Выполнить fn в COM-потоке.

        modal_check (вызывается в фоне раз в секунду) возвращает описание открытого модального
        диалога или None. MCP выполняет вызовы инструментов по очереди, поэтому ждать тайм-аута
        нельзя: как только КОМПАС показал диалог, сразу сообщаем о нём клиенту.
        """
        fut: Future = Future()
        self._q.put((fn, fut, label))
        afut = asyncio.wrap_future(fut)
        start = time.time()
        blocked_since: float | None = None
        probe: asyncio.Future | None = None
        while True:
            done, _ = await asyncio.wait({afut}, timeout=1.0)
            if done:
                return afut.result()
            now = time.time()
            if modal_check is not None:
                # Проверка диалогов не должна блокировать ожидание результата: она идёт в фоне,
                # ограничена по времени, и следующая не стартует, пока не закончилась предыдущая.
                if probe is None:
                    probe = asyncio.ensure_future(asyncio.to_thread(modal_check))
                desc = None
                done_probe, _ = await asyncio.wait({probe}, timeout=3.0)
                if done_probe:
                    desc = probe.result() if not probe.exception() else None
                    probe = None
                blocked_since = (blocked_since or now) if desc else None
                if desc and now - blocked_since >= 1.0:
                    raise KompasBusy(
                        f"Операция «{self.busy_with or label}» ждёт ответа в диалоге КОМПАСа: {desc}. "
                        "Нажмите нужную кнопку через kompas_dialogs(action='click', button=...) — после этого "
                        "операция завершится сама; её результат проверьте отдельно.")
            if now - start > timeout:
                raise KompasBusy(
                    f"Операция «{label}» не завершилась за {timeout:.0f} с. Проверьте kompas_dialogs / "
                    "kompas_window_screenshot.")


# --- Подключение -------------------------------------------------------------
def _cast(module, iface_name: str, obj):
    iface = getattr(module, iface_name)
    return iface(obj._oleobj_.QueryInterface(iface.CLSID, pythoncom.IID_IDispatch))


def _kompas_exe() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Kompas.Application.7\CLSID") as k:
            clsid = winreg.QueryValue(k, None)
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\LocalServer32") as k:
            cmd = winreg.QueryValue(k, None)
    except OSError:
        return None
    cmd = cmd.strip()
    if cmd.startswith('"'):
        return cmd[1:cmd.index('"', 1)]
    return cmd.split(" /")[0]


class Kompas:
    """Состояние подключения. Используется только из потока ComWorker."""

    def __init__(self) -> None:
        self.API7, self.API5 = API7, API5
        self._app7 = None
        self._app5 = None

    # подключение / автозапуск
    def _attach(self) -> bool:
        try:
            raw7 = GetActiveObject("Kompas.Application.7")
        except pywintypes.com_error:
            return False
        self._app7 = _cast(self.API7, "IApplication", raw7)
        self._app7.Visible = True
        self._app7.HideMessage = HIDE_MESSAGE_YES
        raw5 = GetActiveObject("Kompas.Application.5")
        self._app5 = _cast(self.API5, "KompasObject", raw5)
        return True

    def _alive(self) -> bool:
        if self._app7 is None:
            return False
        try:
            self._app7.Visible  # noqa: B018 — пробное обращение
            return True
        except pywintypes.com_error:
            self._app7 = self._app5 = None
            return False

    def connect(self, launch: bool = True, wait: float = 120) -> None:
        if self._alive() or self._attach():
            return
        if not launch:
            raise KompasError("КОМПАС-3D не запущен.")
        exe = _kompas_exe()
        if not exe or not os.path.exists(exe):
            raise KompasError("Не найден исполняемый файл КОМПАС-3D (Kompas.Application.7 не зарегистрирован).")
        # Учебная версия не стартует через CoCreateInstance, поэтому запускаем exe и ждём
        subprocess.Popen([exe], close_fds=True)
        deadline = time.time() + wait
        while time.time() < deadline:
            time.sleep(2)
            if self._attach():
                time.sleep(2)  # даём главному окну догрузиться
                return
        raise KompasError(f"КОМПАС-3D запущен ({exe}), но не ответил по COM за {wait:.0f} с.")

    @property
    def app7(self):
        self.connect()
        return self._app7

    @property
    def app5(self):
        self.connect()
        return self._app5

    # документы
    def doc_info(self, d, index: int | None = None) -> dict:
        info = {
            "index": index,
            "name": d.Name,
            "path": d.PathName or None,
            "type": DOC_TYPE_NAMES.get(d.DocumentType, d.DocumentType),
            "changed": bool(d.Changed),
        }
        try:
            info["active"] = bool(d.Active)
        except pywintypes.com_error:
            pass
        return info

    def documents(self) -> list[dict]:
        docs = self.app7.Documents
        return [self.doc_info(docs.Item(i), i) for i in range(docs.Count)]

    def resolve_doc(self, spec: str | int | None = None):
        app = self.app7
        if spec is None or spec == "":
            d = app.ActiveDocument
            if d is None:
                raise KompasError("Нет активного документа. Откройте или создайте документ.")
            return d
        docs = app.Documents
        if isinstance(spec, int) or (isinstance(spec, str) and spec.isdigit()):
            i = int(spec)
            if not 0 <= i < docs.Count:
                raise KompasError(f"Нет документа с индексом {i} (открыто {docs.Count}).")
            return docs.Item(i)
        want = os.path.normcase(str(spec))
        matches = []
        for i in range(docs.Count):
            d = docs.Item(i)
            if want in (os.path.normcase(d.PathName or ""), os.path.normcase(d.Name)):
                return d
            if want in os.path.normcase(d.PathName or d.Name):
                matches.append(d)
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise KompasError(f"Документ «{spec}» не найден среди открытых.")
        raise KompasError(f"«{spec}» подходит к нескольким документам: " + ", ".join(m.Name for m in matches))

    def activate(self, d) -> None:
        if not d.Active:
            d.Active = True

    def doc2d(self, d=None):
        d = d or self.resolve_doc()
        if d.DocumentType not in (1, 2):
            raise KompasError("Операция доступна только для чертежа или фрагмента.")
        return self.API7.IKompasDocument2D(d)

    def view(self, d=None, number: int | None = None):
        views = self.doc2d(d).ViewsAndLayersManager.Views
        v = views.ActiveView if number is None else views.ViewByNumber(number)
        if v is None:
            raise KompasError(f"Вид с номером {number} не найден.")
        return self.API7.IView(v)

    # растровый рендер (API5) — без диалогов
    def render_png(self, d, dpi: int) -> bytes:
        self.activate(d)
        t = d.DocumentType
        k5 = self.app5
        # Только что созданные объекты остаются выделенными, и экспорт рисует их цветом выделения
        # (синим) — снимаем выделение, чтобы картинка показывала настоящий вид чертежа.
        try:
            iface = self.API7.IKompasDocument2D1 if t in (1, 2) else self.API7.IKompasDocument3D
            iface(d).SelectionManager.UnselectAll()
        except Exception:  # noqa: BLE001 — не у всех типов документов есть менеджер выделения
            pass
        if t in (1, 2):
            doc5 = k5.ActiveDocument2D()
        elif t in (4, 5):
            doc5 = k5.ActiveDocument3D()
        elif t == 3:
            doc5 = k5.SpcActiveDocument()
        else:
            raise KompasError("Предпросмотр поддерживается для чертежей, фрагментов, спецификаций и 3D-моделей.")
        if doc5 is None:
            raise KompasError("Не удалось получить документ через API5 для рендера.")
        par = doc5.RasterFormatParam()
        par.Init()
        par.format = FORMAT_PNG
        par.colorBPP = 24
        par.extResolution = int(dpi)
        par.extScale = 1.0
        fd, path = tempfile.mkstemp(prefix="kompas_preview_", suffix=".png")
        os.close(fd)
        try:
            if not doc5.SaveAsToRasterFormat(path, par):
                raise KompasError("КОМПАС не смог сохранить растровое изображение документа.")
            with open(path, "rb") as f:
                return f.read()
        finally:
            with contextlib.suppress(OSError):
                os.remove(path)

    def sheet_size_mm(self, d) -> tuple[float, float] | None:
        """Размер первого листа чертежа (ширина, высота) в мм."""
        if d.DocumentType != 1:
            return None
        fmt = self.API7.IKompasDocument2D(d).LayoutSheets.Item(0).Format
        w, h = float(fmt.FormatWidth), float(fmt.FormatHeight)
        if w <= 0 or h <= 0:
            return None
        return w, h

    # выполнение произвольного кода (запасной путь для всего, чего нет в инструментах)
    def run_python(self, code: str) -> str:
        ns: dict[str, Any] = {
            "kompas": self, "app7": self.app7, "app5": self.app5,
            "API7": self.API7, "API5": self.API5,
            "doc": self._app7.ActiveDocument,
            "doc2d": lambda d=None: self.doc2d(d),
            "view": lambda d=None, number=None: self.view(d, number),
            "cast": _cast, "LINE_STYLES": LINE_STYLES, "result": None,
        }
        # Свой print вместо redirect_stdout: тот подменяет sys.stdout всему процессу, а код может
        # надолго зависнуть (например, на диалоге) — и перехватить вывод других потоков и протокола MCP.
        buf = io.StringIO()
        ns["print"] = lambda *a, **kw: builtins.print(*a, **{**kw, "file": kw.get("file") or buf})
        exec(compile(code, "<kompas_run_python>", "exec"), ns)  # noqa: S102 — намеренно
        out = buf.getvalue()
        if ns.get("result") is not None:
            out += ("\n" if out else "") + f"result = {ns['result']!r}"
        return out or "(нет вывода)"
