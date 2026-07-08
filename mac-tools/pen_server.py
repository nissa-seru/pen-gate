#!/usr/bin/env python3
"""
pen_server.py — tiny web UI for AxiDraw pen up/down.

Design goal: survive USB unplug, power loss, and EBB reboot without
any reconnect logic. Every request opens a fresh serial connection,
re-sends the servo config (idempotent — the EBB just accepts them),
sends the SP command, and closes. There is no persistent state to
lose, so there is nothing to "reconnect."

Run:
    python3 pen_server.py

Then visit http://localhost:5099
"""
import glob
import time
from flask import Flask, jsonify, render_template_string
import serial

PORT_GLOB = "/dev/cu.usbmodem*"
BAUD      = 9600

# Servo configuration — matches axi_pen.py so the two tools agree.
SERVO_PIN     = 2
SERVO_MIN     = 9855
SERVO_MAX     = 27831
PEN_DOWN_PCT  = 30
PEN_UP_PCT    = 60
RAISE_RATE    = 1618
LOWER_RATE    = 1078


def pulse(pct):
    return int(round(SERVO_MIN + (SERVO_MAX - SERVO_MIN) * pct / 100))


def find_port():
    ports = glob.glob(PORT_GLOB)
    return ports[0] if ports else None


def send_cmd(ser, cmd):
    if not cmd.endswith("\r"):
        cmd += "\r"
    ser.write(cmd.encode())
    ser.flush()
    time.sleep(0.03)
    return ser.read(ser.in_waiting or 1)


def do_pen(direction: int):
    """direction: 1 = up, 0 = down."""
    port = find_port()
    if not port:
        return {"ok": False, "error": "no AxiDraw found (USB unplugged?)"}
    try:
        with serial.Serial(port, BAUD, timeout=1) as ser:
            time.sleep(0.05)  # USB stabilization
            # Always re-send config. Idempotent; also recovers the EBB
            # from a power cycle without any reconnect logic.
            send_cmd(ser, "SC,8,8")
            send_cmd(ser, f"SC,4,{pulse(PEN_UP_PCT)}")
            send_cmd(ser, f"SC,11,{RAISE_RATE}")
            send_cmd(ser, f"SC,5,{pulse(PEN_DOWN_PCT)}")
            send_cmd(ser, f"SC,12,{LOWER_RATE}")
            send_cmd(ser, f"SP,{direction},500,{SERVO_PIN}")
        return {"ok": True, "port": port}
    except Exception as e:
        return {"ok": False, "error": str(e), "port": port}


def check_status():
    """Fresh query the EBB firmware version. Confirms both port and power."""
    port = find_port()
    if not port:
        return {"connected": False, "port": None, "message": "no port"}
    try:
        with serial.Serial(port, BAUD, timeout=1) as ser:
            time.sleep(0.05)
            resp = send_cmd(ser, "V")
            fw = resp.decode("ascii", errors="replace").strip()
            if not fw:
                return {"connected": False, "port": port,
                        "message": "port open, no reply (power off?)"}
            return {"connected": True, "port": port, "fw": fw}
    except Exception as e:
        return {"connected": False, "port": port, "message": str(e)}


app = Flask(__name__)

INDEX_HTML = """<!DOCTYPE html>
<html>
<head>
<title>pen</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {
    background: #111; color: #eee;
    font-family: -apple-system, BlinkMacSystemFont, sans-serif;
    margin: 0; padding: 20px; display: flex; flex-direction: column;
    min-height: 100vh; box-sizing: border-box;
  }
  h1 { font-weight: 300; margin: 0 0 4px 0; font-size: 22px; letter-spacing: 2px; }
  #status {
    font-size: 11px; color: #888; margin-bottom: 24px;
    font-family: 'SF Mono', Menlo, monospace;
  }
  #status.ok { color: #7c7; }
  #status.err { color: #c77; }
  .row { display: flex; gap: 12px; flex: 1; }
  button {
    flex: 1; font-size: 22px; background: #1e1e1e; color: #eee;
    border: 1px solid #444; border-radius: 10px; cursor: pointer;
    padding: 40px 20px; touch-action: manipulation;
    transition: transform 0.05s, background 0.1s;
    letter-spacing: 4px;
  }
  button:hover { background: #262626; }
  button:active { transform: scale(0.98); background: #333; }
  button.up   { border-color: #375; }
  button.down { border-color: #753; }
  #log {
    font-family: 'SF Mono', Menlo, monospace; font-size: 10px;
    color: #666; margin-top: 20px; max-height: 120px; overflow-y: auto;
    line-height: 1.5;
  }
</style>
</head>
<body>
  <h1>pen</h1>
  <div id="status">checking…</div>
  <div class="row">
    <button class="up"   id="btn-up">up</button>
    <button class="down" id="btn-down">down</button>
  </div>
  <div id="log"></div>
<script>
const status = document.getElementById('status');
const log    = document.getElementById('log');

function logMsg(m) {
  const t = new Date().toLocaleTimeString();
  log.innerHTML = `[${t}] ${m}<br>` + log.innerHTML;
}

async function checkStatus() {
  try {
    const r = await fetch('/status');
    const j = await r.json();
    if (j.connected) {
      status.textContent = `${j.port} · ${j.fw}`;
      status.className   = 'ok';
    } else {
      status.textContent = j.message || 'disconnected';
      status.className   = 'err';
    }
  } catch (e) {
    status.textContent = 'server error';
    status.className   = 'err';
  }
}

async function pen(dir) {
  try {
    const r = await fetch('/' + dir, {method: 'POST'});
    const j = await r.json();
    logMsg(dir + (j.ok ? ' ok' : ' FAILED: ' + j.error));
  } catch (e) {
    logMsg(dir + ' request failed: ' + e);
  }
  checkStatus();
}

document.getElementById('btn-up').onclick   = () => pen('up');
document.getElementById('btn-down').onclick = () => pen('down');

checkStatus();
setInterval(checkStatus, 3000);
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(INDEX_HTML)


@app.route("/status")
def status_route():
    return jsonify(check_status())


@app.route("/up", methods=["POST"])
def up_route():
    return jsonify(do_pen(1))


@app.route("/down", methods=["POST"])
def down_route():
    return jsonify(do_pen(0))


if __name__ == "__main__":
    print("pen server → http://localhost:5099")
    app.run(host="0.0.0.0", port=5099, debug=False)
