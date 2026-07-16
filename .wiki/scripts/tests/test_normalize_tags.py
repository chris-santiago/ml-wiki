"""Tests for wiki CLI normalize-tags command."""

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
# Shared config / LLM constants
# ---------------------------------------------------------------------------

_FAKE_LLM_CONFIG = json.dumps({
    "base_url": "https://localhost:9999",
    "api_key_env": "TEST_API_KEY",
    "models": {"nano": "test-nano", "mini": "test-mini", "full": "test-full"},
    "default_params": {"max_tokens": 4096, "temperature": 0},
    "model_overrides": {},
})

_EXISTING_ALIASES = '{"lstm-networks": "lstm"}'

_MOC_GROUPS = json.dumps({"deep-learning": ["dl", "deep-learning"], "cnn": ["cnn"]})

_UNRESOLVED_TAGS = '{"dl": 5, "cnn": 3, "rare-tag": 1}'

_MERGED_ALIASES = json.dumps({"lstm-networks": "lstm", "dl": "deep-learning", "cnn": "convolutional-neural-networks"})


# ---------------------------------------------------------------------------
# Side-effect builders
# ---------------------------------------------------------------------------

def _make_normalize_config_side_effect(existing_aliases: str = _EXISTING_ALIASES):
    """_run_config side_effect covering all keys used by normalize-tags."""

    def side_effect(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "get":
            key = args[1] if len(args) > 1 else ""
            if key == "tag_aliases":
                return _make_completed_process(stdout=existing_aliases)
            if key == "tag_blocklist":
                return _make_completed_process(stdout="[]")
            if key == "llm":
                return _make_completed_process(stdout=_FAKE_LLM_CONFIG)
        if subcommand == "merge-aliases":
            return _make_completed_process(stdout=_MERGED_ALIASES)
        if subcommand == "set-aliases":
            return _make_completed_process(stdout="")
        return _make_completed_process(stdout=json.dumps(None))

    return side_effect



def _make_normalize_index_side_effect_v2(
    moc_groups: str = _MOC_GROUPS,
    unresolved: str = _UNRESOLVED_TAGS,
    tag_counts: dict | None = None,
    canonical_tags: list | None = None,
):
    """_run_index side_effect for new pipeline that also handles get-tag-counts."""
    _tag_counts = tag_counts or {"dl": 10, "deep-learning": 15, "cnn": 8, "rare-tag": 1}
    _canonical_tags = canonical_tags if canonical_tags is not None else []

    def side_effect(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "list-moc-groups":
            return _make_completed_process(stdout=moc_groups)
        if subcommand == "get-unresolved-tags":
            return _make_completed_process(stdout=unresolved)
        if subcommand == "get-tag-counts":
            return _make_completed_process(stdout=json.dumps(_tag_counts))
        if subcommand == "get-canonical-tags":
            return _make_completed_process(stdout=json.dumps(_canonical_tags))
        if subcommand == "normalize-tags":
            return _make_completed_process(stdout="")
        return _make_completed_process(stdout=json.dumps({}))

    return side_effect


def _make_normalize_embed_side_effect(clusters=None, isolated=None, match_canonicals=None):
    """_run_embed side_effect returning cluster-tags and match-canonicals output."""
    _cluster_result = {"clusters": clusters or [], "isolated": isolated or []}
    _match_result = match_canonicals or {"matches": [], "unmatched": []}

    def side_effect(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "cluster-tags":
            return _make_completed_process(stdout=json.dumps(_cluster_result))
        if subcommand == "match-canonicals":
            return _make_completed_process(stdout=json.dumps(_match_result))
        return _make_completed_process(stdout="{}")

    return side_effect


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_apply_calls_index_and_config(mock_run_helpers, wiki_env, tmp_path):
    """With --apply <file>, _run_index('normalize-tags') and _run_config('set-aliases') are called."""
    alias_file = tmp_path / "aliases.json"
    alias_file.write_text(json.dumps({"dl": "deep-learning"}))

    mock_run_helpers["_run_index"].return_value = _make_completed_process()
    mock_run_helpers["_run_config"].return_value = _make_completed_process()

    runner = CliRunner()
    result = runner.invoke(cli, [
        "normalize-tags",
        "--apply", str(alias_file),
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"normalize-tags --apply failed:\n{result.output}\nexc: {result.exception}"

    index_mock = mock_run_helpers["_run_index"]
    config_mock = mock_run_helpers["_run_config"]

    index_subcommands = [c.args[0] for c in index_mock.call_args_list if c.args]
    assert "normalize-tags" in index_subcommands, (
        f"Expected _run_index('normalize-tags') call; got: {index_subcommands}"
    )

    config_subcommands = [c.args[0] for c in config_mock.call_args_list if c.args]
    assert "set-aliases" in config_subcommands, (
        f"Expected _run_config('set-aliases') call; got: {config_subcommands}"
    )


@pytest.mark.unit
def test_apply_aborts_and_skips_config_on_index_failure(mock_run_helpers, wiki_env, tmp_path):
    """If the index rejects the alias map, set-aliases is NOT called and exit is nonzero.

    Regression: a wrong-shaped map (e.g. {"aliases": {...}}) used to no-op the index
    yet still get written to config, with a false 'Applied' success message.
    """
    alias_file = tmp_path / "aliases.json"
    alias_file.write_text(json.dumps({"aliases": {"dl": "deep-learning"}}))

    mock_run_helpers["_run_index"].return_value = _make_completed_process(
        stderr="Invalid alias map: alias map ... maps \"aliases\" to a non-string value",
        returncode=1,
    )
    mock_run_helpers["_run_config"].return_value = _make_completed_process()

    runner = CliRunner()
    result = runner.invoke(cli, [
        "normalize-tags",
        "--apply", str(alias_file),
        "--index", str(wiki_env.index_path),
    ])

    assert result.exit_code != 0, "malformed alias map must fail, not report success"
    assert "Applied aliases" not in result.output

    config_subcommands = [c.args[0] for c in mock_run_helpers["_run_config"].call_args_list if c.args]
    assert "set-aliases" not in config_subcommands, (
        f"set-aliases must NOT be called when the index rejects the map; got: {config_subcommands}"
    )


@pytest.mark.unit
def test_apply_merges_into_existing_by_default(mock_run_helpers, wiki_env, tmp_path):
    """--apply (no --replace) merges the supplied map into existing aliases via
    merge-aliases, and set-aliases writes the MERGED result — not the raw file — so a
    partial map is additive and never clobbers the accumulated vocabulary."""
    alias_file = tmp_path / "aliases.json"
    alias_file.write_text(json.dumps({"cs---ml": "machine-learning"}))

    mock_run_helpers["_run_index"].return_value = _make_completed_process()

    def config_side_effect(*args, **kw):
        sub = args[0] if args else ""
        if sub == "get" and len(args) > 1 and args[1] == "tag_aliases":
            return _make_completed_process(stdout=json.dumps({"old-tag": "old-canon"}))
        if sub == "merge-aliases":
            return _make_completed_process(
                stdout=json.dumps({"old-tag": "old-canon", "cs---ml": "machine-learning"})
            )
        return _make_completed_process(stdout="")

    mock_run_helpers["_run_config"].side_effect = config_side_effect

    runner = CliRunner()
    result = runner.invoke(cli, [
        "normalize-tags", "--apply", str(alias_file), "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"{result.output}\n{result.exception}"

    config_subs = [c.args[0] for c in mock_run_helpers["_run_config"].call_args_list if c.args]
    assert "merge-aliases" in config_subs, f"expected merge-aliases call; got {config_subs}"
    assert "set-aliases" in config_subs
    # set-aliases must receive the merged temp file, NOT the raw apply file
    set_calls = [c for c in mock_run_helpers["_run_config"].call_args_list if c.args and c.args[0] == "set-aliases"]
    assert set_calls[0].args[1] != str(alias_file), "set-aliases should write the merged file, not the raw apply file"
    assert "Merged aliases" in result.output


@pytest.mark.unit
def test_apply_replace_flag_replaces_without_merge(mock_run_helpers, wiki_env, tmp_path):
    """--apply --replace calls set-aliases with the supplied file directly and skips merge-aliases."""
    alias_file = tmp_path / "aliases.json"
    alias_file.write_text(json.dumps({"dl": "deep-learning"}))

    mock_run_helpers["_run_index"].return_value = _make_completed_process()
    mock_run_helpers["_run_config"].return_value = _make_completed_process()

    runner = CliRunner()
    result = runner.invoke(cli, [
        "normalize-tags", "--apply", str(alias_file), "--replace", "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"{result.output}\n{result.exception}"

    config_subs = [c.args[0] for c in mock_run_helpers["_run_config"].call_args_list if c.args]
    assert "merge-aliases" not in config_subs, f"--replace must skip merge-aliases; got {config_subs}"
    set_calls = [c for c in mock_run_helpers["_run_config"].call_args_list if c.args and c.args[0] == "set-aliases"]
    assert set_calls[0].args[1] == str(alias_file), "set-aliases should receive the raw apply file under --replace"
    assert "Replaced alias map" in result.output


@pytest.mark.unit
def test_proposal_flow(mock_run_helpers, wiki_env, mock_llm):
    """Without --apply, the full proposal pipeline runs in order:
    get tag_aliases → list-moc-groups → get-unresolved-tags → clustering → merge-aliases."""
    mock_run_helpers["_run_config"].side_effect = _make_normalize_config_side_effect()
    mock_run_helpers["_run_index"].side_effect = _make_normalize_index_side_effect_v2()
    # Auto-tier cluster drives aliases without LLM call
    mock_run_helpers["_run_embed"].side_effect = _make_normalize_embed_side_effect(
        clusters=[{"tags": ["dl", "deep-learning"], "max_sim": 0.97, "tier": "auto"}]
    )

    runner = CliRunner()
    result = runner.invoke(cli, [
        "normalize-tags",
        "--dry-run",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"normalize-tags proposal flow failed:\n{result.output}\nexc: {result.exception}"

    config_mock = mock_run_helpers["_run_config"]
    index_mock = mock_run_helpers["_run_index"]

    config_subcommands = [c.args[0] for c in config_mock.call_args_list if c.args]
    index_subcommands = [c.args[0] for c in index_mock.call_args_list if c.args]

    # get tag_aliases must be called first
    assert "get" in config_subcommands, f"Expected _run_config('get', ...) call; got: {config_subcommands}"
    get_calls = [(c.args[0], c.args[1]) for c in config_mock.call_args_list if len(c.args) >= 2]
    assert ("get", "tag_aliases") in get_calls, f"Expected ('get', 'tag_aliases') in config calls; got: {get_calls}"

    # list-moc-groups and get-unresolved-tags must be called
    assert "list-moc-groups" in index_subcommands, (
        f"Expected 'list-moc-groups' in index calls; got: {index_subcommands}"
    )
    assert "get-unresolved-tags" in index_subcommands, (
        f"Expected 'get-unresolved-tags' in index calls; got: {index_subcommands}"
    )

    # merge-aliases must be called to merge with existing
    assert "merge-aliases" in config_subcommands, (
        f"Expected 'merge-aliases' in config calls; got: {config_subcommands}"
    )


@pytest.mark.unit
def test_protected_canonical_blocked_in_auto_merge(mock_run_helpers, wiki_env, mock_llm):
    """Stage 4 post-filter blocks a known canonical from being remapped as a non-canonical alias.

    lstm is a known canonical (value in _EXISTING_ALIASES = '{"lstm-networks": "lstm"}').
    The embed mock returns a cluster containing lstm with a lower count than long-short-term-memory,
    so auto-merge Stage 3a would propose lstm -> long-short-term-memory.
    Stage 4 must block that proposal because lstm is in known_canonicals.
    """
    auto_cluster = [{"tags": ["lstm", "long-short-term-memory"], "max_sim": 0.98, "tier": "auto"}]
    # lstm has lower count so auto-merge would pick long-short-term-memory as canonical,
    # making lstm the proposed non-canonical alias — which Stage 4 must block.
    tag_counts = {"lstm": 2, "long-short-term-memory": 20}

    mock_run_helpers["_run_config"].side_effect = _make_normalize_config_side_effect()
    mock_run_helpers["_run_index"].side_effect = _make_normalize_index_side_effect_v2(
        tag_counts=tag_counts,
        unresolved=json.dumps({"lstm": 2, "long-short-term-memory": 2}),
    )
    mock_run_helpers["_run_embed"].side_effect = _make_normalize_embed_side_effect(
        clusters=auto_cluster,
    )

    runner = CliRunner()
    result = runner.invoke(cli, [
        "normalize-tags",
        "--dry-run",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"normalize-tags post-filter failed:\n{result.output}\nexc: {result.exception}"

    # Stage 4 should emit a "Blocked" message for lstm
    assert "Blocked" in result.output
    assert "lstm" in result.output
    # lstm must never appear as a non-canonical alias (i.e. "lstm ->" must not be in output)
    assert "lstm ->" not in result.output


@pytest.mark.unit
def test_dry_run_no_apply(mock_run_helpers, wiki_env, mock_llm):
    """With --dry-run, normalize-tags _run_index call and set-aliases _run_config call are NOT made."""
    mock_run_helpers["_run_config"].side_effect = _make_normalize_config_side_effect()
    mock_run_helpers["_run_index"].side_effect = _make_normalize_index_side_effect_v2()
    mock_run_helpers["_run_embed"].side_effect = _make_normalize_embed_side_effect(
        clusters=[{"tags": ["dl", "deep-learning"], "max_sim": 0.97, "tier": "auto"}]
    )

    runner = CliRunner()
    result = runner.invoke(cli, [
        "normalize-tags",
        "--dry-run",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"normalize-tags --dry-run failed:\n{result.output}\nexc: {result.exception}"

    index_mock = mock_run_helpers["_run_index"]
    config_mock = mock_run_helpers["_run_config"]

    index_subcommands = [c.args[0] for c in index_mock.call_args_list if c.args]
    config_subcommands = [c.args[0] for c in config_mock.call_args_list if c.args]

    assert "normalize-tags" not in index_subcommands, (
        f"_run_index('normalize-tags') must NOT be called with --dry-run; got: {index_subcommands}"
    )
    assert "set-aliases" not in config_subcommands, (
        f"_run_config('set-aliases') must NOT be called with --dry-run; got: {config_subcommands}"
    )

    # Dry-run output should show proposals and path to apply
    assert "dry run" in result.output.lower() or "Proposed" in result.output, (
        f"Expected dry-run proposal output; got:\n{result.output}"
    )


@pytest.mark.unit
def test_merges_with_existing(mock_run_helpers, wiki_env, mock_llm):
    """_run_config('merge-aliases') is called to combine clustering proposals with existing aliases."""
    mock_run_helpers["_run_config"].side_effect = _make_normalize_config_side_effect()
    mock_run_helpers["_run_index"].side_effect = _make_normalize_index_side_effect_v2()
    mock_run_helpers["_run_embed"].side_effect = _make_normalize_embed_side_effect(
        clusters=[{"tags": ["dl", "deep-learning"], "max_sim": 0.97, "tier": "auto"}]
    )

    runner = CliRunner()
    result = runner.invoke(cli, [
        "normalize-tags",
        "--dry-run",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"normalize-tags merge failed:\n{result.output}\nexc: {result.exception}"

    config_mock = mock_run_helpers["_run_config"]
    config_subcommands = [c.args[0] for c in config_mock.call_args_list if c.args]
    assert "merge-aliases" in config_subcommands, (
        f"Expected _run_config('merge-aliases') to be called; got: {config_subcommands}"
    )


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_apply_with_file(populated_index, monkeypatch, tmp_path):
    """With --apply <file> on a populated index, _run_index normalize-tags is called."""
    import wiki_cli

    monkeypatch.chdir(populated_index.root)

    alias_file = tmp_path / "aliases.json"
    alias_file.write_text(json.dumps({"dl": "deep-learning"}))

    index_calls = []

    def fake_run_index(*args, **kw):
        index_calls.append(args[0] if args else "")
        return _make_completed_process()

    def fake_run_config(*args, **kw):
        return _make_completed_process()

    monkeypatch.setattr(wiki_cli, "_run_index", fake_run_index)
    monkeypatch.setattr(wiki_cli, "_run_config", fake_run_config)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "normalize-tags",
        "--apply", str(alias_file),
        "--index", str(populated_index.index_path),
    ])
    assert result.exit_code == 0, (
        f"normalize-tags --apply on populated index failed:\n{result.output}\nexc: {result.exception}"
    )
    assert "normalize-tags" in index_calls, (
        f"Expected _run_index('normalize-tags') to be called; got: {index_calls}"
    )


# ---------------------------------------------------------------------------
# Smoke tests (subprocess)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_normalize_tags_subprocess_help():
    """normalize-tags --help exits 0 and shows --dry-run and --apply."""
    result = _run_cli_subprocess("normalize-tags", "--help")
    assert result.returncode == 0
    assert "--dry-run" in result.stdout
    assert "--apply" in result.stdout


@pytest.mark.smoke
def test_normalize_tags_index_flag():
    """normalize-tags --help shows --index."""
    result = _run_cli_subprocess("normalize-tags", "--help")
    assert result.returncode == 0
    assert "--index" in result.stdout


# ---------------------------------------------------------------------------
# New pipeline tests (4-stage: count gate → clustering → tiered LLM → guard)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pipeline_calls_get_tag_counts(mock_run_helpers, wiki_env, mock_llm):
    """New pipeline calls get-tag-counts for full-index count gate."""
    mock_run_helpers["_run_config"].side_effect = _make_normalize_config_side_effect()
    mock_run_helpers["_run_index"].side_effect = _make_normalize_index_side_effect_v2()
    mock_run_helpers["_run_embed"].side_effect = _make_normalize_embed_side_effect()

    runner = CliRunner()
    result = runner.invoke(cli, ["normalize-tags", "--dry-run", "--index", str(wiki_env.index_path)])
    assert result.exit_code == 0, f"Failed:\n{result.output}\nexc: {result.exception}"

    index_subcommands = [c.args[0] for c in mock_run_helpers["_run_index"].call_args_list if c.args]
    assert "get-tag-counts" in index_subcommands


@pytest.mark.unit
def test_pipeline_calls_cluster_tags(mock_run_helpers, wiki_env, mock_llm):
    """New pipeline calls cluster-tags after count gate."""
    mock_run_helpers["_run_config"].side_effect = _make_normalize_config_side_effect()
    mock_run_helpers["_run_index"].side_effect = _make_normalize_index_side_effect_v2()
    mock_run_helpers["_run_embed"].side_effect = _make_normalize_embed_side_effect()

    runner = CliRunner()
    result = runner.invoke(cli, ["normalize-tags", "--dry-run", "--index", str(wiki_env.index_path)])
    assert result.exit_code == 0, f"Failed:\n{result.output}\nexc: {result.exception}"

    embed_subcommands = [c.args[0] for c in mock_run_helpers["_run_embed"].call_args_list if c.args]
    assert "cluster-tags" in embed_subcommands


@pytest.mark.unit
def test_auto_merge_picks_highest_count_canonical(mock_run_helpers, wiki_env, mock_llm):
    """Auto-tier cluster: tag with highest full-index count becomes canonical."""
    auto_cluster = {"clusters": [{"tags": ["dl", "deep-learning"], "max_sim": 0.97, "tier": "auto"}], "isolated": []}
    tag_counts = {"dl": 3, "deep-learning": 10, "cnn": 5}

    mock_run_helpers["_run_config"].side_effect = _make_normalize_config_side_effect()
    mock_run_helpers["_run_index"].side_effect = _make_normalize_index_side_effect_v2(tag_counts=tag_counts)
    mock_run_helpers["_run_embed"].side_effect = _make_normalize_embed_side_effect(
        clusters=auto_cluster["clusters"]
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["normalize-tags", "--dry-run", "--index", str(wiki_env.index_path)])
    assert result.exit_code == 0, f"Failed:\n{result.output}\nexc: {result.exception}"
    # dl (count=3) should alias to deep-learning (count=10)
    assert "dl" in result.output
    assert "deep-learning" in result.output


@pytest.mark.unit
def test_assign_pass_removes_junk_tags(mock_run_helpers, wiki_env, mock_llm):
    """Orphan tags go through assign pass; 'remove' entries become empty-string aliases."""
    mock_run_helpers["_run_config"].side_effect = _make_normalize_config_side_effect()
    mock_run_helpers["_run_index"].side_effect = _make_normalize_index_side_effect_v2(
        tag_counts={"dl": 5, "cnn": 3, "noise-tag": 1}
    )
    mock_run_helpers["_run_embed"].side_effect = _make_normalize_embed_side_effect(
        isolated=["noise-tag"],
        match_canonicals={"matches": [], "unmatched": [{"tag": "noise-tag", "nearest": []}]},
    )
    mock_llm["normalize-tags-assign"] = {"assign": [], "new": [], "remove": ["noise-tag"]}

    runner = CliRunner()
    result = runner.invoke(cli, ["normalize-tags", "--dry-run", "--index", str(wiki_env.index_path)])
    assert result.exit_code == 0, f"Failed:\n{result.output}\nexc: {result.exception}"
    assert "noise-tag" in result.output
    assert "(remove)" in result.output


@pytest.mark.unit
def test_zero_count_tags_dropped_before_clustering(mock_run_helpers, wiki_env, mock_llm):
    """Tags with zero full-index count never reach cluster-tags."""
    mock_run_helpers["_run_config"].side_effect = _make_normalize_config_side_effect()
    # rare-tag is in unresolved but absent from full_counts (= 0)
    mock_run_helpers["_run_index"].side_effect = _make_normalize_index_side_effect_v2(
        tag_counts={"dl": 5, "cnn": 3}
    )
    mock_run_helpers["_run_embed"].side_effect = _make_normalize_embed_side_effect()

    runner = CliRunner()
    result = runner.invoke(cli, ["normalize-tags", "--dry-run", "--index", str(wiki_env.index_path)])
    assert result.exit_code == 0

    # cluster-tags input should not contain rare-tag
    embed_calls = mock_run_helpers["_run_embed"].call_args_list
    cluster_calls = [c for c in embed_calls if c.args and c.args[0] == "cluster-tags"]
    assert cluster_calls, "Expected cluster-tags to be called when non-zero-count tags exist"
    # input_data is passed as keyword arg
    input_data = cluster_calls[0].kwargs.get("input_data", "[]")
    sent_tags = json.loads(input_data)
    assert "rare-tag" not in sent_tags


# ---------------------------------------------------------------------------
# Canonical matching tests (stages 3c/3d)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_match_canonicals_called_with_remaining(mock_run_helpers, wiki_env, mock_llm):
    """After auto-merge consumes some tags, match-canonicals is called with the remaining ones."""
    mock_run_helpers["_run_config"].side_effect = _make_normalize_config_side_effect()
    mock_run_helpers["_run_index"].side_effect = _make_normalize_index_side_effect_v2(
        unresolved=json.dumps({"dl": 5, "orphan-tag": 2}),
        tag_counts={"dl": 5, "deep-learning": 15, "orphan-tag": 2},
    )
    mock_run_helpers["_run_embed"].side_effect = _make_normalize_embed_side_effect(
        clusters=[{"tags": ["dl", "deep-learning"], "max_sim": 0.97, "tier": "auto"}],
        isolated=["orphan-tag"],
        match_canonicals={"matches": [], "unmatched": [{"tag": "orphan-tag", "nearest": []}]},
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["normalize-tags", "--dry-run", "--index", str(wiki_env.index_path)])
    assert result.exit_code == 0, f"Failed:\n{result.output}\nexc: {result.exception}"

    embed_calls = mock_run_helpers["_run_embed"].call_args_list
    mc_calls = [c for c in embed_calls if c.args and c.args[0] == "match-canonicals"]
    assert mc_calls, "Expected match-canonicals to be called"
    input_data = json.loads(mc_calls[0].kwargs.get("input_data", "{}"))
    assert "orphan-tag" in input_data.get("unresolved", [])
    # dl and deep-learning should NOT be in remaining (consumed by auto-merge)
    assert "dl" not in input_data.get("unresolved", [])
    assert "deep-learning" not in input_data.get("unresolved", [])


@pytest.mark.unit
def test_canonical_match_accepted_creates_alias(mock_run_helpers, wiki_env, mock_llm):
    """When match-canonicals finds a match and LLM confirms, an alias is created."""
    mock_run_helpers["_run_config"].side_effect = _make_normalize_config_side_effect()
    mock_run_helpers["_run_index"].side_effect = _make_normalize_index_side_effect_v2(
        unresolved=json.dumps({"dl-methods": 3}),
        tag_counts={"dl-methods": 3, "lstm": 50},
    )
    mock_run_helpers["_run_embed"].side_effect = _make_normalize_embed_side_effect(
        isolated=["dl-methods"],
        match_canonicals={"matches": [{"tag": "dl-methods", "canonical": "lstm", "similarity": 0.75}], "unmatched": []},
    )
    # LLM confirms the match (normalize-tags-cluster returns canonical=lstm, aliases=[dl-methods])
    mock_llm["normalize-tags-cluster"] = {"canonical": "lstm", "aliases": ["dl-methods"]}

    runner = CliRunner()
    result = runner.invoke(cli, ["normalize-tags", "--dry-run", "--index", str(wiki_env.index_path)])
    assert result.exit_code == 0, f"Failed:\n{result.output}\nexc: {result.exception}"
    assert "dl-methods" in result.output
    assert "lstm" in result.output


@pytest.mark.unit
def test_canonical_match_null_flows_to_assign(mock_run_helpers, wiki_env, mock_llm):
    """When LLM rejects a canonical match (null), the tag flows to assign pass."""
    mock_run_helpers["_run_config"].side_effect = _make_normalize_config_side_effect()
    mock_run_helpers["_run_index"].side_effect = _make_normalize_index_side_effect_v2(
        unresolved=json.dumps({"weird-tag": 2}),
        tag_counts={"weird-tag": 2, "lstm": 50},
    )
    mock_run_helpers["_run_embed"].side_effect = _make_normalize_embed_side_effect(
        isolated=["weird-tag"],
        match_canonicals={"matches": [{"tag": "weird-tag", "canonical": "lstm", "similarity": 0.72}], "unmatched": []},
    )
    # LLM rejects the match
    mock_llm["normalize-tags-cluster"] = {"canonical": None}
    # Assign pass removes it
    mock_llm["normalize-tags-assign"] = {"assign": [], "new": [], "remove": ["weird-tag"]}

    runner = CliRunner()
    result = runner.invoke(cli, ["normalize-tags", "--dry-run", "--index", str(wiki_env.index_path)])
    assert result.exit_code == 0, f"Failed:\n{result.output}\nexc: {result.exception}"
    assert "weird-tag" in result.output
    assert "(remove)" in result.output


@pytest.mark.unit
def test_assign_pass_only_gets_true_orphans(mock_run_helpers, wiki_env, mock_llm):
    """Tags consumed by auto-merge or accepted by canonical match never reach assign pass."""
    mock_run_helpers["_run_config"].side_effect = _make_normalize_config_side_effect()
    mock_run_helpers["_run_index"].side_effect = _make_normalize_index_side_effect_v2(
        unresolved=json.dumps({"dl": 5, "matched-tag": 3, "orphan-tag": 1}),
        tag_counts={"dl": 5, "deep-learning": 15, "matched-tag": 3, "orphan-tag": 1, "lstm": 50},
    )
    mock_run_helpers["_run_embed"].side_effect = _make_normalize_embed_side_effect(
        clusters=[{"tags": ["dl", "deep-learning"], "max_sim": 0.97, "tier": "auto"}],
        isolated=["matched-tag", "orphan-tag"],
        match_canonicals={
            "matches": [{"tag": "matched-tag", "canonical": "lstm", "similarity": 0.80}],
            "unmatched": [{"tag": "orphan-tag", "nearest": []}],
        },
    )
    # LLM confirms matched-tag -> lstm
    mock_llm["normalize-tags-cluster"] = {"canonical": "lstm", "aliases": ["matched-tag"]}
    # Assign pass should only see orphan-tag
    mock_llm["normalize-tags-assign"] = {"assign": [], "new": ["orphan-tag"], "remove": []}

    runner = CliRunner()
    result = runner.invoke(cli, ["normalize-tags", "--dry-run", "--index", str(wiki_env.index_path)])
    assert result.exit_code == 0, f"Failed:\n{result.output}\nexc: {result.exception}"

    # dl should be aliased to deep-learning, matched-tag to lstm
    assert "dl" in result.output
    assert "matched-tag" in result.output


@pytest.mark.unit
def test_assign_pass_assigns_to_canonical(mock_run_helpers, wiki_env, mock_llm):
    """Assign pass can map orphan tags to canonicals that embedding similarity missed."""
    mock_run_helpers["_run_config"].side_effect = _make_normalize_config_side_effect()
    mock_run_helpers["_run_index"].side_effect = _make_normalize_index_side_effect_v2(
        unresolved=json.dumps({"elastic-net": 3}),
        tag_counts={"elastic-net": 3, "lstm": 50},
    )
    mock_run_helpers["_run_embed"].side_effect = _make_normalize_embed_side_effect(
        isolated=["elastic-net"],
        match_canonicals={
            "matches": [],
            "unmatched": [{"tag": "elastic-net", "nearest": [{"canonical": "lstm", "similarity": 0.55}]}],
        },
    )
    mock_llm["normalize-tags-assign"] = {
        "assign": [{"tag": "elastic-net", "canonical": "lstm"}],
        "new": [],
        "remove": [],
    }

    runner = CliRunner()
    result = runner.invoke(cli, ["normalize-tags", "--dry-run", "--index", str(wiki_env.index_path)])
    assert result.exit_code == 0, f"Failed:\n{result.output}\nexc: {result.exception}"
    assert "elastic-net" in result.output
    assert "lstm" in result.output


@pytest.mark.unit
def test_canonical_set_includes_freq_tags(mock_run_helpers, wiki_env, mock_llm):
    """known_canonicals includes freq tags from get-canonical-tags alongside alias targets."""
    mock_run_helpers["_run_config"].side_effect = _make_normalize_config_side_effect()
    mock_run_helpers["_run_index"].side_effect = _make_normalize_index_side_effect_v2(
        unresolved=json.dumps({"new-tag": 2}),
        tag_counts={"new-tag": 2, "freq-canon": 30},
        canonical_tags=["freq-canon"],
    )
    mock_run_helpers["_run_embed"].side_effect = _make_normalize_embed_side_effect(
        isolated=["new-tag"],
        match_canonicals={"matches": [{"tag": "new-tag", "canonical": "freq-canon", "similarity": 0.75}], "unmatched": []},
    )
    mock_llm["normalize-tags-cluster"] = {"canonical": "freq-canon", "aliases": ["new-tag"]}

    runner = CliRunner()
    result = runner.invoke(cli, ["normalize-tags", "--dry-run", "--index", str(wiki_env.index_path)])
    assert result.exit_code == 0, f"Failed:\n{result.output}\nexc: {result.exception}"

    # match-canonicals should have been called with freq-canon in the canonicals list
    embed_calls = mock_run_helpers["_run_embed"].call_args_list
    mc_calls = [c for c in embed_calls if c.args and c.args[0] == "match-canonicals"]
    assert mc_calls
    input_data = json.loads(mc_calls[0].kwargs.get("input_data", "{}"))
    canonicals = input_data.get("canonicals", [])
    assert "freq-canon" in canonicals
