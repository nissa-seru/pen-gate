"""
first_mark.py — a small honest hello.

A single logarithmic spiral, drawn as one continuous line.
Inner radius ~0.1", outer radius ~1", 8 turns, each ~1.45× larger
than the last.

Uses draw_path() so the whole spiral is one continuous velocity
profile with corner blending — much smoother than firing many
individual lineto() calls.
"""
import math
from pyaxidraw import axidraw

# Placement on the letter paper (letter is landscape, home at top-left corner)
CENTER_X = 6.0    # inches from the machine's origin (offset from v1 at x=3)
CENTER_Y = 3.0

# Spiral parameters
TURNS       = 8
INNER_R     = 0.10       # a hair larger than v1 so inner segments aren't tiny
OUTER_R     = 1.0
POINTS_PER_TURN = 72     # 5° per segment; smooth at this scale

ad = axidraw.AxiDraw()
ad.interactive()

# Speeds: 25 pen-down (default), 75 pen-up. Moderate speed is smoother
# than slow speed — the planner can blend segments instead of chase
# setpoints. Accel bumped a touch to keep motion snappy at corners.
ad.options.speed_pendown = 25
ad.options.speed_penup   = 75
ad.options.accel         = 85
ad.options.pen_pos_up    = 60
ad.options.pen_pos_down  = 30

ad.connect()

# Force pen state to "unknown" so the very first penup() actually sends
# an SP command. Otherwise pyaxidraw trusts the EBB's remembered state
# from the last session (which may not match physical reality if the
# pen has been manually moved) and short-circuits pen commands as no-ops.
ad.pen.phys.z_up = None
ad.penup()

# Generate the spiral
theta_max = TURNS * 2 * math.pi
k = math.log(OUTER_R / INNER_R) / theta_max   # growth rate

total_pts = TURNS * POINTS_PER_TURN
path = []
for i in range(total_pts + 1):
    theta = (i / total_pts) * theta_max
    r     = INNER_R * math.exp(k * theta)
    x     = CENTER_X + r * math.cos(theta)
    y     = CENTER_Y + r * math.sin(theta)
    path.append([x, y])

# Move pen-up to the start; draw_path handles the pen-down stroke as
# a single continuous velocity profile and lifts the pen at the end.
ad.moveto(path[0][0], path[0][1])
ad.draw_path(path)
ad.penup()

# Return home
ad.moveto(0, 0)
ad.disconnect()
