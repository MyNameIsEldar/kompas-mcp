"""Сборка листа из сцены: JSON-описание (или Python-скрипт, который его строит) → чертёж.

Сцена делится на разделы; каждый раздел живёт на своём слое КОМПАСа («МКУ-1», «Узел входа», «КИП»).
Пересборка раздела — очистить его слой и нарисовать заново, остальной лист не трогается. Координаты
сцены — в её собственной системе (эскиз, сетка, метры трассы); transform пересчитывает их на лист.

Формат:
{
  "sheet": {"format": "A1", "vertical": false},              — для new_document
  "unit": 5,                                                  — модуль символов, мм (по умолчанию по формату)
  "transform": {"scale": 1, "offset": [0, 0]}                 — лист = сцена·scale + offset
            или {"fit": [x1, y1, x2, y2], "margin": 0}        — вписать габарит сцены в прямоугольник листа
  "stamp": {"1": "Схема технологическая", ...},
  "sections": [
    {"layer": "МКУ-1", "transform": {...},                    — свой transform раздела (необязательно)
     "ops": [
       {"op": "symbols", "items": [{"id": "К1", "symbol": "compressor", "x": 0, "y": 0, "label": "К1"}]},
       {"op": "pipes", "items": [{"points": ["К1.out", [40, 0], "Ф1.in"], "route": "hv"}]},
       {"op": "draw", "items": [{"type": "text", "x": 0, "y": -30, "text": "МКУ-1", "align": "center"}]},
       {"op": "dimensions", "items": [...]},
       {"op": "table", "preset": "perechen", "rows": [...]}   — таблицы в координатах листа
     ]}
  ]
}
Порты символов в трубах: "К1.out" или {"port": "К1.out", "dx": 5, "dy": 0} — смещение в мм листа.
Символы из разделов, которые сейчас не пересобираются, тоже доступны по id: их порты вычисляются.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any

import drawing2d
import pneumo
import tables
from kompas_com import DOC_TYPES, Kompas, KompasError

_PT_KEYS = (("x", "y"), ("x1", "y1"), ("x2", "y2"), ("x3", "y3"), ("xc", "yc"))
_LEN_KEYS = ("r", "w", "h")  # «h» у rect — высота; высота текста — «height» и не масштабируется


class Xf:
    def __init__(self, scale: float = 1.0, dx: float = 0.0, dy: float = 0.0):
        self.s, self.dx, self.dy = scale, dx, dy

    def pt(self, x, y) -> list[float]:
        return [round(float(x) * self.s + self.dx, 4), round(float(y) * self.s + self.dy, 4)]

    def as_dict(self) -> dict:
        return {"scale": round(self.s, 6), "offset": [round(self.dx, 3), round(self.dy, 3)]}


# --- Загрузка ----------------------------------------------------------------
def load(k: Kompas | None, source: str | None = None, scene: dict | None = None,
         params: dict | None = None) -> tuple[dict, str | None]:
    if scene is not None:
        return scene, None
    if not source:
        raise KompasError("Укажите source (путь к .json или .py) или scene.")
    path = tables.resolve_path(k, source)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f), path
    if ext == ".py":
        # Скрипт сцены — код пользователя из его проекта; выполняется так же, как kompas_run_python.
        ns: dict[str, Any] = {"__file__": path, "__name__": "kompas_scene", "math": math,
                              "params": dict(params or {}), "read_rows": tables.read_rows,
                              "project_dir": os.path.dirname(path)}
        with open(path, encoding="utf-8-sig") as f:
            exec(compile(f.read(), path, "exec"), ns)  # noqa: S102 — намеренно
        sc = ns.get("scene")
        if sc is None and callable(ns.get("build_scene")):
            sc = ns["build_scene"](dict(params or {}))
        if not isinstance(sc, dict):
            raise KompasError(f"{path}: скрипт должен присвоить scene = {{...}} или определить build_scene(params).")
        return sc, path
    raise KompasError("Сцена — .json или .py.")


def _sections(sc: dict) -> list[dict]:
    secs = list(sc.get("sections") or [])
    if sc.get("ops"):
        secs.insert(0, {"layer": sc.get("layer", "Схема"), "ops": sc["ops"]})
    for i, s in enumerate(secs):
        if not s.get("layer"):
            raise KompasError(f"Раздел #{i}: нужен layer — по нему раздел очищается при пересборке.")
    return secs


# --- Преобразование координат ------------------------------------------------
def _scene_points(op: dict) -> list[tuple[float, float]]:
    pts = []
    for it in op.get("items") or []:
        if op.get("op") == "pipes":
            pts += [tuple(p) for p in it.get("points", []) if isinstance(p, (list, tuple))]
            continue
        for kx, ky in _PT_KEYS:
            if kx in it and ky in it:
                pts.append((float(it[kx]), float(it[ky])))
        if it.get("type") == "rect" and "w" in it:
            pts.append((float(it["x"]) + float(it["w"]), float(it["y"]) + float(it["h"])))
        for p in it.get("points") or []:
            if isinstance(p, (list, tuple)):
                pts.append((float(p[0]), float(p[1])))
    return pts


def _make_xf(spec: dict | None, sections: list[dict]) -> Xf:
    spec = spec or {}
    if "fit" in spec:
        pts = [p for s in sections for op in s.get("ops", []) if op.get("op") != "table" for p in _scene_points(op)]
        if not pts:
            return Xf()
        x0, y0 = min(p[0] for p in pts), min(p[1] for p in pts)
        x1, y1 = max(p[0] for p in pts), max(p[1] for p in pts)
        m = float(spec.get("margin", 0))
        fx0, fy0, fx1, fy1 = (float(v) for v in spec["fit"])
        fx0, fy0, fx1, fy1 = fx0 + m, fy0 + m, fx1 - m, fy1 - m
        s = min((fx1 - fx0) / max(x1 - x0, 1e-9), (fy1 - fy0) / max(y1 - y0, 1e-9))
        if "max_scale" in spec:
            s = min(s, float(spec["max_scale"]))
        # по центру прямоугольника
        dx = (fx0 + fx1) / 2 - (x0 + x1) / 2 * s
        dy = (fy0 + fy1) / 2 - (y0 + y1) / 2 * s
        return Xf(s, dx, dy)
    off = spec.get("offset", [0, 0])
    return Xf(float(spec.get("scale", 1)), float(off[0]), float(off[1]))


def _xf_item(it: dict, xf: Xf) -> dict:
    out = dict(it)
    for kx, ky in _PT_KEYS:
        if kx in it and ky in it:
            out[kx], out[ky] = xf.pt(it[kx], it[ky])
    for key in _LEN_KEYS:
        if key in it and it.get("type") in ("circle", "arc", "rect", "radius", "diameter", "angular"):
            out[key] = float(it[key]) * xf.s
    if "points" in it:
        out["points"] = [xf.pt(*p) if isinstance(p, (list, tuple)) else p for p in it["points"]]
    return out


# --- Порты символов ----------------------------------------------------------
def symbol_ports(it: dict, unit: float) -> dict:
    """Порты символа на листе — так же, как их считает pneumo.place (с учётом leads)."""
    s = pneumo.build(it["symbol"])
    xf = pneumo._Xform(float(it["x"]), float(it["y"]), float(it.get("unit", unit)),
                       float(it.get("rotate", 0)), bool(it.get("mirror", False)))
    leads = float(it.get("leads", 0))
    ports = {}
    for name, p in s.ports.items():
        px, py = xf.pt(p.x, p.y)
        dx, dy = xf.vec(p.dx, p.dy)
        if leads and (p.dx or p.dy):
            px, py = px + dx * leads, py + dy * leads
        ports[name] = {"x": round(px, 3), "y": round(py, 3), "dir": [round(dx), round(dy)]}
    return ports


def _resolve_point(p: Any, xf: Xf, ports: dict) -> list[float]:
    if isinstance(p, (list, tuple)):
        return xf.pt(*p)
    ref, ddx, ddy = (p, 0.0, 0.0) if isinstance(p, str) else (p.get("port"), float(p.get("dx", 0)),
                                                               float(p.get("dy", 0)))
    if not isinstance(ref, str) or "." not in ref:
        raise KompasError(f"Точка трубы {p!r}: [x, y], \"ID.порт\" или {{\"port\": \"ID.порт\", \"dx\":..}}.")
    sid, pname = ref.rsplit(".", 1)
    if sid not in ports:
        raise KompasError(f"Символ с id «{sid}» не найден в сцене (есть: {', '.join(sorted(ports)) or '—'}).")
    if pname not in ports[sid]:
        raise KompasError(f"У «{sid}» нет порта «{pname}». Порты: {', '.join(ports[sid])}.")
    q = ports[sid][pname]
    return [round(q["x"] + ddx, 4), round(q["y"] + ddy, 4)]


# --- Сборка ------------------------------------------------------------------
def plan(k: Kompas | None, sc: dict, unit: float) -> tuple[list[dict], Xf, dict]:
    """Разделы, общий transform и порты всех символов сцены (координаты листа)."""
    secs = _sections(sc)
    gxf = _make_xf(sc.get("transform"), [s for s in secs if "transform" not in s])
    ports: dict[str, dict] = {}
    for s in secs:
        xf = _make_xf(s["transform"], [s]) if "transform" in s else gxf
        s["_xf"] = xf
        for op in s.get("ops", []):
            if op.get("op") != "symbols":
                continue
            u = float(op.get("unit", unit))
            for it in op.get("items", []):
                if "id" in it:
                    if it["id"] in ports:
                        raise KompasError(f"id «{it['id']}» повторяется в сцене.")
                    x, y = xf.pt(it["x"], it["y"])
                    ports[str(it["id"])] = symbol_ports({**it, "x": x, "y": y}, u)
    return secs, gxf, ports


def build(k: Kompas, source: str | None = None, scene: dict | None = None, sections: list[str] | None = None,
          clear: bool = True, params: dict | None = None, view_number: int | None = None,
          new_document: bool = False, dry_run: bool = False, document: str | None = None) -> dict:
    sc, path = load(k, source, scene, params)
    if new_document and not dry_run:
        d = k.app7.Documents.Add(DOC_TYPES["drawing"], True)
        if d is None:
            raise KompasError("КОМПАС не создал документ.")
    else:
        d = k.resolve_doc(document)
    with k.pinned(d):
        if new_document and not dry_run:
            sh = sc.get("sheet") or {}
            drawing2d.sheet(k, sh.get("format", "A1"), sh.get("vertical", False))
        res = _build(k, sc, path, sections, clear, view_number, dry_run)
    res["document"] = d.Name or "(новый, не сохранён)"
    return res


def _build(k: Kompas, sc: dict, path: str | None, sections: list[str] | None, clear: bool,
           view_number: int | None, dry_run: bool) -> dict:
    unit = float(sc.get("unit") or pneumo.default_unit(k)[0])
    secs, gxf, ports = plan(k, sc, unit)
    names = [s["layer"] for s in secs]
    if sections:
        unknown = [n for n in sections if n not in names]
        if unknown:
            raise KompasError(f"Нет разделов {unknown}. В сцене: {names}.")
    todo = [s for s in secs if not sections or s["layer"] in sections]
    res: dict[str, Any] = {"source": path, "transform": gxf.as_dict(), "unit_mm": unit, "sections": []}
    if dry_run:
        for s in todo:
            pts = [s["_xf"].pt(*p) for op in s.get("ops", []) if op.get("op") != "table" for p in _scene_points(op)]
            bbox = ([round(min(p[0] for p in pts), 2), round(min(p[1] for p in pts), 2),
                     round(max(p[0] for p in pts), 2), round(max(p[1] for p in pts), 2)] if pts else None)
            res["sections"].append({"layer": s["layer"], "bbox_sheet_mm": bbox})
        res["ports"] = ports
        return res
    v = k.view(number=view_number)
    warnings: list[str] = []
    for s in todo:
        layer, xf = s["layer"], s["_xf"]
        rep: dict[str, Any] = {"layer": layer}
        if clear:
            rep["cleared"] = drawing2d.clear_layer(k, v, layer)
        counts: dict[str, int] = {}
        for i, op in enumerate(s.get("ops", [])):
            kind = op.get("op")
            items = op.get("items") or []
            try:
                if kind == "symbols":
                    placed_items = [{key: val for key, val in it.items() if key != "id"}
                                    | {"x": xf.pt(it["x"], it["y"])[0], "y": xf.pt(it["x"], it["y"])[1]}
                                    for it in items]
                    with drawing2d.on_layer(k, v, layer):
                        placed = pneumo.place(k, placed_items, view_number, op.get("unit", unit))
                    for it, p in zip(items, placed):
                        if "id" in it:
                            ports[str(it["id"])] = p["ports"]
                elif kind == "pipes":
                    pipe_items = [dict(it, points=[_resolve_point(p, xf, ports) for p in it.get("points", [])])
                                  for it in items]
                    with drawing2d.on_layer(k, v, layer):
                        pneumo.pipes(k, pipe_items, view_number)
                elif kind == "draw":
                    out = drawing2d.draw(k, [_xf_item(it, xf) for it in items], view_number, layer)
                    warnings += [w for o in out for w in o.get("warnings", [])]
                elif kind == "dimensions":
                    drawing2d.dimensions(k, [_xf_item(it, xf) for it in items], view_number, layer)
                elif kind == "table":
                    args = {a: b for a, b in op.items() if a not in ("op", "items")}
                    t = tables.table(k, layer=layer, clear=False, view_number=view_number, **args)
                    warnings += t.get("warnings", [])
                    items = [None] * t["rows"]
                else:
                    raise KompasError(f"неизвестная операция «{kind}»: symbols, pipes, draw, dimensions, table.")
            except KompasError as e:
                raise KompasError(f"Раздел «{layer}», операция #{i} ({kind}): {e}") from e
            counts[kind] = counts.get(kind, 0) + len(items)
        rep["built"] = counts
        res["sections"].append(rep)
    if sc.get("stamp") and not sections:
        drawing2d.stamp(k, sc["stamp"])
        res["stamp"] = "заполнен"
    if warnings:
        res["warnings"] = list(dict.fromkeys(warnings))
    res["layers"] = drawing2d.layers(k, view_number=view_number)["layers"]
    return res
