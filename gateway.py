"""
gateway.py — pen-gate HTTP server.

Runs as a systemd service on the RPi. Owns the USB serial port to the
AxiDraw EBB. Serves:

  - Structured API for pen up/down and raw EBB commands
  - POST /run: submit a Python script, executed inside a bwrap sandbox
    with pyaxidraw + numpy available, no network, no home access.
    Stdout streams live to /events (SSE).
  - Web UI at /

Auth: bearer token in Authorization header (or ?token= for SSE).
"""
import functools
import glob
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, render_template_string, request
import serial

# ---------- config ----------
HOME             = Path(__file__).parent
TOKEN_FILE       = HOME / "token"
LOG_DIR          = HOME / "logs"
RUN_SCRIPT       = HOME / "run_sandboxed.sh"
PORT_GLOB        = "/dev/ttyACM*"
BAUD             = 9600
LISTEN_HOST      = "0.0.0.0"
LISTEN_PORT      = 5099
SUBPROCESS_TIMEOUT = 600     # seconds — max wall-clock per /run job
STDOUT_MAX_LINES = 2000      # cap stored stdout to avoid unbounded memory

# Camera config
CAMERA_DEV       = "/dev/video0"
CAMERA_SIZE      = "1280x720"
CAMERA_TIMEOUT_S = 5
SIZE_PATTERN     = re.compile(r"^\d{2,4}x\d{2,4}$")


# Servo config (matches axi_pen.py conventions from our earlier work)
SERVO_PIN     = 2
SERVO_MIN     = 9855
SERVO_MAX     = 27831
PEN_DOWN_PCT  = 30
PEN_UP_PCT    = 60
RAISE_RATE    = 1618
LOWER_RATE    = 1078


# ---------- token ----------
def get_or_create_token() -> str:
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    tok = secrets.token_hex(32)
    TOKEN_FILE.write_text(tok)
    TOKEN_FILE.chmod(0o600)
    return tok

TOKEN = get_or_create_token()


# ---------- raw serial helpers ----------
def pulse(pct: int) -> int:
    return int(round(SERVO_MIN + (SERVO_MAX - SERVO_MIN) * pct / 100))

def find_port():
    ports = glob.glob(PORT_GLOB)
    return ports[0] if ports else None

def send_cmd(ser, cmd: str) -> str:
    if not cmd.endswith("\r"):
        cmd += "\r"
    ser.write(cmd.encode())
    ser.flush()
    time.sleep(0.03)
    return ser.read(ser.in_waiting or 1).decode("ascii", errors="replace").strip()

def configure_servo(ser):
    """Idempotent; safe to send on every command."""
    send_cmd(ser, "SC,8,8")
    send_cmd(ser, f"SC,4,{pulse(PEN_UP_PCT)}")
    send_cmd(ser, f"SC,11,{RAISE_RATE}")
    send_cmd(ser, f"SC,5,{pulse(PEN_DOWN_PCT)}")
    send_cmd(ser, f"SC,12,{LOWER_RATE}")

def do_pen(direction: int):
    port = find_port()
    if not port:
        return False, "no AxiDraw port found"
    try:
        with serial.Serial(port, BAUD, timeout=1) as ser:
            time.sleep(0.05)
            configure_servo(ser)
            send_cmd(ser, f"SP,{direction},500,{SERVO_PIN}")
        return True, port
    except Exception as e:
        return False, str(e)

def do_raw(cmd: str):
    port = find_port()
    if not port:
        return False, None, "no AxiDraw port found"
    try:
        with serial.Serial(port, BAUD, timeout=1) as ser:
            time.sleep(0.05)
            resp = send_cmd(ser, cmd)
        return True, port, resp
    except Exception as e:
        return False, port, str(e)

def check_status():
    port = find_port()
    if not port:
        return {"connected": False, "port": None, "message": "no port"}
    try:
        with serial.Serial(port, BAUD, timeout=1) as ser:
            time.sleep(0.05)
            fw = send_cmd(ser, "V")
            if not fw:
                return {"connected": False, "port": port, "message": "no reply (power off?)"}
            return {"connected": True, "port": port, "fw": fw}
    except Exception as e:
        return {"connected": False, "port": port, "message": str(e)}

def safe_raise_pen():
    """Best-effort raise the pen after a subprocess exits."""
    try:
        do_pen(1)
    except Exception:
        pass

def do_motors_off():
    """EM,0,0 — de-energize both stepper motors so they don't hold torque."""
    port = find_port()
    if not port:
        return False, "no AxiDraw port found"
    try:
        with serial.Serial(port, BAUD, timeout=1) as ser:
            time.sleep(0.05)
            send_cmd(ser, "EM,0,0")
        return True, port
    except Exception as e:
        return False, str(e)

def do_motors_on():
    """EM,1,1 — re-energize steppers at default microstepping."""
    port = find_port()
    if not port:
        return False, "no AxiDraw port found"
    try:
        with serial.Serial(port, BAUD, timeout=1) as ser:
            time.sleep(0.05)
            send_cmd(ser, "EM,1,1")
        return True, port
    except Exception as e:
        return False, str(e)

def safe_park_after_job():
    """Called after every /run subprocess exits. Raise pen + kill stepper power."""
    safe_raise_pen()
    try:
        do_motors_off()
    except Exception:
        pass


# ---------- concurrency + job state ----------
port_lock       = threading.Lock()
job_state_lock  = threading.Lock()
camera_lock     = threading.Lock()   # v4l2 lets one process open the device at a time

# job_state schema:
#   active     bool         — a job is currently running
#   id         str | None   — short hex id for the current job
#   started    iso str
#   stdout     list[str]    — accumulated lines
#   exit_code  int | None
#   ended      iso str | None
#   cancelled  bool
#   proc       Popen | None — process handle (never serialized)
job_state = {
    "active": False, "id": None, "started": None,
    "stdout": [], "exit_code": None, "ended": None,
    "cancelled": False, "proc": None,
}

def job_snapshot():
    with job_state_lock:
        return {
            "active":    job_state["active"],
            "id":        job_state["id"],
            "started":   job_state["started"],
            "stdout":    list(job_state["stdout"][-200:]),  # last 200 lines
            "exit_code": job_state["exit_code"],
            "ended":     job_state["ended"],
            "cancelled": job_state["cancelled"],
        }


# ---------- audit log ----------
def audit(event: dict):
    LOG_DIR.mkdir(exist_ok=True)
    event = {"ts": datetime.now(timezone.utc).isoformat(), **event}
    with (LOG_DIR / "gateway.log").open("a") as f:
        f.write(json.dumps(event) + "\n")


# ---------- auth ----------
def _provided_token():
    h = request.headers.get("Authorization", "")
    if h.startswith("Bearer "):
        return h[7:]
    return request.args.get("token")

def require_token(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        p = _provided_token()
        if not p or not secrets.compare_digest(p, TOKEN):
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


# ---------- Flask app ----------
app = Flask(__name__)

@app.route("/status")
@require_token
def route_status():
    # If a plot is active it owns the serial port. Probing the EBB now
    # would open a SECOND handle to /dev/ttyACM0 (check_status sends "V"),
    # interleaving bytes into the plot's command stream and corrupting it
    # — pyaxidraw then reports "USB connection to AxiDraw lost" and the
    # job dies mid-plot. Same USB-contention hazard the camera guards
    # against. Report status from job state without touching the port.
    with job_state_lock:
        job_active = job_state["active"]
    if job_active:
        st = {"connected": True, "port": find_port(), "fw": "plotting…"}
    else:
        st = check_status()
    st["job"] = job_snapshot()
    return jsonify(st)

@app.route("/pen/up", methods=["POST"])
@require_token
def route_pen_up():
    if not port_lock.acquire(blocking=False):
        return jsonify({"error": "busy", "job": job_state["id"]}), 409
    try:
        ok, info = do_pen(1)
        audit({"event": "pen_up", "ok": ok})
        return jsonify({"ok": ok, "info": info})
    finally:
        port_lock.release()

@app.route("/pen/down", methods=["POST"])
@require_token
def route_pen_down():
    if not port_lock.acquire(blocking=False):
        return jsonify({"error": "busy", "job": job_state["id"]}), 409
    try:
        ok, info = do_pen(0)
        audit({"event": "pen_down", "ok": ok})
        return jsonify({"ok": ok, "info": info})
    finally:
        port_lock.release()

@app.route("/motors/off", methods=["POST"])
@require_token
def route_motors_off():
    if not port_lock.acquire(blocking=False):
        return jsonify({"error": "busy", "job": job_state["id"]}), 409
    try:
        ok, info = do_motors_off()
        audit({"event": "motors_off", "ok": ok})
        return jsonify({"ok": ok, "info": info})
    finally:
        port_lock.release()

@app.route("/motors/on", methods=["POST"])
@require_token
def route_motors_on():
    if not port_lock.acquire(blocking=False):
        return jsonify({"error": "busy", "job": job_state["id"]}), 409
    try:
        ok, info = do_motors_on()
        audit({"event": "motors_on", "ok": ok})
        return jsonify({"ok": ok, "info": info})
    finally:
        port_lock.release()

@app.route("/command/raw", methods=["POST"])
@require_token
def route_command_raw():
    data = request.get_json(silent=True) or {}
    cmd = data.get("cmd")
    if not cmd:
        return jsonify({"error": "cmd (string) required"}), 400
    if not port_lock.acquire(blocking=False):
        return jsonify({"error": "busy", "job": job_state["id"]}), 409
    try:
        ok, port, resp = do_raw(cmd)
        audit({"event": "raw", "cmd": cmd, "ok": ok, "response": resp})
        return jsonify({"ok": ok, "port": port, "response": resp})
    finally:
        port_lock.release()


# ---------- camera ----------
def capture_snapshot(size: str = CAMERA_SIZE):
    """Grab one JPEG frame from the webcam via ffmpeg. Returns (bytes | None, error_str | None)."""
    if not SIZE_PATTERN.match(size):
        return None, f"bad size {size!r}"
    if not os.path.exists(CAMERA_DEV):
        return None, f"no camera device at {CAMERA_DEV}"
    with camera_lock:
        try:
            r = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error",
                 "-y",
                 "-f", "v4l2",
                 "-input_format", "mjpeg",
                 "-video_size", size,
                 "-i", CAMERA_DEV,
                 # Skip early frames so auto-exposure has time to settle.
                 # At 30fps this is ~15 frames of preroll, ~500ms of walltime.
                 "-ss", "0.5",
                 "-frames:v", "1",
                 "-f", "mjpeg",
                 "pipe:1"],
                capture_output=True, timeout=CAMERA_TIMEOUT_S,
            )
            if r.returncode != 0:
                return None, r.stderr.decode(errors="replace")[:200]
            return r.stdout, None
        except subprocess.TimeoutExpired:
            return None, f"ffmpeg timeout after {CAMERA_TIMEOUT_S}s"
        except Exception as e:
            return None, str(e)

@app.route("/camera/status")
@require_token
def route_camera_status():
    return jsonify({
        "device":       CAMERA_DEV,
        "exists":       os.path.exists(CAMERA_DEV),
        "default_size": CAMERA_SIZE,
    })

@app.route("/camera/snapshot.jpg")
def route_snapshot():
    p = _provided_token()
    if not p or not secrets.compare_digest(p, TOKEN):
        return Response("", status=401)
    # Serial and camera share a USB bus on the RPi. Doing both at once
    # destabilizes the bus and drops the EBB mid-plot ("USB connection
    # to AxiDraw lost"). Refuse camera captures while a plot is active.
    with job_state_lock:
        job_active = job_state["active"]
    if job_active:
        return Response("plot in progress — camera paused", status=503,
                        mimetype="text/plain",
                        headers={"Retry-After": "5"})
    size = request.args.get("size", CAMERA_SIZE)
    jpg, err = capture_snapshot(size)
    if jpg is None:
        return Response(f"camera error: {err}", status=500, mimetype="text/plain")
    return Response(jpg, mimetype="image/jpeg", headers={
        "Cache-Control": "no-store",
    })


# ---------- /run: sandboxed script execution ----------
def _reset_job(job_id: str):
    with job_state_lock:
        job_state.update({
            "active":    True,
            "id":        job_id,
            "started":   datetime.now(timezone.utc).isoformat(),
            "stdout":    [],
            "exit_code": None,
            "ended":     None,
            "cancelled": False,
            "proc":      None,
        })

def _append_stdout(line: str):
    with job_state_lock:
        job_state["stdout"].append(line)
        if len(job_state["stdout"]) > STDOUT_MAX_LINES:
            job_state["stdout"] = job_state["stdout"][-STDOUT_MAX_LINES:]

def run_worker(script: str, job_id: str):
    """Runs in a thread. Owns port_lock for the entire subprocess lifetime."""
    proc = subprocess.Popen(
        [str(RUN_SCRIPT)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    with job_state_lock:
        job_state["proc"] = proc
    try:
        proc.stdin.write(script)
        proc.stdin.close()
    except BrokenPipeError:
        pass

    # Read stdout live so /events can stream it
    start = time.monotonic()
    for line in iter(proc.stdout.readline, ""):
        _append_stdout(line.rstrip("\n"))
        if time.monotonic() - start > SUBPROCESS_TIMEOUT:
            _append_stdout(f"[gateway] TIMEOUT after {SUBPROCESS_TIMEOUT}s, killing")
            proc.kill()
            break

    proc.wait()
    with job_state_lock:
        job_state["exit_code"] = proc.returncode
        job_state["ended"]     = datetime.now(timezone.utc).isoformat()
        job_state["active"]    = False
        job_state["proc"]      = None
    audit({"event": "run_end", "job_id": job_id, "exit_code": proc.returncode,
           "cancelled": job_state["cancelled"]})

    # Safe-exit hook: raise the pen, then de-energize the steppers so
    # the motors don't hold torque, waste power, or heat up while idle.
    safe_park_after_job()
    port_lock.release()

@app.route("/run", methods=["POST"])
@require_token
def route_run():
    data = request.get_json(silent=True) or {}
    script = data.get("script")
    if not isinstance(script, str) or not script.strip():
        return jsonify({"error": "script (string) required"}), 400
    if not port_lock.acquire(blocking=False):
        return jsonify({"error": "busy", "job": job_state["id"]}), 409
    job_id = uuid.uuid4().hex[:8]
    _reset_job(job_id)
    audit({"event": "run_start", "job_id": job_id, "script_size": len(script)})
    threading.Thread(target=run_worker, args=(script, job_id), daemon=True).start()
    return jsonify({"job_id": job_id}), 202

@app.route("/run/cancel", methods=["POST"])
@require_token
def route_cancel():
    with job_state_lock:
        proc = job_state["proc"]
        if not proc or not job_state["active"]:
            return jsonify({"error": "no active job"}), 400
        job_state["cancelled"] = True
    proc.terminate()
    audit({"event": "cancel", "job_id": job_state["id"]})
    return jsonify({"ok": True})


# ---------- SSE: live stdout stream ----------
@app.route("/events")
def route_events():
    p = _provided_token()
    if not p or not secrets.compare_digest(p, TOKEN):
        return Response("", status=401)

    def generate():
        # Per-client view state. Each SSE connection tracks what it has emitted.
        seen_job_id = None
        stdout_idx  = 0
        end_sent    = False
        while True:
            with job_state_lock:
                job_id    = job_state["id"]
                stdout    = list(job_state["stdout"])
                active    = job_state["active"]
                exit_code = job_state["exit_code"]

            if job_id != seen_job_id:
                seen_job_id = job_id
                stdout_idx  = 0
                end_sent    = False
                if job_id is not None:
                    yield f"event: job_start\ndata: {json.dumps({'job_id': job_id})}\n\n"

            new_lines = stdout[stdout_idx:]
            for line in new_lines:
                yield f"event: stdout\ndata: {json.dumps({'job_id': job_id, 'line': line})}\n\n"
            stdout_idx += len(new_lines)

            if (job_id is not None and not active
                    and exit_code is not None and not end_sent):
                yield (f"event: job_end\n"
                       f"data: {json.dumps({'job_id': job_id, 'exit_code': exit_code})}\n\n")
                end_sent = True

            yield ": ping\n\n"
            time.sleep(0.4)

    return Response(generate(), mimetype="text/event-stream")


# ---------- Web UI ----------
INDEX_HTML = r"""<!DOCTYPE html>
<html>
<head>
<title>pen-gate</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body { background:#111; color:#eee; font-family:-apple-system,sans-serif;
         margin:0; padding:20px; min-height:100vh; box-sizing:border-box; }
  h1 { font-weight:300; margin:0 0 4px; font-size:22px; letter-spacing:3px; }
  #status { font-size:11px; color:#888; margin-bottom:20px; font-family:'SF Mono',Menlo,monospace; }
  #status.ok  { color:#7c7; }
  #status.err { color:#c77; }
  #login input { background:#222; color:#eee; border:1px solid #555; padding:12px;
                 font-family:Menlo,monospace; width:100%; box-sizing:border-box; margin-bottom:8px; }
  .row { display:flex; gap:12px; margin-bottom:20px; }
  button { flex:1; font-size:20px; background:#1e1e1e; color:#eee;
           border:1px solid #444; border-radius:10px; cursor:pointer;
           padding:32px 20px; letter-spacing:3px;
           transition:background .1s, transform .05s; }
  button:hover { background:#262626; }
  button:active { transform:scale(0.98); background:#333; }
  button.up { border-color:#375; }
  button.down { border-color:#753; }
  button.motor { border-color:#457; padding:22px 20px; font-size:16px; }
  button.cancel { border-color:#a55; }
  button:disabled { opacity:.35; cursor:not-allowed; }
  #job { font-family:Menlo,monospace; font-size:11px; color:#aaa;
         background:#181818; border:1px solid #333; border-radius:8px;
         padding:10px; margin-top:12px; max-height:280px; overflow-y:auto;
         line-height:1.55; white-space:pre-wrap; }
  #job.idle { color:#666; }
  .job-header { color:#7c7; margin-bottom:4px; }
  #cam-wrap { margin-top:12px; background:#181818; border:1px solid #333;
              border-radius:8px; padding:6px; }
  #cam-wrap img { width:100%; display:block; border-radius:4px; }
  #cam-controls { display:flex; align-items:center; gap:10px;
                  font-family:Menlo,monospace; font-size:10px; color:#888;
                  padding:6px 4px 2px; }
  #cam-controls label { cursor:pointer; }
</style>
</head>
<body>
  <h1>pen-gate</h1>
  <div id="status">checking…</div>

  <div id="login" style="display:none;">
    <input id="token-input" type="password" placeholder="paste bearer token">
    <button id="login-btn" style="padding:12px;font-size:14px;letter-spacing:2px;">unlock</button>
  </div>

  <div id="app" style="display:none;">
    <div class="row">
      <button class="up"   id="btn-up">up</button>
      <button class="down" id="btn-down">down</button>
    </div>
    <div class="row">
      <button class="motor" id="btn-motors-on">motors on</button>
      <button class="motor" id="btn-motors-off">motors off</button>
    </div>
    <div class="row">
      <button class="cancel" id="btn-cancel" disabled>cancel job</button>
    </div>
    <div id="job" class="idle">no job running</div>

    <div id="cam-wrap">
      <div id="cam-controls">
        <label><input type="checkbox" id="cam-auto" checked> auto-refresh every</label>
        <select id="cam-interval">
          <option value="1500">1.5s</option>
          <option value="3000" selected>3s</option>
          <option value="10000">10s</option>
          <option value="0">off</option>
        </select>
        <button style="padding:2px 8px;font-size:10px;letter-spacing:1px;" id="cam-refresh">now</button>
        <span id="cam-meta"></span>
      </div>
      <img id="cam" alt="camera not loaded yet">
    </div>
  </div>

<script>
const $ = id => document.getElementById(id);
const TOKEN_KEY = "pen-gate-token";
let token = localStorage.getItem(TOKEN_KEY);

function show(auth) {
  $("login").style.display = auth ? "none"  : "block";
  $("app").style.display   = auth ? "block" : "none";
  if (!auth) $("status").textContent = "enter token to unlock";
}

async function api(path, opts={}) {
  return fetch(path, {
    ...opts,
    headers: {
      "Authorization": "Bearer " + token,
      "Content-Type": "application/json",
      ...(opts.headers || {}),
    },
  });
}

async function checkStatus() {
  try {
    const r = await api("/status");
    if (r.status === 401) { token = null; localStorage.removeItem(TOKEN_KEY); show(false); return; }
    const j = await r.json();
    if (j.connected) {
      $("status").textContent = `${j.port} · ${j.fw}`;
      $("status").className = "ok";
    } else {
      $("status").textContent = j.message || "disconnected";
      $("status").className = "err";
    }
    $("btn-cancel").disabled = !j.job || !j.job.active;
    const wasActive = jobActive;
    jobActive = !!(j.job && j.job.active);
    if (wasActive !== jobActive) rescheduleCam();
  } catch (e) {
    $("status").textContent = "server error";
    $("status").className = "err";
  }
}

async function pen(dir) {
  const r = await api("/pen/" + dir, { method: "POST" });
  const j = await r.json();
  if (!j.ok && j.error) alert(dir + " failed: " + (j.error || j.info));
  checkStatus();
}

async function cancel() {
  await api("/run/cancel", { method: "POST" });
}

let sseSource = null;
function startSSE() {
  if (sseSource) sseSource.close();
  sseSource = new EventSource("/events?token=" + encodeURIComponent(token));
  const job = $("job");
  sseSource.addEventListener("job_start", (e) => {
    const d = JSON.parse(e.data);
    if (!d.job_id) return;
    job.classList.remove("idle");
    job.innerHTML = `<div class="job-header">▶ job ${d.job_id}</div>`;
    checkStatus();
  });
  sseSource.addEventListener("stdout", (e) => {
    const d = JSON.parse(e.data);
    const line = document.createTextNode(d.line + "\n");
    job.appendChild(line);
    job.scrollTop = job.scrollHeight;
  });
  sseSource.addEventListener("job_end", (e) => {
    const d = JSON.parse(e.data);
    const footer = document.createElement("div");
    footer.style.color = d.exit_code === 0 ? "#7c7" : "#c77";
    footer.textContent = `■ job ${d.job_id} exit ${d.exit_code}`;
    job.appendChild(footer);
    job.scrollTop = job.scrollHeight;
    checkStatus();
  });
}

$("login-btn").onclick = () => {
  token = $("token-input").value.trim();
  if (!token) return;
  localStorage.setItem(TOKEN_KEY, token);
  show(true);
  checkStatus();
  startSSE();
};
async function motors(state) {
  const r = await api("/motors/" + state, { method: "POST" });
  const j = await r.json();
  if (!j.ok && j.error) alert("motors " + state + " failed: " + (j.error || j.info));
}

$("btn-up").onclick = () => pen("up");
$("btn-down").onclick = () => pen("down");
$("btn-motors-on").onclick  = () => motors("on");
$("btn-motors-off").onclick = () => motors("off");
$("btn-cancel").onclick = cancel;

// --- camera ---
let camTimer = null;
function refreshCam() {
  const url = "/camera/snapshot.jpg?token=" + encodeURIComponent(token) + "&t=" + Date.now();
  const meta = $("cam-meta");
  const t0 = performance.now();
  const img = $("cam");
  const probe = new Image();
  probe.onload = () => {
    img.src = url;
    meta.textContent = `${Math.round(performance.now() - t0)}ms`;
  };
  probe.onerror = () => { meta.textContent = "err"; };
  probe.src = url;
}
let jobActive = false;
function rescheduleCam() {
  if (camTimer) { clearInterval(camTimer); camTimer = null; }
  if (!$("cam-auto").checked) return;
  if (jobActive) return;   // paused during plots — USB bus is shared with EBB
  const ms = parseInt($("cam-interval").value, 10);
  if (ms > 0) camTimer = setInterval(refreshCam, ms);
}
$("cam-refresh").onclick = refreshCam;
$("cam-auto").onchange   = rescheduleCam;
$("cam-interval").onchange = rescheduleCam;

if (token) { show(true); checkStatus(); startSSE(); setInterval(checkStatus, 3000);
              refreshCam(); rescheduleCam(); }
else       { show(false); }
</script>
</body>
</html>
"""

@app.route("/")
def route_index():
    return render_template_string(INDEX_HTML)


# ---------- main ----------
if __name__ == "__main__":
    print(f"pen-gate starting on {LISTEN_HOST}:{LISTEN_PORT}", file=sys.stderr)
    print(f"token: {TOKEN}", file=sys.stderr)
    print(f"       (also written to {TOKEN_FILE})", file=sys.stderr)
    app.run(host=LISTEN_HOST, port=LISTEN_PORT, debug=False, threaded=True)
