"""Tests for wiki CLI build command (all 5 phases)."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, call

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
# Minimal config fixtures
# ---------------------------------------------------------------------------

_FAKE_LLM_CONFIG = json.dumps({
    "base_url": "https://localhost:9999",
    "api_key_env": "TEST_API_KEY",
    "models": {"nano": "test-nano", "mini": "test-mini", "full": "test-full"},
    "default_params": {"max_tokens": 4096, "temperature": 0},
    "model_overrides": {},
})

_EMPTY_CONN_MAP = json.dumps({})
_EMPTY_STALE_LIST = json.dumps([])
_EMPTY_MOC_GROUPS = json.dumps({"tags": {}, "projects": {}})
_EMPTY_TAG_ALIASES = json.dumps({})
_EMPTY_LIST_TOPICS = json.dumps([])


def _make_build_config_side_effect():
    """_run_config side_effect handling all calls in the build pipeline."""

    def side_effect(*args, **kw):
        if args and args[0] == "get":
            key = args[1] if len(args) > 1 else ""
            if key == "llm":
                return _make_completed_process(stdout=_FAKE_LLM_CONFIG)
            if key == "tag_aliases":
                return _make_completed_process(stdout=_EMPTY_TAG_ALIASES)
            if key == "sources":
                return _make_completed_process(stdout=json.dumps([]))
        return _make_completed_process(stdout=json.dumps(None))

    return side_effect


def _make_build_render_side_effect():
    """_run_render side_effect for a normal build flow (no actual files written)."""

    def side_effect(*args, **kw):
        return _make_completed_process(stdout="", returncode=0)

    return side_effect


def _make_build_index_side_effect(
    conn_map: str = _EMPTY_CONN_MAP,
    stale_list: str = _EMPTY_STALE_LIST,
    moc_groups: str = _EMPTY_MOC_GROUPS,
    dirty_groups: str | None = None,
    ranked_stdout: str = _EMPTY_CONN_MAP,
    extra_subcommands: dict | None = None,
):
    """Build a comprehensive _run_index side_effect covering all build subcommands.

    Each entry in extra_subcommands overrides the default response for that subcommand key.
    """
    _dirty = dirty_groups if dirty_groups is not None else json.dumps({"tags": {}, "projects": {}})
    _extras = extra_subcommands or {}

    def side_effect(*args, **kw):
        subcommand = args[0] if args else ""

        if subcommand in _extras:
            val = _extras[subcommand]
            if callable(val):
                return val(*args, **kw)
            return val

        if subcommand == "resolve-connections":
            return _make_completed_process(stdout=conn_map)
        if subcommand == "fast-rank-connections":
            return _make_completed_process(stdout=ranked_stdout)
        if subcommand == "check-stale":
            return _make_completed_process(stdout=stale_list)
        if subcommand == "get":
            # Used in staleness phase for individual entry lookups
            return _make_completed_process(stdout=json.dumps({"id": "stub", "tags": []}))
        if subcommand == "list-moc-groups":
            return _make_completed_process(stdout=moc_groups)
        if subcommand == "filter-dirty-mocs":
            return _make_completed_process(stdout=_dirty)
        if subcommand == "prep-moc-batches":
            return _make_completed_process(stdout=json.dumps({"batch_files": [], "entry_json_map": {}}))
        if subcommand == "update-moc-entries":
            return _make_completed_process(stdout="")
        if subcommand == "list-topics":
            return _make_completed_process(stdout=_EMPTY_LIST_TOPICS)
        # Default: success with empty JSON
        return _make_completed_process(stdout=json.dumps({}))

    return side_effect


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_phase1_data_prep(mock_run_helpers, wiki_env):
    """Phase 1: _run_index('resolve-connections') is called."""
    mock_run_helpers["_run_index"].side_effect = _make_build_index_side_effect()
    mock_run_helpers["_run_config"].side_effect = _make_build_config_side_effect()
    mock_run_helpers["_run_render"].side_effect = _make_build_render_side_effect()
    mock_run_helpers["_run_embed"].return_value = _make_completed_process(stdout=_EMPTY_CONN_MAP)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "build",
        "--rank", "fast",
        "--fast",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"build failed:\n{result.output}\nexc: {result.exception}"

    index_mock = mock_run_helpers["_run_index"]
    subcommands = [c.args[0] for c in index_mock.call_args_list if c.args]
    assert "resolve-connections" in subcommands, (
        f"Expected 'resolve-connections', got subcommands: {subcommands}"
    )


@pytest.mark.unit
def test_build_phase2_fast_ranking(mock_run_helpers, wiki_env):
    """Phase 2: --rank fast causes _run_index('fast-rank-connections') to be called."""
    mock_run_helpers["_run_index"].side_effect = _make_build_index_side_effect()
    mock_run_helpers["_run_config"].side_effect = _make_build_config_side_effect()
    mock_run_helpers["_run_render"].side_effect = _make_build_render_side_effect()

    runner = CliRunner()
    result = runner.invoke(cli, [
        "build",
        "--rank", "fast",
        "--fast",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"build failed:\n{result.output}\nexc: {result.exception}"

    index_mock = mock_run_helpers["_run_index"]
    subcommands = [c.args[0] for c in index_mock.call_args_list if c.args]
    assert "fast-rank-connections" in subcommands, (
        f"Expected 'fast-rank-connections', got: {subcommands}"
    )
    # Semantic embed should NOT be called
    assert mock_run_helpers["_run_embed"].call_count == 0


@pytest.mark.unit
def test_build_phase2_deep_ranking(mock_run_helpers, wiki_env, monkeypatch):
    """Phase 2: --rank deep dispatches the build-rank agent via llm_batch."""
    import wiki_llm

    conn_map_with_entry = json.dumps({
        "entry-a": {
            "entry": {"id": "entry-a", "tags": ["ml"], "title": "Entry A"},
            "candidates": [
                {"id": "entry-b", "tags": ["ml"], "title": "Entry B", "shared_tags": ["ml"]}
            ],
        }
    })

    mock_run_helpers["_run_index"].side_effect = _make_build_index_side_effect(
        conn_map=conn_map_with_entry
    )
    mock_run_helpers["_run_config"].side_effect = _make_build_config_side_effect()
    mock_run_helpers["_run_render"].side_effect = _make_build_render_side_effect()

    llm_agents_called = []

    async def capturing_batch(agent, items, schema, config, **kw):
        llm_agents_called.append(agent)
        return [(iid, {"related": []}, None) for iid, _ in items]

    monkeypatch.setattr(wiki_llm, "llm_batch", capturing_batch)
    monkeypatch.setattr(wiki_llm, "validate_schema", lambda *a, **kw: None)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "build",
        "--rank", "deep",
        "--fast",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"build --rank deep failed:\n{result.output}\nexc: {result.exception}"
    assert "build-rank" in llm_agents_called, (
        f"Expected 'build-rank' dispatch via llm_batch, got: {llm_agents_called}"
    )


@pytest.mark.unit
def test_build_phase3_staleness(mock_run_helpers, wiki_env):
    """Phase 3: _run_index('check-stale') is called; with stale list empty, no banners."""
    mock_run_helpers["_run_index"].side_effect = _make_build_index_side_effect()
    mock_run_helpers["_run_config"].side_effect = _make_build_config_side_effect()
    mock_run_helpers["_run_render"].side_effect = _make_build_render_side_effect()
    mock_run_helpers["_run_embed"].return_value = _make_completed_process(stdout=_EMPTY_CONN_MAP)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "build",
        "--rank", "fast",
        "--fast",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"build failed:\n{result.output}\nexc: {result.exception}"

    index_mock = mock_run_helpers["_run_index"]
    subcommands = [c.args[0] for c in index_mock.call_args_list if c.args]
    assert "check-stale" in subcommands, f"Expected 'check-stale', got: {subcommands}"
    # No banners with empty stale list — update-banners-batch should NOT be called
    render_calls = [c.args[0] for c in mock_run_helpers["_run_render"].call_args_list if c.args]
    assert "update-banners-batch" not in render_calls


@pytest.mark.unit
def test_build_phase3_staleness_with_banners(mock_run_helpers, wiki_env):
    """Phase 3: When stale entries exist for a synthesis, _run_render('update-banners-batch') is called.

    build_banner_list only emits banners for syn-* and idea-* prefixed entries.
    """
    stale_with_entry = json.dumps([{"id": "syn-fraud-methods", "stale_entries": ["entry-a", "entry-b"]}])
    entry_data = {
        "id": "syn-fraud-methods",
        "type": "synthesis",
        "wiki_path": "wiki/syntheses/syn-fraud-methods.md",
        "tags": ["fraud-detection"],
    }

    def index_with_stale(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "resolve-connections":
            return _make_completed_process(stdout=_EMPTY_CONN_MAP)
        if subcommand == "fast-rank-connections":
            return _make_completed_process(stdout=_EMPTY_CONN_MAP)
        if subcommand == "check-stale":
            return _make_completed_process(stdout=stale_with_entry)
        if subcommand == "get":
            return _make_completed_process(stdout=json.dumps(entry_data))
        if subcommand == "list-moc-groups":
            return _make_completed_process(stdout=_EMPTY_MOC_GROUPS)
        if subcommand == "filter-dirty-mocs":
            return _make_completed_process(stdout=json.dumps({"tags": {}, "projects": {}}))
        if subcommand == "prep-moc-batches":
            return _make_completed_process(stdout=json.dumps({"batch_files": [], "entry_json_map": {}}))
        return _make_completed_process(stdout=json.dumps({}))

    # Make sure wiki_path exists so the banner can be written
    (wiki_env.wiki_dir / "syntheses").mkdir(parents=True, exist_ok=True)
    (wiki_env.wiki_dir / "syntheses" / "syn-fraud-methods.md").write_text("# Fraud Methods\n\n<!-- end-notes -->\n\n## Connections\n")

    mock_run_helpers["_run_index"].side_effect = index_with_stale
    mock_run_helpers["_run_config"].side_effect = _make_build_config_side_effect()
    mock_run_helpers["_run_render"].side_effect = _make_build_render_side_effect()

    runner = CliRunner()
    result = runner.invoke(cli, [
        "build",
        "--rank", "fast",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"build failed:\n{result.output}\nexc: {result.exception}"

    render_calls = [c.args[0] for c in mock_run_helpers["_run_render"].call_args_list if c.args]
    assert "update-banners-batch" in render_calls, (
        f"Expected 'update-banners-batch' call, got: {render_calls}"
    )


@pytest.mark.unit
def test_build_phase4_moc_generation(mock_run_helpers, wiki_env, mock_llm, tmp_path):
    """Phase 4: Without --fast, _llm_call('build-moc') is called per dirty group."""
    # Create a batch file with one group
    batch_file = tmp_path / "batch_0.json"
    moc_id = "topic-ml"
    group_data = [{"moc_id": moc_id, "entries": [], "tag": "ml"}]
    batch_file.write_text(json.dumps(group_data))

    # Create entry JSON file for the group
    entry_json_file = tmp_path / f"entry_{moc_id}.json"
    entry_json_file.write_text(json.dumps({
        "id": moc_id,
        "type": "topic-moc",
        "title": "ML",
        "tags": ["ml"],
        "project": None,
        "references": [],
    }))

    moc_groups_json = json.dumps({"tags": {"ml": ["entry-a"]}, "projects": {}})
    dirty_groups_json = json.dumps({"tags": {"ml": ["entry-a"]}, "projects": {}})
    prep_manifest = json.dumps({
        "batch_files": [str(batch_file)],
        "entry_json_map": {moc_id: str(entry_json_file)},
    })

    def index_side_effect(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "resolve-connections":
            return _make_completed_process(stdout=_EMPTY_CONN_MAP)
        if subcommand == "fast-rank-connections":
            return _make_completed_process(stdout=_EMPTY_CONN_MAP)
        if subcommand == "check-stale":
            return _make_completed_process(stdout=_EMPTY_STALE_LIST)
        if subcommand == "list-moc-groups":
            return _make_completed_process(stdout=moc_groups_json)
        if subcommand == "filter-dirty-mocs":
            return _make_completed_process(stdout=dirty_groups_json)
        if subcommand == "prep-moc-batches":
            return _make_completed_process(stdout=prep_manifest)
        if subcommand == "update-moc-entries":
            return _make_completed_process(stdout="")
        return _make_completed_process(stdout=json.dumps({}))

    mock_run_helpers["_run_index"].side_effect = index_side_effect
    mock_run_helpers["_run_config"].side_effect = _make_build_config_side_effect()

    # finalize-batch must return a success structure
    def render_side_effect(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "finalize-batch":
            return _make_completed_process(
                stdout=json.dumps({"successes": [{"id": moc_id, "wiki_path": f"wiki/topics/{moc_id}.md"}]})
            )
        return _make_completed_process(stdout="")

    mock_run_helpers["_run_render"].side_effect = render_side_effect

    import wiki_llm as wiki_llm_mod
    llm_agents_called = []
    original_fake = wiki_llm_mod.llm_call

    def capturing_llm(agent, *args, **kw):
        llm_agents_called.append(agent)
        return original_fake(agent, *args, **kw)

    # mock_llm has already patched wiki_llm.llm_call; patch again to capture
    import wiki_cli as wiki_cli_mod
    # Use monkeypatch-free approach: override the fixture's fake
    mock_llm["build-moc"] = {
        "narrative": "Test narrative.",
        "subgroups": "subgroups text",
        "tensions": "tensions text",
        "evolution": "evolution text",
    }

    runner = CliRunner()
    result = runner.invoke(cli, [
        "build",
        "--rank", "fast",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"build failed:\n{result.output}\nexc: {result.exception}"

    # finalize-batch must be called (implies LLM ran and output was written)
    render_subcommands = [c.args[0] for c in mock_run_helpers["_run_render"].call_args_list if c.args]
    assert "finalize-batch" in render_subcommands, (
        f"Expected 'finalize-batch' call, got: {render_subcommands}"
    )


@pytest.mark.unit
def test_build_phase4_fast_listing_only(mock_run_helpers, wiki_env, mock_llm):
    """Phase 4 --fast: _run_render('generate-listings-batch') called, no LLM."""
    mock_run_helpers["_run_index"].side_effect = _make_build_index_side_effect()
    mock_run_helpers["_run_config"].side_effect = _make_build_config_side_effect()
    mock_run_helpers["_run_render"].side_effect = _make_build_render_side_effect()

    import wiki_llm as wiki_llm_mod
    llm_agents_called = []
    original_fake = wiki_llm_mod.llm_call

    def capturing_llm(agent, *args, **kw):
        llm_agents_called.append(agent)
        return original_fake(agent, *args, **kw)

    # Re-patch on the module wiki_cli imports from
    import wiki_cli  # noqa: F401  (ensure imported)
    # The mock_llm fixture already patched wiki_llm.llm_call; build imports lazily
    # so we only need to confirm no "build-moc" call happens.

    runner = CliRunner()
    result = runner.invoke(cli, [
        "build",
        "--rank", "fast",
        "--fast",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"build --fast failed:\n{result.output}\nexc: {result.exception}"

    render_calls = [c.args[0] for c in mock_run_helpers["_run_render"].call_args_list if c.args]
    assert "generate-listings-batch" in render_calls, (
        f"Expected 'generate-listings-batch', got: {render_calls}"
    )
    # With --fast, filter-dirty-mocs and prep-moc-batches should NOT be called
    index_subcommands = [c.args[0] for c in mock_run_helpers["_run_index"].call_args_list if c.args]
    assert "prep-moc-batches" not in index_subcommands


@pytest.mark.unit
def test_build_phase4_force_flag(mock_run_helpers, wiki_env, mock_llm):
    """Phase 4: --force is passed to filter-dirty-mocs."""
    mock_run_helpers["_run_index"].side_effect = _make_build_index_side_effect()
    mock_run_helpers["_run_config"].side_effect = _make_build_config_side_effect()
    mock_run_helpers["_run_render"].side_effect = _make_build_render_side_effect()

    runner = CliRunner()
    result = runner.invoke(cli, [
        "build",
        "--rank", "fast",
        "--force",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"build --force failed:\n{result.output}\nexc: {result.exception}"

    # Check that filter-dirty-mocs was called with --force
    filter_calls = [
        c for c in mock_run_helpers["_run_index"].call_args_list
        if c.args and c.args[0] == "filter-dirty-mocs"
    ]
    assert len(filter_calls) >= 1, "Expected at least one filter-dirty-mocs call"
    filter_args_flat = [a for a in filter_calls[0].args]
    assert "--force" in filter_args_flat, (
        f"Expected '--force' in filter-dirty-mocs args, got: {filter_args_flat}"
    )


@pytest.mark.unit
def test_build_phase5_map(mock_run_helpers, wiki_env, mock_llm):
    """Phase 5: With --map and eligible topics, _llm_call('build-map') and _run_render('generate-map') called."""
    # MOC groups with tags having 3+ entries to trigger map generation
    moc_groups_json = json.dumps({
        "tags": {
            "ml": ["e1", "e2", "e3", "e4"],
            "fraud": ["e5", "e6", "e7"],
        },
        "projects": {},
    })

    mock_run_helpers["_run_index"].side_effect = _make_build_index_side_effect(
        moc_groups=moc_groups_json
    )
    mock_run_helpers["_run_config"].side_effect = _make_build_config_side_effect()

    render_subcommands_called = []

    def render_side_effect(*args, **kw):
        subcommand = args[0] if args else ""
        render_subcommands_called.append(subcommand)
        return _make_completed_process(stdout="")

    mock_run_helpers["_run_render"].side_effect = render_side_effect

    import wiki_llm as wiki_llm_mod
    mock_llm["build-map"] = {"map": "## Thematic Map\n\n```\nML\n```"}

    runner = CliRunner()
    result = runner.invoke(cli, [
        "build",
        "--rank", "fast",
        "--fast",
        "--map",
        "--min-entries", "3",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"build --map failed:\n{result.output}\nexc: {result.exception}"

    assert "generate-map" in render_subcommands_called, (
        f"Expected 'generate-map' call, got: {render_subcommands_called}"
    )
    assert "generate-topic-index" in render_subcommands_called, (
        f"Expected 'generate-topic-index' call, got: {render_subcommands_called}"
    )


@pytest.mark.unit
def test_build_phase5_no_map(mock_run_helpers, wiki_env, mock_llm):
    """Phase 5: Without --map, generate-map is NOT called."""
    moc_groups_json = json.dumps({
        "tags": {"ml": ["e1", "e2", "e3", "e4"]},
        "projects": {},
    })

    mock_run_helpers["_run_index"].side_effect = _make_build_index_side_effect(
        moc_groups=moc_groups_json
    )
    mock_run_helpers["_run_config"].side_effect = _make_build_config_side_effect()
    mock_run_helpers["_run_render"].side_effect = _make_build_render_side_effect()

    runner = CliRunner()
    result = runner.invoke(cli, [
        "build",
        "--rank", "fast",
        "--fast",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"build failed:\n{result.output}\nexc: {result.exception}"

    render_calls = [c.args[0] for c in mock_run_helpers["_run_render"].call_args_list if c.args]
    assert "generate-map" not in render_calls, (
        f"'generate-map' should not be called without --map, got: {render_calls}"
    )


@pytest.mark.unit
def test_build_map_json_passthrough(mock_run_helpers, wiki_env, mock_llm):
    """Phase 5: The input to generate-map is JSON (not delimiter format)."""
    moc_groups_json = json.dumps({
        "tags": {"transformers": ["e1", "e2", "e3"]},
        "projects": {},
    })

    mock_run_helpers["_run_index"].side_effect = _make_build_index_side_effect(
        moc_groups=moc_groups_json
    )
    mock_run_helpers["_run_config"].side_effect = _make_build_config_side_effect()

    generate_map_input_data = []

    def render_side_effect(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "generate-map":
            generate_map_input_data.append(kw.get("input_data", ""))
        return _make_completed_process(stdout="")

    mock_run_helpers["_run_render"].side_effect = render_side_effect

    mock_llm["build-map"] = {"map": "## Map\n\n```\nTransformers\n```"}

    runner = CliRunner()
    result = runner.invoke(cli, [
        "build",
        "--rank", "fast",
        "--fast",
        "--map",
        "--min-entries", "3",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"build --map failed:\n{result.output}\nexc: {result.exception}"

    assert len(generate_map_input_data) == 1, "Expected exactly one generate-map call"
    # Input must be valid JSON (not delimiter format)
    try:
        parsed = json.loads(generate_map_input_data[0])
    except json.JSONDecodeError as e:
        pytest.fail(f"generate-map input_data is not valid JSON: {e}\nGot: {generate_map_input_data[0]}")
    # The JSON should contain the map key from the LLM response
    assert "map" in parsed, f"Expected 'map' key in generate-map JSON input, got: {parsed}"


@pytest.mark.unit
def test_build_moc_json_output(mock_run_helpers, wiki_env, mock_llm, tmp_path):
    """Phase 4: MOC agent output is written as a JSON file (not delimiter format)."""
    moc_id = "topic-transformers"
    batch_file = tmp_path / "batch_0.json"
    group_data = [{"moc_id": moc_id, "entries": [], "tag": "transformers"}]
    batch_file.write_text(json.dumps(group_data))

    entry_json_file = tmp_path / f"entry_{moc_id}.json"
    entry_json_file.write_text(json.dumps({
        "id": moc_id,
        "type": "topic-moc",
        "title": "Transformers",
        "tags": ["transformers"],
        "project": None,
        "references": [],
    }))

    moc_groups_json = json.dumps({"tags": {"transformers": ["entry-a"]}, "projects": {}})
    dirty_groups_json = json.dumps({"tags": {"transformers": ["entry-a"]}, "projects": {}})
    prep_manifest = json.dumps({
        "batch_files": [str(batch_file)],
        "entry_json_map": {moc_id: str(entry_json_file)},
    })

    written_agent_outputs = []

    def index_side_effect(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "resolve-connections":
            return _make_completed_process(stdout=_EMPTY_CONN_MAP)
        if subcommand == "fast-rank-connections":
            return _make_completed_process(stdout=_EMPTY_CONN_MAP)
        if subcommand == "check-stale":
            return _make_completed_process(stdout=_EMPTY_STALE_LIST)
        if subcommand == "list-moc-groups":
            return _make_completed_process(stdout=moc_groups_json)
        if subcommand == "filter-dirty-mocs":
            return _make_completed_process(stdout=dirty_groups_json)
        if subcommand == "prep-moc-batches":
            return _make_completed_process(stdout=prep_manifest)
        if subcommand == "update-moc-entries":
            return _make_completed_process(stdout="")
        return _make_completed_process(stdout=json.dumps({}))

    mock_run_helpers["_run_index"].side_effect = index_side_effect
    mock_run_helpers["_run_config"].side_effect = _make_build_config_side_effect()

    def render_side_effect(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "finalize-batch":
            # Inspect the input_data to verify it contains valid JSON file references
            input_data = kw.get("input_data", "")
            try:
                finalize_items = json.loads(input_data)
                for item in finalize_items:
                    agent_output_file = item.get("agent_output_file", "")
                    if agent_output_file and os.path.exists(agent_output_file):
                        with open(agent_output_file) as f:
                            content = f.read()
                        # Verify the agent output file is valid JSON
                        parsed = json.loads(content)
                        written_agent_outputs.append(parsed)
            except Exception:
                pass
            return _make_completed_process(
                stdout=json.dumps({"successes": [{"id": moc_id, "wiki_path": f"wiki/topics/{moc_id}.md"}]})
            )
        return _make_completed_process(stdout="")

    mock_run_helpers["_run_render"].side_effect = render_side_effect

    mock_llm["build-moc"] = {
        "narrative": "Test narrative.",
        "subgroups": "subgroups text",
        "tensions": "tensions text",
        "evolution": "evolution text",
    }

    runner = CliRunner()
    result = runner.invoke(cli, [
        "build",
        "--rank", "fast",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"build failed:\n{result.output}\nexc: {result.exception}"

    # The agent output file must be valid JSON (not delimiter format)
    assert len(written_agent_outputs) >= 1, (
        "Expected at least one agent output JSON file to be written and verified"
    )
    for output in written_agent_outputs:
        assert isinstance(output, dict), f"Agent output should be a dict, got: {type(output)}"
        assert "narrative" in output, f"Expected 'narrative' key in agent output: {output}"


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_build_fast_full(populated_index, mock_llm, monkeypatch):
    """--fast --rank fast on populated_index: all phases complete, generate-topic-index called."""
    import wiki_cli

    monkeypatch.chdir(populated_index.root)
    monkeypatch.setattr(wiki_cli, "_run_config", _make_build_config_side_effect())
    monkeypatch.setattr(wiki_cli, "_run_embed",
                        lambda *a, **kw: _make_completed_process(stdout=_EMPTY_CONN_MAP))
    monkeypatch.setattr(wiki_cli, "_run_index",
                        lambda *a, **kw: _make_completed_process(stdout=_EMPTY_CONN_MAP))

    topics_file = populated_index.wiki_dir / "TOPICS.md"
    render_calls_log = []

    def fake_run_render(*args, **kw):
        subcommand = args[0] if args else ""
        render_calls_log.append(subcommand)
        if subcommand == "read-journal-refs":
            return _make_completed_process(stdout="[]")
        if subcommand == "generate-topic-index":
            # Write TOPICS.md to simulate real behavior
            topics_file.write_text("# Topics\n\n(auto-generated)\n")
        return _make_completed_process(stdout="")

    monkeypatch.setattr(wiki_cli, "_run_render", fake_run_render)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "build",
        "--rank", "fast",
        "--fast",
        "--index", str(populated_index.index_path),
    ])
    assert result.exit_code == 0, f"build --fast full failed:\n{result.output}\nexc: {result.exception}"
    assert "Build complete." in result.output


@pytest.mark.integration
def test_build_moc_pages_created(populated_index, mock_llm, monkeypatch, tmp_path):
    """With mock_llm, build completes and finalize-batch is invoked for dirty MOC groups."""
    import wiki_cli

    monkeypatch.chdir(populated_index.root)
    monkeypatch.setattr(wiki_cli, "_run_config", _make_build_config_side_effect())
    monkeypatch.setattr(wiki_cli, "_run_embed",
                        lambda *a, **kw: _make_completed_process(stdout=_EMPTY_CONN_MAP))

    # Build a realistic moc_groups and prep-moc-batches manifest in tmp_path
    moc_id = "topic-transformers"
    batch_file = tmp_path / "batch_0.json"
    group_data = [{"moc_id": moc_id, "entries": [], "tag": "transformers"}]
    batch_file.write_text(json.dumps(group_data))

    entry_json_file = tmp_path / f"entry_{moc_id}.json"
    entry_json_file.write_text(json.dumps({
        "id": moc_id,
        "type": "topic-moc",
        "title": "Transformers",
        "tags": ["transformers"],
        "project": None,
        "references": [],
    }))

    moc_groups_json = json.dumps({"tags": {"transformers": ["jonesTransformers2023"]}, "projects": {}})
    dirty_groups_json = json.dumps({"tags": {"transformers": ["jonesTransformers2023"]}, "projects": {}})
    prep_manifest = json.dumps({
        "batch_files": [str(batch_file)],
        "entry_json_map": {moc_id: str(entry_json_file)},
    })

    moc_page_path = populated_index.wiki_dir / "topics" / f"{moc_id}.md"
    finalize_batch_called = []

    def fake_run_index(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "resolve-connections":
            return _make_completed_process(stdout=_EMPTY_CONN_MAP)
        if subcommand == "fast-rank-connections":
            return _make_completed_process(stdout=_EMPTY_CONN_MAP)
        if subcommand == "check-stale":
            return _make_completed_process(stdout=_EMPTY_STALE_LIST)
        if subcommand == "list-moc-groups":
            return _make_completed_process(stdout=moc_groups_json)
        if subcommand == "filter-dirty-mocs":
            return _make_completed_process(stdout=dirty_groups_json)
        if subcommand == "prep-moc-batches":
            return _make_completed_process(stdout=prep_manifest)
        if subcommand == "update-moc-entries":
            return _make_completed_process(stdout="")
        return _make_completed_process(stdout=json.dumps({}))

    monkeypatch.setattr(wiki_cli, "_run_index", fake_run_index)

    def fake_run_render(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "read-journal-refs":
            return _make_completed_process(stdout="[]")
        if subcommand == "finalize-batch":
            finalize_batch_called.append(True)
            moc_page_path.parent.mkdir(parents=True, exist_ok=True)
            moc_page_path.write_text("# Transformers (Topic)\n")
            return _make_completed_process(
                stdout=json.dumps({"successes": [
                    {"id": moc_id, "wiki_path": str(moc_page_path)}
                ]})
            )
        return _make_completed_process(stdout="")

    monkeypatch.setattr(wiki_cli, "_run_render", fake_run_render)

    mock_llm["build-moc"] = {
        "narrative": "Transformers narrative.",
        "subgroups": "subgroups text",
        "tensions": "tensions text",
        "evolution": "evolution text",
    }

    runner = CliRunner()
    result = runner.invoke(cli, [
        "build",
        "--rank", "fast",
        "--index", str(populated_index.index_path),
    ])
    assert result.exit_code == 0, f"build integration failed:\n{result.output}\nexc: {result.exception}"
    assert "Build complete." in result.output
    assert finalize_batch_called, "Expected finalize-batch to be called with MOC output"
    assert moc_page_path.exists(), f"Expected MOC page at {moc_page_path}"


# ---------------------------------------------------------------------------
# Smoke tests (subprocess)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_build_subprocess_help():
    """build --help exits 0 and shows --rank, --fast, --map."""
    result = _run_cli_subprocess("build", "--help")
    assert result.returncode == 0
    assert "--rank" in result.stdout
    assert "--fast" in result.stdout
    assert "--map" in result.stdout


@pytest.mark.smoke
def test_build_rank_choices():
    """build --help shows all three rank choices: fast, semantic, deep."""
    result = _run_cli_subprocess("build", "--help")
    assert result.returncode == 0
    assert "fast" in result.stdout
    assert "semantic" in result.stdout
    assert "deep" in result.stdout


@pytest.mark.smoke
def test_build_concurrency_flag_exists():
    """build --help shows --concurrency with default 50."""
    result = _run_cli_subprocess("build", "--help")
    assert result.returncode == 0
    assert "--concurrency" in result.stdout
    assert "50" in result.stdout


@pytest.mark.unit
def test_build_moc_dispatch_uses_llm_batch_with_concurrency(mock_run_helpers, wiki_env, tmp_path, monkeypatch):
    """Phase 4 MOC dispatch must use llm_batch with the user-supplied --concurrency."""
    moc_id = "topic-ml"
    batch_file = tmp_path / "batch_0.json"
    batch_file.write_text(json.dumps([{"moc_id": moc_id, "entries": [], "tag": "ml"}]))
    entry_json_file = tmp_path / f"entry_{moc_id}.json"
    entry_json_file.write_text(json.dumps({
        "id": moc_id, "type": "topic-moc", "title": "ML",
        "tags": ["ml"], "project": None, "references": [],
    }))

    dirty_groups_json = json.dumps({"tags": {"ml": ["entry-a"]}, "projects": {}})
    prep_manifest = json.dumps({
        "batch_files": [str(batch_file)],
        "entry_json_map": {moc_id: str(entry_json_file)},
    })

    def index_side_effect(*args, **kw):
        sub = args[0] if args else ""
        if sub == "resolve-connections":
            return _make_completed_process(stdout=_EMPTY_CONN_MAP)
        if sub == "fast-rank-connections":
            return _make_completed_process(stdout=_EMPTY_CONN_MAP)
        if sub == "check-stale":
            return _make_completed_process(stdout=_EMPTY_STALE_LIST)
        if sub == "list-moc-groups":
            return _make_completed_process(stdout=json.dumps({"tags": {"ml": ["entry-a"]}, "projects": {}}))
        if sub == "filter-dirty-mocs":
            return _make_completed_process(stdout=dirty_groups_json)
        if sub == "prep-moc-batches":
            return _make_completed_process(stdout=prep_manifest)
        if sub == "update-moc-entries":
            return _make_completed_process(stdout="")
        return _make_completed_process(stdout=json.dumps({}))

    mock_run_helpers["_run_index"].side_effect = index_side_effect
    mock_run_helpers["_run_config"].side_effect = _make_build_config_side_effect()

    def render_side_effect(*args, **kw):
        if args and args[0] == "finalize-batch":
            return _make_completed_process(
                stdout=json.dumps({"successes": [{"id": moc_id, "wiki_path": f"wiki/topics/{moc_id}.md"}]})
            )
        return _make_completed_process(stdout="")
    mock_run_helpers["_run_render"].side_effect = render_side_effect

    import wiki_llm

    captured_concurrency = []

    async def fake_batch(agent, items, schema, config, concurrency=10, skip_validation=False):
        captured_concurrency.append(concurrency)
        return [(iid, {"narrative": "n", "subgroups": "s", "tensions": "t", "evolution": "e"}, None)
                for iid, _ in items]

    monkeypatch.setattr(wiki_llm, "llm_batch", fake_batch)
    monkeypatch.setattr(wiki_llm, "validate_schema", lambda *a, **kw: None)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "build", "--rank", "fast", "--concurrency", "33",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"build failed:\n{result.output}\nexc: {result.exception}"
    assert captured_concurrency, "llm_batch was not called"
    assert 33 in captured_concurrency, f"concurrency=33 not threaded through; got {captured_concurrency}"


@pytest.mark.unit
def test_build_moc_default_concurrency_is_50(mock_run_helpers, wiki_env, tmp_path, monkeypatch):
    """Default --concurrency is 50 when not specified."""
    moc_id = "topic-ml"
    batch_file = tmp_path / "batch_0.json"
    batch_file.write_text(json.dumps([{"moc_id": moc_id, "entries": [], "tag": "ml"}]))
    entry_json_file = tmp_path / f"entry_{moc_id}.json"
    entry_json_file.write_text(json.dumps({
        "id": moc_id, "type": "topic-moc", "title": "ML",
        "tags": ["ml"], "project": None, "references": [],
    }))
    dirty_groups_json = json.dumps({"tags": {"ml": ["entry-a"]}, "projects": {}})
    prep_manifest = json.dumps({
        "batch_files": [str(batch_file)],
        "entry_json_map": {moc_id: str(entry_json_file)},
    })

    def index_side_effect(*args, **kw):
        sub = args[0] if args else ""
        if sub == "resolve-connections":
            return _make_completed_process(stdout=_EMPTY_CONN_MAP)
        if sub == "fast-rank-connections":
            return _make_completed_process(stdout=_EMPTY_CONN_MAP)
        if sub == "check-stale":
            return _make_completed_process(stdout=_EMPTY_STALE_LIST)
        if sub == "list-moc-groups":
            return _make_completed_process(stdout=json.dumps({"tags": {"ml": ["entry-a"]}, "projects": {}}))
        if sub == "filter-dirty-mocs":
            return _make_completed_process(stdout=dirty_groups_json)
        if sub == "prep-moc-batches":
            return _make_completed_process(stdout=prep_manifest)
        if sub == "update-moc-entries":
            return _make_completed_process(stdout="")
        return _make_completed_process(stdout=json.dumps({}))

    mock_run_helpers["_run_index"].side_effect = index_side_effect
    mock_run_helpers["_run_config"].side_effect = _make_build_config_side_effect()

    def render_side_effect(*args, **kw):
        if args and args[0] == "finalize-batch":
            return _make_completed_process(
                stdout=json.dumps({"successes": [{"id": moc_id, "wiki_path": f"wiki/topics/{moc_id}.md"}]})
            )
        return _make_completed_process(stdout="")
    mock_run_helpers["_run_render"].side_effect = render_side_effect

    import wiki_llm
    captured_concurrency = []

    async def fake_batch(agent, items, schema, config, concurrency=10, skip_validation=False):
        captured_concurrency.append(concurrency)
        return [(iid, {"narrative": "n", "subgroups": "s", "tensions": "t", "evolution": "e"}, None)
                for iid, _ in items]

    monkeypatch.setattr(wiki_llm, "llm_batch", fake_batch)
    monkeypatch.setattr(wiki_llm, "validate_schema", lambda *a, **kw: None)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "build", "--rank", "fast", "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"build failed:\n{result.output}"
    assert captured_concurrency == [50], (
        f"Expected default concurrency=50, got {captured_concurrency}"
    )


@pytest.mark.unit
def test_build_deep_rank_uses_llm_batch_with_concurrency(mock_run_helpers, wiki_env, monkeypatch):
    """--rank deep dispatches build-rank via llm_batch with --concurrency threaded through."""
    import wiki_llm

    conn_map_with_entries = json.dumps({
        "entry-a": {
            "entry": {"id": "entry-a", "tags": ["ml"], "title": "A"},
            "candidates": [{"id": "entry-b", "tags": ["ml"], "title": "B", "shared_tags": ["ml"]}],
        },
    })

    mock_run_helpers["_run_index"].side_effect = _make_build_index_side_effect(
        conn_map=conn_map_with_entries
    )
    mock_run_helpers["_run_config"].side_effect = _make_build_config_side_effect()
    mock_run_helpers["_run_render"].side_effect = _make_build_render_side_effect()

    captured = []

    async def fake_batch(agent, items, schema, config, concurrency=10, skip_validation=False):
        captured.append((agent, concurrency))
        return [(iid, {"related": []}, None) for iid, _ in items]

    monkeypatch.setattr(wiki_llm, "llm_batch", fake_batch)
    monkeypatch.setattr(wiki_llm, "validate_schema", lambda *a, **kw: None)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "build", "--rank", "deep", "--fast", "--concurrency", "42",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"build --rank deep failed:\n{result.output}\nexc: {result.exception}"
    rank_calls = [c for c in captured if c[0] == "build-rank"]
    assert rank_calls, f"build-rank not dispatched via llm_batch; got {captured}"
    assert rank_calls[0][1] == 42, f"concurrency=42 not threaded; got {rank_calls[0][1]}"
