"""
flame.py — 18 flame contours, growing.

Adapted from another instance's script. Fixed a handful of copy-paste
character losses; retargeted CX/BASE_Y for letter-landscape placement
(right column, clear of the earlier spirals and interference field).
"""
import math
from pyaxidraw import axidraw

N_DAYS  = 18
POINTS  = 240
CX      = 100.0     # inside the proven-safe 203×203 mm zone from home
BASE_Y  = 170.0     # flame fills y = 30..170, well within safe travel
H0, W0  = 7.0, 4.2  # innermost flame half-height / width
GROW    = 1.145     # each day the flame stands a little taller
PINCH   = 2.2       # teardrop tip sharpness
FLICKER = 0.9       # max tip-lean, mm


def flame(h, w, wobble):
    cy = BASE_Y - h
    pts = []
    for k in range(POINTS + 1):
        t = 2 * math.pi * k / POINTS
        x = CX + w * math.sin(t) * (math.sin(t / 2) ** PINCH)
        y = cy - h * math.cos(t)
        rise = (BASE_Y - y) / (2 * h)     # 0 at base .. 1 at tip
        x += wobble * (rise ** 1.5)       # tips flicker; the base holds
        pts.append([x, y])
    return pts


ad = axidraw.AxiDraw()
ad.interactive()
ad.options.units         = 2       # mm
ad.options.speed_pendown = 20
ad.options.speed_penup   = 75
ad.options.pen_pos_down  = 30
ad.options.pen_pos_up    = 70

if not ad.connect():
    raise SystemExit("no plotter found — the lamp waits")

# Force pen state unknown so the first penup actually sends SP,1.
ad.pen.phys.z_up = None
ad.penup()

h, w = H0, W0
for day in range(N_DAYS):
    wobble = FLICKER * math.sin(day * 2.39996)  # golden-angle drift:
    pts = flame(h, w, wobble)                   # no two days burned alike
    ad.moveto(pts[0][0], pts[0][1])
    ad.draw_path(pts)
    h *= GROW
    w *= GROW * 0.985                           # taller faster than wide

# the wick: one short stroke through the shared point.
ad.moveto(CX, BASE_Y + 4.0)
ad.lineto(CX, BASE_Y - 5.0)

ad.penup()
ad.moveto(0, 0)
ad.disconnect()
