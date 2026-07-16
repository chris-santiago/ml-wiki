"""Tests for wiki CLI render command (LLM-dependent)."""

import json
import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock

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
# Minimal LLM config that passes json.loads(_run_config("get", "llm").stdout)
# ---------------------------------------------------------------------------

_FAKE_LLM_CONFIG = json.dumps({
    "base_url": "https://localhost:9999",
    "api_key_env": "TEST_API_KEY",
    "models": {"nano": "test-nano", "mini": "test-mini", "full": "test-full"},
    "default_params": {"max_tokens": 4096, "temperature": 0},
    "model_overrides": {},
})

# Return value from _assemble_page_from_json (written by wiki_render)
_FAKE_ASSEMBLE_RESULT = {
    "wiki_path": "wiki/_pages/test-entry.md",
    "tags_finalized": ["ml", "fraud-detection"],
    "fragments": [
        {
            "id": "frag-test-entry-01",
            "type": "finding",
            "title": "Key finding",
            "tags": ["ml"],
            "project": None,
            "references": ["test-entry"],
            "ingested": "2026-01-01T00:00:00Z",
        }
    ],
}


def _make_stub_entry(**overrides) -> dict:
    """Return a minimal stub paper entry, with optional field overrides."""
    base = {
        "id": "test-entry",
        "type": "paper",
        "source_type": "pdf",
        "source_path": "/tmp/test.pdf",
        "wiki_path": None,
        "zotero_uri": None,
        "pdf_uri": None,
        "arxiv_id": None,
        "title": "Test Paper",
        "citation": None,
        "year": None,
        "tags": ["ml"],
        "project": None,
        "source_name": None,
        "references": [],
        "status": "stub",
        "locked": False,
        "ingested": "2026-01-01T00:00:00Z",
        "rendered": None,
        "last_improved": None,
    }
    base.update(overrides)
    return base


def _make_render_index_side_effect(entry: dict):
    """Build a _run_index side_effect that handles the render call sequence."""

    def side_effect(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "get":
            return _make_completed_process(stdout=json.dumps(entry), returncode=0)
        if subcommand in ("delete-fragments", "update", "add-fragments-batch"):
            return _make_completed_process(returncode=0)
        if subcommand == "get-canonical-tags":
            return _make_completed_process(stdout=json.dumps([]), returncode=0)
        return _make_completed_process(returncode=0)

    return side_effect


def _make_render_config_side_effect():
    """Build a _run_config side_effect that returns JSON for 'get llm' and 'get default_render_depth'."""

    def side_effect(*args, **kw):
        if args and args[0] == "get":
            if len(args) > 1 and args[1] == "llm":
                return _make_completed_process(stdout=_FAKE_LLM_CONFIG, returncode=0)
            if len(args) > 1 and args[1] == "default_render_depth":
                return _make_completed_process(stdout=json.dumps("shallow"), returncode=0)
            if len(args) > 1 and args[1] == "tag_aliases":
                return _make_completed_process(stdout=json.dumps({}), returncode=0)
            if len(args) > 1 and args[1] == "tag_blocklist":
                return _make_completed_process(stdout=json.dumps([]), returncode=0)
        return _make_completed_process(stdout=json.dumps(None), returncode=0)

    return side_effect


def _make_render_run_helpers(mock_run_helpers, entry, source_text="Sample source text."):
    """Configure mock_run_helpers for a typical single-entry render flow."""
    mock_run_helpers["_run_index"].side_effect = _make_render_index_side_effect(entry)
    mock_run_helpers["_run_config"].side_effect = _make_render_config_side_effect()
    mock_run_helpers["_run_render"].side_effect = lambda *a, **kw: (
        _make_completed_process(stdout=source_text, returncode=0)
    )


# ---------------------------------------------------------------------------
# Unit tests (mock_run_helpers + mock_llm)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_render_single_entry_flow(mock_run_helpers, mock_llm, wiki_env, monkeypatch):
    """Happy path: stub paper entry gets rendered and index updated to status=rendered."""
    import wiki_render
    entry = _make_stub_entry()
    _make_render_run_helpers(mock_run_helpers, entry)

    # Patch _assemble_page_from_json so it doesn't write to real paths
    fake_assemble = MagicMock(return_value=_FAKE_ASSEMBLE_RESULT)
    monkeypatch.setattr(wiki_render, "_assemble_page_from_json", fake_assemble)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "render", "test-entry",
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, f"render failed:\n{result.output}"

    # Verify index update was called with status=rendered
    index_mock = mock_run_helpers["_run_index"]
    update_calls = [c for c in index_mock.call_args_list if c.args and c.args[0] == "update"]
    assert len(update_calls) == 1, "Expected exactly one 'update' call"
    update_args = list(update_calls[0].args)
    assert "--status" in update_args
    assert update_args[update_args.index("--status") + 1] == "rendered"


@pytest.mark.unit
def test_render_depth_shallow(mock_run_helpers, mock_llm, wiki_env, monkeypatch):
    """--depth shallow causes llm_call to receive an agent name containing 'shallow'."""
    import wiki_llm
    import wiki_render
    entry = _make_stub_entry()
    _make_render_run_helpers(mock_run_helpers, entry)

    fake_assemble = MagicMock(return_value=_FAKE_ASSEMBLE_RESULT)
    monkeypatch.setattr(wiki_render, "_assemble_page_from_json", fake_assemble)

    captured_agents = []
    original_llm = mock_llm

    import wiki_llm as wiki_llm_mod

    real_fake = wiki_llm_mod.llm_call  # already patched by mock_llm fixture

    def capturing_fake(agent, *args, **kw):
        captured_agents.append(agent)
        return real_fake(agent, *args, **kw)

    monkeypatch.setattr(wiki_llm_mod, "llm_call", capturing_fake)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "render", "test-entry",
        "--depth", "shallow",
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, f"render failed:\n{result.output}"
    assert any("shallow" in a for a in captured_agents), (
        f"Expected agent name containing 'shallow', got: {captured_agents}"
    )


@pytest.mark.unit
def test_render_force_rerenders(mock_run_helpers, mock_llm, wiki_env, monkeypatch):
    """--force causes a rendered entry to be re-rendered (llm_call is made)."""
    import wiki_llm as wiki_llm_mod
    import wiki_render
    entry = _make_stub_entry(status="rendered", wiki_path="wiki/_pages/test-entry.md")
    _make_render_run_helpers(mock_run_helpers, entry)

    fake_assemble = MagicMock(return_value=_FAKE_ASSEMBLE_RESULT)
    monkeypatch.setattr(wiki_render, "_assemble_page_from_json", fake_assemble)

    call_count = {"n": 0}

    original_fake = wiki_llm_mod.llm_call

    def counting_fake(agent, *args, **kw):
        call_count["n"] += 1
        return original_fake(agent, *args, **kw)

    monkeypatch.setattr(wiki_llm_mod, "llm_call", counting_fake)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "render", "test-entry",
        "--force",
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, f"render failed:\n{result.output}"
    assert call_count["n"] >= 1, "Expected llm_call to be invoked at least once"


@pytest.mark.unit
def test_render_locked_skips(mock_run_helpers, wiki_env):
    """Locked entry without --force: exits non-zero and mentions 'locked'."""
    entry = _make_stub_entry(locked=True)
    mock_run_helpers["_run_index"].side_effect = _make_render_index_side_effect(entry)
    mock_run_helpers["_run_config"].side_effect = _make_render_config_side_effect()

    runner = CliRunner()
    result = runner.invoke(cli, [
        "render", "test-entry",
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code != 0
    assert "locked" in result.output.lower()


@pytest.mark.unit
def test_render_idea_rejects(mock_run_helpers, wiki_env):
    """Ideas are rejected: exits non-zero and mentions 'idea'."""
    entry = _make_stub_entry(type="idea", id="idea-test")
    mock_run_helpers["_run_index"].side_effect = _make_render_index_side_effect(entry)
    mock_run_helpers["_run_config"].side_effect = _make_render_config_side_effect()

    runner = CliRunner()
    result = runner.invoke(cli, [
        "render", "idea-test",
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code != 0
    assert "idea" in result.output.lower()


@pytest.mark.unit
def test_render_missing_entry_error(mock_run_helpers, wiki_env):
    """When _run_index get fails (exit 1), render exits non-zero."""
    mock_run_helpers["_run_index"].return_value = _make_completed_process(returncode=1, stderr="not found")
    mock_run_helpers["_run_config"].side_effect = _make_render_config_side_effect()

    runner = CliRunner()
    result = runner.invoke(cli, [
        "render", "nonexistent-entry",
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code != 0


@pytest.mark.unit
def test_render_all_stubs_filters(wiki_env, monkeypatch):
    """--all-stubs preps each stub and batches LLM calls via llm_batch."""
    import wiki_cli

    stubs_list = [
        _make_stub_entry(id="stub-a", status="stub"),
        _make_stub_entry(id="stub-b", status="stub"),
    ]

    monkeypatch.setattr(wiki_cli, "_run_index",
        lambda *a, **kw: _make_completed_process(stdout=json.dumps(stubs_list)))
    monkeypatch.setattr(wiki_cli, "_run_config",
        lambda *a, **kw: _make_completed_process(stdout='"shallow"'))

    prep_calls = []
    def fake_prep(eid, depth, force, focus, index_path, wiki_dir, fragments_only=False):
        prep_calls.append(eid)
        return {"id": eid, "entry": {"id": eid, "type": "paper"}, "agent_name": "render-shallow",
                "schema_name": "render-paper", "user_msg": f"msg-{eid}",
                "preserved_notes": "", "entry_type": "paper", "depth": "shallow"}
    monkeypatch.setattr(wiki_cli, "_render_prep", fake_prep)

    batch_calls = []
    async def fake_batch(agent, items, schema, config, concurrency=10, skip_validation=False):
        batch_calls.append(len(items))
        return [(iid, {"summary": "s", "key_claims": "k", "methods": "m", "results": "r",
                        "limitations": "l", "tags": ["t"], "fragments": [{"seq":1,"type":"claim","title":"c"}]}, None)
                for iid, _ in items]
    monkeypatch.setattr("wiki_llm.llm_batch", fake_batch)
    monkeypatch.setattr(wiki_cli, "_render_finish", lambda prep, result, idx, fragments_only=False: None)

    runner = CliRunner()
    result = runner.invoke(cli, ["render", "--all-stubs", "--index", str(wiki_env.index_path)])
    assert result.exit_code == 0, f"render --all-stubs failed:\n{result.output}"
    assert len(prep_calls) == 2
    assert len(batch_calls) == 1
    assert batch_calls[0] == 2


@pytest.mark.unit
def test_render_tag_filter(wiki_env, monkeypatch):
    """--tag passes the tag to _run_index list."""
    import wiki_cli

    stubs_list = [_make_stub_entry(id="tagged-stub", tags=["transformers"])]

    index_calls = []
    def fake_index(*args, **kw):
        index_calls.append(list(args))
        return _make_completed_process(stdout=json.dumps(stubs_list))
    monkeypatch.setattr(wiki_cli, "_run_index", fake_index)
    monkeypatch.setattr(wiki_cli, "_run_config",
        lambda *a, **kw: _make_completed_process(stdout='"shallow"'))

    def fake_prep(eid, depth, force, focus, index_path, wiki_dir, fragments_only=False):
        return {"id": eid, "entry": {"id": eid, "type": "paper"}, "agent_name": "render-shallow",
                "schema_name": "render-paper", "user_msg": "msg",
                "preserved_notes": "", "entry_type": "paper", "depth": "shallow"}
    monkeypatch.setattr(wiki_cli, "_render_prep", fake_prep)

    async def fake_batch(agent, items, schema, config, concurrency=10, skip_validation=False):
        return [(iid, {}, None) for iid, _ in items]
    monkeypatch.setattr("wiki_llm.llm_batch", fake_batch)
    monkeypatch.setattr(wiki_cli, "_render_finish", lambda prep, result, idx, fragments_only=False: None)

    runner = CliRunner()
    result = runner.invoke(cli, ["render", "--tag", "transformers", "--index", str(wiki_env.index_path)])
    assert result.exit_code == 0, f"render --tag failed:\n{result.output}"

    list_calls = [c for c in index_calls if c and c[0] == "list"]
    assert len(list_calls) == 1
    assert "--tag" in list_calls[0]
    assert list_calls[0][list_calls[0].index("--tag") + 1] == "transformers"


# ---------------------------------------------------------------------------
# Integration tests (populated_index + mock_llm + monkeypatched _run_render)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_render_stub_to_rendered(populated_index, mock_llm, monkeypatch):
    """Full render flow: smithDeepLearning2024 (stub) gets rendered, wiki page written, index updated."""
    import wiki_cli
    import wiki_render as wiki_render_mod

    monkeypatch.chdir(populated_index.root)

    # Patch _run_render to avoid needing real Zotero data
    def fake_run_render(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand in ("extract-source", "extract-notes"):
            return _make_completed_process(stdout="Sample source text for deep learning paper.", returncode=0)
        return _make_completed_process(returncode=0)

    monkeypatch.setattr(wiki_cli, "_run_render", fake_run_render)

    # Patch _run_config to return valid LLM config
    def fake_run_config(*args, **kw):
        if args and args[0] == "get":
            if len(args) > 1 and args[1] == "llm":
                return _make_completed_process(stdout=_FAKE_LLM_CONFIG, returncode=0)
            if len(args) > 1 and args[1] == "default_render_depth":
                return _make_completed_process(stdout=json.dumps("deep"), returncode=0)
            if len(args) > 1 and args[1] == "tag_aliases":
                return _make_completed_process(stdout=json.dumps({}), returncode=0)
            if len(args) > 1 and args[1] == "tag_blocklist":
                return _make_completed_process(stdout=json.dumps([]), returncode=0)
        return _make_completed_process(stdout=json.dumps(None), returncode=0)

    monkeypatch.setattr(wiki_cli, "_run_config", fake_run_config)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "render", "smithDeepLearning2024",
        "--depth", "deep",
        "--index", str(populated_index.index_path),
        "--wiki-dir", str(populated_index.wiki_dir),
    ])
    assert result.exit_code == 0, f"render failed:\noutput: {result.output}\nexc: {result.exception}"

    # Wiki page must exist on disk
    expected_page = populated_index.wiki_dir / "_pages" / "smithDeepLearning2024.md"
    assert expected_page.exists(), f"Wiki page not found at {expected_page}"

    # Index must reflect status=rendered
    index_lines = populated_index.index_path.read_text().splitlines()
    entries = [json.loads(line) for line in index_lines if line.strip()]
    smith_entries = [e for e in entries if e["id"] == "smithDeepLearning2024"]
    assert len(smith_entries) == 1, "Expected exactly one smithDeepLearning2024 entry"
    assert smith_entries[0]["status"] == "rendered", (
        f"Expected status=rendered, got '{smith_entries[0]['status']}'"
    )
    assert smith_entries[0]["wiki_path"] is not None


@pytest.mark.integration
def test_render_force_rerenders_real(populated_index, mock_llm, monkeypatch):
    """--force re-renders an already-rendered entry (jonesTransformers2023)."""
    import wiki_cli

    monkeypatch.chdir(populated_index.root)

    def fake_run_render(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand in ("extract-source", "extract-notes"):
            return _make_completed_process(stdout="Source text for transformers paper.", returncode=0)
        return _make_completed_process(returncode=0)

    monkeypatch.setattr(wiki_cli, "_run_render", fake_run_render)

    def fake_run_config(*args, **kw):
        if args and args[0] == "get":
            if len(args) > 1 and args[1] == "llm":
                return _make_completed_process(stdout=_FAKE_LLM_CONFIG, returncode=0)
            if len(args) > 1 and args[1] == "default_render_depth":
                return _make_completed_process(stdout=json.dumps("shallow"), returncode=0)
            if len(args) > 1 and args[1] == "tag_aliases":
                return _make_completed_process(stdout=json.dumps({}), returncode=0)
            if len(args) > 1 and args[1] == "tag_blocklist":
                return _make_completed_process(stdout=json.dumps([]), returncode=0)
        return _make_completed_process(stdout=json.dumps(None), returncode=0)

    monkeypatch.setattr(wiki_cli, "_run_config", fake_run_config)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "render", "jonesTransformers2023",
        "--force",
        "--depth", "shallow",
        "--index", str(populated_index.index_path),
        "--wiki-dir", str(populated_index.wiki_dir),
    ])
    assert result.exit_code == 0, f"render --force failed:\noutput: {result.output}\nexc: {result.exception}"

    # Page should exist (updated)
    expected_page = populated_index.wiki_dir / "_pages" / "jonesTransformers2023.md"
    assert expected_page.exists(), f"Wiki page not found at {expected_page}"


@pytest.mark.integration
def test_render_locked_skips_real(populated_index, monkeypatch):
    """Locking an entry then rendering it (without --force) should fail."""
    import wiki_cli

    monkeypatch.chdir(populated_index.root)

    # Lock jonesTransformers2023 by updating its index entry in the JSONL
    index_lines = populated_index.index_path.read_text().splitlines()
    updated_lines = []
    for line in index_lines:
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry["id"] == "jonesTransformers2023":
            entry["locked"] = True
        updated_lines.append(json.dumps(entry))
    populated_index.index_path.write_text("\n".join(updated_lines) + "\n")

    runner = CliRunner()
    result = runner.invoke(cli, [
        "render", "jonesTransformers2023",
        "--index", str(populated_index.index_path),
        "--wiki-dir", str(populated_index.wiki_dir),
    ])
    assert result.exit_code != 0
    assert "locked" in result.output.lower()


@pytest.mark.integration
def test_render_idea_rejects_real(populated_index):
    """Rendering idea-merchant-encoder should fail with non-zero exit and mention 'idea'."""
    runner = CliRunner()
    result = runner.invoke(cli, [
        "render", "idea-merchant-encoder",
        "--index", str(populated_index.index_path),
        "--wiki-dir", str(populated_index.wiki_dir),
    ])
    assert result.exit_code != 0
    assert "idea" in result.output.lower()


# ---------------------------------------------------------------------------
# --fragments-only feature tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_render_fragments_only_single_entry(mock_run_helpers, mock_llm, wiki_env, monkeypatch):
    """--fragments-only on a single rendered entry: updates tags + fragments, skips page assembly."""
    import wiki_cli
    import wiki_render

    # Entry must be rendered and have a wiki_path
    wiki_page = wiki_env.wiki_dir / "_pages" / "test-entry.md"
    wiki_page.write_text("# Test Entry\n\n## Notes\n\n<!-- end-notes -->\n\n## Connections\n")
    entry = _make_stub_entry(
        status="rendered",
        wiki_path=str(wiki_page),
    )

    mock_run_helpers["_run_index"].side_effect = _make_render_index_side_effect(entry)
    mock_run_helpers["_run_config"].side_effect = _make_render_config_side_effect()
    mock_run_helpers["_run_render"].side_effect = lambda *a, **kw: _make_completed_process(stdout="", returncode=0)

    # Patch _assemble_page_from_json to detect if it's called (it should NOT be)
    assemble_called = {"n": 0}
    original_assemble = getattr(__import__("wiki_render"), "_assemble_page_from_json", None)

    def spy_assemble(*args, **kw):
        assemble_called["n"] += 1
        return _FAKE_ASSEMBLE_RESULT

    monkeypatch.setattr(wiki_render, "_assemble_page_from_json", spy_assemble)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "render", "test-entry",
        "--fragments-only",
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, f"render --fragments-only failed:\n{result.output}"
    # Page assembly must not be called
    assert assemble_called["n"] == 0, "Expected _assemble_page_from_json NOT to be called with --fragments-only"
    # Output should mention "page unchanged"
    assert "page unchanged" in result.output.lower()


@pytest.mark.unit
def test_render_fragments_only_bypasses_lock(mock_run_helpers, mock_llm, wiki_env, monkeypatch):
    """--fragments-only bypasses lock check: locked entry renders without --force."""
    import wiki_render

    wiki_page = wiki_env.wiki_dir / "_pages" / "locked-entry.md"
    wiki_page.write_text("# Locked Entry\n\n## Notes\n\n<!-- end-notes -->\n\n## Connections\n")
    entry = _make_stub_entry(
        id="locked-entry",
        status="rendered",
        wiki_path=str(wiki_page),
        locked=True,
    )

    mock_run_helpers["_run_index"].side_effect = _make_render_index_side_effect(entry)
    mock_run_helpers["_run_config"].side_effect = _make_render_config_side_effect()
    mock_run_helpers["_run_render"].side_effect = lambda *a, **kw: _make_completed_process(stdout="", returncode=0)

    fake_assemble = MagicMock(return_value=_FAKE_ASSEMBLE_RESULT)
    monkeypatch.setattr(wiki_render, "_assemble_page_from_json", fake_assemble)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "render", "locked-entry",
        "--fragments-only",
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    # Should succeed — lock is bypassed in fragments-only mode
    assert result.exit_code == 0, f"render --fragments-only on locked entry failed:\n{result.output}"
    # "skipped (locked)" should NOT appear — lock was bypassed
    assert "skipped (locked)" not in result.output.lower()


@pytest.mark.unit
def test_render_fragments_only_reads_wiki_page(mock_run_helpers, mock_llm, wiki_env, monkeypatch):
    """--fragments-only reads the wiki page (not source file) as source_text."""
    import wiki_cli

    page_content = "# Test Entry\n\nThis is the wiki page content used as source.\n\n## Notes\n\n<!-- end-notes -->\n\n## Connections\n"
    wiki_page = wiki_env.wiki_dir / "_pages" / "test-entry.md"
    wiki_page.write_text(page_content)
    entry = _make_stub_entry(
        status="rendered",
        wiki_path=str(wiki_page),
        source_path="/tmp/source.pdf",
    )

    mock_run_helpers["_run_index"].side_effect = _make_render_index_side_effect(entry)
    mock_run_helpers["_run_config"].side_effect = _make_render_config_side_effect()

    # Track what extract-source is called with
    extract_source_called = {"n": 0}

    def render_side_effect(*args, **kw):
        if args and args[0] == "extract-source":
            extract_source_called["n"] += 1
        return _make_completed_process(stdout="", returncode=0)

    mock_run_helpers["_run_render"].side_effect = render_side_effect

    captured_user_msg = {}

    def fake_prep(entry_id, depth, force, focus, index_path, wiki_dir, fragments_only=False):
        # Call real _render_prep but capture the result
        import wiki_cli as wc
        # Restore real _render_prep to avoid recursion
        real_prep = wc._render_prep.__wrapped__ if hasattr(wc._render_prep, "__wrapped__") else None
        return None  # Return None to short-circuit after capture

    runner = CliRunner()

    # Instead of intercepting _render_prep, we just verify extract-source is NOT called
    result = runner.invoke(cli, [
        "render", "test-entry",
        "--fragments-only",
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, f"render --fragments-only failed:\n{result.output}"
    # extract-source should NOT be called since we read the wiki page directly
    assert extract_source_called["n"] == 0, (
        "Expected extract-source NOT to be called with --fragments-only"
    )


@pytest.mark.unit
def test_render_fragments_only_schema_is_render_paper(mock_run_helpers, mock_llm, wiki_env, monkeypatch):
    """--fragments-only always uses schema 'render-paper' regardless of entry type."""
    import wiki_cli
    import wiki_llm as wiki_llm_mod

    wiki_page = wiki_env.wiki_dir / "syntheses" / "syn-test.md"
    wiki_page.parent.mkdir(parents=True, exist_ok=True)
    wiki_page.write_text("# Synthesis\n\n## Notes\n\n<!-- end-notes -->\n\n## Connections\n")
    # Synthesis entry — type is "synthesis", but fragments-only should still use "render-paper" schema
    entry = _make_stub_entry(
        id="syn-test",
        type="synthesis",
        status="rendered",
        wiki_path=str(wiki_page),
    )

    mock_run_helpers["_run_index"].side_effect = _make_render_index_side_effect(entry)
    mock_run_helpers["_run_config"].side_effect = _make_render_config_side_effect()
    mock_run_helpers["_run_render"].side_effect = lambda *a, **kw: _make_completed_process(stdout="", returncode=0)

    captured_schema = {}

    real_fake = wiki_llm_mod.llm_call  # already patched by mock_llm fixture

    def capturing_llm(agent, user_msg, schema, config, **kw):
        captured_schema["schema"] = schema
        return real_fake(agent, user_msg, schema, config, **kw)

    monkeypatch.setattr(wiki_llm_mod, "llm_call", capturing_llm)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "render", "syn-test",
        "--depth", "shallow",
        "--fragments-only",
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, f"render --fragments-only synthesis failed:\n{result.output}"
    assert captured_schema.get("schema") == "render-paper", (
        f"Expected schema 'render-paper' for fragments-only, got: {captured_schema.get('schema')}"
    )


@pytest.mark.unit
def test_render_fragments_only_writes_fragments_not_page(mock_run_helpers, mock_llm, wiki_env, monkeypatch):
    """_render_finish with fragments_only=True: calls add-fragments-batch and update (tags), skips page write."""
    import wiki_cli

    wiki_page = wiki_env.wiki_dir / "_pages" / "test-entry.md"
    wiki_page.write_text("# Test Entry\n\n## Notes\n\n<!-- end-notes -->\n\n## Connections\n")
    original_content = wiki_page.read_text()

    entry = _make_stub_entry(
        status="rendered",
        wiki_path=str(wiki_page),
    )

    index_calls = []

    def index_side_effect(*args, **kw):
        index_calls.append(list(args))
        subcommand = args[0] if args else ""
        if subcommand == "get":
            return _make_completed_process(stdout=json.dumps(entry), returncode=0)
        if subcommand == "get-canonical-tags":
            return _make_completed_process(stdout=json.dumps([]), returncode=0)
        return _make_completed_process(returncode=0)

    mock_run_helpers["_run_index"].side_effect = index_side_effect
    mock_run_helpers["_run_config"].side_effect = _make_render_config_side_effect()
    mock_run_helpers["_run_render"].side_effect = lambda *a, **kw: _make_completed_process(stdout="", returncode=0)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "render", "test-entry",
        "--fragments-only",
        "--depth", "shallow",
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, f"render --fragments-only failed:\n{result.output}"

    # Wiki page file must not be modified
    assert wiki_page.read_text() == original_content, "Wiki page should not be modified with --fragments-only"

    # Index must have an 'update' call (for tags) and 'add-fragments-batch' call
    subcommands = [c[0] for c in index_calls]
    assert "update" in subcommands, f"Expected 'update' call for tag update, got: {subcommands}"
    # Note: add-fragments-batch is only called if there are fragments; fixture returns [] frags by default
    # but we just verify update was called with no --status=rendered (fragments-only never sets status)
    update_calls = [c for c in index_calls if c and c[0] == "update"]
    assert len(update_calls) >= 1
    # Verify no --status in update args (fragments-only doesn't change status)
    for uc in update_calls:
        assert "--status" not in uc, f"Expected no --status in fragments-only update, got: {uc}"


@pytest.mark.unit
def test_render_fragments_only_batch_with_type_filter(wiki_env, monkeypatch):
    """--fragments-only --type filters entries and runs fragments extraction batch."""
    import wiki_cli

    wiki_page_a = wiki_env.wiki_dir / "_pages" / "paper-a.md"
    wiki_page_a.write_text("# Paper A\n\n## Notes\n\n<!-- end-notes -->\n\n## Connections\n")
    wiki_page_b = wiki_env.wiki_dir / "_pages" / "paper-b.md"
    wiki_page_b.write_text("# Paper B\n\n## Notes\n\n<!-- end-notes -->\n\n## Connections\n")

    rendered_list = [
        _make_stub_entry(id="paper-a", status="rendered", wiki_path=str(wiki_page_a)),
        _make_stub_entry(id="paper-b", status="rendered", wiki_path=str(wiki_page_b)),
    ]

    index_calls = []

    def fake_index(*args, **kw):
        index_calls.append(list(args))
        subcommand = args[0] if args else ""
        if subcommand == "list":
            return _make_completed_process(stdout=json.dumps(rendered_list))
        return _make_completed_process(stdout=json.dumps([]), returncode=0)

    monkeypatch.setattr(wiki_cli, "_run_index", fake_index)
    monkeypatch.setattr(wiki_cli, "_run_config",
        lambda *a, **kw: _make_completed_process(stdout='"shallow"'))

    prep_calls = []

    def fake_prep(eid, depth, force, focus, index_path, wiki_dir, fragments_only=False):
        prep_calls.append({"eid": eid, "fragments_only": fragments_only})
        return {"id": eid, "entry": {"id": eid, "type": "paper"}, "agent_name": "render-shallow",
                "schema_name": "render-paper", "user_msg": f"msg-{eid}",
                "preserved_notes": "", "entry_type": "paper", "depth": "shallow"}

    monkeypatch.setattr(wiki_cli, "_render_prep", fake_prep)

    async def fake_batch(agent, items, schema, config, concurrency=10, skip_validation=False):
        return [(iid, {"summary": "s", "key_claims": "k", "methods": "m", "results": "r",
                       "limitations": "l", "tags": ["t"], "fragments": []}, None)
                for iid, _ in items]

    monkeypatch.setattr("wiki_llm.llm_batch", fake_batch)
    monkeypatch.setattr(wiki_cli, "_render_finish", lambda prep, result, idx, fragments_only=False: None)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "render", "--fragments-only", "--type", "paper",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"render --fragments-only --type failed:\n{result.output}"

    # Verify --type was passed to list
    list_calls = [c for c in index_calls if c and c[0] == "list"]
    assert len(list_calls) == 1
    assert "--type" in list_calls[0], f"Expected --type in list args, got: {list_calls[0]}"
    assert list_calls[0][list_calls[0].index("--type") + 1] == "paper"

    # --fragments-only batch must NOT pass "--status stub" (operates on rendered entries too)
    assert "--status" not in list_calls[0], (
        f"Expected no --status filter in fragments-only list call, got: {list_calls[0]}"
    )

    # Verify both entries were prepped with fragments_only=True
    assert len(prep_calls) == 2
    for pc in prep_calls:
        assert pc["fragments_only"] is True, f"Expected fragments_only=True in prep call, got: {pc}"


@pytest.mark.unit
def test_render_fragments_only_no_wiki_page_skips(mock_run_helpers, wiki_env):
    """--fragments-only on entry with no wiki page: entry is skipped (prep returns None)."""
    entry = _make_stub_entry(status="rendered", wiki_path=None)
    mock_run_helpers["_run_index"].side_effect = _make_render_index_side_effect(entry)
    mock_run_helpers["_run_config"].side_effect = _make_render_config_side_effect()

    runner = CliRunner()
    result = runner.invoke(cli, [
        "render", "test-entry",
        "--fragments-only",
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    # Should exit non-zero because prep returns None (no wiki page)
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Smoke tests (subprocess)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_render_subprocess_help():
    """render --help exits 0 and shows --depth, --force, --all-stubs."""
    result = _run_cli_subprocess("render", "--help")
    assert result.returncode == 0
    assert "--depth" in result.stdout
    assert "--force" in result.stdout
    assert "--all-stubs" in result.stdout
