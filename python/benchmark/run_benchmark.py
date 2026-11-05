#!/usr/bin/env python3
"""
Run the VRphos performance benchmark on a Meta Quest or Android phone over adb and collect
the results (fps, frame times and latency per phosphene count).

The app side is the PerformanceBenchmark component built into both VRphos apps (see docs/benchmark.md).
This script:
  1. clears logcat and starts reading the "VRphosBench" tag,
  2. (re)starts the app with the benchmark settings as intent extra "vrphos_bench",
  3. prints one line per phosphene count as results arrive,
  4. after each count, records 'dumpsys meminfo' of the app (the app pauses meanwhile),
  5. saves results.csv + run_info.json and pulls the CSV files the app wrote on the device,
  6. optionally plots fps / latency / memory against the phosphene count.

Counts above the real layout size (all valid sites: 13062, Utah arrays: 800) are extrapolated by
the app with the dynaphos electrode position noise; see docs/benchmark.md.

Examples:
  python run_benchmark.py                                   # default counts, 10 s each
  python run_benchmark.py --counts 1000,10000,50000,100000 --duration 20 --repeats 3
  python run_benchmark.py -s <adb serial> --label quest3s_sobel --plot
  python run_benchmark.py --implant utah --counts 100,400,800
  python run_benchmark.py --listen                          # collect a run started from the in-app menu

Only the Python standard library is needed (matplotlib for --plot).
"""

import argparse
import csv
import json
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

TAG = "VRphosBench"
EXTRA = "vrphos_bench"
DEFAULT_PACKAGE = "com.INI.VRphos"
DEFAULT_ACTIVITY = "com.unity3d.player.UnityPlayerGameActivity"
DEFAULT_COUNTS = "100,500,1000,2000,5000,10000,max,20000,30000,50000"

# Columns printed live (all columns are saved).
LIVE_COLUMNS = [
    ("n", "N", "{:>6}"),
    ("fps", "fps", "{:>6.1f}"),
    ("ft_p50_ms", "ft p50", "{:>7.2f}"),
    ("ft_p95_ms", "ft p95", "{:>7.2f}"),
    ("slow_frames", "slow", "{:>5}"),
    ("cpu_main_ms", "cpu", "{:>6.2f}"),
    ("gpu_ms", "gpu", "{:>6.2f}"),
    ("sim_ms", "sim", "{:>6.2f}"),
    ("draw_ms", "draw", "{:>6.2f}"),
    ("lat_e2e_p50_ms", "e2e p50", "{:>7.1f}"),
    ("lat_e2e_p95_ms", "e2e p95", "{:>7.1f}"),
    ("pss_mb", "pss MB", "{:>7.0f}"),
    ("dumpsys_graphics_mb", "gfx MB", "{:>7.0f}"),
    ("gc_alloc_kb_per_frame", "gcKB/f", "{:>7.1f}"),
    ("thermal_status", "therm", "{:>5}"),
]


# +-------------------------+
# |           ADB           |
# +-------------------------+

class Adb:
    def __init__(self, serial=None):
        self.exe = shutil.which("adb")
        if self.exe is None:
            sys.exit("adb not found on PATH (install Android platform-tools).")
        self.base = [self.exe] + (["-s", serial] if serial else [])

    def run(self, *args, check=True, timeout=60):
        res = subprocess.run(self.base + list(args), capture_output=True, text=True, timeout=timeout)
        if check and res.returncode != 0:
            raise RuntimeError(f"adb {' '.join(args)} failed: {res.stderr.strip() or res.stdout.strip()}")
        return res.stdout

    def shell(self, command, check=True, timeout=60):
        # One string, so the device shell sees the quoting we chose.
        return self.run("shell", command, check=check, timeout=timeout)

    def popen_logcat(self):
        return subprocess.Popen(self.base + ["logcat", "-v", "raw", "-s", f"{TAG}:I"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)


def check_device(adb):
    state = adb.run("get-state", check=False).strip()
    if state != "device":
        devices = subprocess.run([adb.exe, "devices", "-l"], capture_output=True, text=True).stdout
        sys.exit(f"No usable adb device (state '{state or 'none'}').\n{devices}"
                 "Use -s <serial> if several devices are connected.")
    model = adb.shell("getprop ro.product.model").strip()
    return model


def resolve_activity(adb, package):
    """Find the launch activity of the package, e.g. com.INI.VRphos/com.unity3d.player.UnityPlayerGameActivity."""
    queries = [
        f"cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER {package}",
        f"cmd package resolve-activity --brief -a android.intent.action.MAIN -c com.oculus.intent.category.VR {package}",
        f"cmd package resolve-activity --brief {package}",
    ]
    for q in queries:
        out = adb.shell(q, check=False).strip().splitlines()
        if out and "/" in out[-1] and "No activity" not in out[-1]:
            return out[-1].strip()
    return f"{package}/{DEFAULT_ACTIVITY}"


# +-------------------------+
# |         MEMORY          |
# +-------------------------+

MEMINFO_ROWS = {
    "Java Heap": "dumpsys_java_mb",
    "Native Heap": "dumpsys_native_mb",
    "Code": "dumpsys_code_mb",
    "Stack": "dumpsys_stack_mb",
    "Graphics": "dumpsys_graphics_mb",
    "Private Other": "dumpsys_private_other_mb",
    "System": "dumpsys_system_mb",
    "TOTAL PSS": "dumpsys_total_pss_mb",
    "TOTAL": "dumpsys_total_pss_mb",   # older Android versions
}


def meminfo(adb, package):
    """'App Summary' of dumpsys meminfo in MB (PSS). Graphics = GPU buffers / textures (GL/EGL mtrack)."""
    out = adb.shell(f"dumpsys meminfo {shlex.quote(package)}", check=False, timeout=30)
    result = {key: -1 for key in set(MEMINFO_ROWS.values())}
    in_summary = False
    for line in out.splitlines():
        if "App Summary" in line:
            in_summary = True
            continue
        if not in_summary:
            continue
        m = re.match(r"\s*([A-Za-z ]+):\s+(\d+)", line)
        if m and m.group(1).strip() in MEMINFO_ROWS:
            result[MEMINFO_ROWS[m.group(1).strip()]] = round(int(m.group(2)) / 1024.0, 1)
        if line.strip().startswith("TOTAL"):
            break
    return result


# +-------------------------+
# |         RUNNING         |
# +-------------------------+

def build_config(args):
    parts = [
        f"counts={args.counts}",
        f"warmup={args.warmup}",
        f"duration={args.duration}",
        f"repeats={args.repeats}",
        f"delay={args.delay}",
        f"input={args.input}",
        f"layout={args.layout}",
        *([f"implant={args.implant}"] if args.implant else []),
        f"pause={0 if args.no_meminfo else args.mem_pause}",
        f"fps={args.fps}",
        f"raw={0 if args.no_raw else 1}",
        f"quit={0 if args.keep_open else 1}",
    ]
    if args.label:
        if any(c in args.label for c in ";="):
            sys.exit("--label must not contain ';' or '='.")
        parts.append(f"label={args.label}")
    return ";".join(parts)


def estimate_timeout(args):
    n_counts = len([c for c in args.counts.split(",") if c.strip()])
    per_run = n_counts * (args.warmup + args.duration + 1.0) * args.repeats
    pause = 0 if args.no_meminfo else args.mem_pause
    return args.delay + per_run + n_counts * args.repeats * pause + 90.0  # + app start, camera permission


def reader_thread(proc, lines):
    for line in proc.stdout:
        lines.put(line.rstrip("\n"))
    lines.put(None)


def _width(fmt):
    return len(fmt.format(0.0 if "f" in fmt else 0))


def fmt_row(r, columns=None):
    # -1 means "not available" in the app's output.
    cells = []
    for key, _, fmt in columns or LIVE_COLUMNS:
        v = r.get(key)
        if v is None or v == -1:
            cells.append(f"{'-':>{_width(fmt)}}")
        else:
            cells.append(fmt.format(v))
    return " ".join(cells)


def header_row(columns=None):
    return " ".join(f"{title:>{_width(fmt)}}" for _, title, fmt in columns or LIVE_COLUMNS)


def collect(adb, args, launch_component, config):
    adb.run("logcat", "-c", check=False)
    proc = adb.popen_logcat()
    lines = queue.Queue()
    threading.Thread(target=reader_thread, args=(proc, lines), daemon=True).start()

    if launch_component:
        cmd = f"am start -S -n {shlex.quote(launch_component)} --es {EXTRA} {shlex.quote(config)}"
        print(f"Launching: adb shell {cmd}")
        out = adb.shell(cmd, check=False)
        if "Error" in out:
            proc.kill()
            sys.exit(f"Could not start the app:\n{out}")
    else:
        print("Listening: start the benchmark from the in-app settings menu ('Run performance benchmark').")

    timeout = args.timeout or (estimate_timeout(args) if launch_component else 3600.0)

    def add_meminfo(r):
        # The app waits 'pause' seconds after a result, with the layout of this count.
        if not args.no_meminfo:
            r.update(meminfo(adb, args.package))

    try:
        return consume(lines, timeout, add_meminfo, source="logcat")
    finally:
        proc.kill()


def consume(lines, timeout, on_result=None, columns=None, source="log"):
    """
    Read benchmark lines ("START {json}", "RESULT {json}", ...) from a queue until DONE / ABORT /
    timeout, printing a live table. on_result(r) can add columns to each result before it is stored.
    A None in the queue means the source ended. Returns (info, results, done).
    """
    deadline = time.time() + timeout
    info, results, done = None, [], None

    try:
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                print(f"\nTimed out after {timeout:.0f} s without DONE.", file=sys.stderr)
                break
            try:
                line = lines.get(timeout=min(remaining, 5.0))
            except queue.Empty:
                continue
            if line is None:
                print(f"\n{source} ended (app or device gone?).", file=sys.stderr)
                break

            kind, _, payload = line.partition(" ")
            # Ignore lines from an older run that was still going when this one started.
            if kind in ("PROGRESS", "RESULT", "DONE", "ABORT") and info is None:
                continue
            if kind == "RESULT" and json.loads(payload).get("run_id") != info.get("run_id"):
                continue
            if kind == "START":
                info = json.loads(payload)
                g = info.get  # tolerate older/newer app builds with other fields
                print(f"\nRun {g('run_id')} on {g('device_model')} ({g('gpu')}, {g('graphics_api')})")
                print(f"  input={g('input')} processing={g('processing')} "
                      f"implant={g('implant_name', '-')} ({g('layout_count', g('n_max'))} real, {g('layout_mode', '-')}) "
                      f"display={g('display_hz', 0):.0f} Hz target_fps={g('target_fps', 0):.0f} xr={g('xr_active')}")
                if not info.get("frame_timing_stats"):
                    print("  note: Frame Timing Stats is off in the build, so cpu/gpu columns are empty")
                print("\n" + header_row(columns))
            elif kind == "PROGRESS":
                print(f"  ... {payload}", end="\r", flush=True)
            elif kind == "RESULT":
                r = json.loads(payload)
                if on_result:
                    on_result(r)
                results.append(r)
                print(" " * 40, end="\r")
                print(fmt_row(r, columns))
            elif kind == "DONE":
                done = json.loads(payload)
                print(f"\nDone ({done['steps']} steps).")
                break
            elif kind == "ABORT":
                print(f"\nAborted: {payload}", file=sys.stderr)
                _, _, rest = payload.partition(" {")
                if rest:
                    try:
                        done = json.loads("{" + rest)
                    except json.JSONDecodeError:
                        pass
                break
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)

    return info, results, done


# +-------------------------+
# |         OUTPUT          |
# +-------------------------+

def save(adb, args, model, info, results, done):
    run_id = (info or {}).get("run_id") or datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_model = "".join(c if c.isalnum() or c in "-_" else "_" for c in model)
    out_dir = Path(args.out) / f"{safe_model}_{run_id}" if not args.label else \
        Path(args.out) / f"{safe_model}_{args.label}_{run_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    if info:
        (out_dir / "run_info.json").write_text(json.dumps(info, indent=2))
    if results:
        with open(out_dir / "results.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)

    if done and not args.no_pull:
        for key in ("summary_csv", "frames_csv"):
            remote = done.get(key)
            if not remote:
                continue
            local = out_dir / Path(remote).name
            res = subprocess.run(adb.base + ["pull", remote, str(local)], capture_output=True, text=True)
            if res.returncode != 0:
                print(f"Could not pull {remote}: {res.stderr.strip()} (results.csv has the same summary)",
                      file=sys.stderr)

    print(f"Saved to {out_dir}")
    return out_dir


def plot(out_dir, results, model):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed, skipping --plot", file=sys.stderr)
        return

    def series(key):
        pts = {}
        for r in results:
            if r[key] != -1:
                pts.setdefault(r["n"], []).append(r[key])
        ns = sorted(pts)
        return ns, [sum(pts[n]) / len(pts[n]) for n in ns]

    fig, axes = plt.subplots(1, 4, figsize=(20, 4.2))
    ax = axes[0]
    ax.plot(*series("fps"), "o-", label="fps")
    if results[0].get("display_hz", 0) > 0:
        ax.axhline(results[0]["display_hz"], color="grey", ls="--", lw=1, label="display Hz")
    ax.set(title="Frame rate", xlabel="phosphenes", ylabel="fps")
    ax.legend()

    ax = axes[1]
    for key, label in [("ft_p50_ms", "frame p50"), ("ft_p95_ms", "frame p95"),
                       ("cpu_main_ms", "CPU main"), ("gpu_ms", "GPU"), ("sim_ms", "sim step"), ("draw_ms", "draw")]:
        ns, ys = series(key)
        if ns:
            ax.plot(ns, ys, "o-", label=label)
    ax.set(title="Frame / CPU / GPU time", xlabel="phosphenes", ylabel="ms")
    ax.legend()

    ax = axes[2]
    for key, label in [("lat_e2e_p50_ms", "input→frame p50"), ("lat_e2e_p95_ms", "input→frame p95"),
                       ("lat_proc_p50_ms", "capture→stim p50")]:
        ns, ys = series(key)
        if ns:
            ax.plot(ns, ys, "o-", label=label)
    ax.set(title="Latency", xlabel="phosphenes", ylabel="ms")
    ax.legend()

    ax = axes[3]
    for key, label in [("dumpsys_total_pss_mb", "total PSS"), ("pss_mb", "PSS (app)"),
                       ("dumpsys_graphics_mb", "graphics"), ("gpu_proc_mb", "GPU (nvidia-smi)"),
                       ("dumpsys_native_mb", "native heap"), ("mem_total_mb", "Unity total")]:
        ns, ys = series(key) if key in results[0] else ([], [])
        if ns:
            ax.plot(ns, ys, "o-", label=label)
    ax.set(title="Memory", xlabel="phosphenes", ylabel="MB")
    ax.legend()

    for ax in axes:
        ax.set_xscale("log")

    fig.suptitle(model)
    fig.tight_layout()
    path = out_dir / "benchmark.png"
    fig.savefig(path, dpi=120)
    print(f"Plot: {path}")


# +-------------------------+
# |          MAIN           |
# +-------------------------+

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-s", "--serial", help="adb device serial (when several devices are connected)")
    p.add_argument("--package", default=DEFAULT_PACKAGE)
    p.add_argument("--activity", help="launch component pkg/activity (default: resolved from the device)")
    p.add_argument("--counts", default=DEFAULT_COUNTS, help="phosphene counts, 'max' = all (default: %(default)s)")
    p.add_argument("--warmup", type=float, default=2.0, help="seconds per count before measuring")
    p.add_argument("--duration", type=float, default=10.0, help="seconds measured per count")
    p.add_argument("--repeats", type=int, default=1, help="run the whole list this many times")
    p.add_argument("--delay", type=float, default=5.0, help="seconds after app start before the first count")
    p.add_argument("--input", choices=["keep", "random"], default="keep",
                   help="keep = the app's input (camera/server), random = random stimulation")
    p.add_argument("--layout", choices=["exact", "grow"], default="exact",
                   help="exact = simulate/sample/draw exactly N phosphenes; grow = keep the 10k layout, "
                        "N only limits drawing (counts above 10k still grow the layout)")
    p.add_argument("--implant", default="",
                   help="phosphene layout to benchmark: valid_3 (all valid sites) or utah (default: the app's current)")
    p.add_argument("--no-meminfo", action="store_true", help="don't record 'dumpsys meminfo' after each count")
    p.add_argument("--mem-pause", type=float, default=2.0,
                   help="seconds the app waits after each count while dumpsys meminfo runs")
    p.add_argument("--fps", default="max", help="Android frame cap during the run: max | <number> | keep")
    p.add_argument("--label", default="", help="stored with the results and used in the folder name")
    p.add_argument("--no-raw", action="store_true", help="don't write the per-frame CSV on the device")
    p.add_argument("--keep-open", action="store_true", help="don't close the app when done")
    p.add_argument("--listen", action="store_true",
                   help="don't launch the app, collect a run started from the in-app menu")
    p.add_argument("--timeout", type=float, help="seconds to wait for the run (default: estimated)")
    p.add_argument("--out", default=str(Path(__file__).resolve().parent / "results"), help="output folder")
    p.add_argument("--no-pull", action="store_true", help="don't pull the CSV files from the device")
    p.add_argument("--plot", action="store_true", help="save benchmark.png (needs matplotlib)")
    args = p.parse_args()

    adb = Adb(args.serial)
    model = check_device(adb)
    print(f"Device: {model}")

    component, config = None, None
    if not args.listen:
        component = args.activity or resolve_activity(adb, args.package)
        config = build_config(args)

    info, results, done = collect(adb, args, component, config)
    if not results:
        sys.exit("No results received.")

    out_dir = save(adb, args, model, info, results, done)
    if args.plot:
        plot(out_dir, results, model)


if __name__ == "__main__":
    main()
