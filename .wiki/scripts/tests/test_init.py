"""Tests for wiki CLI init command."""

import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
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
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_init_creates_directories(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--non-interactive"])
    assert result.exit_code == 0

    expected_dirs = [
        "wiki/_pages",
        "wiki/topics",
        "wiki/projects",
        "wiki/syntheses",
        "wiki/images",
        "wiki/assets",
        ".wiki/scripts",
        ".wiki/sources",
        ".wiki/schemas",
    ]
    for rel in expected_dirs:
        assert (tmp_path / rel).is_dir(), f"Expected directory missing: {rel}"


@pytest.mark.integration
def test_init_writes_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--non-interactive"])
    assert result.exit_code == 0

    config_path = tmp_path / ".wiki" / "config.yaml"
    assert config_path.exists(), ".wiki/config.yaml was not created"
    content = config_path.read_text()
    assert "research_wiki:" in content
    assert "wiki_dir:" in content


@pytest.mark.integration
def test_init_creates_empty_index(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--non-interactive"])
    assert result.exit_code == 0

    index_path = tmp_path / ".wiki" / "index.jsonl"
    assert index_path.exists(), ".wiki/index.jsonl was not created"
    assert index_path.read_text() == "", "index.jsonl should be empty after init"


@pytest.mark.integration
def test_init_non_interactive_no_prompts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--non-interactive"])
    assert result.exit_code == 0
    assert "Source name" not in result.output


@pytest.mark.integration
def test_init_refuses_overwrite_without_confirm(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    # First init — succeeds
    first = runner.invoke(cli, ["init", "--non-interactive"])
    assert first.exit_code == 0

    # Second init without --non-interactive, user answers "n"
    second = runner.invoke(cli, ["init"], input="n\n")
    aborted = "Aborted" in second.output or second.exit_code != 0
    assert aborted, (
        f"Expected second init to abort or exit non-zero; "
        f"exit_code={second.exit_code}, output={second.output!r}"
    )


# ---------------------------------------------------------------------------
# Smoke tests (subprocess)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_init_subprocess_help():
    result = _run_cli_subprocess("init", "--help")
    assert result.returncode == 0
    assert "--non-interactive" in result.stdout
