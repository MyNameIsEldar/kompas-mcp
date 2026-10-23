"""MCP-сервер для КОМПАС-3D: документы, 2D-геометрия, размеры, штамп, 3D-операции и просмотр результата.

Запуск (stdio):  python server.py
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import sys
import time
from typing import Annotated, Any, Literal

from PIL import Image as PILImage
from pydantic import Field

import pywintypes
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

import drawing2d
import model3d
import pneumo
import winutil
from kompas_com import DOC_TYPES, ComWorker, Kompas, KompasError, _kompas_exe

def _sdk_include_candidates() -> list[str]:
    exe = _kompas_exe()
    return [
        os.environ.get("KOMPAS_SDK_INCLUDE", ""),
        # <установка>\Bin\kStudy.exe → <установка>\SDK\Include
        os.path.join(os.path.dirname(os.path.dirname(exe)), "SDK", "Include") if exe else "",
    ]

INSTRUCTIONS = """\
Управление КОМПАС-3D (чертежи, фрагменты, детали) через COM API.

Рабочий цикл: kompas_status → kompas_new/kompas_open → kompas_draw / kompas_dimensions / kompas_3d_feature
→ kompas_preview (обязательно смотрите результат после изменений) → правки → kompas_save.

После каждой содержательной правки смотрите kompas_preview (мелкие места — region в мм листа) и не считайте
работу готовой, пока не проверили картинку. Ошибку правьте точечно: kompas_list_objects → kompas_delete(document=…) → заново.

Координаты 2D — миллиметры в системе координат текущего вида. В системном виде (номер 0, масштаб 1)
они совпадают с координатами листа: начало в левом нижнем углу листа, X вправо, Y вверх.
Рамка по ГОСТ 2.104: поле слева 20 мм, остальные 5 мм.
- A4 вертикальный 210×297: рамка x 20…205, y 5…292; основная надпись 185×55 внизу (y 5…60); свободно y 60…278.
- A3 горизонтальный 420×297: рамка x 20…415, y 5…292; основная надпись x 230…415, y 5…60.
От контура до размерной линии 10–15 мм, между размерными линиями 7–10 мм. Текст — 3.5, 5 или 7 мм.

Стили линий: main (видимый контур), thin, axial (оси, выпуск 3–5 мм за контур), dashed (невидимый),
construction (вспомогательная, не печатается), thick — или номер.
Дуги: a1, a2 в градусах, ccw=true — против часовой от a1 к a2; проверяйте по полю mid в kompas_list_objects.
Радиус/диаметр ставьте с привязкой: {"type":"radius","object":<reference дуги>} — стрелка попадёт на дугу.
Штамп: 1 наименование, 2 обозначение, 3 материал, 5 масса, 6 масштаб, 40–42 литера, 9 организация,
110 «Разраб.», 111 «Пров.», 114 «Н. контр.», 115 «Утв.». Шифр схемы в обозначении — по ГОСТ 2.701
(вид + тип: П3 пневматическая принципиальная, Х3 газовая, С3 комбинированная).
Не выдумывайте фамилии, обозначения и материалы — спросите пользователя.

Технологические и пневмосхемы: условные обозначения ставьте через kompas_place_symbols (каталог —
kompas_symbols_catalog; на A1 модуль 5 мм выбирается сам), трубы — kompas_pipes по координатам портов
из ответа вставки. Поток слева направо; обратный клапан в такой линии — rotate=180.

3D (kompas_3d_feature): первая операция mode="add" создаёт тело, далее add — приклеить, cut — вырезать.
direction="normal" — вдоль нормали плоскости (для XOY это +Z). Для вращения в эскизе нужна ось style="axial".

Если нужной операции нет среди инструментов, используйте kompas_run_python (полный доступ к API5/API7),
а имена интерфейсов, методов и констант ищите через kompas_api_search. Объекты из коллекций API7 приводите
к конкретному интерфейсу: API7.ILineSegment(obj), API7.IView(obj) и т.п.

Если инструмент сообщает, что КОМПАС ждёт ответа в диалоге, — текст и кнопки уже в ошибке; при сомнениях
посмотрите kompas_window_screenshot и нажмите кнопку через kompas_dialogs. Решения с последствиями
(сохранить, заменить файл, потерять изменения) — сначала спросите пользователя.
Пользователь может работать в КОМПАСе одновременно с вами: не закрывайте и не меняйте его документы без просьбы.
С тем же КОМПАСом могут работать и другие сессии, поэтому активный документ в любой момент может оказаться чужим.
Удаляйте и меняйте только в явно названном документе (document= в kompas_delete и kompas_run_python).
"""

mcp = MCPServer("kompas", instructions=INSTRUCTIONS)
worker = ComWorker()
_kompas: Kompas | None = None


def K() -> Kompas:
    global _kompas
    if _kompas is None:
        _kompas = Kompas()
    return _kompas


async def com(fn, label: str, timeout: float = 60):
    """Выполнить fn в COM-потоке; любые ошибки отдаются клиенту с понятным текстом."""
    try:
        return await worker.run(fn, timeout, label, modal_check=winutil.modal_dialog_summary)
    except KompasError as e:
        raise ToolError(str(e)) from e
    except pywintypes.com_error as e:
        hr, msg, exc, _ = e.args
        detail = exc[2] if exc and exc[2] else msg
        raise ToolError(f"Ошибка COM КОМПАСа ({hr:#x}): {detail}") from e
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"{type(e).__name__}: {e}") from e


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, default=str)


def _png(img: PILImage.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# --- Документы ---------------------------------------------------------------
@mcp.tool()
async def kompas_status(launch: Annotated[bool, Field(description="Запустить КОМПАС, если он не запущен")] = True) -> str:
    """Подключиться к КОМПАС-3D и показать версию и список открытых документов."""
    def f():
        k = K()
        k.connect(launch=launch)
        return {"application": k.app7.ApplicationName(True), "documents": k.documents()}
    return _json(await com(f, "status", timeout=180))


@mcp.tool()
async def kompas_open(path: Annotated[str, Field(description="Полный путь к .cdw/.frw/.m3d/.a3d/.spw")],
                      read_only: bool = False) -> str:
    """Открыть документ КОМПАС с диска и сделать его активным."""
    def f():
        k = K()
        if not os.path.exists(path):
            raise KompasError(f"Файл не найден: {path}")
        d = k.app7.Documents.Open(path, True, read_only)
        if d is None:
            raise KompasError("КОМПАС не открыл документ.")
        return k.doc_info(d)
    return _json(await com(f, f"open {path}", timeout=120))


@mcp.tool()
async def kompas_new(doc_type: Literal["drawing", "fragment", "part", "assembly", "specification", "text"] = "drawing",
                     sheet_format: Annotated[str | None, Field(description="Только для чертежа: A0…A5")] = None,
                     vertical: Annotated[bool | None, Field(description="Только для чертежа: вертикальная ориентация")] = None) -> str:
    """Создать новый документ (по умолчанию чертёж A4) и сделать его активным."""
    def f():
        k = K()
        d = k.app7.Documents.Add(DOC_TYPES[doc_type], True)
        if d is None:
            raise KompasError("КОМПАС не создал документ.")
        info = k.doc_info(d)
        if doc_type == "drawing" and (sheet_format or vertical is not None):
            info["sheet"] = drawing2d.sheet(k, sheet_format, vertical)
        return info
    return _json(await com(f, f"new {doc_type}"))


@mcp.tool()
async def kompas_activate(document: Annotated[str, Field(description="Индекс, имя файла или часть пути")]) -> str:
    """Сделать документ активным (дальнейшие операции идут в активный документ)."""
    def f():
        k = K()
        d = k.resolve_doc(document)
        k.activate(d)
        return k.doc_info(d)
    return _json(await com(f, "activate"))


@mcp.tool()
async def kompas_save(path: Annotated[str | None, Field(description="Путь для «Сохранить как»; без него — сохранить на место")] = None,
                      document: str | None = None,
                      export: Annotated[list[str] | None, Field(
                          description='Дополнительно выгрузить рядом в эти форматы, например ["pdf"]. '
                                      'Путь документа при этом не меняется.')] = None) -> str:
    """Сохранить документ. Расширение пути определяет формат (.cdw, .frw, .m3d, .dxf, .pdf…)."""
    def save_checked(call, target: str) -> str:
        # Save/SaveAs в API7 возвращают None даже при успехе — судим по файлу на диске.
        target = os.path.abspath(target)
        before = os.path.getmtime(target) if os.path.exists(target) else None
        call(target)
        if not os.path.exists(target):
            raise KompasError(f"КОМПАС не сохранил документ: файла {target} нет.")
        if os.path.getmtime(target) == before:
            raise KompasError(f"КОМПАС не перезаписал {target}: время изменения файла прежнее.")
        return target

    def f():
        k = K()
        d = k.resolve_doc(document)
        if path:
            saved = save_checked(d.SaveAs, path)
        elif d.PathName:
            saved = save_checked(lambda _: d.Save(), d.PathName)
        else:
            raise KompasError("Документ ещё не сохранялся — укажите path.")
        info = k.doc_info(d)
        info["saved"] = saved
        if export:
            base = os.path.splitext(d.PathName or saved)[0]
            info["exported"] = [save_checked(d.SaveAs, f"{base}.{ext.lstrip('.').lower()}") for ext in export]
        return info
    return _json(await com(f, "save", timeout=120))


@mcp.tool()
async def kompas_close(document: str | None = None,
                       save: Annotated[bool, Field(description="Сохранить перед закрытием")] = False) -> str:
    """Закрыть документ (по умолчанию активный) без сохранения или с сохранением."""
    def f():
        k = K()
        d = k.resolve_doc(document)
        name = d.Name
        if not d.Close(1 if save else 0):
            raise KompasError("Документ не закрыт.")
        return {"closed": name, "documents": k.documents()}
    return _json(await com(f, "close"))


# --- Просмотр ----------------------------------------------------------------
@mcp.tool()
async def kompas_preview(
    document: str | None = None,
    region: Annotated[list[float] | None, Field(
        description="Только для чертежей: [x1, y1, x2, y2] в мм листа — увеличить этот участок")] = None,
    orientation: Annotated[str | None, Field(
        description="Только для 3D: стандартная ориентация, например «Изометрия», «Спереди», «Сверху»")] = None,
    max_px: Annotated[int, Field(ge=300, le=2400, description="Наибольшая сторона картинки в пикселях")] = 1400,
) -> list:
    """Отрендерить документ в картинку и вернуть её, чтобы увидеть результат. Файлы на диске не остаются."""
    def f():
        k = K()
        d = k.resolve_doc(document)
        info: dict[str, Any] = {"document": d.Name}
        if d.DocumentType in (4, 5):
            with k.temporarily_active(d):
                info["orientations"] = model3d.set_projection(k, orientation or "")
        sheet = k.sheet_size_mm(d)
        if sheet:
            W, H = sheet
            long_mm = max(W, H)
            if region:
                x1, y1, x2, y2 = region
                span = max(abs(x2 - x1), abs(y2 - y1), 1)
                dpi = max_px * 25.4 / span
                dpi = min(dpi, 1200, 9000 * 25.4 / long_mm)
            else:
                dpi = max_px * 25.4 / long_mm
        else:
            dpi = 300  # 3D/фрагменты: размер картинки зависит от окна, рендерим крупно и уменьшаем
        data = k.render_png(d, max(int(dpi), 20))
        return info, sheet, data
    info, sheet, data = await com(f, "preview", timeout=90)

    img = PILImage.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    if sheet:
        W, H = sheet
        if (w > h) != (W > H):
            W, H = H, W
        sx, sy = w / W, h / H
        info["sheet_mm"] = [W, H]
        if region:
            x1, y1, x2, y2 = region
            x1, x2 = sorted((x1, x2))
            y1, y2 = sorted((y1, y2))
            box = (max(0, int(x1 * sx)), max(0, int(h - y2 * sy)), min(w, int(x2 * sx)), min(h, int(h - y1 * sy)))
            if box[2] <= box[0] or box[3] <= box[1]:
                raise KompasError("Область region вне листа.")
            img = img.crop(box)
            info["region_mm"] = [x1, y1, x2, y2]
        img.thumbnail((max_px, max_px))
        info["mm_per_px"] = round((region[2] - region[0] if region else W) / img.size[0], 4) if img.size[0] else None
    else:
        img.thumbnail((max_px, max_px))
    info["image_px"] = list(img.size)
    return [_json(info), Image(data=_png(img), format="png")]


@mcp.tool()
async def kompas_window_screenshot(
    hwnd: Annotated[int | None, Field(description="Окно из kompas_dialogs; без него — главное окно")] = None,
    max_px: int = 1600,
) -> list:
    """Снимок окна КОМПАСа (интерфейс, диалоги). Работает, даже когда COM занят."""
    data = await asyncio.to_thread(winutil.screenshot, hwnd, max_px)
    return [Image(data=data, format="png")]


@mcp.tool()
async def kompas_dialogs(
    action: Literal["list", "click", "enter", "escape", "close"] = "list",
    button: Annotated[str | None, Field(description="Текст кнопки для action='click'")] = None,
    hwnd: Annotated[int | None, Field(description="Какой диалог; по умолчанию последний открытый")] = None,
) -> str:
    """Найти модальные диалоги КОМПАСа и нажать в них кнопку. Работает, даже когда COM занят."""
    if action == "list":
        dl = await asyncio.to_thread(winutil.dialogs)
        try:
            # главное окно заблокировано — значит, открыт модальный диалог
            main_blocked = not (await asyncio.to_thread(winutil.main_window))["enabled"]
        except KompasError:
            main_blocked = None
        busy = {"busy_for_s": round(time.time() - worker.busy_since, 1),
                "operation": worker.busy_with} if worker.busy_since else None
        return _json({"dialogs": dl, "main_window_blocked": main_blocked, "com_busy": busy})
    return await asyncio.to_thread(winutil.dialog_action, action, hwnd, button)


# --- 2D ----------------------------------------------------------------------
_DRAW_DOC = """Элементы (координаты в мм вида; style — main/thin/axial/dashed/construction/thick или номер):
- {"type":"line","x1":0,"y1":0,"x2":100,"y2":0}
- {"type":"circle","xc":50,"yc":50,"r":10}
- {"type":"arc","xc":0,"yc":0,"r":20,"a1":0,"a2":90,"ccw":true}  (углы в градусах)
- {"type":"rect","x":0,"y":0,"w":100,"h":60,"angle":0}  (x,y — левый нижний угол)
- {"type":"polyline","points":[[0,0],[10,0],[10,10]],"closed":false}
- {"type":"point","x":10,"y":10}
- {"type":"text","x":20,"y":30,"text":"Надпись","height":5,"angle":0,"align":0}"""


@mcp.tool(description="Нарисовать 2D-геометрию в активном (или указанном) виде чертежа/фрагмента.\n" + _DRAW_DOC)
async def kompas_draw(items: list[dict], view: Annotated[int | None, Field(description="Номер вида")] = None) -> str:
    return _json(await com(lambda: drawing2d.draw(K(), items, view), "draw"))


@mcp.tool()
async def kompas_dimensions(
    items: Annotated[list[dict], Field(description=(
        'Размеры: {"type":"linear","x1","y1","x2","y2","x3","y3","orientation":"horizontal|vertical|parallel"} '
        '(x3,y3 — точка на размерной линии); {"type":"diameter"|"radius","object":<reference дуги/окружности>} — '
        'центр, радиус и угол возьмутся из объекта, стрелка укажет на середину дуги (предпочтительно), '
        'или вручную {"type":"radius","xc","yc","r","angle"} — angle должен попадать на саму дугу; '
        '{"type":"angular","xc","yc","x1","y1","x2","y2","r":20}. Необязательно: "prefix", "suffix", '
        '"text" (заменить значение).'))],
    view: int | None = None,
) -> str:
    """Проставить размеры в активном (или указанном) виде."""
    return _json(await com(lambda: drawing2d.dimensions(K(), items, view), "dimensions"))


@mcp.tool()
async def kompas_list_objects(view: int | None = None,
                              limit: Annotated[int, Field(ge=1, le=5000)] = 500) -> str:
    """Перечислить объекты вида (геометрию, размеры, обозначения) с координатами и reference для удаления."""
    return _json(await com(lambda: drawing2d.list_objects(K(), view, limit), "list objects"))


@mcp.tool()
async def kompas_delete(document: Annotated[str, Field(description=(
                            "Документ, в котором удалять: имя файла, часть пути или индекс из kompas_status. "
                            "Обязателен — удаление никогда не идёт «в активный», его мог переключить пользователь."))],
                        references: list[int], view: int | None = None) -> str:
    """Удалить объекты вида по их reference (из kompas_list_objects или kompas_draw)."""
    return _json(await com(lambda: drawing2d.delete_objects(K(), document, references, view), "delete"))


@mcp.tool()
async def kompas_stamp(values: Annotated[dict[str, str] | None, Field(
        description='Номер ячейки → текст, например {"1":"Вал","2":"АБВ.001","3":"Сталь 45 ГОСТ 1050-2013","110":"Иванов"}. '
                    'Без values — только прочитать.')] = None) -> str:
    """Прочитать или заполнить основную надпись (штамп) чертежа."""
    return _json(await com(lambda: drawing2d.stamp(K(), values), "stamp"))


@mcp.tool()
async def kompas_sheet(sheet_format: Annotated[str | None, Field(description="A0…A5")] = None,
                       vertical: bool | None = None,
                       multiplicity: Annotated[int | None, Field(description="Кратность формата (1 — обычный)")] = None) -> str:
    """Прочитать или изменить формат и ориентацию листа чертежа."""
    return _json(await com(lambda: drawing2d.sheet(K(), sheet_format, vertical, multiplicity), "sheet"))


@mcp.tool()
async def kompas_views(
    create: Annotated[dict | None, Field(description='Новый вид: {"x":100,"y":150,"scale":0.5,"name":"Вид А","angle":0} — '
                                                     'x,y — точка привязки на листе, мм; становится текущим')] = None,
    make_current: Annotated[int | None, Field(description="Номер вида, который сделать текущим")] = None,
) -> str:
    """Список видов чертежа; создание нового вида с масштабом; выбор текущего вида."""
    return _json(await com(lambda: drawing2d.views(K(), create, make_current), "views"))


# --- 3D ----------------------------------------------------------------------
@mcp.tool()
async def kompas_3d_feature(
    plane: Annotated[Literal["XOY", "XOZ", "YOZ"], Field(description="Базовая плоскость эскиза")],
    sketch: Annotated[list[dict], Field(description="Контур эскиза: line, circle, arc, rect, polyline (как в kompas_draw), "
                                                    "в координатах плоскости. Для вращения добавьте ось: line со style='axial'")],
    operation: Literal["extrude", "revolve"] = "extrude",
    mode: Annotated[Literal["add", "cut"], Field(description="add — добавить материал, cut — вырезать")] = "add",
    depth: Annotated[float, Field(description="Глубина выдавливания, мм")] = 10.0,
    through_all: Annotated[bool, Field(description="Вырезать насквозь")] = False,
    direction: Literal["normal", "reverse", "both"] = "normal",
    angle: Annotated[float, Field(description="Угол вращения, градусы")] = 360.0,
    plane_offset: Annotated[float, Field(description="Смещение плоскости эскиза от базовой, мм (знак — сторона)")] = 0.0,
    name: str | None = None,
) -> str:
    """Построить в активной детали эскиз и выполнить выдавливание или вращение (добавить или вырезать)."""
    return _json(await com(lambda: model3d.sketch_feature(K(), plane, sketch, operation, mode, depth, through_all,
                                                          direction, angle, plane_offset, name), "3d feature", timeout=120))


# --- Пневмосхемы: условные обозначения ---------------------------------------
@mcp.tool()
async def kompas_symbols_catalog(
    unit: Annotated[float | None, Field(description="Модуль u, мм; без него — для листа A1 (5 мм)")] = None,
) -> str:
    """Каталог условных обозначений для пневмо- и технологических схем: имя, наименование, размер, порты."""
    return _json({"unit_mm": unit or 5.0, "symbols": pneumo.catalog(unit or 5.0)})


@mcp.tool()
async def kompas_place_symbols(
    items: Annotated[list[dict], Field(description=(
        'Символы: {"symbol":"compressor","x":100,"y":300,"rotate":0,"mirror":false,"label":"К1",'
        '"label_pos":"top|bottom|left|right|[dx,dy]","leads":0}. x,y — центр символа в мм; rotate — градусы '
        '(против часовой); leads — длина выводов у портов, мм (для таблиц обозначений). Имена — в kompas_symbols_catalog. '
        'Параметры символов (список — поле params в каталоге): line_break — "text":"на факел","incoming":false; '
        'instrument (прибор КИП) — "code":"PT","position":"101","panel":true,"marks":["H","L"].'))],
    unit: Annotated[float | None, Field(description="Модуль u, мм; по умолчанию по формату листа (A1 — 5 мм)")] = None,
    view: int | None = None,
) -> str:
    """Вставить условные обозначения (компрессоры, фильтры, клапаны, теплообменники…). Каждый символ — один
    макрообъект. Возвращает reference, габарит и координаты портов для подключения трубопроводов."""
    return _json(await com(lambda: pneumo.place(K(), items, view, unit), "place symbols"))


@mcp.tool()
async def kompas_pipes(
    items: Annotated[list[dict], Field(description=(
        'Линии связи: {"points":[[x1,y1],[x2,y2],...],"route":"hv|vh|null","kind":"main|control|thin",'
        '"arrow":"end|start|both|null"}. route вставляет прямой угол между точками (hv — сначала по X). '
        'kind=main — трубопровод (основная), control — линия управления (штриховая).'))],
    view: int | None = None,
) -> str:
    """Провести трубопроводы и линии управления между портами символов (ортогонально, со стрелками потока)."""
    return _json(await com(lambda: pneumo.pipes(K(), items, view), "pipes"))


# --- Запасной путь -----------------------------------------------------------
@mcp.tool()
async def kompas_run_python(code: Annotated[str, Field(description=(
        "Python-код, выполняется в COM-потоке сервера. Доступны: app7 (IApplication API7), app5 (KompasObject API5), "
        "API7, API5 (модули типов), doc (документ из document, иначе активный), doc2d(), view(number=None) — "
        "по умолчанию для doc, cast(module, 'IName', obj), LINE_STYLES. Печатайте через print(); можно присвоить result = ..."))],
                            document: Annotated[str | None, Field(description=(
                                "Документ для doc/doc2d()/view(): имя, часть пути или индекс. Указывайте всегда, когда код "
                                "что-то меняет или удаляет: активный документ может оказаться чужим."))] = None,
                            timeout: Annotated[float, Field(ge=5, le=600)] = 120) -> str:
    """Выполнить произвольный код против API КОМПАСа — для всего, чего нет в остальных инструментах."""
    return await com(lambda: K().run_python(code, document), "run_python", timeout=timeout)


def _sdk_include() -> str | None:
    return next((p for p in _sdk_include_candidates() if p and os.path.isdir(p)), None)


@mcp.tool()
async def kompas_api_search(
    query: Annotated[str, Field(description="Подстрока или регулярное выражение: имя интерфейса, метода, свойства или константы")],
    interface: Annotated[str | None, Field(description="Показать все члены этого интерфейса (например IDrawingContainer, ksPart)")] = None,
    limit: int = 80,
) -> str:
    """Поиск по API КОМПАСа: интерфейсы и члены API7/API5 (из библиотек типов) и константы из заголовков SDK."""
    def f():
        k = K()
        out: list[str] = []
        mods = (("API7", k.API7), ("API5", k.API5))
        if interface:
            for mname, m in mods:
                cls = getattr(m, interface, None)
                if cls is None:
                    continue
                props = sorted(set(getattr(cls, "_prop_map_get_", {})) | set(getattr(cls, "_prop_map_put_", {})))
                methods = sorted(n for n, v in vars(cls).items() if callable(v) and not n.startswith("_"))
                out.append(f"{mname}.{interface}\n  свойства: {', '.join(props)}\n  методы:")
                for n in methods:
                    doc = (getattr(cls, n).__doc__ or "").strip()
                    try:
                        import inspect
                        sig = str(inspect.signature(getattr(cls, n))).replace("self, ", "").replace("self", "")
                    except (TypeError, ValueError):
                        sig = "(...)"
                    out.append(f"    {n}{sig}" + (f" — {doc}" if doc else ""))
            if not out:
                out.append(f"Интерфейс {interface} не найден в API7/API5.")
            return "\n".join(out)
        rx = re.compile(query, re.I)
        hits = 0
        for mname, m in mods:
            for name, cls in vars(m).items():
                if not isinstance(cls, type) or name.startswith("_"):
                    continue
                if rx.search(name):
                    out.append(f"[интерфейс] {mname}.{name}")
                    hits += 1
                members = set(getattr(cls, "_prop_map_get_", {})) | {n for n in vars(cls) if not n.startswith("_")}
                for mem in sorted(members):
                    if rx.search(mem):
                        out.append(f"[член] {mname}.{name}.{mem}")
                        hits += 1
                if hits >= limit:
                    break
        inc = _sdk_include()
        if inc and hits < limit:
            for fn in ("ksConstants.h", "ksConstants3D.h", "ldefin2d.h", "LDefin3D.h"):
                p = os.path.join(inc, fn)
                if not os.path.exists(p):
                    continue
                with open(p, encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if rx.search(line) and ("=" in line or "#define" in line):
                            out.append(f"[константа {fn}] {line.strip()}")
                            hits += 1
                            if hits >= limit:
                                break
        return "\n".join(out[:limit]) or "Ничего не найдено."
    return await com(f, "api search", timeout=60)


if __name__ == "__main__":
    print("kompas-mcp: запуск (stdio)", file=sys.stderr)
    if os.environ.get("KOMPAS_MCP_DUMP_STACKS"):
        # отладка зависаний: периодически печатать стеки всех потоков в stderr
        import faulthandler
        faulthandler.dump_traceback_later(float(os.environ["KOMPAS_MCP_DUMP_STACKS"]), repeat=True)
    mcp.run()
