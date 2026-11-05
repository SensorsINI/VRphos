# TCP stimulation server

Instead of processing the camera on the device, VRphos can receive the stimulation from a PC.
This is the way to drive VRphos with anything that does not fit on the device: large networks,
other sensors, recorded videos, or your own encoder written in any language.

`python/vrphos_server.py` is a ready-to-use server: it captures a webcam (or video), optionally
segments it with YOLO, applies a filter, samples the image at every phosphene with Dynaphos, and
streams the currents to the app.

## Setup

Python 3.11+ is recommended.

```bash
cd python
./setup.sh                    # creates .venv and installs requirements.txt
source .venv/bin/activate
```

## Run

```bash
python vrphos_server.py                        # webcam 0, settings window
python vrphos_server.py --source 1             # another webcam
python vrphos_server.py --source video.mp4     # a video file (loops)
python vrphos_server.py --filter canny --yolo  # start with Canny edges and YOLO on
python vrphos_server.py --preview              # also show raw / segmented / filtered images (slower)
python vrphos_server.py --no-ui                # no settings window
python vrphos_server.py --dry-run              # process frames without streaming (no device needed)
```

The settings window controls, while running:

- **Filter**: `none` (brightness), `canny` or `sobel` edges
- **YOLO segmentation**: on/off, labels, phosphene view (a Dynaphos rendering on the PC), the classes
  to keep, confidence and IoU thresholds. The model (`--model`, default `yolo11n-seg.pt`) is
  downloaded by ultralytics on first use.
- **Number of phosphenes**: only the first N electrodes of the layout are stimulated (the others are sent as 0)

Settings are saved to `server_settings.json` and restored on the next start.
*Save & Close (Exit)* stops the server.

## Connect the app

1. Put the PC and the device in the same network. Easiest: open a Wi-Fi hotspot on the PC and
   connect the headset/phone to it; then the app finds the PC by itself (*Server address: Auto (gateway)*).
   Otherwise set *Server address: Manual* and enter the PC's IP address.
2. In the app menu set *Input: Remote server*, then *Connect / reload*.
3. The app loads the layout from the server and then shows the streamed stimulation.

Over USB instead of Wi-Fi: forward the ports and use the manual address `127.0.0.1`:

```bash
adb reverse tcp:8001 tcp:8001
adb reverse tcp:8002 tcp:8002
```

Allow the ports in the PC firewall if the app cannot connect (e.g. `sudo ufw allow 8001:8002/tcp`).

## Protocol

Ports are set in `python/config/params.yaml` (`webserver.PORT_HTTP`, `webserver.PORT_TCP`); the app
uses 8001 and 8002.

**HTTP, port 8001**: the phosphene layout, loaded by the app when it connects.

| request | content |
|---|---|
| `GET /constants/coords.csv` | 2 rows × N: x and y position of each phosphene in the visual field (degrees) |
| `GET /constants/magnification.csv` | 1 row × N: cortical magnification at each phosphene (mm/degree) |
| websocket `/register` | optional: the device sends `{"device_id": ...}`, the server answers `{"status": "ok", "ip": ...}` |

The files are served from `python/layout_data/` ([layouts.md](layouts.md)).

**TCP, port 8002**: the server listens, the app connects. The server sends one text line per
frame:

```
<I_0>,<I_1>,...,<I_N-1>\n
```

- exactly one value per phosphene, in the order of `coords.csv` (lines of another length are
  ignored by the app, with a warning in its log)
- current in **ampere** (e.g. `1e-4` = 100 µA); `0` = electrode off
- decimal point, no spaces; any number format that parses as a float (`9.96e-05`, `0.0001`)
- send at any rate: the app simulates at display rate and uses the most recent line

The app applies the Dynaphos defaults of `python/config/params.yaml` (pulse width 170 µs,
300 Hz, rheobase 23.9 µA, thresholds, temporal dynamics) to the received currents.

## Writing your own source

Anything that serves the two CSV files and writes lines to a TCP socket works. The Python helper
`python/utils/network_servers.py` does both:

```python
import time

import numpy as np
from utils.network_servers import PhospheneServers
from utils.dynaphos.utils import load_params

params = load_params("config/params.yaml")
servers = PhospheneServers.from_params(params, csv_folder="layout_data")
servers.start()

n = np.loadtxt("layout_data/coords.csv", delimiter=",").shape[1]
try:
    while True:
        currents = np.zeros(n, dtype=np.float32)
        currents[:100] = 100e-6            # your encoder here (ampere)
        servers.tcp.send_line(currents)    # no-op while no app is connected
        time.sleep(1 / 30)
finally:
    servers.stop()
```

Run it from `python/`.
