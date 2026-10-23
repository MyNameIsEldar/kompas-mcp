"""Проверки без КОМПАСа: разрывы под обозначение среды, порты по метке, геометрия автопроверки.

Запуск:  python tests/scheme_unit_test.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pneumo  # noqa: E402
import scheme  # noqa: E402


class FakeDoc5:
    """Записывает вызовы рисования вместо КОМПАСа."""

    def __init__(self):
        self.lines, self.texts, self.fills = [], [], 0

    def ksLineSeg(self, x1, y1, x2, y2, style):
        self.lines.append((x1, y1, x2, y2, style))

    def ksText(self, x, y, angle, h, ksu, bits, s):
        self.texts.append((x, y, angle, h, s))

    def ksColouring(self, c):
        self.fills += 1

    def ksEndObj(self):
        return 1


def test_kinds():
    assert pneumo.pipe_kind("flare")[0] == "vent"
    assert pneumo.PIPE_KINDS["oil"]["code"] == "14"
    assert pneumo.PIPE_KINDS["control"]["style"] == pneumo.DASHED
    assert pneumo.PIPE_KINDS["impulse"]["style"] == pneumo.THIN
    try:
        pneumo.pipe_kind("steam")
    except pneumo.KompasError:
        pass
    else:
        raise AssertionError("неизвестная среда должна давать ошибку")


def test_code_gaps():
    path = pneumo._route([[0, 0], [400, 100]], "hv")  # 400 по X, затем 100 по Y
    assert path == [(0.0, 0.0), (400.0, 0.0), (400.0, 100.0)]
    spots = pneumo._code_spots(path, 4.9, 150)
    assert [i for i, _ in spots].count(0) == 2  # 400 мм — два повтора
    assert [i for i, _ in spots].count(1) == 0  # 100 мм < шага и не самый длинный
    d5 = FakeDoc5()
    pneumo.draw_pipe(d5, path, pneumo.MAIN, "14", 3.5, 4.9, spots, "end", 5.0)
    gap = pneumo._code_gap(4.9)
    horiz = sorted((min(l[0], l[2]), max(l[0], l[2])) for l in d5.lines if l[1] == l[3] == 0)
    # три куска с двумя разрывами длиной gap; текст — по центру каждого разрыва
    assert len(horiz) == 3
    for (a0, a1), (b0, b1) in zip(horiz, horiz[1:]):
        assert math.isclose(b0 - a1, gap, abs_tol=1e-6)
    assert len(d5.texts) == 2 and all(t[2] == 0 and t[4] == "14" for t in d5.texts)
    assert d5.fills == 1  # стрелка

    # вертикальная труба: текст повёрнут вдоль линии
    d5 = FakeDoc5()
    vp = [(0.0, 0.0), (0.0, 60.0)]
    pneumo.draw_pipe(d5, vp, pneumo.MAIN, "Д", 3.5, 3.8, pneumo._code_spots(vp, 3.8, 150), None, 5.0)
    assert len(d5.texts) == 1 and d5.texts[0][2] == 90

    # слишком короткая — обозначение не ставится
    assert pneumo._code_spots([(0.0, 0.0), (5.0, 0.0)], 4.9, 150) == []


def test_symbol_ports_match_catalog():
    tag = {"s": "compressor", "x": 100, "y": 200, "u": 5, "r": 90, "m": 0, "leads": 0}
    ports = pneumo.symbol_ports(tag)
    # повёрнутый на 90° компрессор: вход снизу, выход сверху
    assert math.isclose(ports["in"][0], 100, abs_tol=1e-9) and math.isclose(ports["in"][1], 185)
    assert math.isclose(ports["out"][1], 215)
    led = pneumo.symbol_ports({**tag, "r": 0, "leads": 5})
    assert math.isclose(led["out"][0], 120)


def test_geometry():
    box = [0, 0, 10, 5]
    assert scheme._seg_hits_box(-5, 2, 15, 2, box)
    assert not scheme._seg_hits_box(-5, 6, 15, 6, box)
    assert scheme._seg_hits_box(5, -5, 5, 1, box)
    assert scheme._circle_hits_box(0, 0, 3, box)          # дуга проходит через угол
    assert not scheme._circle_hits_box(5, 2.5, 20, box)   # текст целиком внутри кружка
    assert not scheme._circle_hits_box(50, 50, 3, box)
    assert scheme._boxes_overlap([0, 0, 2, 2], [1, 1, 3, 3])
    assert not scheme._boxes_overlap([0, 0, 2, 2], [2.5, 0, 3, 3])
    assert math.isclose(scheme._seg_dist(0, 1, -1, 0, 1, 0), 1)
    # габарит КОМПАСа у текста высотой 5: снизу хвосты, сверху запас над прописными
    sx = scheme._shrink([100, 88.91, 128.68, 96.29], 0)
    assert 89 < sx[1] < 90 and 95 < sx[3] < 95.5


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
