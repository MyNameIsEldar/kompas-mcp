"""Библиотека условных графических обозначений для пневматических и технологических схем.

Основа — таблица обозначений к курсовому проекту по «Компрессорным станциям и установкам»
(МГТУ им. Баумана, каф. Э5; обобщение ГОСТ 2.786-96, ГОСТ 2.704-2011, ГОСТ 21.205-2016).

Геометрия каждого символа задана в относительных единицах u с началом координат в центре символа.
При вставке она поворачивается, отражается, масштабируется (u мм) и рисуется через API5 внутри
макрообъекта КОМПАСа — символ выделяется и удаляется целиком. Порты — точки подключения
трубопроводов; их координаты на листе возвращаются после вставки.
"""
from __future__ import annotations

import inspect
import math
from dataclasses import dataclass, field
from typing import Callable

from kompas_com import LINE_STYLES, Kompas, KompasError

MAIN, THIN, AXIAL, DASHED = LINE_STYLES["main"], LINE_STYLES["thin"], LINE_STYLES["axial"], LINE_STYLES["dashed"]

# Размер модуля u (мм) по формату листа: на A1 круг компрессора — 30 мм, клапан — 20 мм.
UNIT_BY_FORMAT = {"A0": 6.0, "A1": 5.0, "A2": 4.0, "A3": 3.5, "A4": 2.5, "A5": 2.5}
LABEL_HEIGHT_BY_FORMAT = {"A0": 7.0, "A1": 5.0, "A2": 5.0, "A3": 3.5, "A4": 3.5, "A5": 3.5}


@dataclass
class Port:
    x: float
    y: float
    dx: float  # направление «наружу» — куда уходит трубопровод
    dy: float


@dataclass
class Sym:
    """Геометрия символа в единицах u."""
    items: list = field(default_factory=list)
    ports: dict[str, Port] = field(default_factory=dict)

    def line(self, x1, y1, x2, y2, style=MAIN):
        self.items.append(("line", [(x1, y1), (x2, y2)], style))

    def poly(self, pts, closed=False, style=MAIN):
        pts = list(pts)
        if closed:
            pts.append(pts[0])
        for a, b in zip(pts, pts[1:]):
            self.line(*a, *b, style=style)

    def circle(self, xc, yc, r, style=MAIN):
        self.items.append(("circle", [(xc, yc)], r, style))

    def arc(self, xc, yc, r, a1, a2, ccw=True, style=MAIN):
        self.items.append(("arc", [(xc, yc)], r, a1, a2, ccw, style))

    def fill(self, pts):
        """Залитый многоугольник (стрелки, закрашенные треугольники) вместе с контуром."""
        self.items.append(("fill", list(pts)))
        self.poly(pts, closed=True)

    def arrow(self, x1, y1, x2, y2, head=0.8, width=0.5, filled=True, style=MAIN):
        """Линия со стрелкой на конце (x2, y2)."""
        L = math.hypot(x2 - x1, y2 - y1) or 1.0
        ux, uy = (x2 - x1) / L, (y2 - y1) / L
        bx, by = x2 - ux * head, y2 - uy * head
        tri = [(x2, y2), (bx - uy * width / 2, by + ux * width / 2), (bx + uy * width / 2, by - ux * width / 2)]
        self.line(x1, y1, bx, by, style=style)
        if filled:
            self.fill(tri)
        else:
            self.poly(tri, closed=True)

    def text(self, x, y, s, h=1.0, narrow=1.0, anchor=None, fit=None):
        """Горизонтальный текст: по центру в (x, y) или, если задан anchor (dx, dy), — вплотную за точкой
        в этом направлении (направление поворачивается вместе с символом, сам текст — нет).
        fit — наибольшая ширина: шире по факту — сужается при вставке."""
        self.items.append(("text", [(x, y)], s, h, narrow, anchor, fit))

    def port(self, name, x, y, dx, dy):
        self.ports[name] = Port(x, y, dx, dy)


# --- Общие элементы ----------------------------------------------------------
def _tri(s: Sym, tip, base_center, half_w, filled=False):
    """Треугольник с вершиной tip и основанием, перпендикулярным оси tip—base_center."""
    tx, ty = tip
    bx, by = base_center
    L = math.hypot(tx - bx, ty - by)
    nx, ny = -(ty - by) / L, (tx - bx) / L
    pts = [tip, (bx + nx * half_w, by + ny * half_w), (bx - nx * half_w, by - ny * half_w)]
    if filled:
        s.fill(pts)
    else:
        s.poly(pts, closed=True)


def _diamond(s: Sym, h=3.0):
    s.poly([(-h, 0), (0, h), (h, 0), (0, -h)], closed=True)
    s.port("in", -h, 0, -1, 0)
    s.port("out", h, 0, 1, 0)


def _valve_body(s: Sym, left=True, right=True, bottom=False, top=False, filled_left=False):
    """Корпус клапана из треугольников, сходящихся вершинами в центре."""
    if left:
        _tri(s, (0, 0), (-2, 0), 1.0, filled=filled_left)
        s.port("in", -2, 0, -1, 0)
    if right:
        _tri(s, (0, 0), (2, 0), 1.0)
        s.port("out", 2, 0, 1, 0)
    if bottom:
        _tri(s, (0, 0), (0, -2), 1.0)
        s.port("out" if not right else "side", 0, -2, 0, -1)
    if top:
        _tri(s, (0, 0), (0, 2), 1.0)
        s.port("top", 0, 2, 0, 1)


def _spring(s: Sym, y0=0.0, y1=3.0):
    """Пружина предохранительного клапана — зигзаг над центром."""
    n = 5
    pts = [(0, y0), (0, y0 + 0.4)]
    step = (y1 - y0 - 0.9) / n
    for i in range(n):
        pts.append((0.45 if i % 2 == 0 else -0.45, y0 + 0.4 + step * (i + 0.5)))
    pts += [(0, y1 - 0.5), (0, y1)]
    s.poly(pts)


# --- Символы -----------------------------------------------------------------
def compressor(s: Sym):
    s.circle(0, 0, 3)
    _tri(s, (3, 0), (0.9, 0), 1.15)
    s.port("in", -3, 0, -1, 0)
    s.port("out", 3, 0, 1, 0)


def compressor_piston(s: Sym):
    # головка цилиндра с патрубками, стенки цилиндра, поршень, шток, кривошип
    s.poly([(-2, 0.8), (-2, 2), (2, 2), (2, 0.8)])
    s.line(-2, 0.8, 2, 0.8)
    s.line(-2, 0.8, -2, -1.6)
    s.line(2, 0.8, 2, -1.6)
    s.line(-2, 0.35, 2, 0.35)
    s.line(-2, 0.05, 2, 0.05)
    s.line(0, 0.05, 0, -2.6)
    s.line(-0.45, -2.6, 0.45, -2.6)
    s.poly([(-1.6, -4.2), (-0.9, -4.2), (-0.9, -2.95), (0.9, -2.95), (0.9, -4.2), (1.6, -4.2)])
    s.line(-0.4, -3.35, 0.4, -3.35)
    s.port("in", -2, 1.4, -1, 0)
    s.port("out", 2, 1.4, 1, 0)


def compressor_screw(s: Sym):
    s.circle(0, 0, 3)
    for a1, a2 in ((125, 12), (238, 338)):
        p1 = (3 * math.cos(math.radians(a1)), 3 * math.sin(math.radians(a1)))
        p2 = (3 * math.cos(math.radians(a2)), 3 * math.sin(math.radians(a2)))
        s.line(*p1, *p2)
    s.port("in", -3, 0, -1, 0)
    s.port("out", 3, 0, 1, 0)


def compressor_centrifugal(s: Sym):
    # трапеция: широкая сторона — вход, узкая — выход; штрихпунктир — ось вала
    s.line(-1, 3.2, -1, -4.4)
    s.line(-1, 3.2, 1, 1.9)
    s.line(1, 1.9, 1, -1.9)
    s.line(1, -1.9, -1, -3.2)
    s.line(1, 1.9, 1, 3.6)
    s.line(-2.2, 0, 2.2, 0, style=AXIAL)
    s.port("in", -1, -4.4, 0, -1)
    s.port("out", 1, 3.6, 0, 1)


def filter_main(s: Sym):
    _diamond(s)
    s.line(0, 3, 0, -3, style=DASHED)


def filter_drain(s: Sym):
    filter_main(s)
    s.arrow(0, -3, 0, -4.8)
    s.port("drain", 0, -4.8, 0, -1)


def heater(s: Sym):
    _diamond(s)
    s.line(-3, 0, 3, 0)
    s.arrow(0, 3, 0, 0.1, head=0.7, width=0.45)
    s.arrow(0, -3, 0, -0.1, head=0.7, width=0.45)


def cooler(s: Sym):
    _diamond(s)
    s.line(-3, 0, 3, 0)
    s.arrow(0, 0, 0, 2.9, head=0.7, width=0.45)
    s.arrow(0, 0, 0, -2.9, head=0.7, width=0.45)
    s.arrow(-0.9, -2.1, -0.9, -4.0, head=0.7, width=0.45)
    s.arrow(0.9, -4.0, 0.9, -2.1, head=0.7, width=0.45)
    s.port("coolant_out", -0.9, -4.0, 0, -1)
    s.port("coolant_in", 0.9, -4.0, 0, -1)


def heat_exchanger(s: Sym):
    """Теплообменник как на примерах схем (ВО, МО): ромб и двунаправленная стрелка теплоносителя."""
    _diamond(s)
    s.arrow(0, 0, 0, 2.9, head=0.7, width=0.45)
    s.arrow(0, 0, 0, -2.9, head=0.7, width=0.45)


def dryer(s: Sym):
    _diamond(s)
    s.line(-1.5, 1.5, 1.5, 1.5)
    s.line(-1.5, -1.5, 1.5, -1.5)


def condensate_trap(s: Sym):
    _diamond(s, h=2.2)
    s.line(-1.1, -1.1, 1.1, -1.1)
    s.poly([(-2.75, 0.3), (-2.2, 0), (-2.75, -0.3)])  # стрелка на входе, как в таблице
    s.arrow(0, -2.2, 0, -3.8)
    s.port("drain", 0, -3.8, 0, -1)


def silencer(s: Sym):
    s.poly([(-2.6, -1.6), (-2.6, 1.6), (2.6, 1.6), (2.6, -1.6)], closed=True)
    s.line(-1.0, 1.6, -1.0, -0.6)
    s.line(0, -1.6, 0, 0.6)
    s.line(1.0, 1.6, 1.0, -0.6)
    s.port("in", -2.6, 0, -1, 0)
    s.port("out", 2.6, 0, 1, 0)


def flow_liquid(s: Sym):
    s.line(-2, 0, 2, 0)
    _tri(s, (0.6, 0), (-0.6, 0), 0.55, filled=True)
    s.port("in", -2, 0, -1, 0)
    s.port("out", 2, 0, 1, 0)


def flow_air(s: Sym):
    s.line(-2, 0, -0.6, 0)
    s.line(0.6, 0, 2, 0)
    _tri(s, (0.6, 0), (-0.6, 0), 0.55)
    s.port("in", -2, 0, -1, 0)
    s.port("out", 2, 0, 1, 0)


def fan_centrifugal(s: Sym):
    a_end = 345
    ex, ey = 2 * math.cos(math.radians(a_end)), 2 * math.sin(math.radians(a_end))
    s.arc(0, 0, 2, 90, a_end, ccw=True)
    s.poly([(0, 2), (2.4, 2), (2.4, 0.7), (ex, ey)])
    s.circle(0, 0, 0.75)
    s.port("in", 0, 0, 0, 0)
    s.port("out", 2.4, 1.35, 1, 0)


def fan_axial(s: Sym):
    s.poly([(-0.9, -2.5), (-0.9, 2.5), (0.9, 2.5), (0.9, -2.5)], closed=True)
    s.poly([(-0.6, 1.6), (0.6, 1.6), (0, 0)], closed=True)
    s.poly([(-0.6, -1.6), (0.6, -1.6), (0, 0)], closed=True)
    s.port("in", 0, -2.5, 0, -1)
    s.port("out", 0, 2.5, 0, 1)


def valve(s: Sym):
    _valve_body(s)


def valve_angle(s: Sym):
    _valve_body(s, right=False, bottom=True)


def valve_3way(s: Sym):
    _valve_body(s, bottom=True)


def valve_manual(s: Sym):
    """Запорный клапан с ручным приводом (как на примерах схем)."""
    _valve_body(s)
    s.line(0, 0, 0, 1.7)
    s.line(-0.8, 1.7, 0.8, 1.7)


def control_valve(s: Sym):
    _valve_body(s)
    s.arrow(0, 2.2, 0, 0.05, head=0.7, width=0.45)
    s.port("ctrl", 0, 2.2, 0, 1)


def control_valve_angle(s: Sym):
    _valve_body(s, right=False, bottom=True)
    s.arrow(0, 2.2, 0, 0.05, head=0.7, width=0.45)
    s.port("ctrl", 0, 2.2, 0, 1)


def control_valve_3way(s: Sym):
    _valve_body(s, right=False, bottom=True, top=True)
    s.arrow(2.4, 0, 0.05, 0, head=0.7, width=0.45)
    s.port("ctrl", 2.4, 0, 1, 0)


def check_valve(s: Sym):
    # поток — от светлого треугольника к закрашенному, по стрелке (на таблице и примерах — справа налево);
    # стрелка над корпусом, чтобы не выходить за габарит клапана
    _valve_body(s, filled_left=True)
    s.arrow(1.4, 1.5, -1.4, 1.5, head=0.7, width=0.45)
    s.ports.clear()
    s.port("in", 2, 0, 1, 0)
    s.port("out", -2, 0, -1, 0)


def check_valve_angle(s: Sym):
    _valve_body(s, right=False, bottom=True, filled_left=True)
    s.arrow(0.9, 1.5, -1.6, 1.5, head=0.7, width=0.45)
    s.ports.clear()
    s.port("in", 0, -2, 0, -1)
    s.port("out", -2, 0, -1, 0)


def _stem(s: Sym, y1):
    s.line(0, 0, 0, y1)


def valve_electric(s: Sym):
    """Запорный клапан с электроприводом: круг с буквой М над корпусом."""
    _valve_body(s)
    _stem(s, 1.4)
    s.circle(0, 2.3, 0.9)
    s.text(0, 2.3, "М", h=0.9)
    s.port("ctrl", 0, 3.2, 0, 1)


def valve_solenoid(s: Sym):
    """Запорный клапан с электромагнитным приводом: квадрат с буквой Э."""
    _valve_body(s)
    _stem(s, 1.4)
    s.poly([(-0.85, 1.4), (-0.85, 3.1), (0.85, 3.1), (0.85, 1.4)], closed=True)
    s.text(0, 2.25, "Э", h=0.9)
    s.port("ctrl", 0, 3.1, 0, 1)


def valve_diaphragm(s: Sym):
    """Клапан с мембранным (пневматическим) приводом: полукруг мембраны над корпусом."""
    _valve_body(s)
    _stem(s, 1.5)
    s.line(-1.2, 1.5, 1.2, 1.5)
    s.arc(0, 1.5, 1.2, 0, 180, ccw=True)
    s.port("ctrl", 0, 2.7, 0, 1)


def thermostat_3way(s: Sym):
    """Трёхходовой терморегулятор: трёхходовой клапан с термосиловым приводом (мембрана, капилляр,
    термобаллон). Порт sense — конец термобаллона, к нему подводят линию от точки измерения."""
    _valve_body(s, bottom=True)
    _stem(s, 1.4)
    s.line(-1.1, 1.4, 1.1, 1.4)
    s.arc(0, 1.4, 1.1, 0, 180, ccw=True)
    s.line(0.78, 2.18, 2.0, 2.18, style=THIN)  # капилляр
    s.fill([(2.0, 1.88), (2.9, 1.88), (2.9, 2.48), (2.0, 2.48)])  # термобаллон
    s.port("sense", 2.9, 2.18, 1, 0)


def _level(s: Sym, y, x0, x1, xm):
    """Уровень жидкости: тонкая линия и треугольник ▽ над ней."""
    s.line(x0, y, x1, y, style=THIN)
    s.poly([(xm, y), (xm - 0.35, y + 0.55), (xm + 0.35, y + 0.55)], closed=True, style=THIN)


def _vessel_v(s: Sym, r=1.8, h=3.0, level=-1.6):
    s.line(-r, -h, -r, h)
    s.line(r, -h, r, h)
    s.arc(0, h, r, 0, 180, ccw=True)
    s.arc(0, -h, r, 180, 360, ccw=True)
    if level is not None:
        _level(s, level, -r, r, r - 0.7)
    s.port("in", -r, 1.4, -1, 0)
    s.port("out", 0, h + r, 0, 1)
    s.port("drain", 0, -h - r, 0, -1)
    s.port("side", r, 1.4, 1, 0)


def separator(s: Sym):
    """Сепаратор (газожидкостный) — вертикальный сосуд с уровнем жидкости."""
    _vessel_v(s)


def oil_separator(s: Sym):
    """Маслоотделитель: вертикальный сосуд, входной патрубок опущен внутрь, уровень масла."""
    _vessel_v(s)
    s.poly([(-1.8, 1.4), (-0.6, 1.4), (-0.6, 0.0)])


def filter_separator(s: Sym):
    """Фильтр-сепаратор: вертикальный сосуд с фильтрующим элементом (штриховой) и уровнем жидкости."""
    _vessel_v(s)
    s.poly([(-1.0, 0.0), (-1.0, 3.2), (1.0, 3.2), (1.0, 0.0)], closed=True, style=THIN)
    s.line(-1.0, 0.0, 1.0, 3.2, style=DASHED)
    s.line(-1.0, 3.2, 1.0, 0.0, style=DASHED)


def tank_horizontal(s: Sym):
    """Горизонтальная ёмкость с эллиптическими (сферическими) днищами и уровнем жидкости."""
    r, h, lv = 1.6, 3.0, -0.6
    s.line(-h, r, h, r)
    s.line(-h, -r, h, -r)
    s.arc(-h, 0, r, 90, 270, ccw=True)
    s.arc(h, 0, r, 270, 90, ccw=True)
    dx = math.sqrt(r * r - lv * lv)
    _level(s, lv, -h - dx, h + dx, h - 0.4)
    s.port("in", -2.0, r, 0, 1)
    s.port("top", 1.0, r, 0, 1)
    s.port("out", 2.0, -r, 0, -1)
    s.port("drain", -2.0, -r, 0, -1)
    s.port("left", -h - r, 0, -1, 0)
    s.port("right", h + r, 0, 1, 0)


def motor(s: Sym):
    """Электродвигатель — круг с буквой М; shaft — выход вала (вправо)."""
    s.circle(0, 0, 1.6)
    s.text(0, 0, "М", h=1.3)
    s.port("shaft", 1.6, 0, 1, 0)
    s.port("left", -1.6, 0, -1, 0)
    s.port("top", 0, 1.6, 0, 1)
    s.port("bottom", 0, -1.6, 0, -1)


def air_cooler(s: Sym):
    """Аппарат воздушного охлаждения (АВО): трубный пучок, под ним вентилятор с электродвигателем,
    стрелки — поток воздуха вверх."""
    s.poly([(-3, 0.2), (-3, 1.8), (3, 1.8), (3, 0.2)], closed=True)
    s.poly([(-3, 1.0), (-2.2, 1.0), (-1.7, 1.5), (-0.6, 0.5), (0.6, 1.5), (1.7, 0.5), (2.2, 1.0), (3, 1.0)])
    for x in (-1.8, 1.8):
        s.arrow(x, 2.0, x, 3.0, head=0.6, width=0.4)
    # вентилятор — две лопасти, вал, двигатель
    s.poly([(0, -0.7), (-1.9, -0.3), (-1.9, -1.1)], closed=True)
    s.poly([(0, -0.7), (1.9, -0.3), (1.9, -1.1)], closed=True)
    s.line(0, -0.7, 0, -1.9)
    s.circle(0, -2.8, 0.9)
    s.text(0, -2.8, "М", h=0.9)
    s.port("in", -3, 1.0, -1, 0)
    s.port("out", 3, 1.0, 1, 0)


def heater_electric(s: Sym):
    """Электронагреватель: корпус с нагревательным элементом (зигзаг) и выводами питания сверху."""
    s.poly([(-2.5, -1.5), (-2.5, 1.5), (2.5, 1.5), (2.5, -1.5)], closed=True)
    s.poly([(-1.8, 0), (-1.5, 0.7), (-0.9, -0.7), (-0.3, 0.7), (0.3, -0.7), (0.9, 0.7), (1.5, -0.7), (1.8, 0)])
    for x in (-0.8, 0.8):
        s.line(x, 1.5, x, 2.4, style=THIN)
        s.circle(x, 2.6, 0.2, style=THIN)
    s.port("in", -2.5, 0, -1, 0)
    s.port("out", 2.5, 0, 1, 0)


def line_break(s: Sym, text: str = "в Е-1", incoming: bool = False):
    """Обрыв линии с адресом: короткая линия со стрелкой и надпись за ней («в Е-1», «на свечу»,
    «на факел», «из К-1»). incoming=true — стрелка к порту (поток приходит с другого листа/установки)."""
    if incoming:
        s.arrow(0, 0, -2, 0, head=0.8, width=0.5)
    else:
        s.arrow(-2, 0, 0, 0, head=0.8, width=0.5)
    s.text(0.4, 0, str(text), h=1.0, anchor=(1, 0))
    s.port("in", -2, 0, -1, 0)


_KIP_R = 1.0  # прибор по ГОСТ 21.208 — окружность Ø10 мм на A1 (u = 5 мм)
_GLYPH = 0.82  # ширина заглавной буквы / цифры с промежутком в долях высоты шрифта (замер в КОМПАСе)


def _fit_text(s: str, h_max: float, width_at) -> tuple[float, float]:
    """Высота и сужение, при которых строка помещается в ширину width_at(h) (ширина зависит от высоты —
    хорда круга). Шрифт сужается до 0.75, дальше уменьшается высота."""
    n = max(len(s), 1)
    h = h_max
    while h > 0.15:
        avail = width_at(h)
        if avail > 0:
            k = avail / (_GLYPH * h * n)
            if k >= 0.75:
                return h, round(min(k, 1.0), 3)
        h -= 0.02
    return 0.15, 0.75


def instrument(s: Sym, code: str = "PI", position: str = "", panel: bool = False, marks=None):
    """Прибор КИП по ГОСТ 21.208: окружность, сверху — буквенный код (PT, TIC, PDIA…), снизу — номер
    позиции; panel=true — горизонтальная черта (прибор на щите); marks — метки предельных значений справа
    (["H","L"], "H" и т. п.). Текст подбирается по высоте и сужению так, чтобы поместиться в круг."""
    r = _KIP_R
    s.circle(0, 0, r)
    if panel:
        s.line(-r, 0, r, 0)
    gap, margin = 0.1, 0.12
    chord = lambda y: 2 * math.sqrt(max(r * r - y * y, 0)) - 2 * margin  # noqa: E731
    code, position = str(code or ""), str(position or "")
    if code:
        h, k = _fit_text(code, 0.55 if position or panel else 0.7,
                         lambda h: chord(gap + h) if position or panel else chord(h / 2))
        y = gap + h / 2 if position or panel else 0
        s.text(0, y, code, h=h, narrow=k, fit=chord(abs(y) + h / 2))
    if position:
        h, k = _fit_text(position, 0.5, lambda h: chord(gap + h))
        s.text(0, -gap - h / 2, position, h=h, narrow=k, fit=chord(gap + h))
    if marks:
        marks = [marks] if isinstance(marks, str) else [str(m) for m in marks]
        if len(marks) == 1:
            slots = [(marks[0], -0.5 if marks[0].upper().startswith("L") else 0.5)]
        else:
            slots = [(marks[0], 0.5), (marks[1], -0.5)]
        for m, y in slots[:2]:
            x = math.sqrt(r * r - y * y) + 0.15
            s.text(x, y, m, h=0.5, anchor=(1, 0))
    s.port("bottom", 0, -r, 0, -1)
    s.port("top", 0, r, 0, 1)
    s.port("left", -r, 0, -1, 0)
    s.port("right", r, 0, 1, 0)


def safety_valve(s: Sym):
    _valve_body(s)
    _spring(s, 0, 3.2)
    s.port("set", 0, 3.2, 0, 1)


def safety_valve_angle(s: Sym):
    _valve_body(s, right=False, bottom=True)
    _spring(s, 0, 3.2)
    s.port("set", 0, 3.2, 0, 1)


def reducing_valve(s: Sym):
    s.poly([(-1.8, -0.9), (-1.8, 0.9), (1.8, 0.9), (1.8, -0.9)], closed=True)
    s.line(-1.8, 0.9, 1.8, 0)
    s.line(-1.8, -0.9, 1.8, 0)
    s.arrow(-1.8, 0, -3.8, 0, head=0.8, width=0.55)
    s.port("in", 1.8, 0, 1, 0)
    s.port("out", -3.8, 0, -1, 0)


def throttle_valve(s: Sym):
    s.poly([(-1.5, -0.9), (-1.5, 0.9), (1.5, 0.9), (1.5, -0.9)], closed=True)
    s.line(-1.5, -0.9, 1.5, 0.9)
    s.line(-1.5, 0.9, 1.5, -0.9)
    s.port("in", -1.5, 0, -1, 0)
    s.port("out", 1.5, 0, 1, 0)


def flowmeter(s: Sym):
    r = 1.3
    s.circle(0, 0, r)
    for a in (40, -40):
        s.line(-r, 0, r * math.cos(math.radians(a)), r * math.sin(math.radians(a)))
    s.port("in", -r, 0, -1, 0)
    s.port("out", r, 0, 1, 0)


def pump(s: Sym):
    s.circle(0, 0, 3)
    _tri(s, (3, 0), (2.25, 0), 0.45, filled=True)
    s.port("in", -3, 0, -1, 0)
    s.port("out", 3, 0, 1, 0)


def pump_centrifugal(s: Sym):
    s.circle(0, 0, 2)
    s.line(0, 2, -2.6, 2)
    s.line(0, 0.3, 0, -3.2)
    s.port("in", 0, -3.2, 0, -1)
    s.port("out", -2.6, 2, -1, 0)


def pump_gear(s: Sym):
    for off in (0.0, 0.22):
        r = 0.95 - off
        s.line(-0.75, r, 0.75, r)
        s.line(-0.75, -r, 0.75, -r)
        s.arc(-0.75, 0, r, 90, 270, ccw=True)
        s.arc(0.75, 0, r, 270, 90, ccw=True)
    s.circle(-0.55, 0, 0.42)
    s.circle(0.55, 0, 0.42)
    s.line(0, 0.95, 0, 1.6)
    s.line(0, -0.95, 0, -1.6)
    s.port("in", 0, -1.6, 0, -1)
    s.port("out", 0, 1.6, 0, 1)


def receiver(s: Sym):
    """Ресивер (воздухосборник) — вертикальный сосуд со сферическими днищами."""
    r, h = 1.6, 2.6
    s.line(-r, -h, -r, h)
    s.line(r, -h, r, h)
    s.arc(0, h, r, 0, 180, ccw=True)
    s.arc(0, -h, r, 180, 360, ccw=True)
    s.port("in", -r, h - 0.6, -1, 0)
    s.port("out", r, h - 0.6, 1, 0)
    s.port("drain", 0, -h - r, 0, -1)
    s.port("top", 0, h + r, 0, 1)


def consumer(s: Sym):
    """Потребитель сжатого воздуха «П» (прямоугольник с буквой)."""
    s.poly([(-1.5, -1.2), (-1.5, 1.2), (1.5, 1.2), (1.5, -1.2)], closed=True)
    s.text(0, 0, "П", h=1.0)
    s.port("in", 0, -1.2, 0, -1)
    s.port("top", 0, 1.2, 0, 1)
    s.port("left", -1.5, 0, -1, 0)
    s.port("right", 1.5, 0, 1, 0)


def drain(s: Sym):
    """Слив конденсата — закрашенный треугольник вершиной вниз."""
    s.fill([(-0.7, 0), (0.7, 0), (0, -1.1)])
    s.port("in", 0, 0, 0, 1)


def junction(s: Sym):
    """Узел соединения трубопроводов — закрашенная точка."""
    pts = [(0.3 * math.cos(math.radians(a)), 0.3 * math.sin(math.radians(a))) for a in range(0, 360, 30)]
    s.fill(pts)
    s.port("c", 0, 0, 0, 0)


# имя → (функция, наименование по таблице, № позиции в таблице)
CATALOG: dict[str, tuple[Callable[[Sym], None], str, str]] = {
    "compressor": (compressor, "Компрессор, общее обозначение", "1"),
    "compressor_piston": (compressor_piston, "Компрессор поршневой", "1"),
    "compressor_screw": (compressor_screw, "Компрессор винтовой", "1"),
    "compressor_centrifugal": (compressor_centrifugal, "Компрессор центробежный", "1"),
    "filter": (filter_main, "Фильтр магистральный", "2"),
    "filter_drain": (filter_drain, "Фильтр с конденсатоотводом", "3"),
    "heater": (heater, "Теплообменник-нагреватель потока", "4"),
    "cooler": (cooler, "Теплообменник-охладитель потока", "5"),
    "heat_exchanger": (heat_exchanger, "Теплообменник (как на примерах схем: ВО, МО)", "—"),
    "dryer": (dryer, "Осушитель воздуха", "6"),
    "condensate_trap": (condensate_trap, "Конденсатоотводчик", "7"),
    "silencer": (silencer, "Глушитель шума", "8"),
    "flow_liquid": (flow_liquid, "Направление потока жидкости", "9"),
    "flow_air": (flow_air, "Направление потока воздуха", "9"),
    "fan_centrifugal": (fan_centrifugal, "Вентилятор центробежный", "10"),
    "fan_axial": (fan_axial, "Вентилятор осевой", "10"),
    "valve": (valve, "Клапан запорный проходной", "11"),
    "valve_angle": (valve_angle, "Клапан запорный угловой", "11"),
    "valve_3way": (valve_3way, "Клапан запорный трёхходовой", "11"),
    "valve_manual": (valve_manual, "Клапан запорный с ручным приводом (как на примерах схем)", "—"),
    "control_valve": (control_valve, "Клапан регулируемый проходной", "12"),
    "control_valve_angle": (control_valve_angle, "Клапан регулируемый угловой", "12"),
    "control_valve_3way": (control_valve_3way, "Клапан регулируемый трёхходовой", "12"),
    "check_valve": (check_valve, "Клапан обратный проходной", "13"),
    "check_valve_angle": (check_valve_angle, "Клапан обратный угловой", "13"),
    "safety_valve": (safety_valve, "Клапан предохранительный проходной", "14"),
    "safety_valve_angle": (safety_valve_angle, "Клапан предохранительный угловой", "14"),
    "reducing_valve": (reducing_valve, "Клапан редукционный", "15"),
    "throttle_valve": (throttle_valve, "Клапан дроссельный", "16"),
    "flowmeter": (flowmeter, "Расходомер", "17"),
    "pump": (pump, "Насос, общее обозначение", "18"),
    "pump_centrifugal": (pump_centrifugal, "Насос центробежный", "18"),
    "pump_gear": (pump_gear, "Насос шестерёнчатый", "18"),
    "receiver": (receiver, "Ресивер (воздухосборник) (как на примерах схем: Р)", "—"),
    "consumer": (consumer, "Потребитель сжатого воздуха «П» (как на примерах схем)", "—"),
    "drain": (drain, "Слив конденсата (как на примерах схем)", "—"),
    "junction": (junction, "Узел соединения трубопроводов", "—"),
    "valve_electric": (valve_electric, "Клапан (задвижка) с электроприводом «М»", "—"),
    "valve_solenoid": (valve_solenoid, "Клапан с электромагнитным приводом «Э»", "—"),
    "valve_diaphragm": (valve_diaphragm, "Клапан с мембранным приводом", "—"),
    "thermostat_3way": (thermostat_3way, "Терморегулятор трёхходовой (термосиловой привод)", "—"),
    "separator": (separator, "Сепаратор вертикальный с уровнем жидкости", "—"),
    "oil_separator": (oil_separator, "Маслоотделитель вертикальный с уровнем масла", "—"),
    "filter_separator": (filter_separator, "Фильтр-сепаратор вертикальный", "—"),
    "tank_horizontal": (tank_horizontal, "Ёмкость горизонтальная с уровнем жидкости", "—"),
    "motor": (motor, "Электродвигатель «М»", "—"),
    "air_cooler": (air_cooler, "Аппарат воздушного охлаждения (АВО) с вентилятором и двигателем", "—"),
    "heater_electric": (heater_electric, "Электронагреватель", "—"),
    "line_break": (line_break, "Обрыв линии с адресом (параметр text: «в Е-1», «на свечу», «на факел»)", "—"),
    "instrument": (instrument, "Прибор КИП по ГОСТ 21.208 (параметры code, position, panel, marks)", "—"),
}

# параметры символов, которые можно передать в элементе kompas_place_symbols
SYMBOL_PARAMS = ("text", "incoming", "code", "position", "panel", "marks")


def _params_of(fn) -> dict:
    return {p.name: p.default for p in list(inspect.signature(fn).parameters.values())[1:]}


def build(name: str, params: dict | None = None) -> Sym:
    if name not in CATALOG:
        raise KompasError(f"Неизвестный символ «{name}». Список: kompas_symbols_catalog.")
    fn = CATALOG[name][0]
    params = params or {}
    extra = set(params) - set(_params_of(fn))
    if extra:
        raise KompasError(f"У символа «{name}» нет параметров: {', '.join(sorted(extra))}.")
    s = Sym()
    fn(s, **params)
    return s


def _text_box(x, y, s, h, narrow, anchor, fit=None) -> tuple[float, float, float, float]:
    w = _text_width(s, h) * narrow
    if fit:
        w = min(w, fit)
    if anchor:
        x, y = x + anchor[0] * w / 2, y + anchor[1] * h / 2
    return x - w / 2, y - h / 2, x + w / 2, y + h / 2


def _bbox(s: Sym) -> tuple[float, float, float, float]:
    xs, ys = [], []
    for it in s.items:
        kind = it[0]
        if kind in ("circle", "arc"):
            (xc, yc), r = it[1][0], it[2]
            xs += [xc - r, xc + r]
            ys += [yc - r, yc + r]
        elif kind == "text":
            x0, y0, x1, y1 = _text_box(*it[1][0], *it[2:])
            xs += [x0, x1]
            ys += [y0, y1]
        else:
            for x, y in it[1]:
                xs.append(x)
                ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def catalog(unit: float = 5.0) -> list[dict]:
    out = []
    for name, (fn, title, pos) in CATALOG.items():
        s = build(name)
        x0, y0, x1, y1 = _bbox(s)
        entry = {
            "symbol": name, "title": title, "table_no": pos,
            "size_mm": [round((x1 - x0) * unit, 1), round((y1 - y0) * unit, 1)],
            "ports": {k: {"at_mm": [round(p.x * unit, 2), round(p.y * unit, 2)], "dir": [p.dx, p.dy]}
                      for k, p in s.ports.items()},
        }
        if _params_of(fn):
            entry["params"] = _params_of(fn)
        out.append(entry)
    return out


# --- Вставка в документ ------------------------------------------------------
class _Xform:
    def __init__(self, x, y, unit, rotate, mirror):
        self.x, self.y, self.u = x, y, unit
        self.rot = rotate % 360
        self.mirror = mirror
        a = math.radians(self.rot)
        self.c, self.s = math.cos(a), math.sin(a)

    def vec(self, vx, vy):
        if self.mirror:
            vx = -vx
        return vx * self.c - vy * self.s, vx * self.s + vy * self.c

    def pt(self, px, py):
        rx, ry = self.vec(px, py)
        return self.x + rx * self.u, self.y + ry * self.u

    def angle(self, a):
        if self.mirror:
            a = 180 - a
        return (a + self.rot) % 360


def _sheet_format(k: Kompas) -> str | None:
    d = k.resolve_doc()
    if d.DocumentType != 1:
        return None
    from kompas_com import SHEET_FORMATS
    names = {v: n for n, v in SHEET_FORMATS.items()}
    return names.get(k.API7.IKompasDocument2D(d).LayoutSheets.Item(0).Format.Format)


def default_unit(k: Kompas) -> tuple[float, float]:
    fmt = _sheet_format(k) or "A1"
    return UNIT_BY_FORMAT.get(fmt, 5.0), LABEL_HEIGHT_BY_FORMAT.get(fmt, 5.0)


def _text_width(s: str, h: float) -> float:
    return 0.8 * h * len(s)  # оценка; точная ширина — по габариту после вставки (_put_text)


def _measure(k: Kompas, doc5, texts, cache: dict) -> None:
    """Габарит строк шрифтом высотой 1 мм без сужения: (смещение левого края, ширина). Пробный текст
    вставляется и сразу удаляется — до открытия макрообъекта: внутри него габарит не считается."""
    for text in texts:
        if text in cache:
            continue
        ref = doc5.ksText(0, 0, 0, 10, 1, 0, text)
        rect = k.app5.GetParamStruct(15)  # ko_RectParam
        doc5.ksGetObjGabaritRect(ref, rect)
        x0, x1 = rect.GetpBot().x, rect.GetpTop().x
        doc5.ksDeleteObj(ref)
        cache[text] = (x0 / 10, (x1 - x0) / 10) if x1 > x0 else (0.0, _text_width(text, 1.0))


def _put_text(doc5, cache, x, y, text, h, narrow=1.0, anchor=None, fit=None):
    """Текст по центру в (x, y) или вплотную за точкой в направлении anchor; шире fit — сужается."""
    off, w1 = cache.get(text) or (0.0, _text_width(text, 1.0))
    w = w1 * h * narrow
    if fit and w > fit:
        narrow *= fit / w
        w = fit
    cx, cy = x, y
    if anchor:
        cx, cy = x + anchor[0] * w / 2, y + anchor[1] * h / 2
    return doc5.ksText(cx - w / 2 - off * h * narrow, cy - h / 2, 0, h, narrow, 0, text)


def place(k: Kompas, items: list[dict], view_number: int | None = None, unit: float | None = None) -> list[dict]:
    d = k.resolve_doc()
    if d.DocumentType not in (1, 2):
        raise KompasError("Символы вставляются в чертёж или фрагмент.")
    k.activate(d)
    if view_number is not None:
        v = k.view(d, view_number)
        v.Current = True
        v.Update()
    u_def, label_h_def = default_unit(k)
    u0 = float(unit or u_def)
    doc5 = k.app5.ActiveDocument2D()
    results, metrics = [], {}
    for n, it in enumerate(items):
        name = it.get("symbol")
        s = build(name, {p: it[p] for p in SYMBOL_PARAMS if p in it})
        if "x" not in it or "y" not in it:
            raise KompasError(f"Символ #{n} ({name}): нужны x и y — центр символа, мм.")
        u = float(it.get("unit", u0))
        xf = _Xform(float(it["x"]), float(it["y"]), u, float(it.get("rotate", 0)), bool(it.get("mirror", False)))
        leads = float(it.get("leads", 0))
        label_h = float(it.get("label_height", label_h_def))

        label = it.get("label")
        _measure(k, doc5, [el[2] for el in s.items if el[0] == "text"] + ([str(label)] if label else []), metrics)
        doc5.ksMacro(0)
        for el in s.items:
            kind = el[0]
            if kind == "line":
                (x1, y1), (x2, y2) = xf.pt(*el[1][0]), xf.pt(*el[1][1])
                doc5.ksLineSeg(x1, y1, x2, y2, el[2])
            elif kind == "circle":
                xc, yc = xf.pt(*el[1][0])
                doc5.ksCircle(xc, yc, el[2] * u, el[3])
            elif kind == "arc":
                xc, yc = xf.pt(*el[1][0])
                _, _, r, a1, a2, ccw, style = el
                if xf.mirror:
                    ccw = not ccw
                doc5.ksArcByAngle(xc, yc, r * u, xf.angle(a1), xf.angle(a2), 1 if ccw else -1, style)
            elif kind == "fill":
                pts = [xf.pt(*p) for p in el[1]]
                doc5.ksColouring(0)  # 0 — чёрный
                for a, b in zip(pts, pts[1:] + pts[:1]):
                    doc5.ksLineSeg(*a, *b, MAIN)
                doc5.ksEndObj()
            elif kind == "text":
                _, _, text, h, narrow, anchor, fit = el
                x, y = xf.pt(*el[1][0])
                _put_text(doc5, metrics, x, y, text, h * u, narrow, anchor and xf.vec(*anchor), fit and fit * u)
        ports = {}
        for pname, p in s.ports.items():
            px, py = xf.pt(p.x, p.y)
            dx, dy = xf.vec(p.dx, p.dy)
            if leads and (p.dx or p.dy):
                ex, ey = px + dx * leads, py + dy * leads
                doc5.ksLineSeg(px, py, ex, ey, MAIN)
                px, py = ex, ey
            ports[pname] = {"x": round(px, 3), "y": round(py, 3), "dir": [round(dx), round(dy)]}
        if label:
            _draw_label(metrics, doc5, s, xf, str(label), it.get("label_pos", "top"), label_h)
        ref = doc5.ksEndObj()
        if not ref:
            raise KompasError(f"Символ #{n} ({name}) не создан.")
        x0, y0, x1, y1 = _bbox(s)
        corners = [xf.pt(x0, y0), xf.pt(x1, y1), xf.pt(x0, y1), xf.pt(x1, y0)]
        results.append({
            "symbol": name, "reference": ref, "ports": ports,
            "bbox": [round(min(c[0] for c in corners), 2), round(min(c[1] for c in corners), 2),
                     round(max(c[0] for c in corners), 2), round(max(c[1] for c in corners), 2)],
        })
    return results


def _draw_label(metrics: dict, doc5, s: Sym, xf: _Xform, text: str, pos, h: float) -> None:
    """Позиционное обозначение (К1, Ф, Р…) рядом с символом; текст всегда горизонтальный."""
    x0, y0, x1, y1 = _bbox(s)
    corners = [xf.pt(x0, y0), xf.pt(x1, y1), xf.pt(x0, y1), xf.pt(x1, y0)]
    bx0, bx1 = min(c[0] for c in corners), max(c[0] for c in corners)
    by0, by1 = min(c[1] for c in corners), max(c[1] for c in corners)
    gap, anchor = h * 0.6, None
    if isinstance(pos, (list, tuple)):
        tx, ty = xf.x + float(pos[0]), xf.y + float(pos[1])
    elif pos == "bottom":
        tx, ty = (bx0 + bx1) / 2, by0 - gap - h / 2
    elif pos == "left":
        tx, ty, anchor = bx0 - gap, (by0 + by1) / 2, (-1, 0)
    elif pos == "right":
        tx, ty, anchor = bx1 + gap, (by0 + by1) / 2, (1, 0)
    else:  # top
        tx, ty = (bx0 + bx1) / 2, by1 + gap + h / 2
    _put_text(doc5, metrics, tx, ty, text, h, anchor=anchor)


# --- Трубопроводы ------------------------------------------------------------
PIPE_STYLES = {"main": MAIN, "control": DASHED, "thin": THIN}


def _route(pts: list[list[float]], route: str | None) -> list[tuple[float, float]]:
    """Ортогональная разводка: между соседними точками вставляется излом (hv — сначала по X)."""
    out = [tuple(map(float, pts[0]))]
    for p in pts[1:]:
        x, y = map(float, p)
        px, py = out[-1]
        if route in ("hv", "vh") and px != x and py != y:
            out.append((x, py) if route == "hv" else (px, y))
        out.append((x, y))
    return out


def pipes(k: Kompas, items: list[dict], view_number: int | None = None) -> list[dict]:
    d = k.resolve_doc()
    k.activate(d)
    if view_number is not None:
        v = k.view(d, view_number)
        v.Current = True
        v.Update()
    _, label_h = default_unit(k)
    u, _ = default_unit(k)
    doc5 = k.app5.ActiveDocument2D()
    out = []
    for n, it in enumerate(items):
        pts = it.get("points") or []
        if len(pts) < 2:
            raise KompasError(f"Трубопровод #{n}: нужно минимум 2 точки в points.")
        kind = it.get("kind", "main")
        if kind not in PIPE_STYLES:
            raise KompasError(f"Трубопровод #{n}: kind — {', '.join(PIPE_STYLES)}.")
        path = _route(pts, it.get("route"))
        refs = []
        for a, b in zip(path, path[1:]):
            if a != b:
                refs.append(doc5.ksLineSeg(*a, *b, PIPE_STYLES[kind]))
        arrow = it.get("arrow")  # "end" | "start" | "both"
        for where in (("end",) if arrow == "end" else ("start",) if arrow == "start"
                      else ("start", "end") if arrow == "both" else ()):
            (x1, y1), (x2, y2) = (path[-2], path[-1]) if where == "end" else (path[1], path[0])
            L = math.hypot(x2 - x1, y2 - y1) or 1.0
            ux, uy = (x2 - x1) / L, (y2 - y1) / L
            hl, hw = 0.8 * u, 0.25 * u
            bx, by = x2 - ux * hl, y2 - uy * hl
            tri = [(x2, y2), (bx - uy * hw, by + ux * hw), (bx + uy * hw, by - ux * hw)]
            doc5.ksColouring(0)
            for a, b in zip(tri, tri[1:] + tri[:1]):
                doc5.ksLineSeg(*a, *b, MAIN)
            refs.append(doc5.ksEndObj())
        out.append({"kind": kind, "points": [[round(x, 2), round(y, 2)] for x, y in path], "references": refs})
    return out
