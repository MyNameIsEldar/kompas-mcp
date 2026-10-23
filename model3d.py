"""3D-операции для деталей (API5): эскиз на плоскости + выдавливание/вращение."""
from __future__ import annotations

from kompas_com import (DT_BOTH, DT_NORMAL, DT_REVERSE, ET_BLIND, ET_THROUGH_ALL, O3D_EXTRUSION,
                        O3D_PLANE_OFFSET, O3D_ROTATED, O3D_SKETCH, PART_TOP, PLANES_3D, Kompas,
                        KompasError)
from drawing2d import _req, _style

_DIRECTIONS = {"normal": DT_NORMAL, "reverse": DT_REVERSE, "both": DT_BOTH}


def _part(k: Kompas):
    d = k.resolve_doc()
    if d.DocumentType != 4:
        raise KompasError("Активный документ — не деталь. Создайте деталь: kompas_new(doc_type='part').")
    k.activate(d)
    doc3d = k.app5.ActiveDocument3D()
    return doc3d, doc3d.GetPart(PART_TOP)


def _has_body(part) -> bool:
    try:
        return part.GetMainBody() is not None
    except Exception:  # noqa: BLE001
        return False


def _draw_sketch_api5(doc2d5, items: list[dict]) -> None:
    for n, it in enumerate(items):
        t = it.get("type")
        style = _style(it.get("style"))
        if t == "line":
            x1, y1, x2, y2 = _req(it, "x1", "y1", "x2", "y2")
            doc2d5.ksLineSeg(x1, y1, x2, y2, style)
        elif t == "circle":
            xc, yc, r = _req(it, "xc", "yc", "r")
            doc2d5.ksCircle(xc, yc, r, style)
        elif t == "arc":
            xc, yc, r, a1, a2 = _req(it, "xc", "yc", "r", "a1", "a2")
            doc2d5.ksArcByAngle(xc, yc, r, a1, a2, 1 if it.get("ccw", True) else -1, style)
        elif t == "rect":
            x, y, w, h = _req(it, "x", "y", "w", "h")
            for a, b, c, d in ((x, y, x + w, y), (x + w, y, x + w, y + h),
                               (x + w, y + h, x, y + h), (x, y + h, x, y)):
                doc2d5.ksLineSeg(a, b, c, d, style)
        elif t == "polyline":
            pts = [(float(p[0]), float(p[1])) for p in it.get("points") or []]
            if it.get("closed", False) and pts:
                pts.append(pts[0])
            for (a, b), (c, d) in zip(pts, pts[1:]):
                doc2d5.ksLineSeg(a, b, c, d, style)
        else:
            raise KompasError(f"Эскиз, элемент #{n}: тип «{t}» не поддерживается (line, circle, arc, rect, polyline).")


def sketch_feature(k: Kompas, plane: str, items: list[dict], operation: str = "extrude",
                   mode: str = "add", depth: float = 10.0, through_all: bool = False,
                   direction: str = "normal", angle: float = 360.0, plane_offset: float = 0.0,
                   name: str | None = None) -> dict:
    doc3d, part = _part(k)
    if plane.upper() not in PLANES_3D:
        raise KompasError(f"Плоскость {plane}: доступны {', '.join(PLANES_3D)}")
    base = part.GetDefaultEntity(PLANES_3D[plane.upper()])
    if plane_offset:
        off = part.NewEntity(O3D_PLANE_OFFSET)
        od = off.GetDefinition()
        od.SetPlane(base)
        od.offset = abs(float(plane_offset))
        od.direction = plane_offset > 0
        if not off.Create():
            raise KompasError("Не удалось создать смещённую плоскость.")
        base = off

    sk = part.NewEntity(O3D_SKETCH)
    sd = sk.GetDefinition()
    sd.SetPlane(base)
    if not sk.Create():
        raise KompasError("Не удалось создать эскиз.")
    doc2d5 = sd.BeginEdit()
    try:
        _draw_sketch_api5(doc2d5, items)
    finally:
        sd.EndEdit()

    if mode not in ("add", "cut"):
        raise KompasError("mode: 'add' (приклеить) или 'cut' (вырезать).")
    if mode == "cut":
        kind = "cut"
    else:
        kind = "boss" if _has_body(part) else "base"
    if direction not in _DIRECTIONS:
        raise KompasError(f"direction: {', '.join(_DIRECTIONS)}")

    # У вырезания «прямое» направление КОМПАСа смотрит против нормали плоскости.
    # Приводим к единому смыслу: direction="normal" — всегда вдоль нормали (+Z для XOY).
    if kind == "cut" and direction != "both":
        direction = "reverse" if direction == "normal" else "normal"
    if operation == "extrude":
        ent = part.NewEntity(O3D_EXTRUSION[kind])
        ed = ent.GetDefinition()
        ed.directionType = _DIRECTIONS[direction]
        et = ET_THROUGH_ALL if through_all else ET_BLIND
        normal_side = direction != "reverse"
        ed.SetSideParam(normal_side, et, float(depth), 0, False)
        if direction == "both":
            ed.SetSideParam(False, et, float(depth), 0, False)
    elif operation == "revolve":
        ent = part.NewEntity(O3D_ROTATED[kind])
        ed = ent.GetDefinition()
        ed.directionType = _DIRECTIONS[direction]
        ed.SetSideParam(direction != "reverse", float(angle))
        if direction == "both":
            ed.SetSideParam(False, float(angle))
    else:
        raise KompasError("operation: 'extrude' или 'revolve' (для вращения нужна осевая линия style='axial' в эскизе).")
    if kind == "cut":
        ed.cut = True
    ed.SetSketch(sk)
    if name:
        ent.name = name
    if not ent.Create():
        # не оставляем в дереве пустой эскиз от неудачной операции
        for junk in (sk, base if plane_offset else None):
            if junk is not None:
                try:
                    doc3d.DeleteObject(junk)
                except Exception:  # noqa: BLE001
                    pass
        raise KompasError(f"КОМПАС не смог выполнить операцию {operation}/{mode}. Проверьте, что контур эскиза "
                          "замкнут, лежит там, где есть материал (для cut), и попробуйте другое direction.")
    doc3d.ZoomPrevNextOrAll(2)
    return {"operation": operation, "kind": kind, "sketch": sk.name, "feature": ent.name}


def set_projection(k: Kompas, name: str) -> list[str]:
    """Выбрать стандартную ориентацию 3D-вида (по подстроке имени). Возвращает доступные имена."""
    doc3d = k.app5.ActiveDocument3D()
    coll = doc3d.GetViewProjectionCollection()
    names = [coll.GetByIndex(i).name for i in range(coll.GetCount())]
    if name:
        want = name.lower()
        for i, n in enumerate(names):
            if want in n.lower():
                coll.GetByIndex(i).SetCurrent()
                break
        else:
            raise KompasError(f"Ориентация «{name}» не найдена. Доступны: {', '.join(names)}")
    doc3d.ZoomPrevNextOrAll(2)
    return names
