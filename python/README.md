# VRphos Python tools

```bash
./setup.sh                     # creates .venv and installs requirements.txt
source .venv/bin/activate
```

| file / folder | purpose | docs |
|---|---|---|
| `vrphos_server.py` | stimulation server: webcam → YOLO / filters → Dynaphos sampling → currents over TCP to the app | [tcp_server.md](../docs/tcp_server.md) |
| `generate_layouts.py` | builds the implant layouts (`layout_data/`) from the cortex grids in `config/` | [layouts.md](../docs/layouts.md) |
| `tool_packs/make_tool_pack.py` | create and test tool packs (your own networks) on the PC | [tool_packs.md](../docs/tool_packs.md) |
| `tool_packs/make_examples.py` | rebuilds the example tool packs in `tool_packs/examples/` | [tool_packs.md](../docs/tool_packs.md) |
| `benchmark/run_benchmark.py` | runs the in-app performance benchmark over adb and collects the results | [benchmark.md](../docs/benchmark.md) |
| `config/params.yaml` | Dynaphos parameters, ports, active layout | |
| `layout_data/` | phosphene layouts (served by `vrphos_server.py`) | [layouts.md](../docs/layouts.md) |
| `utils/network_servers.py` | HTTP + TCP servers, reusable for your own stimulation source | [tcp_server.md](../docs/tcp_server.md#writing-your-own-source) |
| `utils/dynaphos/` | adapted Dynaphos code incl. the fast `dynaphos_lite` simulator (GPL-3.0, see `LICENSE` there) | |

All scripts can be started from any directory.
