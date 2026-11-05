"""
Generate the phosphene layouts (visual field positions + cortical magnification) used by
vrphos_server.py and the VRphos apps.

Every layout comes from a grid of valid electrode sites on the cortex (config/grid_coords_*.yaml).
Each site is used exactly once per hemisphere and mapped to the visual field with the dynaphos
cortex model (config/params.yaml -> cortex_model), including its position noise. There is no
dropout and no resampling of sites, so every phosphene is a distinct electrode site.
The phosphenes are shuffled (fixed seed), so any subset of them still covers the whole field.

The same layouts are built into the apps (menu: Implant layout). In "Remote server" mode the app
loads the layout from the server instead, i.e. the active layout written here.

Output:
  layout_data/                 active layout (config/params.yaml -> webserver.layout), served by vrphos_server.py
  layout_data/layouts/<id>/    every layout
    coords.csv                 2 x N, visual field position (x, y) in degrees
    coords_polar.csv           2 x N, eccentricity (deg) and polar angle (rad)
    magnification.csv          1 x N, cortical magnification (mm cortex per degree)
    fac_magnification.csv      1 x N, size factor used by dynaphos

Usage:
  python generate_layouts.py                  # active layout from params.yaml
  python generate_layouts.py --layout utah    # write the Utah layout as the active one
"""

import argparse
import os
import shutil

import numpy as np
import yaml

from utils.dynaphos.utils import load_params, Map
from utils.dynaphos.cortex_models import get_visual_field_coordinates_from_cortex_full

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "layout_data")
CONFIG = os.path.join(HERE, "config", "params.yaml")

# id -> (cortex grid, name shown in the app menu). Order = menu order.
# Only real electrode layouts: all valid sites, and the Utah arrays implant.
LAYOUTS = {
    "valid_3": (os.path.join(HERE, "config", "grid_coords_valid_3.yaml"),      "All valid sites"),
    "utah":    (os.path.join(HERE, "config", "grid_coords_squares_utah.yaml"), "Utah arrays"),
}


def make_layout(params, grid_file, seed):
    """All sites of the grid, once per hemisphere, mapped to the visual field and shuffled."""
    with open(grid_file) as f:
        grid = yaml.safe_load(f)
    x = np.asarray(grid["x"], dtype=float)
    y = np.asarray(grid["y"], dtype=float)

    rng = np.random.default_rng(seed)
    cortex_model = dict(params["cortex_model"], dropout_rate=0.0)  # keep every valid site
    visual_field = get_visual_field_coordinates_from_cortex_full(cortex_model, Map(x, y), rng=rng)

    r, phi = visual_field.polar
    valid = np.isfinite(r) & np.isfinite(phi)
    order = rng.permutation(np.flatnonzero(valid))
    visual_field.use_subset(order)
    return visual_field


def write_layout(visual_field, params, path):
    os.makedirs(path, exist_ok=True)
    np.savetxt(os.path.join(path, "coords.csv"), visual_field.cartesian, delimiter=",")
    np.savetxt(os.path.join(path, "coords_polar.csv"), visual_field.polar, delimiter=",")

    a = params["cortex_model"]["a"]
    k = params["cortex_model"]["k"]
    b = params["cortex_model"]["b"]
    K = params["size"]["current_spread"]
    radius_to_sigma = params["size"]["radius_to_sigma"]

    r, _ = visual_field.polar
    magnification = k * (b - a) / ((r + a) * (r + b))  # mm cortex per degree
    np.savetxt(os.path.join(path, "magnification.csv"), (magnification,), delimiter=",")
    np.savetxt(os.path.join(path, "fac_magnification.csv"),
               (radius_to_sigma / (magnification * np.sqrt(K)),), delimiter=",")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--layout", choices=list(LAYOUTS),
                        help="layout to make active (default: webserver.layout in config/params.yaml)")
    args = parser.parse_args()

    params = load_params(CONFIG)
    active = args.layout or params["webserver"].get("layout", "valid_3")
    if active not in LAYOUTS:
        raise SystemExit(f"webserver.layout '{active}' is not one of {list(LAYOUTS)}")
    seed = params["run"]["seed"]

    counts = {}
    for layout_id, (grid_file, name) in LAYOUTS.items():
        visual_field = make_layout(params, grid_file, seed)
        write_layout(visual_field, params, os.path.join(OUT_DIR, "layouts", layout_id))
        counts[layout_id] = len(visual_field)
        r = visual_field.polar[0]
        print(f"{layout_id:13s} {name:16s} {len(visual_field):6d} phosphenes, eccentricity {r.min():.2f}-{r.max():.2f} deg")

    # The active layout also goes to layout_data/ itself, where vrphos_server.py reads it.
    for name in ("coords.csv", "coords_polar.csv", "magnification.csv", "fac_magnification.csv"):
        shutil.copyfile(os.path.join(OUT_DIR, "layouts", active, name), os.path.join(OUT_DIR, name))
    print(f"active layout: {active} ({counts[active]} phosphenes) -> {OUT_DIR}/")


if __name__ == "__main__":
    main()
