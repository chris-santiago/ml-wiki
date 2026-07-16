"""Tests for wiki CLI save command."""

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
# Sample query result JSON
# ---------------------------------------------------------------------------

SAMPLE_QUERY_RESULT = {
    "query": "What is drift detection for heterogeneous encoders?",
    "title": "Drift Detection for Heterogeneous Encoders",
    "synthesis": "Drift detection in heterogeneous encoder settings requires careful handling.",
    "sources": ["alves2022efficient", "helske2016kfas"],
    "tags": ["drift-detection", "heterogeneous-encoders"],
    "fragments": [
        {"seq": 1, "type": "finding", "title": "Drift detection matters"}
    ],
}

# Fake assemble stdout: what _run_render("assemble", ...) returns on success
_FAKE_WIKI_PATH = "wiki/syntheses/syn-drift-detection-for-heterogeneous.md"
_FAKE_ASSEMBLE_STDOUT = json.dumps({
    "wiki_path": _FAKE_WIKI_PATH,
    "tags_finalized": ["drift-detection", "heterogeneous-encoders"],
    "fragments": [
        {
            "id": "frag-syn-test-01",
            "type": "finding",
            "title": "Drift detection matters",
            "tags": ["drift-detection"],
            "project": None,
            "references": ["syn-test"],
            "ingested": "2026-01-01T00:00:00Z",
        }
    ],
})

# Fake create-entry stdout: minimal valid entry JSON for the add call
_FAKE_CREATE_ENTRY_STDOUT = json.dumps({
    "id": "syn-drift-detection-for-heterogeneous",
    "type": "synthesis",
    "source_type": "query",
    "status": "rendered",
    "wiki_path": _FAKE_WIKI_PATH,
    "tags": ["drift-detection", "heterogeneous-encoders"],
    "project": None,
    "source_name": SAMPLE_QUERY_RESULT["query"],
    "references": ["alves2022efficient", "helske2016kfas"],
    "locked": False,
    "ingested": "2026-01-01T00:00:00Z",
    "rendered": None,
    "last_improved": None,
})


def _make_default_index_side_effect(slug: str = "syn-drift-detection-for-heterogeneous"):
    """Return a side_effect function for _run_index that handles the save call sequence."""

    def side_effect(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "exists":
            # Entry does not exist yet — return exit code 1
            return _make_completed_process(returncode=1)
        if subcommand == "create-entry":
            return _make_completed_process(stdout=_FAKE_CREATE_ENTRY_STDOUT, returncode=0)
        if subcommand in ("add", "add-fragments-batch", "update"):
            return _make_completed_process(returncode=0)
        return _make_completed_process(returncode=0)

    return side_effect


def _write_result_file(tmp_path: Path, data: dict | None = None) -> Path:
    """Write a query result JSON file to tmp_path and return its path."""
    if data is None:
        data = SAMPLE_QUERY_RESULT
    result_file = tmp_path / "last-query-result.json"
    result_file.write_text(json.dumps(data))
    return result_file


# ---------------------------------------------------------------------------
# Unit tests (mock _run_index and _run_render)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_save_slug_from_title(mock_run_helpers, wiki_env):
    """save derives a slug from the title and passes it as the title to create-entry."""
    mock_run_helpers["_run_index"].side_effect = _make_default_index_side_effect()
    mock_run_helpers["_run_render"].return_value = _make_completed_process(
        stdout=_FAKE_ASSEMBLE_STDOUT, returncode=0
    )

    result_file = _write_result_file(wiki_env.root)
    runner = CliRunner()
    result = runner.invoke(cli, [
        "save",
        "--result-file", str(result_file),
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, f"save failed:\n{result.output}"

    # Find the create-entry call and check the title
    index_mock = mock_run_helpers["_run_index"]
    create_entry_calls = [
        call for call in index_mock.call_args_list
        if call[0][0] == "create-entry"
    ]
    assert len(create_entry_calls) == 1, "Expected exactly one create-entry call"
    call_args = list(create_entry_calls[0][0])
    assert "--title" in call_args
    title_idx = call_args.index("--title")
    assert call_args[title_idx + 1] == "Drift Detection for Heterogeneous Encoders"


@pytest.mark.unit
def test_save_explicit_id(mock_run_helpers, wiki_env):
    """save --id my-custom-id produces slug syn-my-custom-id in create-entry args."""
    mock_run_helpers["_run_index"].side_effect = _make_default_index_side_effect(
        slug="syn-my-custom-id"
    )
    mock_run_helpers["_run_render"].return_value = _make_completed_process(
        stdout=_FAKE_ASSEMBLE_STDOUT, returncode=0
    )

    result_file = _write_result_file(wiki_env.root)
    runner = CliRunner()
    result = runner.invoke(cli, [
        "save",
        "--id", "my-custom-id",
        "--result-file", str(result_file),
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, f"save failed:\n{result.output}"

    # Check that create-entry received syn-my-custom-id
    index_mock = mock_run_helpers["_run_index"]
    create_entry_calls = [
        call for call in index_mock.call_args_list
        if call[0][0] == "create-entry"
    ]
    assert len(create_entry_calls) == 1
    call_args = list(create_entry_calls[0][0])
    assert "--id" in call_args
    id_idx = call_args.index("--id")
    assert call_args[id_idx + 1] == "syn-my-custom-id"


@pytest.mark.unit
def test_save_agent_data_has_tags_finalized(mock_run_helpers, wiki_env):
    """The JSON written to --agent-output uses the key tags_finalized (not tags)."""
    captured_agent_data = {}

    def render_side_effect(*args, **kw):
        # Look for --agent-output flag and read the file before it gets cleaned up
        arg_list = list(args)
        if "--agent-output" in arg_list:
            agent_out_path = arg_list[arg_list.index("--agent-output") + 1]
            with open(agent_out_path) as f:
                captured_agent_data.update(json.load(f))
        return _make_completed_process(stdout=_FAKE_ASSEMBLE_STDOUT, returncode=0)

    mock_run_helpers["_run_index"].side_effect = _make_default_index_side_effect()
    mock_run_helpers["_run_render"].side_effect = render_side_effect

    result_file = _write_result_file(wiki_env.root)
    runner = CliRunner()
    result = runner.invoke(cli, [
        "save",
        "--result-file", str(result_file),
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, f"save failed:\n{result.output}"

    assert "tags_finalized" in captured_agent_data, (
        f"Expected 'tags_finalized' key in agent data, got keys: {list(captured_agent_data.keys())}"
    )
    assert "tags" not in captured_agent_data, (
        "Expected agent data to use 'tags_finalized', not 'tags'"
    )


@pytest.mark.unit
def test_save_missing_result_file(wiki_env):
    """save must exit non-zero when the result file does not exist."""
    runner = CliRunner()
    result = runner.invoke(cli, [
        "save",
        "--result-file", str(wiki_env.root / "nonexistent-result.json"),
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code != 0


@pytest.mark.unit
def test_save_invalid_json(wiki_env):
    """save must exit non-zero when the result file contains invalid JSON."""
    bad_file = wiki_env.root / "bad-result.json"
    bad_file.write_text("this is not json {{{{")

    runner = CliRunner()
    result = runner.invoke(cli, [
        "save",
        "--result-file", str(bad_file),
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code != 0


@pytest.mark.unit
def test_save_source_name_is_query_text(mock_run_helpers, wiki_env):
    """The entry stub written to --entry-json has source_name equal to the verbatim query text."""
    captured_entry_stub = {}

    def render_side_effect(*args, **kw):
        arg_list = list(args)
        if "--entry-json" in arg_list:
            entry_json_path = arg_list[arg_list.index("--entry-json") + 1]
            with open(entry_json_path) as f:
                captured_entry_stub.update(json.load(f))
        return _make_completed_process(stdout=_FAKE_ASSEMBLE_STDOUT, returncode=0)

    mock_run_helpers["_run_index"].side_effect = _make_default_index_side_effect()
    mock_run_helpers["_run_render"].side_effect = render_side_effect

    result_file = _write_result_file(wiki_env.root)
    runner = CliRunner()
    result = runner.invoke(cli, [
        "save",
        "--result-file", str(result_file),
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, f"save failed:\n{result.output}"

    expected_query = SAMPLE_QUERY_RESULT["query"]
    assert captured_entry_stub.get("source_name") == expected_query, (
        f"Expected source_name='{expected_query}', got '{captured_entry_stub.get('source_name')}'"
    )


# ---------------------------------------------------------------------------
# Integration tests (real scripts on disk)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_save_creates_wiki_page_and_entry(wiki_env, monkeypatch):
    """Full save with real scripts: index entry and wiki page both created."""
    monkeypatch.chdir(wiki_env.root)

    result_file = _write_result_file(wiki_env.root)
    runner = CliRunner()
    result = runner.invoke(cli, [
        "save",
        "--id", "test-integration",
        "--result-file", str(result_file),
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, (
        f"save failed:\nstdout: {result.output}\nstderr: {result.exception}"
    )

    # Check index has the synthesis entry
    index_lines = wiki_env.index_path.read_text().splitlines()
    entries = [json.loads(line) for line in index_lines if line.strip()]
    syn_entries = [e for e in entries if e["id"] == "syn-test-integration"]
    assert len(syn_entries) == 1, f"Expected syn-test-integration in index; entries: {[e['id'] for e in entries]}"
    entry = syn_entries[0]
    assert entry["type"] == "synthesis"
    assert entry["status"] == "rendered"
    assert entry["id"].startswith("syn-")

    # Check wiki page exists in syntheses/ dir
    wiki_page = wiki_env.wiki_dir / "syntheses" / "syn-test-integration.md"
    assert wiki_page.exists(), f"Wiki page not found at {wiki_page}"


@pytest.mark.integration
def test_save_temp_files_cleaned(wiki_env, monkeypatch):
    """After save completes, the result file should be deleted."""
    monkeypatch.chdir(wiki_env.root)

    result_file = _write_result_file(wiki_env.root)
    assert result_file.exists(), "Result file should exist before save"

    runner = CliRunner()
    result = runner.invoke(cli, [
        "save",
        "--id", "test-cleanup",
        "--result-file", str(result_file),
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, f"save failed:\n{result.output}"
    assert not result_file.exists(), "Result file should have been deleted after save"


@pytest.mark.integration
def test_save_auto_slug(wiki_env, monkeypatch):
    """save without --id generates a slug starting with syn- derived from the query text."""
    monkeypatch.chdir(wiki_env.root)

    result_file = _write_result_file(wiki_env.root)
    runner = CliRunner()
    result = runner.invoke(cli, [
        "save",
        "--result-file", str(result_file),
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, f"save failed:\n{result.output}"

    # Check index has exactly one synthesis entry and its ID starts with syn-
    index_lines = wiki_env.index_path.read_text().splitlines()
    entries = [json.loads(line) for line in index_lines if line.strip()]
    syn_entries = [e for e in entries if e["type"] == "synthesis"]
    assert len(syn_entries) == 1, f"Expected 1 synthesis entry, got {len(syn_entries)}"
    entry_id = syn_entries[0]["id"]
    assert entry_id.startswith("syn-"), f"Entry ID should start with syn-, got '{entry_id}'"


# ---------------------------------------------------------------------------
# Smoke tests (subprocess)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_save_subprocess_help():
    """save --help exits 0 and mentions --result-file."""
    result = _run_cli_subprocess("save", "--help")
    assert result.returncode == 0
    assert "--result-file" in result.stdout
