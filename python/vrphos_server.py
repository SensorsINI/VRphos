"""VRphos stimulation server: webcam -> scene processing -> dynaphos -> electrode currents -> VRphos app.

Runs in one process:
  - HTTP server (default port 8001): serves the phosphene layout (layout_data/coords.csv,
    magnification.csv) at /constants/<file>; devices can register at the /register websocket.
  - TCP stream (default port 8002): one line of comma-separated currents (Ampere) per frame,
    one value per phosphene, in the order of coords.csv.
  - Camera loop: optional YOLO segmentation, a filter (none / canny / sobel), and dynaphos
    sampling of the image at every phosphene position.
  - Settings window (Tk): filter, YOLO on/off, classes, thresholds, number of phosphenes.
    Settings are saved to server_settings.json and restored on the next start.

In the VRphos app choose Input: "Remote server" and set the server address (see docs/tcp_server.md).

Usage:
  python vrphos_server.py                       # webcam 0, settings window
  python vrphos_server.py --source video.mp4    # a video file instead of the webcam
  python vrphos_server.py --preview             # also show camera / processed images (slower)
  python vrphos_server.py --no-ui --filter canny
Ports are set in config/params.yaml (webserver.PORT_HTTP / PORT_TCP).
"""

import argparse
import atexit
import json
import os
import time
import tkinter as tk
from tkinter import ttk

import cv2
import matplotlib.pyplot as plt
import numpy as np
from ultralytics import YOLO

from utils.dynaphos.dynaphos_lite import GaussianSimulator
from utils.dynaphos.image_processing import canny_processor, sobel_processor
from utils.dynaphos.utils import Map, load_params
from utils.network_servers import PhospheneServers

# ---------------- CONFIG ----------------
# All paths are relative to this script, so it can be started from any working directory.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "params.yaml")
CSV_FOLDER = os.path.join(BASE_DIR, "layout_data")  # written by generate_layouts.py
COORDS_POLAR_PATH = os.path.join(CSV_FOLDER, "coords_polar.csv")
CLASS_SELECTION_FILE = os.path.join(BASE_DIR, "server_settings.json")

THRESHOLD_HIGH = 200  # high threshold for canny
# Overwritten from the command line in main():
SHOW_IMG = False       # matplotlib preview of the raw / processed frame (adds latency)
SHOW_UI = True         # Tk settings window
VIDEO_SOURCE = 0       # webcam index or video file
YOLO_SEG_MODEL = "yolo11n-seg.pt"  # downloaded by ultralytics on first use
DEBUG_MODE = False     # if True, frames are processed but not streamed (no Unity needed)
DEBUG_UI = False       # verbose debug prints for settings/toggles


def debug_log(msg: str):
    if DEBUG_UI:
        print(f"[UI-DEBUG] {msg}")

# Mutable globals for settings (changed by UI widgets)
class Settings:
    filter = "none"  # choose 'canny' | 'sobel' | 'none'
    apply_nn = False
    show_labels = False
    show_phosphene_view = False
    num_phosphenes = 0
    threshold_high = 200

settings = Settings()


# Mutable globals for YOLO thresholds (changed by sliders)
class YOLOConfig:
    conf = 0.20
    iou = 0.45

yolo_config = YOLOConfig()
runtime_enabled_classes = {}


def save_all_settings(enabled_classes: dict):
    """Save selected classes and filter settings to JSON file."""
    try:
        classes_serializable = {str(k): bool(v) for k, v in enabled_classes.items()}
        data = {
            "classes": classes_serializable,
            "filter": settings.filter,
            "apply_nn": settings.apply_nn,
            "show_labels": settings.show_labels,
            "show_phosphene_view": settings.show_phosphene_view,
            "num_phosphenes": int(settings.num_phosphenes),
            "conf": float(yolo_config.conf),
            "iou": float(yolo_config.iou),
        }
        with open(CLASS_SELECTION_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"⚠️  Error saving settings: {e}")


def load_all_settings() -> tuple:
    """Load selected classes and filter settings from JSON file.
    Returns (enabled_classes_dict, filter_name, apply_nn_bool, show_labels_bool, show_phosphene_view_bool, num_phosphenes_int, conf, iou).
    """
    if os.path.exists(CLASS_SELECTION_FILE):
        try:
            with open(CLASS_SELECTION_FILE, 'r') as f:
                data = json.load(f)
                # Handle legacy format (just classes dict)
                if isinstance(data, dict) and "classes" in data:
                    raw_classes = data.get("classes", {})
                    classes = {int(k): bool(v) for k, v in raw_classes.items()}
                    return (
                        classes,
                        data.get("filter", "none"),
                        data.get("apply_nn", True),
                        data.get("show_labels", True),
                        data.get("show_phosphene_view", False),
                        data.get("num_phosphenes", 0),
                        data.get("conf", 0.20),
                        data.get("iou", 0.45),
                    )
                else:
                    # Legacy: was just enabled_classes dict
                    classes = {int(k): bool(v) for k, v in data.items()}
                    return (classes, "none", True, True, False, 0, 0.20, 0.45)
        except Exception as e:
            print(f"⚠️  Error loading settings: {e}")
    return ({}, "none", True, True, False, 0, 0.20, 0.45)


# Register save on exit
def _cleanup_on_exit():
    """Called when program exits to save settings."""
    save_all_settings(runtime_enabled_classes)

atexit.register(_cleanup_on_exit)


class SettingsWindow:
    """Tkinter settings UI using tk.Checkbutton for reliable state management."""
    def __init__(self, enabled_classes: dict, class_names: dict, max_phosphenes: int):
        self.root = tk.Tk()
        self.root.title("YOLO Settings")
        # Never open larger than the screen: cap the preferred size against the
        # available screen area (leaving room for panels/window decorations).
        pref_w, pref_h = 700, 800
        win_w = min(pref_w, self.root.winfo_screenwidth() - 80)
        win_h = min(pref_h, self.root.winfo_screenheight() - 120)
        self.root.geometry(f"{win_w}x{win_h}")
        self.root.minsize(420, 400)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.should_exit = False
        
        self.enabled_classes = enabled_classes
        self.class_names = class_names
        self.max_phosphenes = max(1, int(max_phosphenes))
        self.class_checkboxes = {}  # cls_id -> tk.Checkbutton widget
        
        # Filter selection
        frame_filter = ttk.LabelFrame(self.root, text="Filter", padding=6)
        frame_filter.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
        self.filter_var = tk.StringVar(value=settings.filter)
        for filt in ['none', 'canny', 'sobel']:
            rb = ttk.Radiobutton(
                frame_filter,
                text=filt,
                variable=self.filter_var,
                value=filt,
                command=lambda f=filt: self._on_filter_change(f),
            )
            # Laid out in a row rather than a column to keep the fixed part of
            # the window short, leaving more room for the class list.
            rb.pack(side=tk.LEFT, anchor=tk.W, padx=6)
        
        # YOLO toggles
        frame_nn = ttk.LabelFrame(self.root, text="YOLO Segmentation", padding=10)
        frame_nn.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
        self.nn_var = tk.BooleanVar(value=settings.apply_nn)
        tk.Checkbutton(frame_nn, text="Enable Neural Network", variable=self.nn_var,
                       command=self._on_nn_change).pack(anchor=tk.W, pady=2)
        
        self.labels_var = tk.BooleanVar(value=settings.show_labels)
        tk.Checkbutton(frame_nn, text="Show Labels", variable=self.labels_var,
                       command=self._on_labels_change).pack(anchor=tk.W, pady=2)

        self.phosphene_view_var = tk.BooleanVar(value=settings.show_phosphene_view)
        tk.Checkbutton(
            frame_nn,
            text="Display Phosphene View",
            variable=self.phosphene_view_var,
            command=self._on_phosphene_view_change,
        ).pack(anchor=tk.W, pady=2)

        frame_phos = ttk.LabelFrame(self.root, text="Phosphene Settings", padding=10)
        frame_phos.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
        ttk.Label(frame_phos, text="Number of Phosphenes:").grid(row=0, column=0, sticky=tk.W, padx=5)
        self.num_phos_var = tk.IntVar(value=settings.num_phosphenes)
        self.num_phos_label = ttk.Label(frame_phos, text=str(settings.num_phosphenes))
        self.num_phos_label.grid(row=0, column=2, sticky=tk.W, padx=5)
        self.num_phos_scale = tk.Scale(
            frame_phos,
            from_=1,
            to=self.max_phosphenes,
            orient=tk.HORIZONTAL,
            resolution=1,
            variable=self.num_phos_var,
            command=self._on_num_phos_change,
        )
        self.num_phos_scale.grid(row=0, column=1, sticky=tk.EW, padx=5)
        frame_phos.columnconfigure(1, weight=1)
        
        # Thresholds
        frame_thresh = ttk.LabelFrame(self.root, text="Thresholds", padding=10)
        frame_thresh.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
        
        ttk.Label(frame_thresh, text="CONF:").grid(row=0, column=0, sticky=tk.W, padx=5)
        self.conf_label = ttk.Label(frame_thresh, text=f"{yolo_config.conf:.2f}")
        self.conf_label.grid(row=0, column=2, sticky=tk.W)
        self.conf_scale = ttk.Scale(frame_thresh, from_=0.0, to=1.0, orient=tk.HORIZONTAL,
                                   command=self._on_conf_change)
        self.conf_scale.grid(row=0, column=1, sticky=tk.EW, padx=5)
        self.conf_scale.set(yolo_config.conf)
        
        ttk.Label(frame_thresh, text="IOU:").grid(row=1, column=0, sticky=tk.W, padx=5)
        self.iou_label = ttk.Label(frame_thresh, text=f"{yolo_config.iou:.2f}")
        self.iou_label.grid(row=1, column=2, sticky=tk.W)
        self.iou_scale = ttk.Scale(frame_thresh, from_=0.0, to=1.0, orient=tk.HORIZONTAL,
                                  command=self._on_iou_change)
        self.iou_scale.grid(row=1, column=1, sticky=tk.EW, padx=5)
        self.iou_scale.set(yolo_config.iou)
        frame_thresh.columnconfigure(1, weight=1)
        
        # Class selection with scrollable frame
        frame_classes = ttk.LabelFrame(self.root, text="Classes (Column-wise alphabetical)", padding=5)
        frame_classes.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.selected_count_label = ttk.Label(self.root, text="Selected classes: 0/0")
        self.selected_count_label.pack(side=tk.TOP, anchor=tk.W, padx=8, pady=2)
        
        # Scrollable canvas. It has no fixed height: it takes whatever vertical
        # space is left over, and scrolls when the class list does not fit.
        canvas = tk.Canvas(frame_classes, height=180, highlightthickness=0)
        scrollbar = ttk.Scrollbar(frame_classes, orient=tk.VERTICAL, command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        self.class_canvas = canvas
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Create class checkboxes with tk.Checkbutton (classic widget)
        self._create_class_checkboxes(scrollable_frame)
        self._update_selected_count_label()
        
        # Keep the inner frame as wide as the canvas and reflow the columns so
        # the checkboxes never overflow horizontally (only vertical scrolling).
        def _on_canvas_resize(event):
            canvas.itemconfigure(canvas_window, width=event.width)
            self._reflow_class_checkboxes(event.width)
        canvas.bind("<Configure>", _on_canvas_resize)
        
        self._bind_mousewheel(canvas)
        self._bind_mousewheel(scrollable_frame)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Bulk action buttons
        frame_bulk = ttk.Frame(self.root)
        frame_bulk.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=3)
        ttk.Button(frame_bulk, text="Select All", command=self.select_all).pack(side=tk.LEFT, padx=4)
        ttk.Button(frame_bulk, text="Deselect All", command=self.deselect_all).pack(side=tk.LEFT, padx=4)
        ttk.Button(self.root, text="Save & Close (Exit)", command=self.on_close).pack(side=tk.BOTTOM, pady=5)
    
    # Nominal pixel width of one checkbox column, used to decide how many
    # columns fit into the current canvas width.
    CLASS_COL_WIDTH = 190
    
    def _create_class_checkboxes(self, parent):
        """Create checkboxes in column-wise alphabetical order using tk.Checkbutton."""
        class_items = sorted(
            [(self.class_names.get(int(k), f"Class {k}"), int(k)) for k in self.class_names.keys()],
            key=lambda x: x[0].lower(),
        )
        self._class_items = class_items
        self._class_frame = parent
        self._class_num_cols = 0  # forces the first reflow to lay everything out
        
        def make_toggle_command(cls_id: int):
            def _cmd():
                # Explicitly flip the state instead of reading from widget (callback fires before var updates)
                current_state = self.enabled_classes.get(cls_id, False)
                new_state = not current_state
                self.enabled_classes[cls_id] = new_state
                
                # Update the variable to match
                self.class_checkboxes[cls_id].var.set(new_state)
                
                label = self.class_names.get(cls_id, f"Class {cls_id}")
                debug_log(f"toggle class_id={cls_id} label='{label}' -> {new_state}")
                self._update_selected_count_label()
                save_all_settings(self.enabled_classes)
            return _cmd
        
        for label, cls_id in class_items:
            var = tk.BooleanVar(value=self.enabled_classes.get(cls_id, False))
            cb = tk.Checkbutton(
                parent,
                text=label,
                variable=var,
                command=make_toggle_command(cls_id),
                anchor=tk.W,
            )
            cb.var = var  # Store var on widget for easy access
            self.class_checkboxes[cls_id] = cb
            self._bind_mousewheel(cb)
            
            # Explicitly set visual state based on loaded enabled_classes
            if self.enabled_classes.get(cls_id, False):
                cb.select()
            else:
                cb.deselect()
        
        self._reflow_class_checkboxes(self.CLASS_COL_WIDTH * 3)
    
    def _reflow_class_checkboxes(self, available_width: int):
        """Re-grid the class checkboxes so they fit into `available_width`.
        
        Column-wise alphabetical ordering is preserved; only the number of
        columns changes, so the list grows taller (and scrolls) instead of
        spilling off the right edge of the window.
        """
        items = getattr(self, "_class_items", None)
        if not items:
            return
        
        num_cols = max(1, int(available_width) // self.CLASS_COL_WIDTH)
        num_cols = min(num_cols, len(items))
        if num_cols == self._class_num_cols:
            return
        self._class_num_cols = num_cols
        
        parent = self._class_frame
        num_rows = (len(items) + num_cols - 1) // num_cols
        
        for col in range(num_cols):
            parent.columnconfigure(col, weight=1)
        for col in range(num_cols, parent.grid_size()[0]):
            parent.columnconfigure(col, weight=0, minsize=0)
        
        idx = 0
        for col in range(num_cols):
            for row in range(num_rows):
                if idx >= len(items):
                    continue
                _label, cls_id = items[idx]
                idx += 1
                self.class_checkboxes[cls_id].grid(
                    row=row, column=col, sticky=tk.EW, padx=6, pady=2
                )
    
    def _bind_mousewheel(self, widget):
        """Scroll the class canvas from the wheel (X11 buttons 4/5 and Windows/macOS deltas)."""
        def _on_wheel(event):
            if event.num == 4:
                delta = -1
            elif event.num == 5:
                delta = 1
            else:
                delta = -1 if event.delta > 0 else 1
            self.class_canvas.yview_scroll(delta, "units")
            return "break"
        
        widget.bind("<Button-4>", _on_wheel)
        widget.bind("<Button-5>", _on_wheel)
        widget.bind("<MouseWheel>", _on_wheel)
    
    def _update_selected_count_label(self):
        selected = sum(1 for v in self.enabled_classes.values() if v)
        total = len(self.class_names)
        self.selected_count_label.config(text=f"Selected classes: {selected}/{total}")
    
    def select_all(self):
        """Enable all classes - directly call .select() on each widget."""
        for cls_id, cb in self.class_checkboxes.items():
            cb.var.set(True)  # Update the variable first
            cb.select()  # Then visually select the widget
            self.enabled_classes[cls_id] = True
        debug_log("Select All clicked")
        self._update_selected_count_label()
        save_all_settings(self.enabled_classes)
    
    def deselect_all(self):
        """Disable all classes - directly call .deselect() on each widget."""
        for cls_id, cb in self.class_checkboxes.items():
            cb.var.set(False)  # Update the variable first
            cb.deselect()  # Then visually deselect the widget
            self.enabled_classes[cls_id] = False
        debug_log("Deselect All clicked")
        self._update_selected_count_label()
        save_all_settings(self.enabled_classes)
    
    def _on_filter_change(self, filt: str):
        """Handle filter radio button change."""
        settings.filter = filt
        self.filter_var.set(filt)
        debug_log(f"filter -> {settings.filter}")
        save_all_settings(self.enabled_classes)
    
    def _on_nn_change(self):
        """Handle NN checkbox change robustly across Tk callback timing differences."""
        new_state = bool(self.nn_var.get())
        # Some Tk backends fire the callback before var update, others after.
        # If unchanged, flip once so one click always changes runtime state.
        if new_state == settings.apply_nn:
            new_state = not settings.apply_nn
            self.nn_var.set(new_state)
        settings.apply_nn = new_state
        debug_log(f"apply_nn -> {settings.apply_nn}")
        save_all_settings(self.enabled_classes)
    
    def _on_labels_change(self):
        """Handle labels checkbox change - flip state explicitly before var updates."""
        settings.show_labels = not bool(settings.show_labels)
        self.labels_var.set(settings.show_labels)
        debug_log(f"show_labels -> {settings.show_labels}")
        save_all_settings(self.enabled_classes)

    def _on_phosphene_view_change(self):
        """Handle phosphene view checkbox change - flip state explicitly before var updates."""
        settings.show_phosphene_view = not bool(settings.show_phosphene_view)
        self.phosphene_view_var.set(settings.show_phosphene_view)
        debug_log(f"show_phosphene_view -> {settings.show_phosphene_view}")
        save_all_settings(self.enabled_classes)

    def _on_num_phos_change(self, val):
        """Handle phosphene count slider change."""
        count = max(1, min(self.max_phosphenes, int(float(val))))
        settings.num_phosphenes = count
        self.num_phos_var.set(count)
        self.num_phos_label.config(text=str(count))
        debug_log(f"num_phosphenes -> {settings.num_phosphenes}")
        save_all_settings(self.enabled_classes)
    
    def _on_conf_change(self, val):
        """Handle CONF slider change."""
        yolo_config.conf = float(val)
        self.conf_label.config(text=f"{yolo_config.conf:.2f}")
        debug_log(f"conf -> {yolo_config.conf:.3f}")
        save_all_settings(self.enabled_classes)
    
    def _on_iou_change(self, val):
        """Handle IOU slider change."""
        yolo_config.iou = float(val)
        self.iou_label.config(text=f"{yolo_config.iou:.2f}")
        debug_log(f"iou -> {yolo_config.iou:.3f}")
        save_all_settings(self.enabled_classes)
    
    def on_close(self):
        """Save settings and request full program shutdown."""
        save_all_settings(self.enabled_classes)
        self.should_exit = True
        self.root.destroy()
    
    def show(self):
        """Show the settings window."""
        self.root.mainloop()
    
    def update(self):
        """Update window (call from main thread)."""
        try:
            self.root.update()
        except:
            pass


def get_available_phosphene_count() -> int:
    polar_coords = np.loadtxt(COORDS_POLAR_PATH, delimiter=",")
    return int(polar_coords.shape[1])


def build_simulator(params, num_phosphenes: int = 0):
    polar_coords = np.loadtxt(COORDS_POLAR_PATH, delimiter=",")
    r = polar_coords[0, :]
    phi = polar_coords[1, :]
    max_count = len(r)
    if num_phosphenes <= 0:
        count = max_count
    else:
        count = max(1, min(int(num_phosphenes), max_count))
    r = r[:count]
    phi = phi[:count]
    coordinates_visual_field = Map(r=r, phi=phi)
    return GaussianSimulator(params, coordinates_visual_field)


def configure_low_latency_capture(cap, resolution):
    """Configure OpenCV capture for lower latency where backend/camera supports it."""
    try:
        # Important on Linux/V4L2 if supported: keep camera queue shallow.
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass

    try:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(resolution[0]))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(resolution[1]))
    except Exception:
        pass


def read_latest_frame(cap, max_grabs: int = 2):
    """Drop queued camera frames and return the freshest frame available."""
    for _ in range(max_grabs):
        if not cap.grab():
            break
    return cap.retrieve()


def preprocess_frame(frame_gray):
    frame_blur = cv2.GaussianBlur(frame_gray, (3, 3), 0)
    if settings.filter == "sobel":
        return sobel_processor(frame_blur)
    if settings.filter == "canny":
        return canny_processor(frame_blur, THRESHOLD_HIGH // 2, THRESHOLD_HIGH)
    if settings.filter == "none":
        return frame_blur
    raise ValueError(f"{settings.filter} is not a valid filter.")


def get_class_color(class_id: int):
    """Return a deterministic BGR color for a class id."""
    rng = np.random.default_rng(class_id + 12345)
    color = rng.integers(64, 256, size=3, dtype=np.uint8)
    return int(color[0]), int(color[1]), int(color[2])


def apply_yolo_segmentation(frame_bgr, yolo_model, class_names=None, enabled_classes=None):
    """Keep only segmented regions and color each class with a different color. Add class name labels.
    
    Args:
        frame_bgr: Input frame in BGR format
        yolo_model: YOLO model for segmentation
        class_names: Dict mapping class_id to class name
        enabled_classes: Dict mapping class_id to bool (whether to display)
    """
    segmented = np.zeros_like(frame_bgr)

    results = yolo_model.predict(frame_bgr, conf=yolo_config.conf, iou=yolo_config.iou, verbose=False)
    if not results:
        return segmented

    result = results[0]
    if result.masks is None or result.boxes is None or result.boxes.cls is None:
        return segmented

    masks = result.masks.data.detach().cpu().numpy()
    classes = result.boxes.cls.detach().cpu().numpy().astype(int)
    confs = result.boxes.conf.detach().cpu().numpy()
    boxes = result.boxes.xyxy.detach().cpu().numpy()
    h, w = frame_bgr.shape[:2]

    for mask, cls_id in zip(masks, classes):
        if cls_id not in enabled_classes or not enabled_classes[cls_id]:
            continue
        if mask.shape != (h, w):
            mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
        segmented[mask > 0.5] = get_class_color(int(cls_id))

    # Add labels on segmented regions using bounding box centers
    if settings.show_labels:
        for cls_id, conf, box in zip(classes, confs, boxes):
            if cls_id not in enabled_classes or not enabled_classes[cls_id]:
                continue
            x1, y1, x2, y2 = box
            cx, cy = int((x1 + x2) / 2), int((y1 + y2) / 2)

            # Use class name if available, otherwise use class ID
            if class_names and int(cls_id) in class_names:
                class_name = class_names[int(cls_id)]
            else:
                class_name = f"Class {int(cls_id)}"

            label = f"{class_name} ({conf:.2f})"
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = 0.5
            thickness = 1
            color = (255, 255, 255)  # white text

            text_size = cv2.getTextSize(label, font, font_scale, thickness)[0]
            text_x = cx - text_size[0] // 2
            text_y = cy + text_size[1] // 2

            cv2.putText(segmented, label, (text_x, text_y), font, font_scale, color, thickness)

    return segmented


def run_tcp_stream_server(params, tcp_server, args=None):
    global runtime_enabled_classes
    resolution = params["run"]["resolution"]
    fps = params["run"]["fps"]
    max_phosphenes = get_available_phosphene_count()
    yolo_model = None

    # Load saved state (single source of truth)
    saved_classes, saved_filter, saved_apply_nn, saved_show_labels, saved_show_phosphene_view, saved_num_phosphenes, saved_conf, saved_iou = load_all_settings()
    settings.filter = saved_filter
    settings.apply_nn = saved_apply_nn
    settings.show_labels = saved_show_labels
    settings.show_phosphene_view = saved_show_phosphene_view
    if int(saved_num_phosphenes) <= 0:
        settings.num_phosphenes = max_phosphenes
    else:
        settings.num_phosphenes = max(1, min(int(saved_num_phosphenes), max_phosphenes))
    yolo_config.conf = float(saved_conf)
    yolo_config.iou = float(saved_iou)
    if args is not None and args.filter:
        settings.filter = args.filter
    if args is not None and args.yolo:
        settings.apply_nn = True
    simulator = build_simulator(params, settings.num_phosphenes)

    # Always load model so NN can be toggled on/off at runtime from UI
    print(f"Loading YOLO segmentation model: {YOLO_SEG_MODEL}")
    yolo_model = YOLO(YOLO_SEG_MODEL)
    class_names = yolo_model.names  # Extract class name mapping

    # Initialize class state (default all False), then apply saved selections
    enabled_classes = {int(k): False for k in class_names.keys()}
    enabled_classes.update(saved_classes)

    loaded_on_ids = sorted([cid for cid, is_on in enabled_classes.items() if is_on])
    loaded_on_names = [class_names[cid] for cid in loaded_on_ids if cid in class_names]
    preview_ids = loaded_on_ids[:12]
    preview_names = loaded_on_names[:12]
    suffix = " ..." if len(loaded_on_ids) > 12 else ""
    debug_log(f"loaded class IDs set to True: {preview_ids}{suffix}")
    debug_log(f"loaded class labels set to True: {preview_names}{suffix}")

    runtime_enabled_classes = enabled_classes
    debug_log(
        f"loaded settings: filter={settings.filter}, apply_nn={settings.apply_nn}, show_labels={settings.show_labels}, show_phosphene_view={settings.show_phosphene_view}, num_phosphenes={settings.num_phosphenes}, conf={yolo_config.conf:.2f}, iou={yolo_config.iou:.2f}, selected={sum(1 for v in enabled_classes.values() if v)}"
    )

    fig = None
    axes = None
    settings_window = None
    phosphene_window_name = "Phosphene Representation"
    phosphene_window_open = False
    phosphene_window_created = False
    phosphene_simulator = build_simulator(params, settings.num_phosphenes)
    active_num_phosphenes = settings.num_phosphenes

    if SHOW_IMG:
        fig, axes = plt.subplots(1, 3, figsize=(15, 5))
        fig.suptitle("Phosphene Simulation - Live Feed", fontsize=12)
    if SHOW_UI and class_names:
        settings_window = SettingsWindow(enabled_classes, class_names, max_phosphenes=max_phosphenes)

    if DEBUG_MODE:
        print("Dry run: frames are processed but not streamed.")
    else:
        print("Waiting for Unity TCP connection...")

    prev = 0.0
    debug_prev = 0.0
    cap = cv2.VideoCapture(VIDEO_SOURCE)
    configure_low_latency_capture(cap, resolution)

    try:
        while True:
            ret, frame = read_latest_frame(cap)
            if not ret:
                print("Camera read failed. Reinitializing capture...")
                cap.release()
                cap = cv2.VideoCapture(VIDEO_SOURCE)
                configure_low_latency_capture(cap, resolution)
                continue

            if settings_window is not None and settings_window.should_exit:
                print("Save & Close requested. Exiting program...")
                return

            if time.time() - prev <= 1 / fps:
                continue
            prev = time.time()

            frame = cv2.resize(frame, resolution)
            frame_show = np.copy(frame)

            if settings_window is not None:
                settings_window.update()

            if settings.num_phosphenes != active_num_phosphenes:
                active_num_phosphenes = settings.num_phosphenes
                simulator = build_simulator(params, active_num_phosphenes)
                phosphene_simulator = build_simulator(params, active_num_phosphenes)
                print(f"Updated phosphene count to {active_num_phosphenes}")

            if time.time() - debug_prev > 2.0:
                debug_prev = time.time()
                selected_count = sum(1 for v in enabled_classes.values() if v)
                debug_log(
                    f"runtime state: apply_nn={settings.apply_nn}, filter={settings.filter}, selected={selected_count}, num_phosphenes={active_num_phosphenes}"
                )

            if settings.apply_nn and yolo_model is not None:
                nn_frame = apply_yolo_segmentation(frame, yolo_model, class_names, enabled_classes)
            else:
                nn_frame = frame

            frame_gray = cv2.cvtColor(nn_frame, cv2.COLOR_BGR2GRAY)
            processed_img = preprocess_frame(frame_gray)

            if SHOW_IMG and axes is not None:
                axes[0].imshow(frame_show)
                axes[0].set_title("Raw Frame")
                axes[0].axis("off")

                if settings.apply_nn:
                    axes[1].imshow(cv2.cvtColor(nn_frame, cv2.COLOR_BGR2RGB))
                    axes[1].set_title("YOLO Segmented (Class Colors)")
                    axes[1].axis("off")

                    axes[2].imshow(processed_img, cmap="gray")
                    axes[2].set_title(f"Filtered ({settings.filter})")
                    axes[2].axis("off")
                else:
                    axes[1].imshow(frame_show)
                    axes[1].set_title("NN Disabled")
                    axes[1].axis("off")
                    axes[2].imshow(processed_img, cmap="gray")
                    axes[2].set_title(f"Filtered ({settings.filter})")
                    axes[2].axis("off")

                plt.pause(0.001)
                for ax in axes:
                    ax.cla()

            stim_pattern = (
                simulator.sample_stimulus(processed_img, rescale=True)
                .squeeze()
            )

            if settings.show_phosphene_view:
                phosphene_canvas = phosphene_simulator(stim_pattern).detach().cpu().numpy()
                phosphene_canvas = np.clip(phosphene_canvas, 0.0, 1.0)
                phosphene_u8 = (phosphene_canvas * 255).astype(np.uint8)
                if not phosphene_window_created:
                    cv2.namedWindow(phosphene_window_name, cv2.WINDOW_NORMAL)
                    cv2.resizeWindow(phosphene_window_name, 400, 400)
                    phosphene_window_created = True
                cv2.imshow(phosphene_window_name, phosphene_u8)
                phosphene_window_open = True
            elif phosphene_window_open:
                cv2.destroyWindow(phosphene_window_name)
                phosphene_window_open = False
                phosphene_window_created = False

            stim_pattern = stim_pattern.cpu().numpy().astype(np.float32).reshape(-1)
            if stim_pattern.size < max_phosphenes:
                # The app expects one value per phosphene of the layout: the others are off.
                stim_pattern = np.pad(stim_pattern, (0, max_phosphenes - stim_pattern.size))
            tcp_server.send_line(stim_pattern)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                print("Stream loop stopped by keyboard keypress.")
                return

    finally:
        cap.release()
        if phosphene_window_open or phosphene_window_created:
            cv2.destroyWindow(phosphene_window_name)
        cv2.destroyAllWindows()
        if fig is not None:
            plt.close(fig)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", default="0", help="webcam index or video file (default: %(default)s)")
    p.add_argument("--model", default=YOLO_SEG_MODEL, help="ultralytics segmentation model (default: %(default)s)")
    p.add_argument("--filter", choices=["none", "canny", "sobel"],
                   help="initial filter (default: the last one used)")
    p.add_argument("--yolo", action="store_true", help="start with YOLO segmentation on")
    p.add_argument("--preview", action="store_true", help="show raw / segmented / filtered frames (slower)")
    p.add_argument("--no-ui", action="store_true", help="no settings window")
    p.add_argument("--dry-run", action="store_true", help="process frames without streaming them")
    p.add_argument("--verbose", action="store_true", help="print every settings change")
    return p.parse_args()


def main():
    global SHOW_IMG, SHOW_UI, VIDEO_SOURCE, YOLO_SEG_MODEL, DEBUG_MODE, DEBUG_UI
    args = parse_args()
    VIDEO_SOURCE = int(args.source) if args.source.isdigit() else args.source
    YOLO_SEG_MODEL = args.model
    SHOW_IMG = args.preview
    SHOW_UI = not args.no_ui
    DEBUG_MODE = args.dry_run
    DEBUG_UI = args.verbose

    params = load_params(CONFIG_PATH)
    servers = PhospheneServers.from_params(params, csv_folder=CSV_FOLDER, debug_mode=DEBUG_MODE)
    servers.start()
    try:
        run_tcp_stream_server(params, servers.tcp, args)
    finally:
        servers.stop()


if __name__ == "__main__":
    main()
