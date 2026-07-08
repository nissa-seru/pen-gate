"""
First mark from the RPi — a slow logarithmic spiral.
Placed at (4, 3) inches, comfortably inside the proven-safe area.
"""
import math
from pyaxidraw import axidraw

CENTER_X, CENTER_Y = 4.0, 3.0   # inches
TURNS       = 8
INNER_R     = 0.10
OUTER_R     = 1.0
POINTS_PER_TURN = 72

ad = axidraw.AxiDraw()
ad.interactive()
ad.options.speed_pendown = 25
ad.options.speed_penup   = 75
ad.options.accel         = 85
ad.options.pen_pos_up    = 60
ad.options.pen_pos_down  = 30

if not ad.connect():
    raise SystemExit("no plotter found")

ad.pen.phys.z_up = None       # force explicit servo command
ad.penup()

# Generate the log spiral
theta_max = TURNS * 2 * math.pi
k = math.log(OUTER_R / INNER_R) / theta_max
total = TURNS * POINTS_PER_TURN
path = []
for i in range(total + 1):
    theta = (i / total) * theta_max
    r     = INNER_R * math.exp(k * theta)
    path.append([CENTER_X + r * math.cos(theta),
                 CENTER_Y + r * math.sin(theta)])

print(f"spiral: {total} pts, r {INNER_R}..{OUTER_R} in, center ({CENTER_X}, {CENTER_Y})", flush=True)
ad.moveto(path[0][0], path[0][1])
print("draw start", flush=True)
ad.draw_path(path)
print("draw done", flush=True)
ad.penup()
ad.moveto(0, 0)
ad.disconnect()
print("home + disconnected", flush=True)
