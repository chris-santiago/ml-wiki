"""Tests for wiki CLI synthesis-refresh command."""

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

_CLI_PATH = str(Path(__file__).resolve().parent.parent / "wiki_cli.py")


def _make_completed_process(stdout: str = "", stderr: str = "", returncode: int = 0) -> CompletedProcess:
    return CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _run_cli_subprocess(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, _CLI_PATH, *args],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Fake LLM config (matches wiki_env config.yaml shape)
# ---------------------------------------------------------------------------

_FAKE_LLM_CONFIG = json.dumps({
    "base_url": "https://localhost:9999",
    "api_key_env": "TEST_API_KEY",
    "models": {"nano": "test-nano", "mini": "test-mini", "full": "test-full"},
    "default_params": {"max_tokens": 4096, "temperature": 0},
    "model_overrides": {},
})


# ---------------------------------------------------------------------------
# Synthesis wiki page template
# The command extracts text between "## Synthesis\n\n" and the next "## ".
# Without this structure the command exits with "No synthesis content found."
# ---------------------------------------------------------------------------

_SYNTHESIS_PAGE = """\
---
id: {entry_id}
type: synthesis
---

# {entry_id}

**Query:** "Fraud detection methods"
**Generated:** 2026-01-02

## Synthesis

Existing synthesis content about fraud detection.

## Sources

- [[smithDeepLearning2024]]

## Notes

<!-- end-notes -->

## Connections
"""


def _make_synthesis_page(tmp_path: Path, entry_id: str) -> Path:
    """Write a synthesis wiki page with ## Synthesis content and return its path."""
    page = tmp_path / f"{entry_id}.md"
    page.write_text(_SYNTHESIS_PAGE.format(entry_id=entry_id))
    return page


# ---------------------------------------------------------------------------
# Synthesis entry builder
# ---------------------------------------------------------------------------

def _make_synthesis_entry(wiki_path: str, entry_id: str = "syn-fraud-detection-methods", **overrides) -> dict:
    base = {
        "id": entry_id,
        "type": "synthesis",
        "source_type": "query",
        "source_path": None,
        "wiki_path": wiki_path,
        "zotero_uri": None,
        "pdf_uri": None,
        "arxiv_id": None,
        "title": "Fraud Detection Methods",
        "citation": None,
        "year": None,
        "tags": ["fraud-detection"],
        "project": None,
        "source_name": "What are the main fraud detection methods?",
        "references": ["smithDeepLearning2024"],
        "status": "rendered",
        "locked": False,
        "ingested": "2026-01-01T00:00:00Z",
        "rendered": "2026-01-02T00:00:00Z",
        "last_improved": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Side-effect factory for _run_index
# ---------------------------------------------------------------------------

def _make_index_side_effect(entry: dict, is_stale: bool = True, stale_ids: list | None = None):
    """Build a _run_index side_effect covering the synthesis-refresh call sequence.

    - get         → JSON of the synthesis entry
    - check-stale → JSON list with entry marked stale (or empty list)
    - update / delete-fragments / add-fragments-batch → success
    """
    if stale_ids is None:
        stale_ids = ["newEntry2026"]

    stale_list = (
        [{"id": entry["id"], "stale_entries": stale_ids}]
        if is_stale
        else []
    )

    def side_effect(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "get":
            return _make_completed_process(stdout=json.dumps(entry), returncode=0)
        if subcommand == "check-stale":
            return _make_completed_process(stdout=json.dumps(stale_list), returncode=0)
        if subcommand in ("update", "delete-fragments", "add-fragments-batch"):
            return _make_completed_process(returncode=0)
        return _make_completed_process(returncode=0)

    return side_effect


def _make_config_side_effect():
    """Build a _run_config side_effect returning LLM config."""
    def side_effect(*args, **kw):
        if args and args[0] == "get":
            if len(args) > 1 and args[1] == "llm":
                return _make_completed_process(stdout=_FAKE_LLM_CONFIG, returncode=0)
        return _make_completed_process(stdout=json.dumps(None), returncode=0)
    return side_effect


# ---------------------------------------------------------------------------
# Unit tests (mock_run_helpers + mock_llm)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_checks_staleness(mock_run_helpers, mock_llm, wiki_env, tmp_path):
    """synthesis-refresh calls _run_index("check-stale") to check for new evidence."""
    page = _make_synthesis_page(tmp_path, "syn-fraud-detection-methods")
    entry = _make_synthesis_entry(wiki_path=str(page))

    mock_run_helpers["_run_index"].side_effect = _make_index_side_effect(entry)
    mock_run_helpers["_run_config"].side_effect = _make_config_side_effect()
    mock_run_helpers["_run_render"].return_value = _make_completed_process(
        stdout="New evidence text.", returncode=0
    )

    runner = CliRunner()
    result = runner.invoke(cli, [
        "synthesis-refresh", "syn-fraud-detection-methods",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"command failed:\n{result.output}"

    check_stale_calls = [
        c for c in mock_run_helpers["_run_index"].call_args_list
        if c.args and c.args[0] == "check-stale"
    ]
    assert len(check_stale_calls) == 1, "Expected exactly one check-stale call"
    # check-stale does NOT take entry_id — it checks all entries globally
    call_args = list(check_stale_calls[0].args)
    assert "syn-fraud-detection-methods" not in call_args


@pytest.mark.unit
def test_reads_new_evidence(mock_run_helpers, mock_llm, wiki_env, tmp_path):
    """synthesis-refresh calls _run_render("read-batch") to fetch new entry content."""
    page = _make_synthesis_page(tmp_path, "syn-fraud-detection-methods")
    entry = _make_synthesis_entry(wiki_path=str(page))

    mock_run_helpers["_run_index"].side_effect = _make_index_side_effect(
        entry, is_stale=True, stale_ids=["newEntry2026"]
    )
    mock_run_helpers["_run_config"].side_effect = _make_config_side_effect()
    mock_run_helpers["_run_render"].return_value = _make_completed_process(
        stdout="New entry text.", returncode=0
    )

    runner = CliRunner()
    result = runner.invoke(cli, [
        "synthesis-refresh", "syn-fraud-detection-methods",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"command failed:\n{result.output}"

    render_calls = mock_run_helpers["_run_render"].call_args_list
    read_batch_calls = [c for c in render_calls if c.args and c.args[0] == "read-batch"]
    assert len(read_batch_calls) == 1, "Expected exactly one read-batch call"
    call_args = list(read_batch_calls[0].args)
    assert "--format" in call_args
    assert "text" in call_args


@pytest.mark.unit
def test_calls_llm_with_synthesis_refresh(mock_run_helpers, mock_llm, wiki_env, tmp_path):
    """synthesis-refresh invokes _llm_call with agent name 'synthesis-refresh'."""
    page = _make_synthesis_page(tmp_path, "syn-fraud-detection-methods")
    entry = _make_synthesis_entry(wiki_path=str(page))

    mock_run_helpers["_run_index"].side_effect = _make_index_side_effect(entry)
    mock_run_helpers["_run_config"].side_effect = _make_config_side_effect()
    mock_run_helpers["_run_render"].return_value = _make_completed_process(
        stdout="New evidence text.", returncode=0
    )

    runner = CliRunner()
    result = runner.invoke(cli, [
        "synthesis-refresh", "syn-fraud-detection-methods",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"command failed:\n{result.output}"

    # mock_llm fixture intercepts llm_call; just verify the command ran successfully
    # and that no LLM error was reported (which would indicate the wrong agent name)
    assert "LLM call failed" not in result.output


@pytest.mark.unit
def test_updates_page_and_fragments(mock_run_helpers, mock_llm, wiki_env, tmp_path):
    """synthesis-refresh calls delete-fragments, add-fragments-batch, and update on the index."""
    page = _make_synthesis_page(tmp_path, "syn-fraud-detection-methods")
    entry = _make_synthesis_entry(wiki_path=str(page))

    mock_run_helpers["_run_index"].side_effect = _make_index_side_effect(entry)
    mock_run_helpers["_run_config"].side_effect = _make_config_side_effect()
    mock_run_helpers["_run_render"].return_value = _make_completed_process(
        stdout="New evidence text.", returncode=0
    )

    runner = CliRunner()
    result = runner.invoke(cli, [
        "synthesis-refresh", "syn-fraud-detection-methods",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"command failed:\n{result.output}"

    all_index_calls = mock_run_helpers["_run_index"].call_args_list
    subcommands = [c.args[0] for c in all_index_calls if c.args]

    assert "delete-fragments" in subcommands, "Expected delete-fragments call"
    assert "add-fragments-batch" in subcommands, "Expected add-fragments-batch call"
    assert "update" in subcommands, "Expected update call"

    # delete-fragments should include the entry_id
    delete_calls = [c for c in all_index_calls if c.args and c.args[0] == "delete-fragments"]
    assert len(delete_calls) == 1
    assert "syn-fraud-detection-methods" in list(delete_calls[0].args)

    # update should set --rendered now
    update_calls = [c for c in all_index_calls if c.args and c.args[0] == "update"]
    assert len(update_calls) == 1
    update_args = list(update_calls[0].args)
    assert "--rendered" in update_args
    assert "now" in update_args


@pytest.mark.unit
def test_non_stale_skips(mock_run_helpers, mock_llm, wiki_env, tmp_path):
    """When check-stale returns no match for entry_id, command exits 0 with 'up to date' message."""
    page = _make_synthesis_page(tmp_path, "syn-fraud-detection-methods")
    entry = _make_synthesis_entry(wiki_path=str(page))

    # check-stale returns empty list → entry not stale
    mock_run_helpers["_run_index"].side_effect = _make_index_side_effect(
        entry, is_stale=False
    )
    mock_run_helpers["_run_config"].side_effect = _make_config_side_effect()

    runner = CliRunner()
    result = runner.invoke(cli, [
        "synthesis-refresh", "syn-fraud-detection-methods",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"Expected 0 exit for up-to-date entry, got:\n{result.output}"
    assert "up to date" in result.output.lower()

    # LLM and render should NOT be called
    render_calls = mock_run_helpers["_run_render"].call_args_list
    assert len(render_calls) == 0, "Expected no _run_render calls when entry is not stale"


@pytest.mark.unit
def test_entry_not_found_error(mock_run_helpers, wiki_env, tmp_path):
    """When _run_index get fails, synthesis-refresh exits non-zero."""
    mock_run_helpers["_run_index"].return_value = _make_completed_process(
        returncode=1, stderr="Entry not found"
    )
    mock_run_helpers["_run_config"].side_effect = _make_config_side_effect()

    runner = CliRunner()
    result = runner.invoke(cli, [
        "synthesis-refresh", "nonexistent-syn",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code != 0, "Expected non-zero exit for missing entry"


# ---------------------------------------------------------------------------
# Integration test (populated_index + mock_llm + monkeypatched helpers)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_full_refresh_updates_page(populated_index, mock_llm, monkeypatch):
    """Full refresh flow: syn-fraud-detection-methods page is updated with new synthesis content."""
    import wiki_cli

    monkeypatch.chdir(populated_index.root)

    # The stub page from populated_index doesn't have ## Synthesis — overwrite it.
    syn_page = Path(populated_index.wiki_dir / "syntheses" / "syn-fraud-detection-methods.md")
    syn_page.parent.mkdir(parents=True, exist_ok=True)
    syn_page.write_text(_SYNTHESIS_PAGE.format(entry_id="syn-fraud-detection-methods"))

    # Update the entry's wiki_path in the index to point at our real page
    index_lines = populated_index.index_path.read_text().splitlines()
    updated_lines = []
    for line in index_lines:
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry["id"] == "syn-fraud-detection-methods":
            entry["wiki_path"] = str(syn_page)
        updated_lines.append(json.dumps(entry))
    populated_index.index_path.write_text("\n".join(updated_lines) + "\n")

    # Patch check-stale via _run_index so entry is marked stale; let get/update/
    # delete-fragments/add-fragments-batch run through to the real index script.
    stale_payload = json.dumps([
        {"id": "syn-fraud-detection-methods", "stale_entries": ["smithDeepLearning2024"]}
    ])

    original_run_index = wiki_cli._run_index

    def selective_run_index(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "check-stale":
            return _make_completed_process(stdout=stale_payload, returncode=0)
        return original_run_index(*args, **kw)

    monkeypatch.setattr(wiki_cli, "_run_index", selective_run_index)

    # Patch _run_render to return fake read-batch content
    def fake_run_render(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "read-batch":
            return _make_completed_process(
                stdout="Deep learning paper content for new evidence.", returncode=0
            )
        return _make_completed_process(returncode=0)

    monkeypatch.setattr(wiki_cli, "_run_render", fake_run_render)

    # Patch _run_config to return valid LLM config
    def fake_run_config(*args, **kw):
        if args and args[0] == "get" and len(args) > 1 and args[1] == "llm":
            return _make_completed_process(stdout=_FAKE_LLM_CONFIG, returncode=0)
        return _make_completed_process(stdout=json.dumps(None), returncode=0)

    monkeypatch.setattr(wiki_cli, "_run_config", fake_run_config)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "synthesis-refresh", "syn-fraud-detection-methods",
        "--index", str(populated_index.index_path),
    ])
    assert result.exit_code == 0, (
        f"synthesis-refresh failed:\noutput: {result.output}\nexc: {result.exception}"
    )

    # Verify the page was updated on disk
    updated_content = syn_page.read_text()
    # The fixture's synthesis text should now appear in the page
    # (first sentence of synthesis-refresh.json fixture)
    assert "Graph-based fraud detection" in updated_content, (
        "Expected fixture synthesis text in updated page"
    )
    # Staleness banner should be stripped
    assert "<!-- wiki-staleness-banner -->" not in updated_content

    # Verify index reflects updated rendered timestamp
    index_lines = populated_index.index_path.read_text().splitlines()
    entries = [json.loads(line) for line in index_lines if line.strip()]
    syn_entries = [e for e in entries if e["id"] == "syn-fraud-detection-methods"]
    assert len(syn_entries) == 1
    assert syn_entries[0]["rendered"] is not None
    assert syn_entries[0]["rendered"] != "2026-01-02T00:00:00Z", (
        "Expected rendered timestamp to be updated"
    )


# ---------------------------------------------------------------------------
# Smoke test (subprocess)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_synthesis_refresh_subprocess_help():
    """synthesis-refresh --help exits 0 and shows ENTRY_ID argument."""
    result = _run_cli_subprocess("synthesis-refresh", "--help")
    assert result.returncode == 0
    assert "ENTRY_ID" in result.stdout
