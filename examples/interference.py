"""
interference.py — two circle-fields, overlaid.

Two sets of 18 concentric circles, centers 2" apart. Where the sets
cross, moiré rings emerge from the phase relationship of the two grids.

Stress test for:
  - servo cycle count (36 pen up/down transitions)
  - step registration (if the machine drifts, the offset drifts too)
  - accel/decel at small radii
  - moderate-to-fast plotting speeds
"""
import math
from pyaxidraw import axidraw

# Two centers, both on the fresh part of the letter (below the spirals)
CX_A, CY_A = 4.5, 6.5
CX_B, CY_B = 6.5, 6.5   # 2" offset in X

# Circle family
N_CIRCLES = 18
R_MIN     = 0.15
R_MAX     = 1.5
POINTS_PER_CIRCLE = 96   # 3.75° per segment; smooth at these sizes

def circle_path(cx, cy, r, n=POINTS_PER_CIRCLE):
    """Return a closed polygon approximating a circle."""
    pts = []
    for i in range(n + 1):  # +1 to close the loop
        theta = 2 * math.pi * i / n
        pts.append([cx + r * math.cos(theta), cy + r * math.sin(theta)])
    return pts

ad = axidraw.AxiDraw()
ad.interactive()

# Faster than the spiral — see if the machine holds together at these speeds.
ad.options.speed_pendown = 40
ad.options.speed_penup   = 100
ad.options.accel         = 90
ad.options.pen_pos_up    = 60
ad.options.pen_pos_down  = 30

ad.connect()
ad.pen.phys.z_up = None    # force explicit pen state on first command
ad.penup()

# Interleave the two sets so the machine doesn't just do all of A then all of
# B — interleaving means if drift happens partway through, it's visible
# throughout the piece rather than as a sudden step between halves.
radii = [R_MIN + (R_MAX - R_MIN) * i / (N_CIRCLES - 1) for i in range(N_CIRCLES)]

for r in radii:
    for cx, cy in [(CX_A, CY_A), (CX_B, CY_B)]:
        path = circle_path(cx, cy, r)
        ad.moveto(path[0][0], path[0][1])   # pen-up to start
        ad.draw_path(path)                  # pen-down, continuous stroke, lifts at end

ad.penup()
ad.moveto(0, 0)
ad.disconnect()
