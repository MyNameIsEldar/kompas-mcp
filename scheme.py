"""Блок «Условные обозначения» и автопроверка листа схемы.

Обе операции опираются на метки макрообъектов из pneumo.py (символы, трубы): легенда собирается только
из того, что реально есть на листе, а проверка знает порты символов и концы труб.
"""
from __future__ import annotations

import math
import re
from typing import Any

import pneumo
from kompas_com import Kompas, KompasError

THIN = pneumo.THIN

# Символы-приборы КИП: по имени или наименованию в каталоге
INSTRUMENT_RX = re.compile(r"instrument|kip|gauge|sensor|прибор", re.I)
# Отдельные окружности (не в макрообъекте) такого радиуса, мм, считаются приборами КИП, нарисованными вручную
LOOSE_INSTRUMENT_R = (2.5, 8.5)


# --- Сбор геометрии ----------------------------------------------------------
class Sheet:
    """Геометрия вида: отрезки, окружности, тексты с владельцем (верхний макрообъект или сам объект)."""

    def __init__(self, k: Kompas, d, view_number: int | None = None):
        self.k, self.d = k, d
        self.v = k.view(d, view_number)
        a = math.radians(self.v.Angle or 0)
        self._sc, self._c, self._s = float(self.v.Scale or 1), math.cos(a), math.sin(a)
        self._ox, self._oy = float(self.v.X or 0), float(self.v.Y or 0)
        self.segs: list[tuple[float, float, float, float, int]] = []
        self.circles: list[tuple[float, float, float, int]] = []
        self.texts: list[dict] = []
        self.owners: dict[int, dict] = {}
        self.macros: list[tuple[Any, dict | None]] = []  # верхние макрообъекты и их метки
        self._walk(k.API7.IDrawingContainer(self.v), None)

    def to_sheet(self, x: float, y: float) -> list[float]:
        x, y = x * self._sc, y * self._sc
        return [round(self._ox + x * self._c - y * self._s, 2), round(self._oy + x * self._s + y * self._c, 2)]

    def region(self, x1, y1, x2, y2, pad=10.0) -> list[float]:
        a, b = self.to_sheet(x1, y1), self.to_sheet(x2, y2)
        return [round(min(a[0], b[0]) - pad), round(min(a[1], b[1]) - pad),
                round(max(a[0], b[0]) + pad), round(max(a[1], b[1]) + pad)]

    def _items(self, coll_name: str, iface: str, dc):
        try:
            coll = getattr(dc, coll_name)
            n = coll.Count
        except Exception:  # noqa: BLE001
            return
        cast = getattr(self.k.API7, iface)
        for i in range(n):
            yield cast(coll.Item(i))

    def _own(self, owner, ref: int, desc: str) -> int:
        if owner is None:
            self.owners[ref] = {"desc": desc, "tag": None}
            return ref
        return owner

    def _walk(self, dc, owner: int | None, in_instrument: bool = False) -> None:
        for o in self._items("LineSegments", "ILineSegment", dc):
            self.segs.append((o.X1, o.Y1, o.X2, o.Y2, self._own(owner, o.Reference, "отрезок")))
        for o in self._items("PolyLines2D", "IPolyLine2D", dc):
            own = self._own(owner, o.Reference, "полилиния")
            pts = [o.GetPoint(i)[1:] for i in range(o.PointsCount)]
            if o.Closed:
                pts.append(pts[0])
            self.segs += [(*a, *b, own) for a, b in zip(pts, pts[1:])]
        for o in self._items("Rectangles", "IRectangle", dc):
            own = self._own(owner, o.Reference, "прямоугольник")
            a = math.radians(o.Angle or 0)
            ca, sa = math.cos(a), math.sin(a)
            loc = [(0, 0), (o.Width, 0), (o.Width, o.Height), (0, o.Height), (0, 0)]
            pts = [(o.X + x * ca - y * sa, o.Y + x * sa + y * ca) for x, y in loc]
            self.segs += [(*a, *b, own) for a, b in zip(pts, pts[1:])]
        for o in self._items("Arcs", "IArc", dc):
            own = self._own(owner, o.Reference, "дуга")
            a1, a2 = o.Angle1, o.Angle2
            if o.Direction:  # по часовой — меняем концы, чтобы идти против часовой
                a1, a2 = a2, a1
            sweep = (a2 - a1) % 360 or 360
            n = max(4, int(sweep / 15))
            pts = [(o.Xc + o.Radius * math.cos(math.radians(a1 + sweep * i / n)),
                    o.Yc + o.Radius * math.sin(math.radians(a1 + sweep * i / n))) for i in range(n + 1)]
            self.segs += [(*a, *b, own) for a, b in zip(pts, pts[1:])]
        for o in self._items("Circles", "ICircle", dc):
            own = self._own(owner, o.Reference, "окружность")
            self.circles.append((o.Xc, o.Yc, o.Radius, own))
            r = o.Radius
            if in_instrument or owner is None and LOOSE_INSTRUMENT_R[0] <= r * self._sc <= LOOSE_INSTRUMENT_R[1]:
                self.owners[own].setdefault("instrument_circles", []).append((o.Xc, o.Yc, r))
        for o in self._items("DrawingTexts", "IDrawingText", dc):
            own = self._own(owner, o.Reference, "текст")
            box = pneumo.gabarit(self.k, self.d, o)
            if box:
                self.texts.append({"ref": o.Reference, "text": self.k.API7.IText(o).Str, "owner": own,
                                   "box": _shrink(box, o.Angle or 0)})
        for o in self._items("MacroObjects", "IMacroObject", dc):
            if owner is None:
                tag = pneumo.read_tag(o)
                self.owners[o.Reference] = {"desc": _describe(tag), "tag": tag}
                self.macros.append((o, tag))
                inst = bool(tag and tag.get("t") == "sym" and _is_instrument(tag.get("s", "")))
                self._walk(self.k.API7.IDrawingContainer(o), o.Reference, inst)
            else:
                self._walk(self.k.API7.IDrawingContainer(o), owner, in_instrument)

    def shift(self, macro, tag: dict) -> tuple[float, float] | None:
        """Сдвиг макрообъекта с момента вставки (пользователь мог его передвинуть); None — изменён иначе."""
        old = tag.get("gab")
        if not old:
            return 0.0, 0.0
        new = pneumo.gabarit(self.k, self.d, macro)
        if not new:
            return 0.0, 0.0
        if abs((new[2] - new[0]) - (old[2] - old[0])) > 0.5 or abs((new[3] - new[1]) - (old[3] - old[1])) > 0.5:
            return None
        return new[0] - old[0], new[1] - old[1]


def _is_instrument(name: str) -> bool:
    title = pneumo.CATALOG[name][1] if name in pneumo.CATALOG else ""
    return bool(INSTRUMENT_RX.search(name) or INSTRUMENT_RX.search(title))


def _describe(tag: dict | None) -> str:
    if not tag:
        return "макрообъект"
    if tag.get("t") == "pipe":
        return f"трубопровод {tag.get('kind')}" + (f" «{tag['code']}»" if tag.get("code") else "")
    if tag.get("t") == "sym":
        return f"символ {tag.get('s')}" + (f" {tag['label']}" if tag.get("label") else "")
    if tag.get("t") == "legend":
        return "условные обозначения"
    return "макрообъект"


def _shrink(box: list[float], angle: float) -> list[float]:
    """Габарит текста КОМПАСа с запасом над прописными и под строкой → почти точный контур знаков."""
    x1, y1, x2, y2 = box
    a = round(angle) % 360
    if a in (0, 180):
        h = (y2 - y1) / 1.48
        top, bot, side = 0.25 * h, 0.1 * h, 0.05 * h
        return [x1 + side, y1 + (bot if a == 0 else top), x2 - side, y2 - (top if a == 0 else bot)]
    if a in (90, 270):
        h = (x2 - x1) / 1.48
        top, bot, side = 0.25 * h, 0.1 * h, 0.05 * h
        return [x1 + (top if a == 90 else bot), y1 + side, x2 - (bot if a == 90 else top), y2 - side]
    s = 0.05 * min(x2 - x1, y2 - y1)
    return [x1 + s, y1 + s, x2 - s, y2 - s]


# --- Геометрические проверки ---------------------------------------------
def _seg_hits_box(x1, y1, x2, y2, box) -> bool:
    """Отрезок задевает прямоугольник (отсечение Лианга — Барски)."""
    bx1, by1, bx2, by2 = box
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - bx1), (dx, bx2 - x1), (-dy, y1 - by1), (dy, by2 - y1)):
        if p == 0:
            if q < 0:
                return False
        else:
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1:
                return False
    return True


def _circle_hits_box(xc, yc, r, box) -> bool:
    """Окружность (линия, не круг) проходит через прямоугольник."""
    bx1, by1, bx2, by2 = box
    near = math.hypot(xc - min(max(xc, bx1), bx2), yc - min(max(yc, by1), by2))
    far = max(math.hypot(xc - x, yc - y) for x in (bx1, bx2) for y in (by1, by2))
    return near < r < far


def _boxes_overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _seg_dist(px, py, x1, y1, x2, y2) -> float:
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
    return math.hypot(px - x1 - t * dx, py - y1 - t * dy)


def check(k: Kompas, d, view_number: int | None = None, tolerance: float = 1.0,
          region: list[float] | None = None, limit: int = 200) -> dict:
    if d.DocumentType not in (1, 2):
        raise KompasError("Проверка листа — для чертежа или фрагмента.")
    sh = Sheet(k, d, view_number)
    problems: list[dict] = []

    # 1. Текст поверх линий, символов и другого текста
    for t in sh.texts:
        box, hits = t["box"], {}
        for x1, y1, x2, y2, own in sh.segs:
            if own not in hits and _seg_hits_box(x1, y1, x2, y2, box):
                hits[own] = sh.owners[own]["desc"] if own != t["owner"] else "свой " + sh.owners[own]["desc"]
        for xc, yc, r, own in sh.circles:
            if own not in hits and _circle_hits_box(xc, yc, r, box):
                hits[own] = sh.owners[own]["desc"] if own != t["owner"] else "свой " + sh.owners[own]["desc"]
        for t2 in sh.texts:
            if t2["ref"] != t["ref"] and t2["ref"] > t["ref"] and _boxes_overlap(box, t2["box"]):
                hits[("t", t2["ref"])] = f"текст «{t2['text']}»"
        if hits:
            x1, y1, x2, y2 = box
            problems.append({"type": "text_overlap", "text": t["text"], "at": sh.to_sheet((x1 + x2) / 2, (y1 + y2) / 2),
                             "in": sh.owners[t["owner"]]["desc"], "overlaps": sorted(set(hits.values())),
                             "region": sh.region(x1, y1, x2, y2)})

    # 2. Концы труб вне портов
    ports, pipe_paths, untagged_boxes = [], [], []
    for m, tag in sh.macros:
        if tag is None:
            g = pneumo.gabarit(k, d, m)
            if g:
                untagged_boxes.append(g)
            continue
        if tag.get("t") not in ("sym", "pipe"):
            continue
        sft = sh.shift(m, tag)
        if sft is None:
            problems.append({"type": "changed_after_insert", "object": _describe(tag), "reference": m.Reference,
                             "note": "габарит изменился (поворот, правка) — порты и концы не проверены",
                             "region": sh.region(*tag["gab"])})
            continue
        dx, dy = sft
        if tag["t"] == "sym" and tag.get("s") in pneumo.CATALOG:
            for pname, (px, py) in pneumo.symbol_ports(tag).items():
                ports.append((px + dx, py + dy, _describe(tag), pname))
        elif tag["t"] == "pipe":
            pipe_paths.append((m.Reference, tag, [(x + dx, y + dy) for x, y in tag["pts"]]))
    unverified = 0
    for ref, tag, pts in pipe_paths:
        for end, (x, y) in (("start", pts[0]), ("end", pts[-1])):
            best = min(ports, key=lambda p: math.hypot(p[0] - x, p[1] - y), default=None)
            dist = math.hypot(best[0] - x, best[1] - y) if best else math.inf
            if dist <= tolerance:
                continue
            on_pipe = any(_seg_dist(x, y, *a, *b) <= tolerance
                          for r2, _, p2 in pipe_paths if r2 != ref for a, b in zip(p2, p2[1:]))
            if on_pipe:
                continue  # ответвление от другой трубы
            if any(g[0] - tolerance <= x <= g[2] + tolerance and g[1] - tolerance <= y <= g[3] + tolerance
                   for g in untagged_boxes):
                unverified += 1  # у старого символа без метки порты неизвестны
                continue
            rec = {"type": "pipe_end_not_at_port", "pipe": _describe(tag), "reference": ref, "end": end,
                   "at": sh.to_sheet(x, y), "region": sh.region(x, y, x, y, pad=15)}
            if best and dist < 40:
                rec["nearest_port"] = {"of": best[2], "port": best[3], "at": sh.to_sheet(best[0], best[1]),
                                       "distance_mm": round(dist * sh._sc, 2)}
            problems.append(rec)

    # 3. Чужие линии внутри кружков приборов КИП
    tol_in = 0.3
    for own, info in sh.owners.items():
        for xc, yc, r in info.get("instrument_circles", []):
            seen = set()
            for x1, y1, x2, y2, o2 in sh.segs:
                if o2 == own or o2 in seen:
                    continue
                # хорды с концами на окружности и внутри (разделитель прибора) — часть самого прибора;
                # ошибка — линия, которая заходит в кружок снаружи
                outside = max(math.hypot(x1 - xc, y1 - yc), math.hypot(x2 - xc, y2 - yc)) > r + tol_in
                if outside and _seg_dist(xc, yc, x1, y1, x2, y2) < r - tol_in:
                    seen.add(o2)
                    problems.append({"type": "line_in_instrument", "instrument": info["desc"],
                                     "at": sh.to_sheet(xc, yc), "r_mm": round(r * sh._sc, 2),
                                     "line": sh.owners[o2]["desc"], "line_reference": o2,
                                     "region": sh.region(xc - r, yc - r, xc + r, yc + r, pad=12)})

    if region:
        rx1, ry1, rx2, ry2 = min(region[0], region[2]), min(region[1], region[3]), \
            max(region[0], region[2]), max(region[1], region[3])
        problems = [p for p in problems if "at" not in p or rx1 <= p["at"][0] <= rx2 and ry1 <= p["at"][1] <= ry2]
    counts: dict[str, int] = {}
    for p in problems:
        counts[p["type"]] = counts.get(p["type"], 0) + 1
    tagged = sum(1 for _, t in sh.macros if t)
    return {
        "document": d.Name, "view": sh.v.Number, "problems_total": len(problems), "by_type": counts,
        "checked": {"texts": len(sh.texts), "segments": len(sh.segs), "pipes": len(pipe_paths),
                    "ports": len(ports), "macros_tagged": tagged, "macros_untagged": len(sh.macros) - tagged},
        "pipe_ends_unverified": unverified,
        "problems": problems[:limit],
        "note": "Координаты — мм листа; region каждой проблемы можно сразу передать в kompas_preview.",
    }


# --- Условные обозначения ----------------------------------------------------
def _row_name(kind: str, item) -> str:
    if kind == "pipe":
        return item.get("title") or pneumo.PIPE_KINDS.get(item["kind"], {}).get("title", item["kind"])
    return re.sub(r"\s*\(как на примерах[^)]*\)", "", pneumo.CATALOG[item][1])


def legend(k: Kompas, x: float, y: float, symbols: bool | list[str] = False, width: float = 120.0,
           title: str = "Условные обозначения", replace: bool = True, view_number: int | None = None) -> dict:
    d = k.resolve_doc()
    if d.DocumentType not in (1, 2):
        raise KompasError("Условные обозначения строятся в чертеже или фрагменте.")
    k.activate(d)
    if view_number is not None:
        v = k.view(d, view_number)
        v.Current = True
        v.Update()
    sh = Sheet(k, d, view_number)
    u, label_h = pneumo.default_unit(k)
    text_h = pneumo.CODE_HEIGHT_BY_FORMAT.get(pneumo._sheet_format(k) or "A1", 3.5)

    # что есть на листе (кроме прошлой легенды)
    kinds: dict[tuple, dict] = {}
    syms: list[str] = []
    old = []
    for m, tag in sh.macros:
        if not tag:
            continue
        if tag.get("t") == "legend":
            old.append(m)
        elif tag.get("t") == "pipe":
            key = (tag.get("kind"), tag.get("code"), tag.get("style"), tag.get("title"))
            kinds.setdefault(key, tag)
        elif tag.get("t") == "sym" and tag.get("s") not in syms:
            syms.append(tag["s"])
    order = list(pneumo.PIPE_KINDS)
    pipe_rows = sorted(kinds.values(), key=lambda t: (order.index(t["kind"]) if t["kind"] in order else 99,
                                                     t.get("code") or ""))
    if symbols is True:
        sym_rows = [s for s in pneumo.CATALOG if s in syms]  # в порядке каталога
    elif symbols:
        unknown = [s for s in symbols if s not in pneumo.CATALOG]
        if unknown:
            raise KompasError(f"Нет в каталоге: {', '.join(unknown)}.")
        sym_rows = list(symbols)
    else:
        sym_rows = []
    if not pipe_rows and not sym_rows:
        raise KompasError("На листе нет трубопроводов и символов с метками kompas_pipes / kompas_place_symbols — "
                          "легенде не из чего собираться.")

    # размеры строк: символ показывается в натуральную величину
    sym_sizes = {}
    for s in sym_rows:
        x0, y0, x1, y1 = pneumo._bbox(pneumo.build(s))
        sym_sizes[s] = ((x1 - x0) * u, (y1 - y0) * u, (x0 + x1) / 2 * u, (y0 + y1) / 2 * u)
    w1 = max([8 * u] + [w + 2 * u for w, _, _, _ in sym_sizes.values()])
    row_min = 2 * u
    rows = [("pipe", t, row_min) for t in pipe_rows] + \
           [("sym", s, max(row_min, sym_sizes[s][1] + 1.5 * u)) for s in sym_rows]
    total_h = sum(h for _, _, h in rows)
    names = [_row_name(kind, item) for kind, item, _ in rows]
    w = w1 + max(float(width), max(pneumo.code_width(n, text_h) for n in names) + 5)

    doc5 = k.doc5(d)
    code_w = {t["code"]: pneumo.measure_text(k, d, t["code"], text_h) for t in pipe_rows if t.get("code")}
    for m in old if replace else []:
        m.Delete()
    doc5.ksMacro(0)
    doc5.ksText(x, y + 0.6 * label_h, 0, label_h, 1, 0, title)
    doc5.ksLineSeg(x, y, x + w, y, THIN)
    doc5.ksLineSeg(x, y - total_h, x + w, y - total_h, THIN)
    doc5.ksLineSeg(x, y, x, y - total_h, THIN)
    doc5.ksLineSeg(x + w1, y, x + w1, y - total_h, THIN)
    doc5.ksLineSeg(x + w, y, x + w, y - total_h, THIN)
    yy = y
    out_rows = []
    for (kind, item, h), name in zip(rows, names):
        cy = yy - h / 2
        if kind == "pipe":
            path = [(x + u, cy), (x + w1 - u, cy)]
            code = item.get("code")
            spots = [(0, (w1 - 2 * u) / 2)] if code else []
            doc5.ksMacro(0)
            pneumo.draw_pipe(doc5, path, int(item.get("style", pneumo.MAIN)), code, text_h, code_w.get(code, 0.0),
                             spots, None, u)
            doc5.ksEndObj()
        else:
            _, _, ox, oy = sym_sizes[item]
            pneumo.place(k, [{"symbol": item, "x": x + w1 / 2 - ox, "y": cy - oy}], unit=u, tag=False)
        doc5.ksText(x + w1 + 2, cy - text_h / 2, 0, text_h, 1, 0, name)
        yy -= h
        if yy > y - total_h + 1e-6:
            doc5.ksLineSeg(x, yy, x + w, yy, THIN)
        out_rows.append({"row": kind, "name": name, **({"kind": item["kind"], "code": item.get("code")}
                                                       if kind == "pipe" else {"symbol": item})})
    ref = doc5.ksEndObj()
    if not ref:
        raise KompasError("Блок условных обозначений не создан.")
    pneumo.set_tag(k, d, ref, {"t": "legend"})
    return {"reference": ref, "replaced": len(old) if replace else 0, "rows": out_rows,
            "bbox": [round(x, 2), round(y - total_h, 2), round(x + w, 2), round(y + 1.6 * label_h, 2)]}
