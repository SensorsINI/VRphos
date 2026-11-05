# Performance benchmark

Both apps contain a benchmark that steps through phosphene counts and measures frame rate, frame
times, latency and memory for each. `python/benchmark/run_benchmark.py` starts it over adb and
collects the results; it only needs the Python standard library (matplotlib for `--plot`).

## Run

Connect the device with USB debugging enabled (`adb devices` lists it), install the app, then:

```bash
cd python/benchmark
python run_benchmark.py --plot
python run_benchmark.py --counts 1000,10000,50000,100000 --duration 20 --repeats 3 --label sobel
python run_benchmark.py --implant utah --counts 100,400,800
python run_benchmark.py --input random      # rendering only, without the camera
python run_benchmark.py -s <serial> ...     # several devices connected
python run_benchmark.py --listen            # collect a run started from the in-app menu
```

On the Quest, wear the headset (or cover the proximity sensor) during the run: the app pauses
while the headset is asleep.

The script restarts the app with the settings, prints one row per phosphene count, and writes
`results/<device>_<run>/`:

- `results.csv`: one row per phosphene count (columns below)
- `run_info.json`: device, GPU, graphics API, input, processing mode, display Hz, settings
- `bench_<run>.csv`, `bench_<run>_frames.csv`: written by the app and pulled from the device
  (`frames` = every single frame)
- `benchmark.png` with `--plot`

Default counts: 100, 500, 1000, 2000, 5000, 10000, max, 20000, 30000, 50000 (any number works;
`max` is the size of the layout). Options: `--warmup` and `--duration` (seconds per count),
`--repeats`, `--implant valid_3|utah`, `--fps max|unlimited|keep|<n>`, `--no-meminfo`,
`--label`; see `python run_benchmark.py --help`.

### Phosphene counts and layouts

Below the layout size the benchmark uses an evenly spread subset of the layout. Above it, the
extra phosphenes are copies of real ones with a new draw of the Dynaphos electrode position noise
(0.4 mm on the cortex, converted with the local magnification). With the default `--layout exact`
the simulation, camera sampling and drawing all handle exactly N phosphenes; `--layout grow` keeps
the real layout and only limits drawing (like lowering *Phosphenes* in the menu). The app's own
settings are restored after the run.

### What is measured

The app uses the input and processing mode it was last left in (camera, Sobel, YOLO, tool pack,
server). Set these in the menu first, or use `--input random`.
While a run started by the script is active, the settings menu stays closed; a run started from the
menu is cancelled by opening the menu.

On Android the app normally runs at 30 fps (Unity's mobile default); during a run the frame cap is
set to the display refresh rate (`--fps max`) so the headroom is visible. On the Quest the headset
sets the frame rate (72 Hz by default).

After each count the app pauses 2 s (`--mem-pause`) while the script reads `dumpsys meminfo`.

## Columns

| column | meaning |
|---|---|
| `n`, `layout_n` | phosphenes drawn; phosphenes simulated / sampled |
| `fps`, `ft_*_ms` | frames per second; frame interval mean / p50 / p95 / p99 / max |
| `slow_frames` | frames longer than 1.5× the display interval |
| `cpu_main_ms`, `cpu_render_ms`, `gpu_ms` | Unity FrameTimingManager (-1 if not available) |
| `sim_ms`, `sim_hz` | simulation step CPU time and rate |
| `draw_ms` | CPU time to place the phosphene quads and issue the draw calls |
| `lat_proc_*` | camera frame grabbed → stimulation ready (readback + processing + sampling) |
| `lat_e2e_*` | camera frame grabbed (or TCP line received) → end of the frame that shows it |
| `pss_mb`, `native_heap_mb` | app process memory (Android PSS, native heap), read by the app |
| `mem_system_mb`, `mem_total_mb`, `mem_gc_mb`, `mem_gfx_mb` | Unity memory counters (-1 where a release build does not provide them) |
| `gc_alloc_kb_per_frame`, `gc_collections` | managed allocations per frame (development builds only) and GC runs |
| `dumpsys_*_mb` | `dumpsys meminfo` App Summary: total PSS, Java / native heap, code, stack, graphics, other |
| `thermal_status`, `thermal_headroom`, `battery_level` | Android thermal state (throttling indicator) and battery |

Latency excludes the camera exposure before the app receives the frame and the display pipeline
after the frame is submitted (compositor, scan-out). With `--input random` the e2e latency only
shows the wait for the next simulation step. On the Quest, `cpu_main_ms` includes the wait for the
XR frame and is therefore close to the frame interval; GPU times are not reported by the headset.

## Without the script

The app prints its results to logcat (tag `VRphosBench`) and writes the CSV files to
`/sdcard/Android/data/com.INI.VRphos/files/benchmarks/`. A run can also be started by hand:

```bash
adb logcat -c && adb logcat -v raw -s VRphosBench:I &
adb shell am start -S -n com.INI.VRphos/com.unity3d.player.UnityPlayerGameActivity \
    --es vrphos_bench "'counts=100,1000,max;duration=10;quit=1'"
```

(Android app: activity `com.unity3d.player.appui.AppUIGameActivity`.)
Config keys: `counts`, `warmup`, `duration`, `repeats`, `delay`, `pause`, `input` (keep/random),
`implant`, `layout` (exact/grow), `fps`, `raw` (per-frame CSV, 0/1), `quit` (close the app at the
end, 0/1), `label`.
