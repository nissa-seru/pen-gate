"""
second_mark.py — a small inward spiral, alongside the flame.

The flame grows outward, day by day. This one turns inward, one
continuous line that starts at an outer radius and winds down
to a point at its own center. Placed in the empty right of the
sheet — companion mark, not a rival.

Small on purpose.
"""
import math
from pyaxidraw import axidraw

# Placement: right of the flame, at the height of its vertical midpoint
CENTER_X = 165.0
CENTER_Y = 100.0

TURNS       = 6
OUTER_R     = 10.0
POINTS_PER_TURN = 72

ad = axidraw.AxiDraw()
ad.interactive()
ad.options.units         = 2       # mm
ad.options.speed_pendown = 18      # unhurried
ad.options.speed_penup   = 75
ad.options.pen_pos_up    = 60
ad.options.pen_pos_down  = 30

if not ad.connect():
    raise SystemExit("no plotter found")

ad.pen.phys.z_up = None
ad.penup()

# Archimedean spiral, going inward (radius shrinks linearly to 0)
total_pts = TURNS * POINTS_PER_TURN
path = []
for i in range(total_pts + 1):
    t     = i / total_pts        # 0..1
    theta = t * TURNS * 2 * math.pi
    r     = OUTER_R * (1 - t)
    x     = CENTER_X + r * math.cos(theta)
    y     = CENTER_Y + r * math.sin(theta)
    path.append([x, y])

ad.moveto(path[0][0], path[0][1])
ad.draw_path(path)
ad.penup()
ad.moveto(0, 0)
ad.disconnect()
