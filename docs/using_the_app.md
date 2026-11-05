# Using the app

VRphos shows only the simulated phosphenes on a black background. The camera image is turned into
one stimulation current per electrode; the Dynaphos model turns the currents into phosphenes
(position, size and brightness), including thresholds and the slow fading under constant
stimulation (temporal dynamics).

All settings are in one menu. While it is open the phosphenes are hidden, except while a row that
changes their appearance (count, size, FOV, layout) is selected.

## Controls

**Meta Quest** (Touch controllers, no pointing needed). The menu opens in front of you and stays
fixed in the room.

| control | action |
|---|---|
| left menu button | open / close the menu |
| thumbstick up / down | select a row |
| thumbstick left / right | change the value (hold to repeat; longer hold = bigger steps) |
| A / X / trigger | press a button, toggle, or the row's extra action (shown in the label) |
| B / Y | close the menu |

**Android phone** (touch, landscape)

| control | action |
|---|---|
| gear button (top left), Android back | open / close the menu |
| `<` `>` | change a value (hold to repeat) |
| tap a value | toggle / cycle through the options |
| Bluetooth gamepad / keyboard | stick, D-pad or arrows select and change; A / Enter press; B close |

## Input

| *Input* | stimulation comes from |
|---|---|
| **Headset camera** (Quest) / **Phone camera** (Android) | the device camera, processed on the device (below) |
| **Remote server** | a PC streaming currents over TCP, see [tcp_server.md](tcp_server.md) |

## Processing (device camera input)

| *Processing* | what is stimulated | settings |
|---|---|---|
| None (brightness) | image brightness at each phosphene | *Brightness gain* |
| Sobel edges | edge strength (Sobel magnitude) | *Edge threshold*, *Brightness gain* |
| Neural net (YOLO) | pixels inside segmentation masks of detected objects | *NN model*, *NN confidence*, *NN rate (Hz)* |
| Tool pack | output of your own network, see [tool_packs.md](tool_packs.md) | *Tool*, *Tool rate (Hz)*, *Tool folder*, *Rescan tool folder* |

Bundled YOLO models: YOLOv8n-seg (Quest); YOLO26n-seg (320 px) and YOLO11n-seg (Android).
The network runs asynchronously at *NN rate*, so a slow network never lowers the display rate.

Further camera rows: *Flip image vertically*, *Show camera preview* (a small preview of the camera
image), *Camera FOV* (Android: field of view of the phone camera, to map the image correctly).

## Phosphenes

| row | meaning |
|---|---|
| *Implant layout* | *All valid sites* (13,062 electrodes) or *Utah arrays* (800 electrodes), see [layouts.md](layouts.md). Device camera input only; the server sends its own layout. |
| *Phosphenes* | number of active electrodes (a spatially even subset of the layout) |
| *Phosphene size* | scales all phosphene sizes |
| *View FOV* | field of view of the virtual camera: zooms the whole phosphene field. Quest: press A to return to the headset FOV. |
| *Stereo (VR viewer)* | Android: side-by-side view for a phone VR viewer (applied when the menu closes) |
| *Temporal dynamics* | Dynaphos activation, memory trace and habituation; off = instantaneous brightness |
| *Random test input* | random stimulation instead of the camera (e.g. to test rendering) |

## Server (Remote server input)

| row | meaning |
|---|---|
| *Server address* | *Auto (gateway)*: the network gateway, i.e. the PC when the device is connected to a hotspot of that PC. *Manual*: the IP set in the next row. |
| *Manual IP* / *Server IP* | Quest: thumbstick left/right changes the selected part, A moves to the next part |
| *Connect / reload* | reconnect and reload the layout from the server |

## Debug

*Run performance benchmark* steps through phosphene counts and measures frame rate, latency and
memory; see [benchmark.md](benchmark.md) for running it from a PC and collecting the results.
