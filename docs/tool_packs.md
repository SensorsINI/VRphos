# VRphos tool packs: your own scene processing, no rebuild

A **tool pack** adds a scene-processing network to the VRphos apps (Meta Quest and Android) without
recompiling VRphos. VRphos scans a folder on the device at start-up and lists every valid tool in the
settings menu under *Processing → Tool pack*.

```
tools/
└── my_tool/
    ├── tool.json      # manifest: what the network expects and returns
    └── model.sentis   # the network, converted from ONNX
```

Tool folder, chosen in the settings menu (*Tool folder*):

| Option | Quest and Android |
|---|---|
| App folder (default) | `/sdcard/Android/data/com.INI.VRphos/files/tools/` |
| Shared: VRphos/tools | `/sdcard/VRphos/tools/` |
| Shared: Download/VRphos/tools | `/sdcard/Download/VRphos/tools/` |

The shared folders are easy to fill over USB or with a file manager on the device. On Android 11+
and Quest they need *All files access*: the menu then shows *Allow file access (tool folder)*,
which opens the system page; the tools are read again when you return to VRphos.

## The tool API

Every frame of the device camera is resized to the tool's input size and passed to the network as a
float32 tensor `[1, C, H, W]` (RGB or grey, values in [0, 1], then `(x - mean) / std`).
VRphos runs the network asynchronously at `rate_hz` and reuses the last result in between, so a slow
tool never lowers the display rate. The output is interpreted according to `type`:

| `type`        | network output                              | how VRphos uses it |
|---------------|---------------------------------------------|--------------------|
| `map`         | `[1,1,H,W]`, `[1,H,W]` or `[H,W]`, values in [0, 1] | stimulation map, sampled at each phosphene like the Sobel mode |
| `yolo-seg`    | YOLOv8/YOLO11 (anchor-based) or YOLO26 (NMS-free) segmentation outputs | decoded by VRphos; pixels inside masks of the listed classes are stimulated |
| `stimulation` | `[1,N]` or `[N]`, one value per electrode     | used directly as currents (learned encoders); `N` must equal the number of phosphenes |

### tool.json

```json
{
  "format_version": 1,
  "name": "My edge network",
  "description": "What it does",
  "author": "you",
  "version": "1.0",
  "engine": "2.6.1",
  "model": "model.sentis",
  "type": "map",
  "rate_hz": 10,
  "input":  { "width": 256, "height": 256, "channels": "rgb", "mean": [0,0,0], "std": [1,1,1] },
  "output": { "activation": "none", "threshold": -1, "invert": false, "unit": "normalized" },
  "yolo":   { "confidence": 0.4, "iou": 0.5, "classes": [] }
}
```

- `output.activation`: `"sigmoid"` if the map is logits; `threshold` zeroes values below it (-1 = off).
- `output.unit` (stimulation tools): `"normalized"` = [0, 1] × max current (100 µA), or `"ampere"`.
- `yolo.classes`: COCO ids to keep, e.g. `[56, 60]` for chairs and tables; empty = all classes.
- `engine` is filled in by the converter. Models must be converted with the Inference Engine version
  of the app (currently 2.6.1); otherwise VRphos reports the mismatch in the menu.
- If the model has a fixed input size, VRphos uses it and ignores `input.width/height`.

## Workflow

All commands run in `python/` with the virtual environment active (see [tcp_server.md](tcp_server.md#setup)).

1. **Export** your PyTorch model to ONNX (`torch.onnx.export(..., opset_version=17)`).
2. **Describe** it: `python tool_packs/make_tool_pack.py init my_tool --onnx model.onnx --type map`
   writes `my_tool/` with `model.onnx` and a `tool.json` to edit.
3. **Test on the PC**: `python tool_packs/make_tool_pack.py check my_tool --source image.png`
   runs the model with the same preprocessing as the app and shows camera | tool output | phosphenes
   (`--source 0` for the webcam, `--save preview.png` to save the figure).
4. **Convert** ONNX to `.sentis` with the converter project in
   [`unity_tool_pack_converter/`](../unity_tool_pack_converter) (Unity 6000.2 or newer; the
   Inference Engine package is installed when the project is opened the first time):
   ```bash
   Unity -batchmode -nographics -quit -projectPath unity_tool_pack_converter \
         -executeMethod VRphosToolPackExporter.ConvertFromCommandLine \
         -onnx python/my_tool/model.onnx -out python/my_tool -logFile -
   ```
   or open the project in the Unity editor and use the menu **VRphos → Convert ONNX to tool pack…**.
   Unsupported ONNX operators are reported here, on the PC. The converter writes `model.sentis`
   and fills in `engine` in `tool.json`.
5. **Install**: copy the folder (`tool.json` + `model.sentis`) to the device:
   ```bash
   adb push my_tool /sdcard/Android/data/com.INI.VRphos/files/tools/
   ```
   (or into the shared tool folder you selected), then *Rescan tool folder* in the VRphos menu (or
   restart the app), set *Processing* to *Tool pack* and select the tool.

`python tool_packs/make_examples.py` rebuilds the two examples in `python/tool_packs/examples/`
(`map` type; ready to install, `model.sentis` included): a Sobel edge network and a Canny edge
detector (blur, Sobel, thinning, double threshold, hysteresis; thin, connected outlines with less
texture noise). `--yolo` additionally exports YOLO11n-seg restricted to indoor furniture
(`yolo-seg` type).

## What needs a rebuild of VRphos

Nothing, as long as the tool fits the API above. A rebuild is only needed for new output types,
new pre-processing, operators the Inference Engine cannot run, or a newer Inference Engine version.
Processing that is not a network, or does not fit on the device, can run on a PC in any language
and be streamed to the app over TCP (see [tcp_server.md](tcp_server.md)).
