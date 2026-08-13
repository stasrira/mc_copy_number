#!/bin/bash
set -euo pipefail

script_dir=$(dirname "$0")
cd "$script_dir"
echo "Working directory: $script_dir"

source .venv/bin/activate

# Run the full pipeline (both steps)
python mc_copy_number.py

deactivate
