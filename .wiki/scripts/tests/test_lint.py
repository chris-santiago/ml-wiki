"""Tests for wiki CLI lint command."""

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
# Shared config helper (mirrors _make_build_config_side_effect in test_build.py)
# ---------------------------------------------------------------------------

_FAKE_LLM_CONFIG = json.dumps({
    "base_url": "https://localhost:9999",
    "api_key_env": "TEST_API_KEY",
    "models": {"nano": "test-nano", "mini": "test-mini", "full": "test-full"},
    "default_params": {"max_tokens": 4096, "temperature": 0},
    "model_overrides": {},
})


def _make_lint_config_side_effect():
    """_run_config side_effect that handles all config keys used by lint."""

    def side_effect(*args, **kw):
        if args and args[0] == "get":
            key = args[1] if len(args) > 1 else ""
            if key == "llm":
                return _make_completed_process(stdout=_FAKE_LLM_CONFIG)
        return _make_completed_process(stdout=json.dumps(None))

    return side_effect


# ---------------------------------------------------------------------------
# Shared index side_effect builder for lint
# ---------------------------------------------------------------------------

_EMPTY_ISSUES = json.dumps({"issues": []})
_ISSUES_WITH_ORPHAN = json.dumps({"issues": ["orphan fragment foo-frag-1"]})


def _make_lint_index_side_effect(
    structural_issues: str = _EMPTY_ISSUES,
    orphan_ids: list | None = None,
    frags_by_tag: dict | None = None,
    entries_batch: dict | None = None,
):
    """Build a comprehensive _run_index side_effect for the lint pipeline.

    - structural_issues: JSON returned by lint-structural
    - orphan_ids: list returned by get-orphan-ids (None → empty list)
    - frags_by_tag: dict returned by get-fragments-by-type (None → empty)
    - entries_batch: dict/list returned by get-entries-batch (None → {})
    """
    _orphans = json.dumps(orphan_ids if orphan_ids is not None else [])
    _frags = json.dumps(frags_by_tag if frags_by_tag is not None else {})
    _entries = json.dumps(entries_batch if entries_batch is not None else {})

    def side_effect(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "lint-structural":
            return _make_completed_process(stdout=structural_issues)
        if subcommand == "get-orphan-ids":
            return _make_completed_process(stdout=_orphans)
        if subcommand == "delete-fragments-batch":
            return _make_completed_process(stdout="")
        if subcommand == "get-fragments-by-type":
            return _make_completed_process(stdout=_frags)
        if subcommand == "get-entries-batch":
            return _make_completed_process(stdout=_entries)
        return _make_completed_process(stdout=json.dumps({}))

    return side_effect


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_lint_mechanical_only(mock_run_helpers, wiki_env):
    """With --mechanical-only, only lint-structural is called; no LLM operations."""
    mock_run_helpers["_run_index"].side_effect = _make_lint_index_side_effect()
    mock_run_helpers["_run_config"].side_effect = _make_lint_config_side_effect()

    runner = CliRunner()
    result = runner.invoke(cli, [
        "lint",
        "--mechanical-only",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"lint --mechanical-only failed:\n{result.output}\nexc: {result.exception}"

    index_mock = mock_run_helpers["_run_index"]
    subcommands = [c.args[0] for c in index_mock.call_args_list if c.args]
    assert "lint-structural" in subcommands, f"Expected 'lint-structural', got: {subcommands}"

    # LLM paths (get-fragments-by-type, get-entries-batch) must NOT be reached
    assert "get-fragments-by-type" not in subcommands, (
        f"'get-fragments-by-type' should not be called with --mechanical-only; got: {subcommands}"
    )
    assert "get-entries-batch" not in subcommands, (
        f"'get-entries-batch' should not be called with --mechanical-only; got: {subcommands}"
    )


@pytest.mark.unit
def test_lint_fix_mechanical(mock_run_helpers, wiki_env):
    """With --mechanical-only --fix and structural issues, delete-fragments-batch is called."""
    # The fix branch (`if fix and issues:`) only triggers when issues are non-empty.
    mock_run_helpers["_run_index"].side_effect = _make_lint_index_side_effect(
        structural_issues=_ISSUES_WITH_ORPHAN,
        orphan_ids=["foo-frag-1"],
    )
    mock_run_helpers["_run_config"].side_effect = _make_lint_config_side_effect()

    runner = CliRunner()
    result = runner.invoke(cli, [
        "lint",
        "--mechanical-only",
        "--fix",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"lint --mechanical-only --fix failed:\n{result.output}\nexc: {result.exception}"

    index_mock = mock_run_helpers["_run_index"]
    subcommands = [c.args[0] for c in index_mock.call_args_list if c.args]
    assert "delete-fragments-batch" in subcommands, (
        f"Expected 'delete-fragments-batch' to be called with --fix; got: {subcommands}"
    )


@pytest.mark.unit
def test_lint_orphan_cleanup(mock_run_helpers, wiki_env):
    """get-orphan-ids is called during the --fix path (before mechanical-only exit)."""
    mock_run_helpers["_run_index"].side_effect = _make_lint_index_side_effect(
        structural_issues=_ISSUES_WITH_ORPHAN,
        orphan_ids=["foo-frag-1"],
    )
    mock_run_helpers["_run_config"].side_effect = _make_lint_config_side_effect()

    runner = CliRunner()
    result = runner.invoke(cli, [
        "lint",
        "--mechanical-only",
        "--fix",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"lint --mechanical-only --fix failed:\n{result.output}\nexc: {result.exception}"

    index_mock = mock_run_helpers["_run_index"]
    subcommands = [c.args[0] for c in index_mock.call_args_list if c.args]
    assert "get-orphan-ids" in subcommands, (
        f"Expected 'get-orphan-ids' to be called during --fix; got: {subcommands}"
    )


@pytest.mark.unit
def test_lint_pass1_contradictions(mock_run_helpers, wiki_env, monkeypatch):
    """Without --mechanical-only, get-fragments-by-type is called for Pass 1."""
    import wiki_llm

    # Provide enough fragments (>= min_frags default of 5) to trigger the LLM path
    frags_by_tag = {
        "transformers": [
            {"frag_id": f"frag-{i}", "type": "claim", "text": f"claim {i}"}
            for i in range(6)
        ]
    }

    mock_run_helpers["_run_index"].side_effect = _make_lint_index_side_effect(
        frags_by_tag=frags_by_tag,
    )
    mock_run_helpers["_run_config"].side_effect = _make_lint_config_side_effect()

    # llm_batch is async — must be monkeypatched with an async function
    async def fake_llm_batch(agent, items, schema, config, **kwargs):
        return [(item_id, {"contradictions": []}, None) for item_id, _ in items]

    monkeypatch.setattr(wiki_llm, "llm_batch", fake_llm_batch)

    # Pass 2 uses llm_call ("lint") — register a no-op to avoid errors if reached
    import wiki_llm as wl
    monkeypatch.setattr(wl, "llm_call", lambda *a, **kw: {"crosslinks": []})
    monkeypatch.setattr(wl, "validate_schema", lambda data, schema: None)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "lint",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"lint pass1 failed:\n{result.output}\nexc: {result.exception}"

    index_mock = mock_run_helpers["_run_index"]
    subcommands = [c.args[0] for c in index_mock.call_args_list if c.args]
    assert "get-fragments-by-type" in subcommands, (
        f"Expected 'get-fragments-by-type', got: {subcommands}"
    )


@pytest.mark.unit
def test_lint_pass2_crosslinks(mock_run_helpers, wiki_env, monkeypatch):
    """Without --mechanical-only, get-entries-batch is called for Pass 2 when orphan IDs exist."""
    import wiki_llm

    # Pass 2 path: get-orphan-ids (at line 1802) must return a non-empty list
    # so that get-entries-batch is reached.
    mock_run_helpers["_run_index"].side_effect = _make_lint_index_side_effect(
        orphan_ids=["some-orphan-id"],
        entries_batch={"some-orphan-id": {"id": "some-orphan-id", "title": "Orphan Entry"}},
    )
    mock_run_helpers["_run_config"].side_effect = _make_lint_config_side_effect()

    # Mock llm_batch (async) for Pass 1 — frags_by_tag is empty so it won't be called,
    # but patch defensively.
    async def fake_llm_batch(agent, items, schema, config, **kwargs):
        return [(item_id, {"contradictions": []}, None) for item_id, _ in items]

    monkeypatch.setattr(wiki_llm, "llm_batch", fake_llm_batch)

    # Mock llm_call (sync) for Pass 2
    monkeypatch.setattr(wiki_llm, "llm_call", lambda *a, **kw: {"crosslinks": []})
    monkeypatch.setattr(wiki_llm, "validate_schema", lambda data, schema: None)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "lint",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"lint pass2 failed:\n{result.output}\nexc: {result.exception}"

    index_mock = mock_run_helpers["_run_index"]
    subcommands = [c.args[0] for c in index_mock.call_args_list if c.args]
    assert "get-entries-batch" in subcommands, (
        f"Expected 'get-entries-batch', got: {subcommands}"
    )


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_lint_mechanical_on_populated(populated_index, monkeypatch):
    """--mechanical-only on a populated index runs without error."""
    import wiki_cli

    monkeypatch.chdir(populated_index.root)

    # Stub out the index subcommands so no real wiki_index.py subprocess is needed
    def fake_run_index(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "lint-structural":
            return _make_completed_process(stdout=json.dumps({"issues": []}))
        return _make_completed_process(stdout=json.dumps({}))

    monkeypatch.setattr(wiki_cli, "_run_index", fake_run_index)
    monkeypatch.setattr(wiki_cli, "_run_config", _make_lint_config_side_effect())

    runner = CliRunner()
    result = runner.invoke(cli, [
        "lint",
        "--mechanical-only",
        "--index", str(populated_index.index_path),
    ])
    assert result.exit_code == 0, (
        f"lint --mechanical-only on populated index failed:\n{result.output}\nexc: {result.exception}"
    )
    assert "Structural checks" in result.output


@pytest.mark.integration
def test_lint_fix_on_populated(populated_index, monkeypatch):
    """--mechanical-only --fix on a populated index runs without error."""
    import wiki_cli

    monkeypatch.chdir(populated_index.root)

    def fake_run_index(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "lint-structural":
            # Return a non-empty issue list so the --fix branch is exercised
            return _make_completed_process(
                stdout=json.dumps({"issues": ["orphan fragment wiki-frag-x"]})
            )
        if subcommand == "get-orphan-ids":
            return _make_completed_process(stdout=json.dumps(["wiki-frag-x"]))
        if subcommand == "delete-fragments-batch":
            return _make_completed_process(stdout="")
        return _make_completed_process(stdout=json.dumps({}))

    monkeypatch.setattr(wiki_cli, "_run_index", fake_run_index)
    monkeypatch.setattr(wiki_cli, "_run_config", _make_lint_config_side_effect())

    runner = CliRunner()
    result = runner.invoke(cli, [
        "lint",
        "--mechanical-only",
        "--fix",
        "--index", str(populated_index.index_path),
    ])
    assert result.exit_code == 0, (
        f"lint --mechanical-only --fix on populated index failed:\n{result.output}\nexc: {result.exception}"
    )
    assert "Structural checks" in result.output


# ---------------------------------------------------------------------------
# Smoke tests (subprocess)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_lint_subprocess_help():
    """lint --help exits 0 and shows --mechanical-only and --fix."""
    result = _run_cli_subprocess("lint", "--help")
    assert result.returncode == 0
    assert "--mechanical-only" in result.stdout
    assert "--fix" in result.stdout


@pytest.mark.smoke
def test_lint_index_flag_in_help():
    """lint --help shows --index."""
    result = _run_cli_subprocess("lint", "--help")
    assert result.returncode == 0
    assert "--index" in result.stdout
