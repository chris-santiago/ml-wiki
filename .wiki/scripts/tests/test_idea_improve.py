"""Tests for wiki CLI `idea improve` subcommand."""

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
# Idea wiki page template
# ---------------------------------------------------------------------------

_IDEA_PAGE = """\
---
id: {entry_id}
type: idea
---

# {entry_id}

## Idea

A novel approach to temporal merchant encoding using sequence models.

<!-- end-user-zone -->

## Evidence Review

No evidence yet.

## Notes

<!-- end-notes -->

## Connections
"""


def _make_idea_page(tmp_path: Path, entry_id: str) -> Path:
    """Write an idea wiki page and return its path."""
    page = tmp_path / f"{entry_id}.md"
    page.write_text(_IDEA_PAGE.format(entry_id=entry_id))
    return page


# ---------------------------------------------------------------------------
# Idea entry builder
# ---------------------------------------------------------------------------

def _make_idea_entry(wiki_path: str, entry_id: str = "idea-merchant-encoder", **overrides) -> dict:
    base = {
        "id": entry_id,
        "type": "idea",
        "source_type": "manual",
        "source_path": None,
        "wiki_path": wiki_path,
        "zotero_uri": None,
        "pdf_uri": None,
        "arxiv_id": None,
        "title": "Merchant Encoder Idea",
        "citation": None,
        "year": None,
        "tags": ["sequence-modeling"],
        "project": None,
        "source_name": None,
        "references": [],
        "status": "rendered",
        "locked": False,
        "ingested": "2026-01-01T00:00:00Z",
        "rendered": "2026-01-02T00:00:00Z",
        "last_improved": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Side-effect factories
# ---------------------------------------------------------------------------

_IDEA_TEXT = "A novel approach to temporal merchant encoding using sequence models."

_SCORED_EVIDENCE = json.dumps([
    {"id": "jonesTransformers2023", "score": 0.91, "text": "Transformers excel at sequence modeling."},
    {"id": "smithDeepLearning2024", "score": 0.75, "text": "Deep learning for tabular data."},
])


def _make_index_side_effect(entry: dict):
    """Build a _run_index side_effect covering the idea-improve call sequence.

    - get           → JSON of the idea entry
    - update / delete-fragments / add-fragments-batch → success
    """
    def side_effect(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "get":
            return _make_completed_process(stdout=json.dumps(entry), returncode=0)
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


def _make_render_side_effect(captured_agent_output: dict | None = None):
    """Build a _run_render side_effect.

    - extract-zone  → returns idea text content
    - splice-idea   → optionally captures the agent output JSON, returns success
    """
    def side_effect(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "extract-zone":
            return _make_completed_process(stdout=_IDEA_TEXT, returncode=0)
        if subcommand == "splice-idea":
            if captured_agent_output is not None:
                # Locate the --agent-output flag and read the temp file
                arg_list = list(args)
                if "--agent-output" in arg_list:
                    idx = arg_list.index("--agent-output")
                    path = arg_list[idx + 1]
                    with open(path) as f:
                        captured_agent_output["data"] = json.load(f)
            return _make_completed_process(returncode=0)
        return _make_completed_process(returncode=0)

    return side_effect


# ---------------------------------------------------------------------------
# Unit tests (mock_run_helpers + mock_llm)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extracts_zone(mock_run_helpers, mock_llm, wiki_env, tmp_path):
    """idea improve calls _run_render('extract-zone') to read idea content."""
    page = _make_idea_page(tmp_path, "idea-merchant-encoder")
    entry = _make_idea_entry(wiki_path=str(page))

    mock_run_helpers["_run_index"].side_effect = _make_index_side_effect(entry)
    mock_run_helpers["_run_config"].side_effect = _make_config_side_effect()
    mock_run_helpers["_run_render"].side_effect = _make_render_side_effect()
    mock_run_helpers["_run_embed"].return_value = _make_completed_process(
        stdout=_SCORED_EVIDENCE, returncode=0
    )

    runner = CliRunner()
    result = runner.invoke(cli, [
        "idea", "improve", "idea-merchant-encoder",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"command failed:\n{result.output}\n{result.exception}"

    render_calls = mock_run_helpers["_run_render"].call_args_list
    extract_zone_calls = [c for c in render_calls if c.args and c.args[0] == "extract-zone"]
    assert len(extract_zone_calls) == 1, "Expected exactly one extract-zone call"
    # The wiki_path should be passed as the second positional arg
    assert str(page) in list(extract_zone_calls[0].args)


@pytest.mark.unit
def test_scores_against_index(mock_run_helpers, mock_llm, wiki_env, tmp_path):
    """idea improve calls _run_embed('score-idea') to find evidence (default, no --local)."""
    page = _make_idea_page(tmp_path, "idea-merchant-encoder")
    entry = _make_idea_entry(wiki_path=str(page))

    mock_run_helpers["_run_index"].side_effect = _make_index_side_effect(entry)
    mock_run_helpers["_run_config"].side_effect = _make_config_side_effect()
    mock_run_helpers["_run_render"].side_effect = _make_render_side_effect()
    mock_run_helpers["_run_embed"].return_value = _make_completed_process(
        stdout=_SCORED_EVIDENCE, returncode=0
    )

    runner = CliRunner()
    result = runner.invoke(cli, [
        "idea", "improve", "idea-merchant-encoder",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"command failed:\n{result.output}\n{result.exception}"

    embed_calls = mock_run_helpers["_run_embed"].call_args_list
    score_calls = [c for c in embed_calls if c.args and c.args[0] == "score-idea"]
    assert len(score_calls) == 1, "Expected exactly one score-idea call"
    # Should exclude the idea's own ID
    call_args = list(score_calls[0].args)
    assert "--exclude-id" in call_args
    assert "idea-merchant-encoder" in call_args


@pytest.mark.unit
def test_calls_llm_with_evidence(mock_run_helpers, mock_llm, wiki_env, tmp_path):
    """idea improve calls _llm_call('idea-improve') and includes evidence in the input."""
    page = _make_idea_page(tmp_path, "idea-merchant-encoder")
    entry = _make_idea_entry(wiki_path=str(page))

    mock_run_helpers["_run_index"].side_effect = _make_index_side_effect(entry)
    mock_run_helpers["_run_config"].side_effect = _make_config_side_effect()
    mock_run_helpers["_run_render"].side_effect = _make_render_side_effect()
    mock_run_helpers["_run_embed"].return_value = _make_completed_process(
        stdout=_SCORED_EVIDENCE, returncode=0
    )

    # Track the input passed to idea-improve
    captured_inputs: list[str] = []
    original_fixture = None

    import wiki_llm
    _FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

    def tracking_llm_call(agent, input_data, schema, config, **kwargs):
        if agent == "idea-improve":
            captured_inputs.append(input_data)
            fixture_path = _FIXTURES_DIR / "idea-improve.json"
            with open(fixture_path) as f:
                return json.load(f)
        # fall back for search-terms etc.
        fixture_path = _FIXTURES_DIR / f"{agent}.json"
        if fixture_path.exists():
            with open(fixture_path) as f:
                return json.load(f)
        raise ValueError(f"No fixture for agent '{agent}'")

    import wiki_llm
    import unittest.mock as mock
    with mock.patch.object(wiki_llm, "llm_call", side_effect=tracking_llm_call), \
         mock.patch.object(wiki_llm, "validate_schema", return_value=None):
        runner = CliRunner()
        result = runner.invoke(cli, [
            "idea", "improve", "idea-merchant-encoder",
            "--index", str(wiki_env.index_path),
        ])

    assert result.exit_code == 0, f"command failed:\n{result.output}\n{result.exception}"
    assert len(captured_inputs) == 1, "Expected exactly one idea-improve LLM call"
    # The evidence from score-idea should appear in the critique input
    assert "## Evidence" in captured_inputs[0]
    assert "## Idea" in captured_inputs[0]


@pytest.mark.unit
def test_tags_renamed_to_tags_finalized(mock_run_helpers, mock_llm, wiki_env, tmp_path):
    """The JSON file written for splice-idea has 'tags_finalized', NOT 'tags'.

    This tests the bug fix: idea-improve LLM schema returns 'tags', but
    splice-idea expects 'tags_finalized'. The rename must happen before the
    JSON file is written.
    """
    page = _make_idea_page(tmp_path, "idea-merchant-encoder")
    entry = _make_idea_entry(wiki_path=str(page))

    captured_agent_output: dict = {}

    mock_run_helpers["_run_index"].side_effect = _make_index_side_effect(entry)
    mock_run_helpers["_run_config"].side_effect = _make_config_side_effect()
    mock_run_helpers["_run_render"].side_effect = _make_render_side_effect(
        captured_agent_output=captured_agent_output
    )
    mock_run_helpers["_run_embed"].return_value = _make_completed_process(
        stdout=_SCORED_EVIDENCE, returncode=0
    )

    runner = CliRunner()
    result = runner.invoke(cli, [
        "idea", "improve", "idea-merchant-encoder",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"command failed:\n{result.output}\n{result.exception}"

    assert "data" in captured_agent_output, (
        "splice-idea was never called or --agent-output not found"
    )
    data = captured_agent_output["data"]

    # The fixture has "tags": [...] — after rename it must be "tags_finalized"
    assert "tags_finalized" in data, (
        f"Expected 'tags_finalized' key in agent output JSON, got keys: {list(data.keys())}"
    )
    assert "tags" not in data, (
        f"'tags' key must be absent from agent output JSON (was renamed), got keys: {list(data.keys())}"
    )
    # Verify the tag values were preserved
    assert data["tags_finalized"] == ["fraud-detection", "temporal-graphs", "graph-neural-networks", "dynamic-graphs"]


@pytest.mark.unit
def test_calls_splice_idea(mock_run_helpers, mock_llm, wiki_env, tmp_path):
    """idea improve calls _run_render('splice-idea') with --agent-output flag."""
    page = _make_idea_page(tmp_path, "idea-merchant-encoder")
    entry = _make_idea_entry(wiki_path=str(page))

    mock_run_helpers["_run_index"].side_effect = _make_index_side_effect(entry)
    mock_run_helpers["_run_config"].side_effect = _make_config_side_effect()
    mock_run_helpers["_run_render"].side_effect = _make_render_side_effect()
    mock_run_helpers["_run_embed"].return_value = _make_completed_process(
        stdout=_SCORED_EVIDENCE, returncode=0
    )

    runner = CliRunner()
    result = runner.invoke(cli, [
        "idea", "improve", "idea-merchant-encoder",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"command failed:\n{result.output}\n{result.exception}"

    render_calls = mock_run_helpers["_run_render"].call_args_list
    splice_calls = [c for c in render_calls if c.args and c.args[0] == "splice-idea"]
    assert len(splice_calls) == 1, "Expected exactly one splice-idea call"

    call_args = list(splice_calls[0].args)
    assert "--agent-output" in call_args, "Expected --agent-output flag in splice-idea call"
    # The wiki_path should also be passed
    assert str(page) in call_args


@pytest.mark.unit
def test_merges_tags_from_finalized(mock_run_helpers, mock_llm, wiki_env, tmp_path):
    """Index update uses tags from tags_finalized after the rename, not a stale 'tags' key.

    The entry has tags=['sequence-modeling']. The fixture produces
    tags_finalized=['fraud-detection', 'temporal-graphs', ...] after rename.
    The update call should contain the merged set.
    """
    page = _make_idea_page(tmp_path, "idea-merchant-encoder")
    entry = _make_idea_entry(wiki_path=str(page), tags=["sequence-modeling"])

    captured_update_args: list[list] = []

    def index_side_effect_capturing(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "get":
            return _make_completed_process(stdout=json.dumps(entry), returncode=0)
        if subcommand == "update":
            captured_update_args.append(list(args))
            return _make_completed_process(returncode=0)
        return _make_completed_process(returncode=0)

    mock_run_helpers["_run_index"].side_effect = index_side_effect_capturing
    mock_run_helpers["_run_config"].side_effect = _make_config_side_effect()
    mock_run_helpers["_run_render"].side_effect = _make_render_side_effect()
    mock_run_helpers["_run_embed"].return_value = _make_completed_process(
        stdout=_SCORED_EVIDENCE, returncode=0
    )

    runner = CliRunner()
    result = runner.invoke(cli, [
        "idea", "improve", "idea-merchant-encoder",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"command failed:\n{result.output}\n{result.exception}"

    assert len(captured_update_args) == 1, "Expected exactly one update call"
    update_args = captured_update_args[0]

    assert "--tags" in update_args, "Expected --tags in update call"
    tags_idx = update_args.index("--tags")
    merged_tags_str = update_args[tags_idx + 1]
    merged_tags = merged_tags_str.split(",")

    # Entry had sequence-modeling; fixture tags_finalized has 4 new tags
    # merge_tags preserves existing + adds new
    assert "sequence-modeling" in merged_tags, (
        "Original tag 'sequence-modeling' should survive merge"
    )
    assert "fraud-detection" in merged_tags, (
        "New tag from tags_finalized should be in merged set"
    )
    # Ensure --last-improved is set to now
    assert "--last-improved" in update_args
    assert "now" in update_args


@pytest.mark.unit
def test_entry_not_found_error(mock_run_helpers, wiki_env):
    """When _run_index get fails, idea improve exits non-zero."""
    mock_run_helpers["_run_index"].return_value = _make_completed_process(
        returncode=1, stderr="Entry not found"
    )
    mock_run_helpers["_run_config"].side_effect = _make_config_side_effect()

    runner = CliRunner()
    result = runner.invoke(cli, [
        "idea", "improve", "nonexistent-idea",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code != 0, "Expected non-zero exit for missing entry"


@pytest.mark.unit
def test_non_idea_entry_error(mock_run_helpers, wiki_env):
    """When the entry has no wiki_path (e.g. a stub paper), idea improve exits non-zero.

    The code checks 'if not wiki_path or not os.path.exists(wiki_path)'
    and exits 1, so a paper entry without a rendered wiki_path triggers this.
    """
    paper_entry = {
        "id": "smithDeepLearning2024",
        "type": "paper",
        "wiki_path": None,
        "tags": [],
        "title": "Deep Learning",
        "project": None,
    }

    def index_side_effect(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "get":
            return _make_completed_process(stdout=json.dumps(paper_entry), returncode=0)
        return _make_completed_process(returncode=0)

    mock_run_helpers["_run_index"].side_effect = index_side_effect
    mock_run_helpers["_run_config"].side_effect = _make_config_side_effect()

    runner = CliRunner()
    result = runner.invoke(cli, [
        "idea", "improve", "smithDeepLearning2024",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code != 0, (
        "Expected non-zero exit when entry has no wiki_path (non-idea type)"
    )


# ---------------------------------------------------------------------------
# Integration test (populated_index + mock_llm + monkeypatched helpers)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_full_improve_updates_page(populated_index, mock_llm, monkeypatch):
    """Full improve flow: idea-merchant-encoder page is updated via splice-idea."""
    import wiki_cli

    monkeypatch.chdir(populated_index.root)

    # Write a real idea page at the path the index entry points to
    idea_page = Path(populated_index.wiki_dir / "ideas" / "idea-merchant-encoder.md")
    idea_page.parent.mkdir(parents=True, exist_ok=True)
    idea_page.write_text(_IDEA_PAGE.format(entry_id="idea-merchant-encoder"))

    # Update the entry's wiki_path in the index to point at our real page
    index_lines = populated_index.index_path.read_text().splitlines()
    updated_lines = []
    for line in index_lines:
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry["id"] == "idea-merchant-encoder":
            entry["wiki_path"] = str(idea_page)
        updated_lines.append(json.dumps(entry))
    populated_index.index_path.write_text("\n".join(updated_lines) + "\n")

    # Track whether splice-idea was called
    splice_called: dict = {"called": False}

    def fake_run_render(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "extract-zone":
            return _make_completed_process(stdout=_IDEA_TEXT, returncode=0)
        if subcommand == "splice-idea":
            splice_called["called"] = True
            return _make_completed_process(returncode=0)
        return _make_completed_process(returncode=0)

    monkeypatch.setattr(wiki_cli, "_run_render", fake_run_render)

    # Patch _run_embed to return scored evidence
    def fake_run_embed(*args, **kw):
        return _make_completed_process(stdout=_SCORED_EVIDENCE, returncode=0)

    monkeypatch.setattr(wiki_cli, "_run_embed", fake_run_embed)

    # Patch _run_config to return valid LLM config
    def fake_run_config(*args, **kw):
        if args and args[0] == "get" and len(args) > 1 and args[1] == "llm":
            return _make_completed_process(stdout=_FAKE_LLM_CONFIG, returncode=0)
        return _make_completed_process(stdout=json.dumps(None), returncode=0)

    monkeypatch.setattr(wiki_cli, "_run_config", fake_run_config)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "idea", "improve", "idea-merchant-encoder",
        "--index", str(populated_index.index_path),
    ])
    assert result.exit_code == 0, (
        f"idea improve failed:\noutput: {result.output}\nexc: {result.exception}"
    )

    # Verify splice-idea was invoked
    assert splice_called["called"], "Expected splice-idea to be called"

    # Verify the page still exists on disk
    assert idea_page.exists(), "Idea page should still exist after improve"

    # Verify index reflects last_improved timestamp
    index_lines = populated_index.index_path.read_text().splitlines()
    entries = [json.loads(line) for line in index_lines if line.strip()]
    idea_entries = [e for e in entries if e["id"] == "idea-merchant-encoder"]
    assert len(idea_entries) == 1
    assert idea_entries[0]["last_improved"] is not None, (
        "Expected last_improved to be set after improve"
    )


# ---------------------------------------------------------------------------
# Smoke test (subprocess)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_idea_improve_subprocess_help():
    """idea improve --help exits 0 and shows --local flag."""
    result = _run_cli_subprocess("idea", "improve", "--help")
    assert result.returncode == 0
    assert "--local" in result.stdout
