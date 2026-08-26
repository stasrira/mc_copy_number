#!/bin/bash
set -euo pipefail

script_dir=$(dirname "$0")
cd "$script_dir"
echo "wrk_dir=$script_dir"

# activate conda environment with the required odbc-enabled python
conda_env="python3.12.10_odbc"
eval "$(conda shell.bash hook)"  # required to run conda activate from a script

if ! activate_err=$(conda activate "$conda_env" 2>&1); then
    echo "Error: failed to activate conda environment '$conda_env'. Create it before running this script." >&2
    echo "$activate_err" >&2
    exit 1
fi

source .venv/bin/activate
python mc_copy_number.py
deactivate

conda deactivate
