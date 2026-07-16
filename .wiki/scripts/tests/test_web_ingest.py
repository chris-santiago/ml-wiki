"""Tests for wiki CLI web-ingest command."""

import json
import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wiki_cli import cli

# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def _make_completed_process(stdout: str = "", stderr: str = "", returncode: int = 0) -> CompletedProcess:
    """Create a CompletedProcess for use in mocks."""
    return CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


_CLI_PATH = str(Path(__file__).resolve().parent.parent / "wiki_cli.py")


def _run_cli_subprocess(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, _CLI_PATH, *args],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Shared arxiv side_effect factory
# ---------------------------------------------------------------------------

_FAKE_ARXIV_ID = "2301.12345"
_FAKE_ENTRY_ID = "2301.12345"
_FAKE_METADATA = {
    "id": _FAKE_ENTRY_ID,
    "title": "Test Paper",
    "authors": ["A"],
    "abstract": "Test",
    "categories": ["cs.LG"],
}


def _make_arxiv_side_effect(arxiv_id: str = _FAKE_ARXIV_ID, metadata: dict | None = None):
    """Return a side_effect callable for _run_arxiv mock."""
    if metadata is None:
        metadata = _FAKE_METADATA

    def side_effect(*args, **kw):
        if "normalize" in args:
            return _make_completed_process(stdout=arxiv_id)
        elif "fetch" in args:
            return _make_completed_process(stdout=json.dumps(metadata))
        elif "download-pdf" in args:
            return _make_completed_process()
        return _make_completed_process()

    return side_effect


# ---------------------------------------------------------------------------
# Unit tests (mock all helpers)
# ---------------------------------------------------------------------------


def _make_index_side_effect_not_found():
    """Return a side_effect for _run_index: exists→1 (not found), all others→0 (success)."""
    def side_effect(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "exists":
            return _make_completed_process(returncode=1)
        return _make_completed_process(returncode=0)
    return side_effect


@pytest.mark.unit
def test_web_ingest_calls_arxiv_sequence(mock_run_helpers, wiki_env):
    """_run_arxiv is called in order: normalize, fetch, download-pdf."""
    mock_run_helpers["_run_arxiv"].side_effect = _make_arxiv_side_effect()
    mock_run_helpers["_run_index"].side_effect = _make_index_side_effect_not_found()

    runner = CliRunner()
    result = runner.invoke(cli, [
        "web-ingest", _FAKE_ARXIV_ID,
        "--index", str(wiki_env.index_path),
        "--yes",
    ])
    assert result.exit_code == 0, f"web-ingest failed:\n{result.output}"

    arxiv_mock = mock_run_helpers["_run_arxiv"]
    call_subcommands = [call[0][0] for call in arxiv_mock.call_args_list]
    assert "normalize" in call_subcommands
    assert "fetch" in call_subcommands
    assert "download-pdf" in call_subcommands

    # Verify ordering: normalize before fetch, fetch before download-pdf
    normalize_pos = call_subcommands.index("normalize")
    fetch_pos = call_subcommands.index("fetch")
    dl_pos = call_subcommands.index("download-pdf")
    assert normalize_pos < fetch_pos < dl_pos, (
        f"Expected normalize < fetch < download-pdf, got positions {normalize_pos}, {fetch_pos}, {dl_pos}"
    )


@pytest.mark.unit
def test_web_ingest_collision_skips(mock_run_helpers, wiki_env):
    """When the entry already exists in the index, web-ingest exits without downloading."""
    mock_run_helpers["_run_arxiv"].side_effect = _make_arxiv_side_effect()
    # exists returns exit 0 → already in index
    mock_run_helpers["_run_index"].return_value = _make_completed_process(returncode=0)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "web-ingest", _FAKE_ARXIV_ID,
        "--index", str(wiki_env.index_path),
        "--yes",
    ])

    # CLI calls sys.exit(1) on collision — CliRunner converts to SystemExit
    assert result.exit_code != 0

    # "already exists" message or "skip" should appear in output/stderr
    combined = (result.output or "") + (result.exception.__str__() if result.exception else "")
    assert "already" in combined.lower() or "skip" in combined.lower() or result.exit_code == 1

    # download-pdf must NOT be called
    arxiv_mock = mock_run_helpers["_run_arxiv"]
    dl_calls = [c for c in arxiv_mock.call_args_list if c[0][0] == "download-pdf"]
    assert len(dl_calls) == 0, "download-pdf should not be called when collision detected"


@pytest.mark.unit
def test_web_ingest_dry_run(mock_run_helpers, wiki_env):
    """--dry-run calls normalize and fetch but NOT download-pdf, and skips index add."""
    mock_run_helpers["_run_arxiv"].side_effect = _make_arxiv_side_effect()
    # exists returns exit 1 → not in index
    mock_run_helpers["_run_index"].return_value = _make_completed_process(returncode=1)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "web-ingest", _FAKE_ARXIV_ID,
        "--index", str(wiki_env.index_path),
        "--dry-run",
    ])
    assert result.exit_code == 0, f"web-ingest --dry-run failed:\n{result.output}"

    arxiv_mock = mock_run_helpers["_run_arxiv"]
    call_subcommands = [call[0][0] for call in arxiv_mock.call_args_list]

    assert "normalize" in call_subcommands, "normalize should be called in dry-run"
    assert "fetch" in call_subcommands, "fetch should be called in dry-run"
    assert "download-pdf" not in call_subcommands, "download-pdf must NOT be called in dry-run"

    # _run_index should not have an "add" call
    index_mock = mock_run_helpers["_run_index"]
    add_calls = [c for c in index_mock.call_args_list if c[0][0] == "add"]
    assert len(add_calls) == 0, "_run_index add must NOT be called in dry-run"


@pytest.mark.unit
def test_web_ingest_tags_passthrough(mock_run_helpers, wiki_env):
    """--tags value is passed through to the _run_index update call."""
    mock_run_helpers["_run_arxiv"].side_effect = _make_arxiv_side_effect()
    mock_run_helpers["_run_index"].side_effect = _make_index_side_effect_not_found()

    runner = CliRunner()
    result = runner.invoke(cli, [
        "web-ingest", _FAKE_ARXIV_ID,
        "--index", str(wiki_env.index_path),
        "--tags", "ml,fraud",
        "--yes",
    ])
    assert result.exit_code == 0, f"web-ingest --tags failed:\n{result.output}"

    index_mock = mock_run_helpers["_run_index"]
    # Find the update call that carries --tags
    update_calls = [c for c in index_mock.call_args_list if c[0][0] == "update"]
    assert len(update_calls) >= 1, "Expected at least one _run_index update call for tags"

    # Verify --tags and value appear in the update args
    update_args = list(update_calls[0][0])
    assert "--tags" in update_args, f"--tags not found in update args: {update_args}"
    tags_idx = update_args.index("--tags")
    assert update_args[tags_idx + 1] == "ml,fraud", (
        f"Expected tags value 'ml,fraud', got '{update_args[tags_idx + 1]}'"
    )


# ---------------------------------------------------------------------------
# Integration test (real index, mock only _run_arxiv)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_web_ingest_collision_real_index(populated_index, monkeypatch):
    """Collision is detected against the real populated index without mocking _run_index."""
    import wiki_cli

    # jonesTransformers2023 is already in populated_index
    collision_id = "jonesTransformers2023"
    collision_metadata = {
        "id": collision_id,
        "title": "Transformers for Tabular Data",
        "authors": ["Jones"],
        "abstract": "Test",
        "categories": ["cs.LG"],
    }

    def arxiv_side_effect(*args, **kw):
        if "normalize" in args:
            return _make_completed_process(stdout=collision_id)
        elif "fetch" in args:
            return _make_completed_process(stdout=json.dumps(collision_metadata))
        return _make_completed_process()

    monkeypatch.setattr(wiki_cli, "_run_arxiv", arxiv_side_effect)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "web-ingest", collision_id,
        "--index", str(populated_index.index_path),
        "--yes",
    ])

    # Should exit non-zero because entry already exists
    assert result.exit_code != 0, (
        f"Expected non-zero exit on collision, got 0.\nOutput: {result.output}"
    )


# ---------------------------------------------------------------------------
# Smoke tests (subprocess)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_web_ingest_subprocess_help():
    """web-ingest --help exits 0 and mentions --dry-run and --tags."""
    result = _run_cli_subprocess("web-ingest", "--help")
    assert result.returncode == 0
    assert "--dry-run" in result.stdout
    assert "--tags" in result.stdout


@pytest.mark.smoke
def test_web_ingest_yes_flag_in_help():
    """web-ingest --help shows --yes or -y flag."""
    result = _run_cli_subprocess("web-ingest", "--help")
    assert result.returncode == 0
    assert "--yes" in result.stdout or "-y" in result.stdout
