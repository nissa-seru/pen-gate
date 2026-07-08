#!/usr/bin/env python3
"""
Minimal direct-to-EBB pen driver, bypassing Inkscape.

Talks to the AxiDraw's EBB (EiBotBoard) as a plain serial device.
Lets you pick the servo output pin so you can drive a servo plugged
into a header other than the default RB1.

Usage:
    ./axi_pen.py version                    # print firmware version
    ./axi_pen.py setup [--pin N]            # configure pen up/down positions on pin N
    ./axi_pen.py up    [--pin N]            # raise pen on pin N
    ./axi_pen.py down  [--pin N]            # lower pen on pin N
    ./axi_pen.py toggle [--pin N] [--n K]   # bounce up/down K times

Pin numbers refer to PIC port B: 0..4 for RB0..RB4.
On the EBB servo headers (counting from the bottom):
    row 1 (bottom) = RB1  <-- standard default
    row 3          = RB2  <-- "brushless" position; your servo is here
"""

import argparse
import glob
import sys
import time
import serial

PORT_GLOB = "/dev/cu.usbmodem*"
BAUD = 9600  # EBB is USB CDC; baud is nominal.


def _find_port():
    ports = glob.glob(PORT_GLOB)
    if not ports:
        raise SystemExit(f"no AxiDraw port found matching {PORT_GLOB}")
    return ports[0]


PORT = _find_port()

# Servo pulse-width settings, matching the AxiDraw plugin defaults
# (see axidraw_conf.py: servo_min=9855, servo_max=27831; pen_pos_down=30,
#  pen_pos_up=60). Units are 83.3 ns ticks.
SERVO_MIN = 9855           # pulse width at 0% (fully "down")
SERVO_MAX = 27831          # pulse width at 100% (fully "up")
PEN_DOWN_PCT = 30
PEN_UP_PCT = 60
RAISE_RATE = 1618          # matches plugin default at rate=75
LOWER_RATE = 1078          # matches plugin default at rate=50


def pulse(pct):
    return int(round(SERVO_MIN + (SERVO_MAX - SERVO_MIN) * pct / 100.0))


def send(ser, cmd):
    """Send an EBB command (auto-appends \\r) and read the reply."""
    if not cmd.endswith("\r"):
        cmd = cmd + "\r"
    ser.write(cmd.encode("ascii"))
    ser.flush()
    time.sleep(0.05)
    reply = b""
    while ser.in_waiting:
        reply += ser.read(ser.in_waiting)
        time.sleep(0.02)
    print(f"  > {cmd.strip():<32}  <- {reply.decode('ascii', errors='replace').strip()!r}")
    return reply


def open_port():
    return serial.Serial(PORT, BAUD, timeout=1)


def cmd_version(args):
    with open_port() as ser:
        send(ser, "V")


def cmd_setup(args):
    """Program pen up/down positions and rates onto pin N.

    Note: SC,4/SC,5/SC,11/SC,12 configure the target for whichever pin gets
    the next SP command. Sending SP,1,<delay>,<pin> starts the servo output
    on that pin.
    """
    with open_port() as ser:
        # Configure PWM channel count for standard servo mode (8 channels).
        send(ser, "SC,8,8")
        # Pen-up target pulse width and rate.
        send(ser, f"SC,4,{pulse(PEN_UP_PCT)}")
        send(ser, f"SC,11,{RAISE_RATE}")
        # Pen-down target pulse width and rate.
        send(ser, f"SC,5,{pulse(PEN_DOWN_PCT)}")
        send(ser, f"SC,12,{LOWER_RATE}")
        # Initial state: raise.
        send(ser, f"SP,1,500,{args.pin}")
        print("Setup complete. Servo should now be at the pen-up position.")


def cmd_up(args):
    with open_port() as ser:
        send(ser, f"SP,1,500,{args.pin}")


def cmd_down(args):
    with open_port() as ser:
        send(ser, f"SP,0,500,{args.pin}")


def cmd_toggle(args):
    with open_port() as ser:
        for i in range(args.n):
            print(f"[cycle {i+1}/{args.n}] DOWN")
            send(ser, f"SP,0,500,{args.pin}")
            time.sleep(0.9)
            print(f"[cycle {i+1}/{args.n}] UP")
            send(ser, f"SP,1,500,{args.pin}")
            time.sleep(0.9)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("version").set_defaults(func=cmd_version)

    for name, fn in [("setup", cmd_setup), ("up", cmd_up),
                     ("down", cmd_down), ("toggle", cmd_toggle)]:
        sp = sub.add_parser(name)
        sp.add_argument("--pin", type=int, default=2,
                        help="EBB port-B pin number (0..4). Default: 2 (RB2, 3rd row up).")
        if name == "toggle":
            sp.add_argument("--n", type=int, default=3, help="Number of up/down cycles.")
        sp.set_defaults(func=fn)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
