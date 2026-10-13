# pen-gate

An HTTP gateway for an [AxiDraw](https://axidraw.com/) pen plotter, run on a Raspberry Pi with the plotter plugged in over USB. You send it a Python script that draws with the [AxiDraw Python API](https://axidraw.com/doc/py_api/) (`pyaxidraw`). It runs the script in a [bubblewrap](https://github.com/containers/bubblewrap) sandbox with no network access and streams the script's output back as server-sent events. It also raises and lowers the pen, switches the stepper motors on and off, sends raw commands to the plotter's EiBotBoard (EBB), and takes webcam snapshots. Its page at `/` has buttons for the pen, the motors and cancelling a job, and shows the job's output and the camera.

## What's here

| Path | What it is |
|---|---|
| `gateway.py` | The gateway, a Flask app. It drives the plotter over USB serial, and jobs and the pen, motor and raw-command routes share a lock that gives the port to one of them at a time. |
| `run_sandboxed.sh` | Runs a Python script, read from stdin, under `bwrap` |
| `pen-gate.service` | A systemd unit that runs the gateway as user `rpi3` from `/home/rpi3/pen-gate` |
| `examples/` | Plot scripts to submit to `/run` |
| `mac-tools/` | Tools from before the gateway that drive the pen-lift servo over USB serial from a Mac: `axi_pen.py`, a command line, and `pen_server.py`, a page with up and down buttons |
| `hardware/` | A pen-lift servo horn for an MG90S or SG90 servo, as OpenSCAD source (`servo_horn.scad`) and an STL |

## Installing

On the Pi, the gateway needs Python 3 with Flask and pyserial, `bwrap` to run jobs, and `ffmpeg` and a webcam at `/dev/video0` that delivers MJPEG for snapshots. Jobs run with the gateway's own virtual environment, so install pyaxidraw there too, with anything else your scripts import. `run_sandboxed.sh` expects the repository at `~/pen-gate`, and `pen-gate.service` at `/home/rpi3/pen-gate`:

```sh
sudo apt install git bubblewrap ffmpeg python3-venv
git clone https://github.com/anima-research/pen-gate.git ~/pen-gate
cd ~/pen-gate
python3 -m venv venv
venv/bin/pip install flask pyserial https://cdn.evilmadscientist.com/dl/ad/public/AxiDraw_API.zip
chmod +x run_sandboxed.sh
mkdir scripts lib
```

The gateway runs `run_sandboxed.sh` directly, and the repository stores it without its executable bit, hence the `chmod`. Without it, a job fails to start and the gateway answers `busy` until it's restarted. `run_sandboxed.sh` mounts `venv/`, `scripts/` and `lib/` into the sandbox, and `bwrap` refuses to start if one is missing. `scripts/` and `lib/` aren't in the repository, so create them. A job can import modules from either.

## Running it

```sh
venv/bin/python gateway.py
```

The gateway listens on port 5099 on all interfaces, over plain HTTP. The account that runs it needs the `dialout` group for the serial port and `video` for the webcam. On its first start it writes a random token to `token`, beside `gateway.py`, then makes the file readable only by its owner, and it prints the token at every start. Every route `gateway.py` defines, except the page at `/`, needs the token, either as `Authorization: Bearer <token>` or as a `token` query parameter. To make a new token, delete `token` and restart.

The token is the only access control. Whoever has it can run any Python script in the sandbox, move the plotter and see the camera. It travels over plain HTTP, and in the URL when it's passed as a query parameter, as the page passes it for `/events` and snapshots, so it also appears in the gateway's request log.

To run it as a service, edit the user, group and paths in `pen-gate.service` to match your install, then:

```sh
sudo cp pen-gate.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pen-gate
journalctl -u pen-gate    # shows the token
```

The unit adds both groups.

## Running a job

The job below plots `examples/first_mark.py`, which moves the plotter's carriage to fixed coordinates on a US Letter sheet in landscape, so set up the plotter and its paper first, as the [AxiDraw User Guide](https://axidraw.com/guide) describes. Run it from a clone of this repository, on any machine that can reach the gateway:

```sh
GATE=http://<pi-address>:5099
TOKEN=<the token>

# Submit a script:
python3 -c 'import json, sys; print(json.dumps({"script": open(sys.argv[1]).read()}))' examples/first_mark.py |
  curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" --data-binary @- "$GATE/run"

# Follow its kept output and the events after it:
curl -N "$GATE/events?token=$TOKEN"
```

The gateway runs one job at a time and keeps the last 2000 lines of its output, stdout and stderr together. When the job exits, the gateway tries to raise the pen and switch the motors off.

The sandbox has no network, its own `/tmp`, an empty `/home`, and read-only `/usr` and `/sys`. It gets the install's `venv/`, `scripts/` and `lib/`, read-only at `/venv`, `/scripts` and `/lib_user`, and `PYTHONPATH` holds `/venv/lib/python3.13/site-packages`, `/lib_user` and `/scripts`. It gets the plotter's serial device if the path in `AXI_PORT` (default `/dev/ttyACM0`) exists, and runs the job without it otherwise. `run_sandboxed.sh` takes the install directory from `PEN_GATE_HOME` (default `~/pen-gate`). The gateway runs it with its own environment, so set these for the gateway if yours differ, with `Environment=` lines when it runs as a service. The gateway's own routes find the plotter through `PORT_GLOB` instead (see Settings), so `AXI_PORT` should name the same device.

## API

`/pen/*`, `/motors/*`, `/command/raw` and `/run` answer `409` with the error `busy` while a job or another of these requests holds the serial port. `/command/raw` and `/run` read a JSON body, so send it with `Content-Type: application/json`.

| Route | What it does |
|---|---|
| `GET /` | The web page. It asks for the token and keeps it in the browser's local storage. |
| `GET /status` | Whether the EBB answers on the serial port, with its firmware version, and the current or last job with the latest 200 lines of its output. During a job it answers from the job's state and leaves the port alone. |
| `POST /pen/up`, `POST /pen/down` | Raises or lowers the pen |
| `POST /motors/on`, `POST /motors/off` | Energizes or releases the stepper motors (EBB `EM,1,1` and `EM,0,0`) |
| `POST /command/raw` | Sends `{"cmd": "..."}` to the EBB and returns its reply |
| `POST /run` | Starts a job from `{"script": "..."}` and answers `202` with `{"job_id": "..."}` |
| `POST /run/cancel` | Stops the running job |
| `GET /events` | Server-sent events: `job_start`, a `stdout` event for each line of output, and `job_end` with the exit code. A client that connects during or after a job first gets the output the gateway has kept for it. Each client gets at most 2000 `stdout` events for a job, so once a job has printed more than 2000 lines, a client misses some of them. `job_end` still comes. |
| `GET /camera/status` | The camera device, whether it exists, and the default snapshot size |
| `GET /camera/snapshot.jpg` | One JPEG from the webcam, 1280x720 unless `size=WxH` asks for another size. During a job it answers `503`. |

## Settings

The settings are constants at the top of `gateway.py`:

| Constant | Value | What it sets |
|---|---|---|
| `LISTEN_HOST`, `LISTEN_PORT` | `0.0.0.0`, `5099` | Where the gateway listens |
| `PORT_GLOB`, `BAUD` | `/dev/ttyACM*`, `9600` | Where it looks for the plotter, taking the first path `glob` returns (in directory order, not sorted), and the baud rate it opens the port with |
| `SUBPROCESS_TIMEOUT` | `600` | Seconds after which the gateway kills a job. It checks the time only when the job prints a line, so a job that prints nothing isn't stopped. |
| `STDOUT_MAX_LINES` | `2000` | Lines of a job's output the gateway keeps |
| `CAMERA_DEV`, `CAMERA_SIZE`, `CAMERA_TIMEOUT_S` | `/dev/video0`, `1280x720`, `5` | The webcam, the default snapshot size, and the seconds a snapshot may take |
| `SERVO_PIN` | `2` | The EBB pin (RB2) that `/pen/up` and `/pen/down` drive the pen-lift servo on |
| `SERVO_MIN`, `SERVO_MAX` | `9855`, `27831` | The servo pulse widths that 0% and 100% stand for |
| `PEN_UP_PCT`, `PEN_DOWN_PCT` | `60`, `30` | The pen heights for `/pen/up` and `/pen/down`, as percentages of the range from `SERVO_MIN` to `SERVO_MAX` |
| `RAISE_RATE`, `LOWER_RATE` | `1618`, `1078` | How fast the servo raises and lowers the pen (EBB `SC,11` and `SC,12`) |

Jobs set their own pen heights and speeds through pyaxidraw, as the examples do.

## Examples

Each script in `examples/` connects to the plotter in pyaxidraw's interactive mode, draws, and returns to the home corner. `first_mark.py` and `plot_first_mark.py` draw logarithmic spirals, `second_mark.py` an inward spiral, `interference.py` two overlapping sets of concentric circles, and `flame.py` 18 flame outlines of growing size. Each draws at fixed coordinates from the home corner, in inches or millimetres. `first_mark.py` and `flame.py` say they're placed for a US Letter sheet in landscape.
