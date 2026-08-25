#!/bin/bash
set -euo pipefail

script_dir=$(dirname "$0")
cd "$script_dir"
echo "wrk_dir=$script_dir"

# activate conda environment with the required odbc-enabled python
conda_env="python3.12.10_odbc"
eval "$(conda shell.bash hook)"  # required to run conda activate from a script

if ! conda env list | awk '{print $1}' | grep -Fxq "$conda_env"; then
    echo "Error: conda environment '$conda_env' does not exist. Create it before running this script." >&2
    exit 1
fi

conda activate "$conda_env"

source .venv/bin/activate
# forwards any CLI args, e.g. --input_file path/to/aligned.csv or --input_dir path/to/dir
python mc_copy_number_counts.py "$@"
deactivate

conda deactivate
