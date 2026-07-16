"""Tests for wiki CLI lock and unlock commands."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from wiki_cli import cli

# ---------------------------------------------------------------------------
# Local subprocess helper (mirrors conftest.run_cli_subprocess)
# ---------------------------------------------------------------------------

_CLI_PATH = str(Path(__file__).resolve().parent.parent / "wiki_cli.py")


def _run_cli_subprocess(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, _CLI_PATH, *args],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Unit tests (mock _run_index)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_lock_calls_index_update(mock_run_helpers, wiki_env):
    runner = CliRunner()
    result = runner.invoke(cli, ["lock", "jonesTransformers2023", "--index", str(wiki_env.index_path)])
    assert result.exit_code == 0

    mock = mock_run_helpers["_run_index"]
    mock.assert_called_once()
    args = mock.call_args[0]
    assert args[0] == "update"
    assert args[1] == "jonesTransformers2023"
    assert "--locked" in args
    locked_idx = list(args).index("--locked")
    assert args[locked_idx + 1] == "true"


@pytest.mark.unit
def test_unlock_calls_index_update(mock_run_helpers, wiki_env):
    runner = CliRunner()
    result = runner.invoke(cli, ["unlock", "jonesTransformers2023", "--index", str(wiki_env.index_path)])
    assert result.exit_code == 0

    mock = mock_run_helpers["_run_index"]
    mock.assert_called_once()
    args = mock.call_args[0]
    assert args[0] == "update"
    assert args[1] == "jonesTransformers2023"
    assert "--locked" in args
    locked_idx = list(args).index("--locked")
    assert args[locked_idx + 1] == "false"


# ---------------------------------------------------------------------------
# Integration tests (real index on disk)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_lock_sets_locked_in_index(populated_index):
    runner = CliRunner()
    result = runner.invoke(cli, ["lock", "jonesTransformers2023", "--index", str(populated_index.index_path)])
    assert result.exit_code == 0

    entries = {
        entry["id"]: entry
        for line in populated_index.index_path.read_text().splitlines()
        if line.strip()
        for entry in [json.loads(line)]
    }
    assert entries["jonesTransformers2023"]["locked"] is True


@pytest.mark.integration
def test_unlock_after_lock(populated_index):
    runner = CliRunner()
    runner.invoke(cli, ["lock", "jonesTransformers2023", "--index", str(populated_index.index_path)])
    result = runner.invoke(cli, ["unlock", "jonesTransformers2023", "--index", str(populated_index.index_path)])
    assert result.exit_code == 0

    entries = {
        entry["id"]: entry
        for line in populated_index.index_path.read_text().splitlines()
        if line.strip()
        for entry in [json.loads(line)]
    }
    assert entries["jonesTransformers2023"]["locked"] is False


@pytest.mark.integration
def test_lock_nonexistent_entry_fails(wiki_env):
    runner = CliRunner()
    result = runner.invoke(cli, ["lock", "nonexistent-id", "--index", str(wiki_env.index_path)])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Smoke tests (subprocess)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_lock_subprocess_help():
    result = _run_cli_subprocess("lock", "--help")
    assert result.returncode == 0
    assert "lock" in result.stdout.lower()


@pytest.mark.smoke
def test_unlock_subprocess_help():
    result = _run_cli_subprocess("unlock", "--help")
    assert result.returncode == 0
    assert "unlock" in result.stdout.lower()
