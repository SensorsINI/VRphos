"""Build example VRphos tool packs (ONNX + tool.json) to start from.

    python tool_packs/make_examples.py            # Sobel and Canny edge networks (map tools)
    python tool_packs/make_examples.py --yolo     # also YOLO11n-seg restricted to indoor objects

Each example lands in tool_packs/examples/<name>/. Convert model.onnx to model.sentis in Unity
(unity_tool_pack_converter/, see docs/tool_packs.md) and copy the folder to the device.
"""
import argparse
import json
import os
import shutil
import sys

import torch
import torch.nn as nn
import torch.nn.functional as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from make_tool_pack import default_manifest  # noqa: E402

OUT = os.path.join(HERE, "examples")


class SobelEdges(nn.Module):
    """Grey image [1,1,H,W] in [0,1] -> edge map [1,1,H,W] in [0,1] (3x3 Sobel magnitude)."""

    def __init__(self, gain=2.0):
        super().__init__()
        kx = torch.tensor([[-1.0, 0.0, 1.0], [-2.0, 0.0, 2.0], [-1.0, 0.0, 1.0]])
        self.register_buffer("k", torch.stack([kx, kx.t()])[:, None])
        self.gain = gain

    def forward(self, x):
        g = F.conv2d(x, self.k, padding=1)
        mag = torch.sqrt(g[:, :1] ** 2 + g[:, 1:] ** 2 + 1e-12)
        return torch.clamp(self.gain * mag / 4.0, 0.0, 1.0)


class CannyEdges(nn.Module):
    """Grey image [1,1,H,W] in [0,1] -> binary Canny edge map [1,1,H,W] (0 or 1).

    The classic pipeline built only from Conv, MaxPool and element-wise ops, so it runs in
    Unity's Inference Engine: Gaussian blur -> Sobel -> non-maximum suppression (4 directions)
    -> double threshold -> hysteresis (strong edges grow into weak ones, `hysteresis_steps`
    pixels far). Thresholds are on the gradient magnitude of the [0,1] image
    (cv2.Canny thresholds / 255 with L2gradient=True).
    """

    def __init__(self, size=256, low=0.2, high=0.45, sigma=2.0, hysteresis_steps=16, border=3):
        super().__init__()
        # Zero a border: the zero padding creates false edges at the image edge.
        inner = torch.zeros(1, 1, size, size)
        inner[..., border:size - border, border:size - border] = 1.0
        self.register_buffer("inner", inner)
        r = torch.arange(5, dtype=torch.float32) - 2
        g = torch.exp(-(r ** 2) / (2 * sigma ** 2))
        g = g / g.sum()
        self.register_buffer("gauss", (g[:, None] * g[None, :])[None, None])
        kx = torch.tensor([[-1.0, 0.0, 1.0], [-2.0, 0.0, 2.0], [-1.0, 0.0, 1.0]])
        self.register_buffer("sobel", torch.stack([kx, kx.t()])[:, None])
        # One-hot 3x3 kernels: channel k holds the neighbour at (dy, dx); out[y,x] = mag[y+dy, x+dx].
        offsets = [(0, -1), (0, 1), (-1, 0), (1, 0), (-1, -1), (1, 1), (-1, 1), (1, -1)]
        shift = torch.zeros(len(offsets), 1, 3, 3)
        for k, (dy, dx) in enumerate(offsets):
            shift[k, 0, dy + 1, dx + 1] = 1.0
        self.register_buffer("shift", shift)
        self.low, self.high = low, high
        self.steps = hysteresis_steps
        self.t = 0.41421356  # tan(22.5 deg)

    def forward(self, x):
        x = F.conv2d(x, self.gauss, padding=2)
        g = F.conv2d(x, self.sobel, padding=1)
        gx, gy = g[:, :1], g[:, 1:]
        mag = torch.sqrt(gx * gx + gy * gy + 1e-12)
        n = F.conv2d(mag, self.shift, padding=1)  # 8 neighbour magnitudes

        ax, ay = gx.abs(), gy.abs()
        horiz = (ay <= self.t * ax).float()       # gradient along x: compare left/right
        vert = (ax <= self.t * ay).float()        # gradient along y: compare up/down
        diag = 1.0 - horiz - vert
        same_sign = (gx * gy > 0).float()         # down-right/up-left diagonal
        keep_h = ((mag >= n[:, 0:1]) & (mag >= n[:, 1:2])).float()
        keep_v = ((mag >= n[:, 2:3]) & (mag >= n[:, 3:4])).float()
        keep_d1 = ((mag >= n[:, 4:5]) & (mag >= n[:, 5:6])).float()
        keep_d2 = ((mag >= n[:, 6:7]) & (mag >= n[:, 7:8])).float()
        keep = horiz * keep_h + vert * keep_v + diag * (same_sign * keep_d1 + (1.0 - same_sign) * keep_d2)
        thin = mag * keep

        thin = thin * self.inner

        weak = (thin >= self.low).float()
        edges = (thin >= self.high).float()
        for _ in range(self.steps):
            edges = F.max_pool2d(edges, 3, stride=1, padding=1) * weak
        return edges


def write(folder, manifest, onnx_src=None):
    os.makedirs(folder, exist_ok=True)
    if onnx_src:
        shutil.copy(onnx_src, os.path.join(folder, "model.onnx"))
    with open(os.path.join(folder, "tool.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    print("wrote", folder)


def sobel_example():
    folder = os.path.join(OUT, "sobel_edges")
    os.makedirs(folder, exist_ok=True)
    torch.onnx.export(SobelEdges().eval(), torch.zeros(1, 1, 256, 256), os.path.join(folder, "model.onnx"),
                      input_names=["image"], output_names=["map"], opset_version=17, dynamo=False)
    m = default_manifest("Sobel edges (network)", "map", 256, 256, "gray")
    m.update(description="3x3 Sobel gradient magnitude as a network; a template for learned edge or saliency maps.",
             author="VRphos", rate_hz=30.0)
    m["output"]["threshold"] = 0.12
    write(folder, m)


def canny_example():
    folder = os.path.join(OUT, "canny_edges")
    os.makedirs(folder, exist_ok=True)
    torch.onnx.export(CannyEdges(256).eval(), torch.zeros(1, 1, 256, 256), os.path.join(folder, "model.onnx"),
                      input_names=["image"], output_names=["map"], opset_version=17, dynamo=False)
    m = default_manifest("Canny edges", "map", 256, 256, "gray")
    m.update(description="Canny edge detector as a network (blur, Sobel, thinning, double threshold, "
                         "hysteresis): thin, connected outlines with less noise than Sobel.",
             author="VRphos", rate_hz=30.0)
    m["output"]["threshold"] = 0.5  # output is already binary
    write(folder, m)


def yolo_example():
    from ultralytics import YOLO
    folder = os.path.join(OUT, "yolo11n_seg_indoor")
    onnx_path = YOLO("yolo11n-seg.pt").export(format="onnx", imgsz=320, opset=17)
    m = default_manifest("YOLO11n-seg: chairs, tables, screens", "yolo-seg", 320, 320, "rgb")
    m.update(description="Instance segmentation restricted to furniture and screens (COCO ids).", author="VRphos")
    m["yolo"]["classes"] = [56, 57, 59, 60, 62, 63]  # chair, couch, bed, dining table, tv, laptop
    write(folder, m, onnx_path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yolo", action="store_true", help="also export the YOLO example (needs ultralytics + onnx)")
    args = ap.parse_args()
    sobel_example()
    canny_example()
    if args.yolo:
        yolo_example()
