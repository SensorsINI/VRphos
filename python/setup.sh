#!/bin/bash
# Create a virtual environment in python/.venv and install the requirements.
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
echo "Done. Activate with: source $(pwd)/.venv/bin/activate"
