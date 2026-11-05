# VRphos

VRphos simulates the phosphene vision produced by a cortical visual prosthesis, in real time, on a
**Meta Quest** headset or an **Android phone** (in a VR viewer). It runs the
[Dynaphos](https://doi.org/10.7554/eLife.85812) phosphene model (cortical magnification,
current-dependent size and brightness, thresholds, temporal dynamics) for up to tens of thousands
of electrodes at display rate.

The camera image can be turned into stimulation on the device itself (brightness, Sobel edges,
YOLO segmentation, or your own network as a *tool pack*), or by a Python server on a PC that
streams electrode currents to the app over TCP.

```
 on the device                                   or from a PC
 ┌────────────────────────────────────────┐      ┌──────────────────────────────────┐
 │ headset / phone camera                 │      │ webcam → YOLO / filters → dynaphos│
 │   → processing (Sobel, YOLO, tool pack)│      │   sampling → currents (A)         │
 │   → currents per electrode             │      └───────────────┬──────────────────┘
 │   → Dynaphos simulation → phosphenes   │◄──── TCP, one line per frame ─┘
 └────────────────────────────────────────┘      (+ HTTP: phosphene layout)
```

## Contents

| folder | what |
|---|---|
| [`apps/`](apps) | Release APKs: `VRphos_MetaQuest.apk` (Quest 3 / 3S) and `VRphos_Android.apk` (phone) |
| [`docs/`](docs) | Installation, app usage, TCP server, tool packs, implant layouts, benchmark |
| [`python/`](python) | Stimulation server (`vrphos_server.py`), layout generator, tool pack tools, benchmark script |
| [`unity_tool_pack_converter/`](unity_tool_pack_converter) | Minimal Unity project that converts ONNX models into tool packs |

## Quick start

1. **Install an app** ([docs/installation.md](docs/installation.md)):
   ```bash
   adb install -r apps/VRphos_MetaQuest.apk    # or apps/VRphos_Android.apk on a phone
   ```
2. **Run it with the device camera**: open the settings menu (Quest: left controller menu button;
   phone: gear button), set *Input* to *Headset camera* / *Phone camera*, pick a *Processing* mode.
   See [docs/using_the_app.md](docs/using_the_app.md).
3. **Or stream from a PC** ([docs/tcp_server.md](docs/tcp_server.md)):
   ```bash
   cd python && ./setup.sh && source .venv/bin/activate
   python vrphos_server.py
   ```
   and set *Input* to *Remote server* in the app.

## Documentation

- [Installation](docs/installation.md): sideloading the APKs on the Quest and on Android phones
- [Using the app](docs/using_the_app.md): controls, settings menu, input and processing modes
- [TCP stimulation server](docs/tcp_server.md): running `vrphos_server.py`, the network protocol,
  writing your own stimulation source in any language
- [Tool packs](docs/tool_packs.md): adding your own scene-processing network without rebuilding the app
- [Implant layouts](docs/layouts.md): the electrode layouts and how they are generated
- [Performance benchmark](docs/benchmark.md): measuring fps, latency and memory over phosphene counts via adb

## Requirements

- Meta Quest 3 or 3S (Horizon OS with passthrough camera access), or an Android phone
  (Android 10+, arm64, Vulkan or OpenGL ES 3)
- For the PC tools: Python 3.11+, `adb` (Android platform tools)
- For converting tool packs: Unity 6 (6000.2 or newer) with the Inference Engine package (installed
  automatically by the converter project)

## Acknowledgements

VRphos continues the semester project [MSP-2025-09-VRphos](https://github.com/SensorsINI/MSP-2025-09-VRphos)
(Sensors Group, Institute of Neuroinformatics, University of Zurich and ETH Zurich).

The phosphene model is [Dynaphos](https://github.com/neuralcodinglab/dynaphos)
(Neural Coding Lab, Donders Institute); `python/utils/dynaphos/` contains an adapted copy of it, and
the apps implement the same model. If you use VRphos, please also cite the Dynaphos paper:

> van der Grinten, M., de Ruyter van Steveninck, J., Lozano, A., Pijnacker, L., Rueckauer, B.,
> Roelfsema, P., van Gerven, M., van Wezel, R., Güçlü, U., & Güçlütürk, Y. (2024).
> Towards biologically plausible phosphene simulation for the differentiable optimization of
> visual cortical prostheses. *eLife*, 13, e85812. https://doi.org/10.7554/eLife.85812

## Citation

If you use VRphos, please cite:

```bibtex
@article{vrphos,
  title   = {TODO},
  author  = {TODO},
  journal = {TODO},
  year    = {TODO}
}

@article{vandergrinten2024dynaphos,
  title   = {Towards biologically plausible phosphene simulation for the differentiable
             optimization of visual cortical prostheses},
  author  = {van der Grinten, Maureen and de Ruyter van Steveninck, Jaap and Lozano, Antonio
             and Pijnacker, Laura and Rueckauer, Bodo and Roelfsema, Pieter and van Gerven, Marcel
             and van Wezel, Richard and G{\"u}{\c{c}}l{\"u}, Umut and G{\"u}{\c{c}}l{\"u}t{\"u}rk, Ya{\u{g}}mur},
  journal = {eLife},
  volume  = {13},
  pages   = {e85812},
  year    = {2024},
  doi     = {10.7554/eLife.85812}
}
```

## License

VRphos is released under the [GNU General Public License v3.0](LICENSE) (GPL-3.0), the license of
Dynaphos, on which it builds.
