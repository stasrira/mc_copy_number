"""Subprocess tests for the run_mc_copy_number*.sh wrapper scripts.

Each script is executed for real (as bash), but in an isolated sandbox: a fake `conda`
on PATH and a fake `.venv/bin/activate` + `.venv/bin/python`. Nothing here touches a real
conda environment, virtualenv, or the actual pipeline code, so these tests also catch
runtime bash errors (unbound variables, bad quoting, wrong command names, ...) that
`bash -n` alone would miss.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

CONDA_ENV_NAME = 'python3.12.10_odbc'

SCRIPTS = {
    'run_mc_copy_number.sh': 'mc_copy_number.py',
    'run_mc_copy_number_alignment.sh': 'mc_copy_number_alignment.py',
    'run_mc_copy_number_counts.sh': 'mc_copy_number_counts.py',
    'run_mc_copy_number_requests.sh': 'mc_copy_number_requests.py',
}

# Mimics `eval "$(conda shell.bash hook)"`: called once as a real executable to print a
# shell function definition, which then shadows `conda` for the rest of the script.
FAKE_CONDA = """#!/bin/bash
if [ "$1" = "shell.bash" ] && [ "$2" = "hook" ]; then
    cat <<'HOOK'
conda() {
    case "$1" in
        env)
            if [ "$2" = "list" ] && [ -n "${FAKE_CONDA_ENVS:-}" ]; then
                echo "$FAKE_CONDA_ENVS"
            fi
            ;;
        activate)
            echo "activate:$2" >> "$FAKE_CONDA_LOG"
            ;;
        deactivate)
            echo "deactivate" >> "$FAKE_CONDA_LOG"
            ;;
    esac
}
HOOK
    exit 0
fi
exit 1
"""

FAKE_VENV_ACTIVATE = """
export VIRTUAL_ENV="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$VIRTUAL_ENV/bin:$PATH"
echo "activate" >> "$FAKE_VENV_LOG"
deactivate() {
    echo "deactivate" >> "$FAKE_VENV_LOG"
    unset -f deactivate
}
"""

FAKE_PYTHON = """#!/bin/bash
echo "$*" >> "$FAKE_PYTHON_LOG"
exit 0
"""


def _write_executable(path, content):
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def sandbox(tmp_path):
    """Build an isolated sandbox (fake conda + fake .venv) and return a factory
    (script_name, conda_env_exists) -> (script_path, env) plus the dict of log file paths
    the fake conda/venv/python stubs write their calls to.
    """

    fakebin = tmp_path / 'fakebin'
    fakebin.mkdir()
    _write_executable(fakebin / 'conda', FAKE_CONDA)

    venv_bin = tmp_path / '.venv' / 'bin'
    venv_bin.mkdir(parents=True)
    (venv_bin / 'activate').write_text(FAKE_VENV_ACTIVATE)
    _write_executable(venv_bin / 'python', FAKE_PYTHON)

    logs = {
        'conda': tmp_path / 'conda.log',
        'venv': tmp_path / 'venv.log',
        'python': tmp_path / 'python.log',
    }

    def _configure(script_name: str, conda_env_exists: bool):
        script_dst = tmp_path / script_name
        shutil.copy(PROJECT_ROOT / script_name, script_dst)
        script_dst.chmod(script_dst.stat().st_mode | stat.S_IEXEC)

        env = dict(os.environ)
        env['PATH'] = f"{fakebin}:{env.get('PATH', '')}"
        env['FAKE_CONDA_LOG'] = str(logs['conda'])
        env['FAKE_VENV_LOG'] = str(logs['venv'])
        env['FAKE_PYTHON_LOG'] = str(logs['python'])
        env['FAKE_CONDA_ENVS'] = (
            f'{CONDA_ENV_NAME}                 *  /fake/envs/{CONDA_ENV_NAME}' if conda_env_exists else ''
        )
        return script_dst, env

    return _configure, logs


def _run(script_path, env, *extra_args):
    return subprocess.run(
        ['bash', str(script_path), *extra_args],
        cwd=script_path.parent, env=env, capture_output=True, text=True,
    )


@pytest.mark.parametrize('script_name', SCRIPTS)
def test_syntax_is_valid(script_name):
    result = subprocess.run(['bash', '-n', str(PROJECT_ROOT / script_name)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('script_name', SCRIPTS)
def test_missing_conda_env_fails_before_activating_anything(sandbox, script_name):
    configure, logs = sandbox
    script_path, env = configure(script_name, conda_env_exists=False)

    result = _run(script_path, env)

    assert result.returncode == 1
    assert CONDA_ENV_NAME in result.stderr
    assert 'does not exist' in result.stderr
    assert not logs['conda'].exists()
    assert not logs['venv'].exists()
    assert not logs['python'].exists()


@pytest.mark.parametrize('script_name, entry_point', SCRIPTS.items())
def test_runs_cleanly_and_invokes_expected_entry_point(sandbox, script_name, entry_point):
    """Executes the real script end-to-end against the sandbox stubs, so any bash runtime
    error (unbound variable, bad quoting, command-not-found, ...) surfaces as a failure here.
    """
    configure, logs = sandbox
    script_path, env = configure(script_name, conda_env_exists=True)

    result = _run(script_path, env)

    assert result.returncode == 0, f'stdout={result.stdout!r} stderr={result.stderr!r}'
    assert logs['conda'].read_text().splitlines() == [f'activate:{CONDA_ENV_NAME}', 'deactivate']
    assert logs['venv'].read_text().splitlines() == ['activate', 'deactivate']
    assert logs['python'].read_text().strip() == entry_point


def test_counts_script_forwards_cli_args(sandbox):
    configure, logs = sandbox
    script_path, env = configure('run_mc_copy_number_counts.sh', conda_env_exists=True)

    result = _run(script_path, env, '--input_file', 'path/to/aligned.csv')

    assert result.returncode == 0, f'stdout={result.stdout!r} stderr={result.stderr!r}'
    assert logs['python'].read_text().strip() == 'mc_copy_number_counts.py --input_file path/to/aligned.csv'


def test_counts_script_runs_with_no_args(sandbox):
    """set -u must not choke on an empty "$@" forward when no args are passed."""
    configure, logs = sandbox
    script_path, env = configure('run_mc_copy_number_counts.sh', conda_env_exists=True)

    result = _run(script_path, env)

    assert result.returncode == 0, f'stdout={result.stdout!r} stderr={result.stderr!r}'
    assert logs['python'].read_text().strip() == 'mc_copy_number_counts.py'
