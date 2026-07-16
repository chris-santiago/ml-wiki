"""Tests for wiki CLI idea command group (create and ingest subcommands)."""

import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from click.testing import CliRunner

from wiki_cli import cli


# ---------------------------------------------------------------------------
# Local helper (mirrors conftest._make_completed_process)
# ---------------------------------------------------------------------------


def _make_completed_process(stdout: str = "", stderr: str = "", returncode: int = 0) -> CompletedProcess:
    return CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)

# ---------------------------------------------------------------------------
# Local subprocess helper
# ---------------------------------------------------------------------------

_CLI_PATH = str(Path(__file__).resolve().parent.parent / "wiki_cli.py")


def _run_cli_subprocess(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, _CLI_PATH, *args],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Shared side_effect: exists returns not-found (exit 1), all else exit 0
# ---------------------------------------------------------------------------


def _exists_not_found(*args, **kwargs):
    """Return returncode=1 for 'exists' calls (entry not found), 0 otherwise."""
    if args and args[0] == "exists":
        return _make_completed_process(returncode=1)
    return _make_completed_process()


# ---------------------------------------------------------------------------
# Unit tests (mock _run_index and _run_self)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_create_calls_index_add(mock_run_helpers, wiki_env):
    mock_run_helpers["_run_index"].side_effect = _exists_not_found

    runner = CliRunner()
    result = runner.invoke(cli, [
        "idea", "create", "Test Idea",
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, result.output

    mock = mock_run_helpers["_run_index"]
    all_calls = mock.call_args_list

    # Verify create-entry was called with type=idea
    create_entry_call = next(
        (c for c in all_calls if c.args and c.args[0] == "create-entry"),
        None,
    )
    assert create_entry_call is not None, "create-entry not called"
    create_args = list(create_entry_call.args)
    assert "--type" in create_args
    assert create_args[create_args.index("--type") + 1] == "idea"

    # Verify add was called
    add_call = next(
        (c for c in all_calls if c.args and c.args[0] == "add"),
        None,
    )
    assert add_call is not None, "add not called"


@pytest.mark.unit
def test_create_collision_error(mock_run_helpers, wiki_env):
    # Default side_effect returns returncode=0, so exists() finds a match → collision
    runner = CliRunner()
    result = runner.invoke(cli, [
        "idea", "create", "Test Idea",
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code != 0


@pytest.mark.unit
def test_create_tags_project_passthrough(mock_run_helpers, wiki_env):
    mock_run_helpers["_run_index"].side_effect = _exists_not_found

    runner = CliRunner()
    result = runner.invoke(cli, [
        "idea", "create", "Test Idea",
        "--tags", "ml,test",
        "--project", "ato",
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, result.output

    mock = mock_run_helpers["_run_index"]
    all_calls = mock.call_args_list

    create_entry_call = next(
        (c for c in all_calls if c.args and c.args[0] == "create-entry"),
        None,
    )
    assert create_entry_call is not None
    create_args = list(create_entry_call.args)

    assert "--tags" in create_args
    assert "--project" in create_args
    assert create_args[create_args.index("--project") + 1] == "ato"


@pytest.mark.unit
def test_ingest_text_generates_page(mock_run_helpers, wiki_env):
    mock_run_helpers["_run_index"].side_effect = _exists_not_found

    runner = CliRunner()
    result = runner.invoke(cli, [
        "idea", "ingest",
        "--text", "My idea content",
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, result.output

    mock = mock_run_helpers["_run_index"]
    all_calls = mock.call_args_list
    add_call = next(
        (c for c in all_calls if c.args and c.args[0] == "add"),
        None,
    )
    assert add_call is not None, "add not called after ingest --text"


@pytest.mark.unit
def test_ingest_file_reads_content(mock_run_helpers, wiki_env, tmp_path):
    mock_run_helpers["_run_index"].side_effect = _exists_not_found

    source_file = tmp_path / "my_idea.txt"
    source_file.write_text("# Great Idea\n\nSome detailed content here.")

    runner = CliRunner()
    result = runner.invoke(cli, [
        "idea", "ingest", str(source_file),
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, result.output

    mock = mock_run_helpers["_run_index"]
    all_calls = mock.call_args_list
    add_call = next(
        (c for c in all_calls if c.args and c.args[0] == "add"),
        None,
    )
    assert add_call is not None, "add not called after file ingest"


@pytest.mark.unit
def test_ingest_improve_chains(mock_run_helpers, wiki_env):
    mock_run_helpers["_run_index"].side_effect = _exists_not_found

    runner = CliRunner()
    result = runner.invoke(cli, [
        "idea", "ingest",
        "--text", "Improve this idea",
        "--improve",
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, result.output

    mock_self = mock_run_helpers["_run_self"]
    mock_self.assert_called_once()
    self_args = list(mock_self.call_args.args)
    assert self_args[0] == "idea"
    assert self_args[1] == "improve"


# ---------------------------------------------------------------------------
# Integration tests (real index on disk)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_create_produces_valid_page(wiki_env):
    runner = CliRunner()
    result = runner.invoke(cli, [
        "idea", "create", "Valid Page Idea",
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, result.output

    page_path = wiki_env.wiki_dir / "ideas" / "idea-valid-page-idea.md"
    assert page_path.exists(), f"Expected page at {page_path}"

    content = page_path.read_text()

    # Frontmatter with id and type
    assert "id: idea-valid-page-idea" in content
    assert "type: idea" in content

    # Required sections and markers
    assert "## Notes" in content
    assert "<!-- end-notes -->" in content
    assert "## Connections" in content


@pytest.mark.integration
def test_create_collision_fails(wiki_env):
    runner = CliRunner()

    first = runner.invoke(cli, [
        "idea", "create", "Duplicate Idea",
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert first.exit_code == 0, first.output

    second = runner.invoke(cli, [
        "idea", "create", "Duplicate Idea",
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert second.exit_code != 0


@pytest.mark.integration
def test_ingest_text_writes_page(wiki_env):
    runner = CliRunner()
    result = runner.invoke(cli, [
        "idea", "ingest",
        "--text", "An ingested text idea",
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, result.output

    ideas_dir = wiki_env.wiki_dir / "ideas"
    idea_files = list(ideas_dir.glob("idea-*.md"))
    assert len(idea_files) == 1, f"Expected one idea page, found: {idea_files}"


@pytest.mark.integration
def test_ingest_file_writes_page(wiki_env, tmp_path):
    source_file = tmp_path / "source_idea.txt"
    source_file.write_text("# File Source Idea\n\nContent from a file.")

    runner = CliRunner()
    result = runner.invoke(cli, [
        "idea", "ingest", str(source_file),
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, result.output

    page_path = wiki_env.wiki_dir / "ideas" / "idea-file-source-idea.md"
    assert page_path.exists(), f"Expected page at {page_path}"
    content = page_path.read_text()
    assert "Content from a file." in content


# ---------------------------------------------------------------------------
# Smoke tests (subprocess)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_idea_subprocess_help():
    result = _run_cli_subprocess("idea", "--help")
    assert result.returncode == 0
    assert "create" in result.stdout
    assert "ingest" in result.stdout
    assert "improve" in result.stdout


@pytest.mark.smoke
def test_idea_create_subprocess_help():
    result = _run_cli_subprocess("idea", "create", "--help")
    assert result.returncode == 0
    assert "--tags" in result.stdout
    assert "--project" in result.stdout
