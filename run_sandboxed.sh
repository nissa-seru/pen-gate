#!/bin/bash
# run_sandboxed.sh — execute a Python script inside a bwrap sandbox.
#
# The script is read from stdin. The sandbox:
#   - no network (unshare-net)
#   - no access to $HOME (tmpfs over /home)
#   - readonly / (only /tmp writable)
#   - has access to the pyaxidraw venv + the persistent script library
#   - has access to the AxiDraw serial device (AXI_PORT, defaults to /dev/ttyACM0)
#   - killed if parent dies
#
# Env vars:
#   AXI_PORT   — path to the EBB serial device. Default /dev/ttyACM0
#   PEN_GATE_HOME — root of the pen-gate install. Default ~/pen-gate
#
# Usage:
#   echo 'from pyaxidraw import axidraw; print("ok")' | run_sandboxed.sh

set -e

: "${PEN_GATE_HOME:=$HOME/pen-gate}"
: "${AXI_PORT:=/dev/ttyACM0}"

# Only bind the serial device if it actually exists — otherwise the
# script can still run (useful for offline canary tests).
serial_binds=()
if [[ -e "$AXI_PORT" ]]; then
    serial_binds=(--dev-bind "$AXI_PORT" "$AXI_PORT")
fi

exec bwrap \
    --unshare-net --unshare-pid --unshare-uts --unshare-cgroup --unshare-ipc \
    --die-with-parent \
    --new-session \
    --ro-bind /usr /usr \
    --symlink /usr/bin /bin \
    --symlink /usr/lib /lib \
    --symlink /usr/sbin /sbin \
    --ro-bind /etc/alternatives /etc/alternatives \
    --ro-bind /etc/ld.so.cache /etc/ld.so.cache \
    --ro-bind "$PEN_GATE_HOME/venv" /venv \
    --ro-bind "$PEN_GATE_HOME/scripts" /scripts \
    --ro-bind "$PEN_GATE_HOME/lib" /lib_user \
    --dev /dev \
    "${serial_binds[@]}" \
    --ro-bind /sys /sys \
    --proc /proc \
    --tmpfs /tmp \
    --tmpfs /run \
    --tmpfs /home \
    --setenv HOME /tmp \
    --setenv PATH /venv/bin:/usr/bin:/bin \
    --setenv PYTHONPATH "/venv/lib/python3.13/site-packages:/lib_user:/scripts" \
    --setenv PYTHONUNBUFFERED 1 \
    /venv/bin/python3 -
