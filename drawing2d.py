"""2D-операции над чертежами и фрагментами (API7)."""
from __future__ import annotations

import contextlib
import math
import re
from typing import Any

from kompas_com import (LINE_DIM_ORIENTATION, LINE_STYLES, SHEET_FORMATS, VIEW_TYPE_NORMAL,
                        Kompas, KompasError)
from winutil import missing_glyphs


def _style(v: Any, default: int = 1) -> int:
    if v is None:
        return default
    if isinstance(v, str):
        if v.lstrip("-").isdigit():
            return int(v)
        if v not in LINE_STYLES:
            raise KompasError(f"Неизвестный стиль линии «{v}». Доступны: {', '.join(LINE_STYLES)} или номер.")
        return LINE_STYLES[v]
    return int(v)


def _req(item: dict, *keys: str) -> list[float]:
    missing = [k for k in keys if k not in item]
    if missing:
        raise KompasError(f"У элемента {item.get('type')} не хватает полей: {', '.join(missing)}")
    return [float(item[k]) for k in keys]


# --- Геометрия ---------------------------------------------------------------
def draw(k: Kompas, items: list[dict], view_number: int | None = None, layer: str | None = None) -> list[dict]:
    v = k.view(number=view_number)
    dc = k.API7.IDrawingContainer(v)
    layer_no = layer_number(k, v, layer)
    created = []
    for n, it in enumerate(items):
        t = it.get("type")
        style = _style(it.get("style"))
        if t == "line":
            x1, y1, x2, y2 = _req(it, "x1", "y1", "x2", "y2")
            o = dc.LineSegments.Add()
            o.X1, o.Y1, o.X2, o.Y2, o.Style = x1, y1, x2, y2, style
        elif t == "circle":
            xc, yc, r = _req(it, "xc", "yc", "r")
            o = dc.Circles.Add()
            o.Xc, o.Yc, o.Radius, o.Style = xc, yc, r, style
        elif t == "arc":
            xc, yc, r, a1, a2 = _req(it, "xc", "yc", "r", "a1", "a2")
            o = dc.Arcs.Add()
            o.Xc, o.Yc, o.Radius, o.Angle1, o.Angle2 = xc, yc, r, a1, a2
            o.Direction = not bool(it.get("ccw", True))  # в API7 Direction=True — по часовой
            o.Style = style
        elif t == "rect":
            x, y, w, h = _req(it, "x", "y", "w", "h")
            o = dc.Rectangles.Add()
            o.X, o.Y, o.Width, o.Height = x, y, w, h
            o.Angle, o.Style = float(it.get("angle", 0)), style
        elif t == "polyline":
            pts = it.get("points") or []
            if len(pts) < 2:
                raise KompasError("polyline: нужно минимум 2 точки в points: [[x,y], ...]")
            o = dc.PolyLines2D.Add()
            for x, y in pts:
                o.AddPoint(-1, float(x), float(y))
            o.Closed = bool(it.get("closed", False))
            o.Style = style
        elif t == "point":
            x, y = _req(it, "x", "y")
            o = dc.Points.Add()
            o.X, o.Y = x, y
        elif t == "text":
            o, warn = make_text(k, dc, it)
            _to_layer(o, layer_no)
            rec = {"type": t, "reference": o.Reference, "width": round(o.Width, 2), "height": round(o.Height, 2)}
            if warn:
                rec["warnings"] = warn
            created.append(rec)
            continue
        else:
            raise KompasError(f"Элемент #{n}: неизвестный тип «{t}». "
                              "Доступны: line, circle, arc, rect, polyline, point, text.")
        if not o.Update():
            raise KompasError(f"Элемент #{n} ({t}) не создан: КОМПАС отклонил параметры.")
        _to_layer(o, layer_no)
        created.append({"type": t, "reference": o.Reference})
    return created


# --- Текст -------------------------------------------------------------------
ALIGN = {"left": 0, "center": 1, "right": 2}
_TI_STRING, _TI_UPPER, _TI_LOWER, _TI_DEV_END = 0, 4, 5, 6  # ksTextItemEnum
_INDEX_RX = re.compile(r"\^\{([^}]*)\}|_\{([^}]*)\}")
_SUP = dict(zip("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-"))
_SUB = dict(zip("₀₁₂₃₄₅₆₇₈₉₊₋", "0123456789+-"))
DEFAULT_FONT = "GOST Type AU"  # шрифт текстов чертежа по умолчанию в КОМПАС v25
# Замены для символов, которых нет в шрифте: варианты по порядку, берётся первый, что есть в шрифте.
# «~» в шрифтах ГОСТ есть в таблице глифов, но рисуется пустым — поэтому не используется.
GLYPH_SUBSTITUTES = {
    "≤": ["<=", "не более"], "≥": [">=", "не менее"], "≈": ["около"], "≠": ["не равно"],
    "→": ["->"], "←": ["<-"], "↑": ["вверх"], "↓": ["вниз"], "∙": ["·", "."], "√": ["корень "],
    "∞": ["беск."], "−": ["-"], "–": ["-"], "—": ["-"], "∅": ["Ø", "диам. "], "⌀": ["Ø", "диам. "],
    "Δ": ["дельта "], "μ": ["мк"], "π": ["пи"], "‰": ["промилле"],
}


def _align(v: Any) -> int:
    if v is None:
        return 0
    if isinstance(v, str) and not v.isdigit():
        if v not in ALIGN:
            raise KompasError(f"align: {', '.join(ALIGN)} или 0/1/2.")
        return ALIGN[v]
    return int(v)


def _substitute(ch: str, font: str) -> str | None:
    if ch in _SUP:
        return "^{" + _SUP[ch] + "}"
    if ch in _SUB:
        return "_{" + _SUB[ch] + "}"
    for cand in GLYPH_SUBSTITUTES.get(ch, []):
        if not missing_glyphs(font, cand):
            return cand
    return None


def check_glyphs(text: str, font: str, fix: bool) -> tuple[str, list[str]]:
    """Найти символы, которых нет в шрифте; при fix=True заменить их на доступные (или индексы ^{}/_{})."""
    plain = _INDEX_RX.sub(lambda m: m.group(1) or m.group(2) or "", text)
    missing = missing_glyphs(font, plain)
    warnings = []
    for ch in missing:
        sub = _substitute(ch, font)
        if fix and sub is not None:
            text = text.replace(ch, sub)
            warnings.append(f"«{ch}» нет в шрифте {font} — заменено на «{sub}».")
        elif sub is not None:
            warnings.append(f"«{ch}» нет в шрифте {font}, на чертеже будет квадрат. Замена: «{sub}» "
                            "(или fix_glyphs: true).")
        else:
            warnings.append(f"«{ch}» нет в шрифте {font}, на чертеже будет квадрат — перепишите словами.")
    return text, warnings


def _fill_line(k: Kompas, ln, s: str, h: float, wf: float, font: str | None) -> None:
    """Строка текста с индексами: «м^{3}», «CO_{2}» — верхний/нижний индекс средствами КОМПАСа."""
    A = k.API7

    def item(text: str, kind: int, height: float):
        ti = A.ITextItem(ln.Add())
        ti.ItemType = kind
        ti.Str = text
        f = A.ITextFont(ti)
        f.Height, f.WidthFactor = height, wf
        if font:
            f.FontName = font
        ti.Update()

    pos = 0
    for m in _INDEX_RX.finditer(s):
        if m.start() > pos:
            item(s[pos:m.start()], _TI_STRING, h)
        item(m.group(1) or "", _TI_UPPER, h * 0.7)
        item(m.group(2) or "", _TI_LOWER, h * 0.7)
        item("", _TI_DEV_END, h)
        pos = m.end()
    if pos < len(s) or pos == 0:
        item(s[pos:], _TI_STRING, h)


def make_text(k: Kompas, dc, it: dict):
    """Надпись: выравнивание, несколько строк с шагом, сужение (в том числе автоматически под max_width),
    индексы ^{…}/_{…}, проверка глифов шрифта. Возвращает (IDrawingText, предупреждения)."""
    A = k.API7
    x, y = _req(it, "x", "y")
    raw = it.get("text", "")
    lines = [str(t) for t in raw] if isinstance(raw, list) else str(raw).split("\n")
    h = float(it.get("height", 5))
    wf = float(it.get("width_factor", 1.0))
    al = _align(it.get("align"))
    font = it.get("font")
    warnings: list[str] = []
    fixed = []
    for s in lines:
        s, w = check_glyphs(s, font or DEFAULT_FONT, bool(it.get("fix_glyphs", False)))
        warnings += w
        fixed.append(s)

    o = dc.DrawingTexts.Add()
    o.X, o.Y = x, y
    o.Angle = float(it.get("angle", 0))
    o.Allocation = al  # положение блока относительно точки; Align строк — внутри блока
    txt = A.IText(o)

    def fill(factor: float):
        if txt.Count:
            txt.Clear()
        for s in fixed:
            ln = txt.Add()
            ln.Align = al
            if it.get("line_step"):
                ln.Step = float(it["line_step"])
            _fill_line(k, ln, s, h, factor, font)

    fill(wf)
    if not o.Update():
        raise KompasError(f"Текст «{lines[0][:30]}» не создан: КОМПАС отклонил параметры.")
    max_w = it.get("max_width")
    if max_w and o.Width > float(max_w):
        wf = max(0.3, wf * float(max_w) / o.Width * 0.98)
        fill(wf)
        o.Update()
        if wf < 0.6:
            warnings.append(f"Текст «{lines[0][:30]}» сужен до {wf:.2f} — уменьшите высоту или сократите.")
    return o, warnings


# --- Размеры -----------------------------------------------------------------
def _curve_geometry(k: Kompas, v, reference: int) -> tuple[float, float, float, float]:
    """Центр, радиус и «удобный» угол стрелки для окружности/дуги по её reference."""
    for kind, _, o in _iter_objects(k, v):
        if o.Reference != reference:
            continue
        if kind == "arc":
            # X3, Y3 — средняя точка дуги: туда и должна указывать стрелка радиуса
            mid = math.degrees(math.atan2(o.Y3 - o.Yc, o.X3 - o.Xc)) % 360
            return o.Xc, o.Yc, o.Radius, mid
        if kind == "circle":
            return o.Xc, o.Yc, o.Radius, 45.0
        raise KompasError(f"Объект {reference} — {kind}; радиус/диаметр ставится на circle или arc.")
    raise KompasError(f"Объект с reference {reference} не найден в виде.")


def dimensions(k: Kompas, items: list[dict], view_number: int | None = None,
               layer: str | None = None) -> list[dict]:
    v = k.view(number=view_number)
    sc = k.API7.ISymbols2DContainer(v)
    layer_no = layer_number(k, v, layer)
    created = []
    for n, it in enumerate(items):
        t = it.get("type")
        if t == "linear":
            x1, y1, x2, y2, x3, y3 = _req(it, "x1", "y1", "x2", "y2", "x3", "y3")
            o = sc.LineDimensions.Add()
            o.X1, o.Y1, o.X2, o.Y2, o.X3, o.Y3 = x1, y1, x2, y2, x3, y3
            orient = it.get("orientation", "parallel")
            o.Orientation = LINE_DIM_ORIENTATION.get(orient, orient) if isinstance(orient, str) else int(orient)
        elif t in ("diameter", "radius"):
            if "object" in it:
                xc, yc, r, mid = _curve_geometry(k, v, int(it["object"]))
                angle = float(it.get("angle", mid))
            else:
                xc, yc, r = _req(it, "xc", "yc", "r")
                angle = float(it.get("angle", 45))
            o = (sc.DiametralDimensions if t == "diameter" else sc.RadialDimensions).Add()
            o.Xc, o.Yc, o.Radius = xc, yc, r
            o.Angle = angle
        elif t == "angular":
            xc, yc, x1, y1, x2, y2 = _req(it, "xc", "yc", "x1", "y1", "x2", "y2")
            o = sc.AngleDimensions.Add()
            o.Xc, o.Yc, o.X1, o.Y1, o.X2, o.Y2 = xc, yc, x1, y1, x2, y2
            o.Radius = float(it.get("r", 20))
        else:
            raise KompasError(f"Размер #{n}: неизвестный тип «{t}». Доступны: linear, diameter, radius, angular.")
        if "prefix" in it or "suffix" in it or "text" in it:
            dt = k.API7.IDimensionText(o)
            if "prefix" in it:
                dt.Prefix.Str = str(it["prefix"])
            if "suffix" in it:
                dt.Suffix.Str = str(it["suffix"])
            if "text" in it:
                dt.AutoNominalValue = False
                dt.NominalText.Str = str(it["text"])
        if not o.Update():
            raise KompasError(f"Размер #{n} ({t}) не создан: КОМПАС отклонил параметры.")
        _to_layer(o, layer_no)
        created.append({"type": t, "reference": o.Reference})
    return created


# --- Чтение и удаление объектов ---------------------------------------------
# коллекция → (тип, интерфейс элемента, поля для вывода)
_GEOM = {
    "LineSegments": ("line", "ILineSegment", ["X1", "Y1", "X2", "Y2", "Style"]),
    "Circles": ("circle", "ICircle", ["Xc", "Yc", "Radius", "Style"]),
    "Arcs": ("arc", "IArc", ["Xc", "Yc", "Radius", "Angle1", "Angle2", "Style"]),
    "Rectangles": ("rect", "IRectangle", ["X", "Y", "Width", "Height", "Angle", "Style"]),
    "PolyLines2D": ("polyline", "IPolyLine2D", ["PointsCount", "Closed", "Style"]),
    "Points": ("point", "IPoint", ["X", "Y"]),
    "DrawingTexts": ("text", "IDrawingText", ["X", "Y", "Angle"]),
    "Ellipses": ("ellipse", "IEllipse", ["Xc", "Yc", "SemiAxisA", "SemiAxisB", "Angle", "Style"]),
    "Hatches": ("hatch", "IHatch", []),
    "Beziers": ("bezier", "IBezier", ["Style"]),
    "Nurbses": ("nurbs", "INurbs", ["Style"]),
    "MacroObjects": ("macro", "IMacroObject", []),  # условные обозначения из kompas_place_symbols
}
_SYMB = {
    "LineDimensions": ("linear_dim", "ILineDimension", ["X1", "Y1", "X2", "Y2", "X3", "Y3", "Orientation"]),
    "DiametralDimensions": ("diameter_dim", "IDiametralDimension", ["Xc", "Yc", "Radius", "Angle"]),
    "RadialDimensions": ("radius_dim", "IRadialDimension", ["Xc", "Yc", "Radius", "Angle"]),
    "AngleDimensions": ("angular_dim", "IAngleDimension", ["Xc", "Yc", "X1", "Y1", "X2", "Y2"]),
    "Roughs": ("rough", "IRough", []),
    "Leaders": ("leader", "ILeader", []),
    "Tolerances": ("tolerance", "ITolerance", []),
    "AxisLines": ("axis_line", "IAxisLine", []),
    "CentreMarkers": ("centre_marker", "ICentreMarker", []),
}


def _iter_objects(k: Kompas, v):
    for container, table in ((k.API7.IDrawingContainer(v), _GEOM), (k.API7.ISymbols2DContainer(v), _SYMB)):
        for coll_name, (kind, iface_name, fields) in table.items():
            try:
                coll = getattr(container, coll_name)
                count = coll.Count
            except Exception:  # noqa: BLE001 — коллекции может не быть в этой версии
                continue
            iface = getattr(k.API7, iface_name, None)
            for i in range(count):
                o = coll.Item(i)
                if iface is not None:
                    try:
                        o = iface(o)
                    except Exception:  # noqa: BLE001
                        pass
                yield kind, fields, o


def _round(v):
    return round(v, 4) if isinstance(v, float) else v


def list_objects(k: Kompas, view_number: int | None = None, limit: int = 500) -> dict:
    v = k.view(number=view_number)
    objs = []
    total = 0
    for kind, fields, o in _iter_objects(k, v):
        total += 1
        if len(objs) >= limit:
            continue
        rec: dict[str, Any] = {"type": kind, "reference": o.Reference}
        with contextlib.suppress(Exception):
            rec["layer"] = o.LayerNumber
        for f in fields:
            try:
                rec[f] = _round(getattr(o, f))
            except Exception:  # noqa: BLE001
                pass
        if kind == "polyline":
            try:
                rec["points"] = [[_round(c) for c in o.GetPoint(i)[1:]] for i in range(o.PointsCount)]
            except Exception:  # noqa: BLE001
                pass
        if kind == "arc":
            rec["ccw"] = not bool(o.Direction)
            rec["mid"] = [_round(o.X3), _round(o.Y3)]
        if kind == "text":
            try:
                rec["text"] = k.API7.IText(o).Str
            except Exception:  # noqa: BLE001
                pass
        if kind.endswith("_dim"):
            try:
                rec["value"] = _round(k.API7.IDimensionParams(o).NominalValue)
            except Exception:  # noqa: BLE001
                with_text = k.API7.IDimensionText(o)
                try:
                    rec["value"] = _round(with_text.NominalValue)
                except Exception:  # noqa: BLE001
                    pass
        objs.append(rec)
    return {"view": {"number": v.Number, "name": v.Name, "scale": v.Scale},
            "total": total, "shown": len(objs), "objects": objs}


def delete_objects(k: Kompas, references: list[int], view_number: int | None = None) -> dict:
    want = {int(r) for r in references}
    v = k.view(number=view_number)
    found = [o for _, _, o in _iter_objects(k, v) if o.Reference in want]
    deleted = []
    for o in found:
        ref = o.Reference
        if o.Delete():
            deleted.append(ref)
    return {"deleted": deleted, "not_found": sorted(want - set(deleted))}



# --- Слои --------------------------------------------------------------------
def _layers(k: Kompas, v) -> list:
    ls = v.Layers
    return [k.API7.ILayer(ls.Item(i)) for i in range(ls.Count)]


def find_layer(k: Kompas, v, name: str, create: bool = False):
    for L in _layers(k, v):
        if L.Name == name:
            return L
    if not create:
        return None
    existing = _layers(k, v)
    number = max(x.LayerNumber for x in existing) + 1
    prev = next((x for x in existing if x.Current), None)
    L = k.API7.ILayer(v.Layers.Add())
    L.Name, L.LayerNumber = name, number
    if not L.Update():
        raise KompasError(f"Слой «{name}» не создан.")
    if prev is not None and L.Current:  # новый слой становится текущим — возвращаем прежний
        prev.Current = True
        prev.Update()
    return L


def layer_number(k: Kompas, v, name: str | None) -> int | None:
    """Номер слоя по имени (слой создаётся при необходимости); None — объекты остаются где есть."""
    return None if not name else find_layer(k, v, name, create=True).LayerNumber


def _to_layer(o, layer_no: int | None) -> None:
    # API7 кладёт новые объекты на слой 0, а не на текущий; номер слоя принимается только после Update
    if layer_no is not None and o.LayerNumber != layer_no:
        o.LayerNumber = layer_no
        o.Update()


@contextlib.contextmanager
def on_layer(k: Kompas, v, name: str | None):
    """Сделать слой текущим на время построений через API5 (символы, трубы) и вернуть прежний."""
    if not name:
        yield None
        return
    L = find_layer(k, v, name, create=True)
    prev = next((x for x in _layers(k, v) if x.Current), None)
    L.Current = True
    L.Update()
    try:
        yield L.LayerNumber
    finally:
        if prev is not None and prev.LayerNumber != L.LayerNumber:
            prev.Current = True
            prev.Update()


def _layer_objects(L) -> list:
    objs = L.GetLayerObjects(0)
    if objs is None:
        return []
    return list(objs) if isinstance(objs, (tuple, list)) else [objs]


def clear_layer(k: Kompas, v, name: str) -> int:
    """Удалить все объекты слоя (сам слой остаётся). Возвращает число удалённых объектов."""
    L = find_layer(k, v, name)
    if L is None:
        return 0
    return sum(1 for o in _layer_objects(L) if o.Delete())


def layers(k: Kompas, action: str = "list", name: str | None = None, view_number: int | None = None,
           new_name: str | None = None) -> dict:
    v = k.view(number=view_number)
    res: dict[str, Any] = {}
    if action != "list":
        if not name:
            raise KompasError("Укажите name — имя слоя.")
        L = find_layer(k, v, name, create=action in ("create", "current"))
        if L is None:
            raise KompasError(f"Слоя «{name}» нет. Есть: {', '.join(x.Name for x in _layers(k, v))}.")
        if action == "current":
            L.Current = True
            L.Update()
        elif action == "clear":
            res["deleted"] = clear_layer(k, v, name)
        elif action in ("hide", "show"):
            L.Visible = action == "show"
            L.Update()
        elif action == "rename":
            if not new_name:
                raise KompasError("Укажите new_name.")
            L.Name = new_name
            L.Update()
        elif action == "delete":
            if L.LayerNumber == 0:
                raise KompasError("Системный слой 0 удалить нельзя.")
            res["deleted"] = clear_layer(k, v, name)
            if L.Current:
                _layers(k, v)[0].Current = True
            L.Delete()
        elif action != "create":
            raise KompasError("action: list, create, current, clear, hide, show, rename, delete.")
    res["layers"] = [{"number": x.LayerNumber, "name": x.Name, "current": bool(x.Current),
                      "visible": bool(x.Visible), "objects": x.ObjectCount} for x in _layers(k, v)]
    return res


# --- Лист, штамп, виды -------------------------------------------------------
# Коды ячеек основной надписи КОМПАСа (форма 1 ГОСТ 2.104), сверены по превью штампа.
# «Лист»/«Листов» КОМПАС заполняет сам.
STAMP_CELLS = {
    1: "Наименование", 2: "Обозначение", 3: "Материал", 40: "Литера 1", 41: "Литера 2",
    42: "Литера 3", 5: "Масса", 6: "Масштаб", 9: "Организация",
    110: "Разраб. (фамилия)", 111: "Пров.", 112: "Т. контр.", 113: "(свободная строка)",
    114: "Н. контр.", 115: "Утв.", 120: "Подпись (разраб.)", 121: "Подпись (пров.)",
    130: "Дата (разраб.)", 131: "Дата (пров.)",
}

# ГОСТ 2.701-2008: шифр схемы = вид (буква) + тип (цифра), например П3 — пневматическая принципиальная.
SCHEMA_CODES = {
    "kinds": {"Э": "электрическая", "Г": "гидравлическая", "П": "пневматическая", "Х": "газовая",
              "К": "кинематическая", "В": "вакуумная", "Л": "оптическая", "Р": "энергетическая",
              "Е": "деления", "С": "комбинированная"},
    "types": {1: "структурная", 2: "функциональная", 3: "принципиальная (полная)", 4: "соединений (монтажная)",
              5: "подключения", 6: "общая", 7: "расположения", 0: "объединённая"},
}


def stamp(k: Kompas, values: dict | None = None, sheet: int = 0) -> dict:
    d2 = k.doc2d()
    if d2.DocumentType != 1:
        raise KompasError("Основная надпись есть только у чертежа.")
    st = d2.LayoutSheets.Item(sheet).Stamp
    if values:
        for cell, text in values.items():
            st.Text(int(cell)).Str = str(text)
        st.Update()
    cells = {}
    for cell in range(1, 300):
        try:
            s = st.Text(cell).Str
        except Exception:  # noqa: BLE001
            continue
        if s:
            cells[cell] = {"label": STAMP_CELLS.get(cell, ""), "text": s}
    return {"cells": cells, "known_cells": STAMP_CELLS, "schema_codes_gost_2_701": SCHEMA_CODES}


def sheet(k: Kompas, fmt: str | None = None, vertical: bool | None = None,
          multiplicity: int | None = None, index: int = 0) -> dict:
    d2 = k.doc2d()
    if d2.DocumentType != 1:
        raise KompasError("Формат листа задаётся только у чертежа.")
    sh = d2.LayoutSheets.Item(index)
    f = sh.Format
    if fmt is not None:
        if fmt.upper() not in SHEET_FORMATS:
            raise KompasError(f"Формат {fmt}: доступны {', '.join(SHEET_FORMATS)}")
        f.Format = SHEET_FORMATS[fmt.upper()]
    if vertical is not None:
        f.VerticalOrientation = bool(vertical)
    if multiplicity is not None:
        f.FormatMultiplicity = int(multiplicity)
    if fmt is not None or vertical is not None or multiplicity is not None:
        sh.Update()
    names = {v: k_ for k_, v in SHEET_FORMATS.items()}
    return {"sheets": d2.LayoutSheets.Count, "format": names.get(f.Format, f.Format),
            "vertical": bool(f.VerticalOrientation), "multiplicity": f.FormatMultiplicity,
            "width_mm": f.FormatWidth, "height_mm": f.FormatHeight}


def views(k: Kompas, create: dict | None = None, make_current: int | None = None) -> dict:
    d2 = k.doc2d()
    vs = d2.ViewsAndLayersManager.Views
    IView = k.API7.IView
    if create:
        v = IView(vs.Add(VIEW_TYPE_NORMAL))
        v.X, v.Y = float(create.get("x", 0)), float(create.get("y", 0))
        v.Scale = float(create.get("scale", 1))
        v.Angle = float(create.get("angle", 0))
        if create.get("name"):
            v.Name = str(create["name"])
        v.Current = True
        if not v.Update():
            raise KompasError("Вид не создан.")
    if make_current is not None:
        v = vs.ViewByNumber(int(make_current))
        if v is None:
            raise KompasError(f"Вид {make_current} не найден.")
        v = IView(v)
        v.Current = True
        v.Update()
    out = []
    for i in range(vs.Count):
        v = IView(vs.Item(i))
        out.append({"number": v.Number, "name": v.Name, "x": v.X, "y": v.Y, "scale": v.Scale,
                    "angle": v.Angle, "current": bool(v.Current), "objects": v.ObjectCount})
    return {"views": out}
