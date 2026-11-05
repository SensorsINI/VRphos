# Dynaphos (adapted copy)

This folder contains code from **Dynaphos** (https://github.com/neuralcodinglab/dynaphos,
PyPI package `dynaphos` 0.1.3) by Maureen van der Grinten, Jaap de Ruyter van Steveninck,
Bodo Rueckauer and colleagues (Neural Coding Lab, Donders Institute), licensed under the
GNU General Public License v3.0 (see `LICENSE` in this folder).

> van der Grinten, M., et al. (2024). Towards biologically plausible phosphene simulation for the
> differentiable optimization of visual cortical prostheses. *eLife*, 13, e85812.
> https://doi.org/10.7554/eLife.85812

## Modifications (VRphos, 2025–2026)

| file | origin | changes |
|---|---|---|
| `utils.py` | `dynaphos/utils.py` | unchanged |
| `image_processing.py` | `dynaphos/image_processing.py` | unchanged |
| `cortex_models.py` | `dynaphos/cortex_models.py` | import path changed to `utils.dynaphos.utils` |
| `dynaphos_lite.py` | `dynaphos/simulator.py` | modified by the VRphos authors: a lighter `GaussianSimulator` for real-time use (renders phosphenes in small local patches instead of full-resolution maps per phosphene) |

The modified files are distributed under the same GPL-3.0 license.
