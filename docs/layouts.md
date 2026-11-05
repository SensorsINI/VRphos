# Implant layouts

A layout is the set of electrodes: one phosphene per electrode, with its position in the visual
field and the cortical magnification at that position (which sets the phosphene size).
Both apps contain two layouts, selectable in the menu (*Implant layout*):

| id | menu name | electrodes | cortex grid |
|---|---|---|---|
| `valid_3` | All valid sites | 13,062 | `python/config/grid_coords_valid_3.yaml`: every valid electrode site on V1 |
| `utah` | Utah arrays | 800 | `python/config/grid_coords_squares_utah.yaml`: an implant of Utah arrays |

In *Remote server* mode the app uses the layout of the server instead (`python/layout_data/`).

## How they are generated

`python/generate_layouts.py` builds both from the cortex grids with the Dynaphos cortex model
(`cortex_model` in `python/config/params.yaml`: dipole model, k = 17.3, a = 0.75, b = 120,
α = 0.95):

- each grid site is used exactly once per hemisphere and mapped to the visual field, including the
  Dynaphos electrode position noise (0.4 mm on the cortex);
- there is no dropout and no resampling, so every phosphene is a distinct electrode site;
- the order is shuffled with a fixed seed (`run.seed`), so the first N phosphenes (the *Phosphenes*
  setting) are spread over the whole field;
- cortical magnification M = k (b − a) / ((r + a)(r + b)) in mm/degree at eccentricity r.

```bash
cd python
python generate_layouts.py                 # active layout = webserver.layout in params.yaml
python generate_layouts.py --layout utah   # serve the Utah layout
```

Output in `python/layout_data/`:

| file | content |
|---|---|
| `coords.csv` | 2 × N: x, y in degrees (served to the app) |
| `coords_polar.csv` | 2 × N: eccentricity (degrees), polar angle (radians) |
| `magnification.csv` | 1 × N: cortical magnification, mm/degree (served to the app) |
| `fac_magnification.csv` | 1 × N: size factor used by Dynaphos |
| `layouts/<id>/` | the same files for every layout |

The script is deterministic: running it again reproduces the files shipped here and built into
the apps. To serve your own layout, write the two served CSV files in the same format (or add a
grid to `LAYOUTS` in `generate_layouts.py`) and restart `vrphos_server.py`.
