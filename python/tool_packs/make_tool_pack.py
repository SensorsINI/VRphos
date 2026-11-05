"""Create and test VRphos tool packs on the PC before copying them to a headset or phone.

A tool pack is a folder with tool.json and a model. VRphos loads it at run time from
/sdcard/Android/data/com.INI.VRphos/files/tools/<name>/ (see ../docs/tool_packs.md).

    # 1. write tool.json next to an exported ONNX model
    python tool_packs/make_tool_pack.py init my_tool --onnx model.onnx --type map --size 256 256

    # 2. run it on an image, a video or the webcam and preview the phosphenes
    python tool_packs/make_tool_pack.py check my_tool --source image.png --save preview.png

    # 3. convert model.onnx to model.sentis with the Unity converter project (see ../docs/tool_packs.md)
    # 4. install it
    adb push my_tool /sdcard/Android/data/com.INI.VRphos/files/tools/

The preprocessing in `run_tool` mirrors ToolPackProcessor.cs: nearest-neighbour resize, RGB or
grey in [0,1], (x - mean) / std, NCHW float32. Needs onnx and onnxruntime; can be run from any directory.
"""
import argparse
import json
import os
import shutil
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TYPES = ("map", "yolo-seg", "stimulation")
FORMAT_VERSION = 1


def default_manifest(name, tool_type, width, height, channels):
    return {
        "format_version": FORMAT_VERSION,
        "name": name,
        "description": "",
        "author": "",
        "version": "1.0",
        "engine": "",
        "model": "model.sentis",
        "type": tool_type,
        "rate_hz": 10.0,
        "input": {"width": width, "height": height, "channels": channels,
                  "mean": [0.0, 0.0, 0.0], "std": [1.0, 1.0, 1.0]},
        "output": {"activation": "none", "threshold": -1.0, "invert": False, "unit": "normalized"},
        "yolo": {"confidence": 0.4, "iou": 0.5, "classes": []},
    }


def load_manifest(folder):
    with open(os.path.join(folder, "tool.json")) as f:
        m = json.load(f)
    problems = []
    if m.get("format_version", 1) > FORMAT_VERSION:
        problems.append(f"format_version {m['format_version']} is newer than {FORMAT_VERSION}")
    if m.get("type") not in TYPES:
        problems.append(f"type must be one of {TYPES}, got {m.get('type')!r}")
    inp = m.get("input", {})
    if inp.get("channels", "rgb") not in ("rgb", "gray"):
        problems.append("input.channels must be 'rgb' or 'gray'")
    if int(inp.get("width", 0)) <= 0 or int(inp.get("height", 0)) <= 0:
        problems.append("input.width and input.height must be positive")
    return m, problems


def preprocess(bgr, manifest):
    inp = manifest["input"]
    w, h = int(inp["width"]), int(inp["height"])
    img = cv2.resize(bgr, (w, h), interpolation=cv2.INTER_NEAREST)
    if inp.get("channels", "rgb") == "gray":
        b, g, r = (img[..., k].astype(np.float32) for k in range(3))
        x = ((r * 77 + g * 150 + b * 29) / (255.0 * 256.0))[None]
    else:
        x = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32).transpose(2, 0, 1) / 255.0
    mean = np.asarray(inp.get("mean", [0, 0, 0]), np.float32)[: x.shape[0], None, None]
    std = np.asarray(inp.get("std", [1, 1, 1]), np.float32)[: x.shape[0], None, None]
    std[std == 0] = 1
    return ((x - mean) / std)[None].astype(np.float32)


def postprocess_map(out, manifest, shape_hw):
    """Map output -> stimulation image in [0,1] at frame size, as ToolPackProcessor.WriteMap."""
    a = np.asarray(out, np.float32)
    if a.ndim == 4:
        a = a[0, 0]
    elif a.ndim == 3:
        a = a[0]
    elif a.ndim != 2:
        raise ValueError(f"map output must have rank 2-4, got shape {a.shape}")
    o = manifest.get("output", {})
    if o.get("activation", "none") == "sigmoid":
        a = 1 / (1 + np.exp(-a))
    if o.get("invert", False):
        a = 1 - a
    thr = float(o.get("threshold", -1))
    if thr >= 0:
        a = np.where(a < thr, 0, a)
    a = np.clip(a, 0, 1)
    return cv2.resize(a, shape_hw[::-1], interpolation=cv2.INTER_NEAREST)


def run_tool(session, manifest, bgr):
    x = preprocess(bgr, manifest)
    outputs = session.run(None, {session.get_inputs()[0].name: x})
    return outputs


def load_simulator():
    from utils.dynaphos.dynaphos_lite import GaussianSimulator
    from utils.dynaphos.utils import Map, load_params
    params = load_params(os.path.join(ROOT, "config", "params.yaml"))
    params["run"]["print_stats"] = False
    polar = np.loadtxt(os.path.join(ROOT, "layout_data", "coords_polar.csv"), delimiter=",")
    return GaussianSimulator(params, Map(r=polar[0], phi=polar[1]))


def layout_size():
    """Number of phosphenes in the layout the app uses (stimulation tools must output this many values)."""
    return np.loadtxt(os.path.join(ROOT, "layout_data", "coords_polar.csv"), delimiter=",").shape[1]


# --------------------------------------------------------------------------------------------
def cmd_init(args):
    os.makedirs(args.folder, exist_ok=True)
    path = os.path.join(args.folder, "tool.json")
    if os.path.exists(path) and not args.force:
        sys.exit(f"{path} exists (use --force to overwrite)")
    m = default_manifest(args.name or os.path.basename(os.path.normpath(args.folder)),
                         args.type, args.size[0], args.size[1], args.channels)
    if args.onnx:
        shutil.copy(args.onnx, os.path.join(args.folder, "model.onnx"))
        m["input"].update(onnx_input_size(args.onnx) or {})
    with open(path, "w") as f:
        json.dump(m, f, indent=2)
    print(f"wrote {path}")
    print("next: check it with  make_tool_pack.py check", args.folder,
          "  then convert model.onnx to model.sentis in Unity (VRphos > Convert ONNX to tool pack...)")


def onnx_input_size(onnx_path):
    import onnx
    dims = onnx.load(onnx_path).graph.input[0].type.tensor_type.shape.dim
    if len(dims) == 4 and all(d.dim_value > 0 for d in dims[2:]):
        return {"height": dims[2].dim_value, "width": dims[3].dim_value}
    return None


def cmd_check(args):
    import onnxruntime as ort
    m, problems = load_manifest(args.folder)
    onnx_path = args.onnx or os.path.join(args.folder, "model.onnx")
    if not os.path.exists(onnx_path):
        sys.exit(f"no ONNX model to test ({onnx_path}); pass --onnx")
    for p in problems:
        print("manifest problem:", p)

    session = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    inp = session.get_inputs()[0]
    print(f"model input  {inp.name} {inp.shape}")
    for o in session.get_outputs():
        print(f"model output {o.name} {o.shape}")

    frames = read_frames(args.source, args.frames)
    sim = load_simulator() if not args.no_preview else None
    for k, bgr in enumerate(frames):
        out = run_tool(session, m, bgr)
        if m["type"] == "map":
            stim_img = postprocess_map(out[0], m, bgr.shape[:2])
            print(f"frame {k}: map {np.asarray(out[0]).shape}, mean {stim_img.mean():.3f}, "
                  f"{100 * (stim_img > 0).mean():.1f} % of pixels stimulated")
            stim = sim.sample_stimulus(stim_img.astype(np.float32), rescale=True) if sim else None
        elif m["type"] == "stimulation":
            vals = np.asarray(out[0], np.float32).ravel()
            n = layout_size()
            print(f"frame {k}: {vals.size} electrode values, layout has {n}")
            if vals.size != n:
                print("  WARNING: VRphos ignores the output unless its length equals the number of phosphenes")
            stim_img, stim = None, None
            if sim and vals.size == sim.num_phosphenes:
                import torch
                scale = 1.0 if m["output"].get("unit") == "ampere" else 100e-6
                stim = torch.as_tensor(np.clip(vals, 0, None) * scale, **sim.data_kwargs)
        else:
            print(f"frame {k}: yolo-seg outputs {[np.asarray(o).shape for o in out]} "
                  "(decoded on the device; preview not implemented here)")
            stim_img, stim = None, None
        if sim is not None and stim is not None:
            for _ in range(4):  # a few steps, as in the paper figures
                img = sim(stim).detach().cpu().numpy()
            if args.save:
                save_preview(args.save, k, bgr, stim_img, img)
            else:
                show_preview(bgr, stim_img, img)


def read_frames(source, n):
    if source is None:
        source = 0
    if isinstance(source, str) and os.path.splitext(source)[1].lower() in (".png", ".jpg", ".jpeg", ".bmp"):
        img = cv2.imread(source)
        if img is None:
            sys.exit(f"cannot read {source}")
        return [img]
    cap = cv2.VideoCapture(int(source) if str(source).isdigit() else source)
    frames = []
    while len(frames) < n:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    if not frames:
        sys.exit(f"no frames from {source}")
    return frames


def _panels(bgr, stim_img, phos):
    h = 256
    panels = [cv2.resize(bgr, (h, h))]
    if stim_img is not None:
        panels.append(cv2.cvtColor((cv2.resize(stim_img, (h, h)) * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR))
    panels.append(cv2.cvtColor((cv2.resize(np.clip(phos, 0, 1), (h, h)) * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR))
    return np.hstack(panels)


def save_preview(path, k, bgr, stim_img, phos):
    root, ext = os.path.splitext(path)
    out = path if k == 0 else f"{root}_{k}{ext or '.png'}"
    cv2.imwrite(out, _panels(bgr, stim_img, phos))
    print("  preview:", out)


def show_preview(bgr, stim_img, phos):
    cv2.imshow("camera | tool output | phosphenes", _panels(bgr, stim_img, phos))
    cv2.waitKey(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("init", help="write tool.json (and copy the ONNX model) into a tool folder")
    a.add_argument("folder")
    a.add_argument("--name")
    a.add_argument("--onnx", help="exported ONNX model, copied to <folder>/model.onnx")
    a.add_argument("--type", choices=TYPES, default="map")
    a.add_argument("--size", type=int, nargs=2, default=[256, 256], metavar=("W", "H"))
    a.add_argument("--channels", choices=("rgb", "gray"), default="rgb")
    a.add_argument("--force", action="store_true")
    a.set_defaults(func=cmd_init)
    c = sub.add_parser("check", help="validate tool.json, run the ONNX model and preview phosphenes")
    c.add_argument("folder")
    c.add_argument("--onnx", help="ONNX model (default <folder>/model.onnx)")
    c.add_argument("--source", help="image, video file or camera index (default 0)")
    c.add_argument("--frames", type=int, default=1, help="frames to process from a video or camera")
    c.add_argument("--save", help="write previews to this PNG instead of showing a window")
    c.add_argument("--no-preview", action="store_true", help="only run the model, no phosphene rendering")
    c.set_defaults(func=cmd_check)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
