"""Tests for wiki CLI query command (P1/P2 flow, web enrichment, save, self-citation filter)."""

import json
import subprocess
import sys
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
# Shared config stub
# ---------------------------------------------------------------------------

_FAKE_LLM_CONFIG = json.dumps({
    "base_url": "https://localhost:9999",
    "api_key_env": "TEST_API_KEY",
    "models": {"nano": "test-nano", "mini": "test-mini", "full": "test-full"},
    "default_params": {"max_tokens": 4096, "temperature": 0},
    "model_overrides": {},
})


def _fake_run_config(*args, **kw):
    """Return valid LLM config JSON for 'get llm'; null for other keys."""
    if args and args[0] == "get":
        if len(args) > 1 and args[1] == "llm":
            return _make_completed_process(stdout=_FAKE_LLM_CONFIG, returncode=0)
    return _make_completed_process(stdout=json.dumps(None), returncode=0)


# ---------------------------------------------------------------------------
# Fixture data shared by unit tests
# ---------------------------------------------------------------------------

# Two entries that P1 will request; IDs referenced in the P1 fixture
_CANDIDATE_IDS = ["smithDeepLearning2024", "jonesTransformers2023"]

# Minimal index entries for the two candidates (unit tests use these for get calls)
_ENTRY_SMITH = {
    "id": "smithDeepLearning2024",
    "type": "paper",
    "source_type": "zotero",
    "status": "stub",
    "wiki_path": None,
    "tags": [],
    "source_name": None,
    "title": "Deep Learning",
}
_ENTRY_JONES = {
    "id": "jonesTransformers2023",
    "type": "paper",
    "source_type": "pdf",
    "status": "rendered",
    "wiki_path": "/tmp/jonesTransformers2023.md",
    "tags": ["transformers"],
    "source_name": None,
    "title": "Transformers for Tabular Data",
}

# P1 response that requests page reads for our two candidates
_P1_NEEDS_PAGES = {
    "routing": {
        "decision": "needs_pages",
        "coverage": "partial",
        "specificity": "high",
        "source_diversity": "low",
        "question_type": "comparative",
        "ambiguity": "low",
    },
    "needed_page_ids": list(_CANDIDATE_IDS),
}

# P1 response that answers directly (no P2)
_P1_DIRECT = {
    "routing": {
        "decision": "direct",
        "coverage": "full",
        "specificity": "high",
        "source_diversity": "low",
        "question_type": "factual",
        "ambiguity": "low",
    },
    "synthesis": "A direct answer with [[smithDeepLearning2024]] as a source.",
    "title": "Direct Answer",
    "query": "What is deep learning?",
    "tags": ["deep-learning"],
    "fragments": [],
}

# P2 synthesis response
_P2_RESULT = {
    "query": "What is deep learning?",
    "title": "Overview of Deep Learning",
    "synthesis": "Deep learning uses [[smithDeepLearning2024]] and [[jonesTransformers2023]] as key references.",
    "tags": ["deep-learning", "transformers"],
    "fragments": [],
}


def _make_query_index_side_effect(extra_entries=None):
    """Return a side_effect function for _run_index that handles search + get calls."""
    extra = extra_entries or {}

    def side_effect(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "search":
            return _make_completed_process(stdout=json.dumps(_CANDIDATE_IDS), returncode=0)
        # Other subcommands succeed silently
        return _make_completed_process(returncode=0)

    return side_effect


# ---------------------------------------------------------------------------
# Unit tests — mock_run_helpers + mock_llm
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_query_p1_flow(mock_run_helpers, mock_llm, wiki_env, monkeypatch):
    """P1 flow: search is called, llm_call receives query-p1, result is dispatched."""
    monkeypatch.chdir(wiki_env.root)

    mock_run_helpers["_run_index"].side_effect = _make_query_index_side_effect()
    mock_run_helpers["_run_config"].side_effect = _fake_run_config

    # P1 returns direct so no P2 or read-batch needed
    mock_llm["query-p1"] = _P1_DIRECT

    runner = CliRunner()
    result = runner.invoke(cli, [
        "query", "What is deep learning?",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"query failed:\n{result.output}\nexc: {result.exception}"

    # Verify _run_index search was called
    search_calls = [
        c for c in mock_run_helpers["_run_index"].call_args_list
        if c.args and c.args[0] == "search"
    ]
    assert len(search_calls) == 1, "Expected exactly one 'search' call"
    search_args = list(search_calls[0].args)
    assert "--query" in search_args
    assert "What is deep learning?" in search_args

    # Verify result file was written
    result_file = wiki_env.root / ".wiki" / "last-query-result.md"
    assert result_file.exists(), "Result file not written"
    data = json.loads(result_file.read_text())
    assert "synthesis" in data


@pytest.mark.unit
def test_query_p2_flow(mock_run_helpers, mock_llm, wiki_env, monkeypatch):
    """P1 returns needs_pages → read-batch is called → P2 synthesizes → result written as JSON."""
    monkeypatch.chdir(wiki_env.root)

    mock_run_helpers["_run_index"].side_effect = _make_query_index_side_effect()
    mock_run_helpers["_run_config"].side_effect = _fake_run_config
    # read-batch returns stub page text
    mock_run_helpers["_run_render"].return_value = _make_completed_process(
        stdout="===PAGE: smithDeepLearning2024===\nSome content\n===END_PAGE===",
        returncode=0,
    )

    mock_llm["query-p1"] = _P1_NEEDS_PAGES
    mock_llm["query-p2"] = _P2_RESULT

    runner = CliRunner()
    result = runner.invoke(cli, [
        "query", "What is deep learning?",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"query failed:\n{result.output}\nexc: {result.exception}"

    # Verify read-batch was called
    render_calls = [
        c for c in mock_run_helpers["_run_render"].call_args_list
        if c.args and c.args[0] == "read-batch"
    ]
    assert len(render_calls) >= 1, "Expected read-batch to be called"

    # Verify result file was written with P2 content
    result_file = wiki_env.root / ".wiki" / "last-query-result.md"
    assert result_file.exists(), "Result file not written"
    data = json.loads(result_file.read_text())
    assert data.get("title") == "Overview of Deep Learning"
    assert "sources" in data


@pytest.mark.unit
def test_query_self_citation_filter(mock_run_helpers, mock_llm, wiki_env, monkeypatch):
    """Self-citation: synthesis entry whose source_name matches the question is excluded from read-batch."""
    monkeypatch.chdir(wiki_env.root)

    question = "What are the main fraud detection methods?"
    # Write a synthesis entry to the index that matches the question exactly
    syn_entry = {
        "id": "syn-fraud-detection-methods",
        "type": "synthesis",
        "source_type": "query",
        "source_path": None,
        "wiki_path": str(wiki_env.wiki_dir / "syntheses" / "syn-fraud-detection-methods.md"),
        "zotero_uri": None,
        "pdf_uri": None,
        "arxiv_id": None,
        "title": "Fraud Detection Methods",
        "citation": None,
        "year": None,
        "tags": ["fraud-detection"],
        "project": None,
        "source_name": question,  # Exact match — should be filtered out
        "references": [],
        "status": "rendered",
        "locked": False,
        "ingested": "2026-01-01T00:00:00Z",
        "rendered": "2026-01-02T00:00:00Z",
        "last_improved": None,
    }
    # Include syn entry in the search results (P1 will request it)
    candidate_ids_with_syn = [*_CANDIDATE_IDS, "syn-fraud-detection-methods"]

    def index_side_effect(*args, **kw):
        if args and args[0] == "search":
            return _make_completed_process(stdout=json.dumps(candidate_ids_with_syn), returncode=0)
        return _make_completed_process(returncode=0)

    mock_run_helpers["_run_index"].side_effect = index_side_effect
    mock_run_helpers["_run_config"].side_effect = _fake_run_config
    mock_run_helpers["_run_render"].return_value = _make_completed_process(stdout="", returncode=0)

    # Write the syn entry to the real index file (self-citation filter reads disk directly)
    with open(wiki_env.index_path, "w") as f:
        f.write(json.dumps(syn_entry) + "\n")

    p1_needs_syn = {
        "routing": {"decision": "needs_pages", "coverage": "partial", "specificity": "high",
                    "source_diversity": "low", "question_type": "comparative", "ambiguity": "low"},
        "needed_page_ids": candidate_ids_with_syn,
    }
    mock_llm["query-p1"] = p1_needs_syn
    mock_llm["query-p2"] = _P2_RESULT

    runner = CliRunner()
    result = runner.invoke(cli, [
        "query", question,
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"query failed:\n{result.output}\nexc: {result.exception}"

    # Verify read-batch input does NOT contain the syn entry
    render_calls = [
        c for c in mock_run_helpers["_run_render"].call_args_list
        if c.args and c.args[0] == "read-batch"
    ]
    assert len(render_calls) >= 1, "Expected read-batch to be called"
    batch_call = render_calls[0]
    input_data = json.loads(batch_call.kwargs.get("input_data", "[]"))
    assert "syn-fraud-detection-methods" not in input_data, (
        "Self-citation entry should have been excluded from read-batch input"
    )


@pytest.mark.unit
def test_query_null_wiki_path_skipped(mock_run_helpers, mock_llm, wiki_env, monkeypatch):
    """Entry with wiki_path=null in needed_page_ids: read-batch is still called (graceful handling inside)."""
    monkeypatch.chdir(wiki_env.root)

    # smithDeepLearning2024 has wiki_path=None in our fixture list
    p1_with_null_path = {
        "routing": {"decision": "needs_pages", "coverage": "partial", "specificity": "high",
                    "source_diversity": "low", "question_type": "comparative", "ambiguity": "low"},
        "needed_page_ids": ["smithDeepLearning2024", "jonesTransformers2023"],
    }
    mock_run_helpers["_run_index"].side_effect = _make_query_index_side_effect()
    mock_run_helpers["_run_config"].side_effect = _fake_run_config
    mock_run_helpers["_run_render"].return_value = _make_completed_process(
        stdout="===PAGE: jonesTransformers2023===\nContent\n===END_PAGE===",
        returncode=0,
    )

    mock_llm["query-p1"] = p1_with_null_path
    mock_llm["query-p2"] = _P2_RESULT

    runner = CliRunner()
    result = runner.invoke(cli, [
        "query", "What is deep learning?",
        "--index", str(wiki_env.index_path),
    ])
    # Should succeed — null wiki_path is handled inside read-batch (content: null, skipped)
    assert result.exit_code == 0, f"query failed:\n{result.output}\nexc: {result.exception}"

    # Both IDs should be in the read-batch input (the null handling is read-batch's responsibility)
    render_calls = [
        c for c in mock_run_helpers["_run_render"].call_args_list
        if c.args and c.args[0] == "read-batch"
    ]
    assert len(render_calls) >= 1
    input_data = json.loads(render_calls[0].kwargs.get("input_data", "[]"))
    assert "smithDeepLearning2024" in input_data


@pytest.mark.unit
def test_query_web_calls_enrichment(mock_run_helpers, mock_llm, wiki_env, monkeypatch):
    """--web flag causes _run_web_enrichment to be called."""
    import wiki_cli

    monkeypatch.chdir(wiki_env.root)
    mock_run_helpers["_run_index"].side_effect = _make_query_index_side_effect()
    mock_run_helpers["_run_config"].side_effect = _fake_run_config

    mock_llm["query-p1"] = _P1_DIRECT

    enrichment_called = {"n": 0}

    def fake_enrichment(question, index_path, auto, select):
        enrichment_called["n"] += 1
        return 0  # 0 acquired — skips re-query

    monkeypatch.setattr(wiki_cli, "_run_web_enrichment", fake_enrichment)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "query", "What is deep learning?",
        "--web",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"query failed:\n{result.output}\nexc: {result.exception}"
    assert enrichment_called["n"] == 1, "Expected _run_web_enrichment to be called once"


@pytest.mark.unit
def test_query_save_calls_self(mock_run_helpers, mock_llm, wiki_env, monkeypatch):
    """--save flag causes _run_self('save', ...) to be called after query."""
    monkeypatch.chdir(wiki_env.root)
    mock_run_helpers["_run_index"].side_effect = _make_query_index_side_effect()
    mock_run_helpers["_run_config"].side_effect = _fake_run_config

    mock_llm["query-p1"] = _P1_DIRECT

    runner = CliRunner()
    result = runner.invoke(cli, [
        "query", "What is deep learning?",
        "--save",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"query failed:\n{result.output}\nexc: {result.exception}"

    save_calls = [
        c for c in mock_run_helpers["_run_self"].call_args_list
        if c.args and c.args[0] == "save"
    ]
    assert len(save_calls) == 1, "Expected exactly one _run_self('save', ...) call"


@pytest.mark.unit
def test_query_auto_passthrough(mock_run_helpers, mock_llm, wiki_env, monkeypatch):
    """--web --auto passes auto=True to _run_web_enrichment."""
    import wiki_cli

    monkeypatch.chdir(wiki_env.root)
    mock_run_helpers["_run_index"].side_effect = _make_query_index_side_effect()
    mock_run_helpers["_run_config"].side_effect = _fake_run_config

    mock_llm["query-p1"] = _P1_DIRECT

    captured = {}

    def fake_enrichment(question, index_path, auto, select):
        captured["auto"] = auto
        captured["select"] = select
        return 0

    monkeypatch.setattr(wiki_cli, "_run_web_enrichment", fake_enrichment)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "query", "What is deep learning?",
        "--web", "--auto",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"query failed:\n{result.output}\nexc: {result.exception}"
    assert captured.get("auto") is True, "Expected auto=True forwarded to enrichment"


@pytest.mark.unit
def test_query_no_requery_skips(mock_run_helpers, mock_llm, wiki_env, monkeypatch):
    """--web --no-requery: after web enrichment acquires papers, second _run_core_query is NOT called."""
    import wiki_cli

    monkeypatch.chdir(wiki_env.root)

    core_query_call_count = {"n": 0}
    original_core = wiki_cli._run_core_query

    def counting_core(question, index_path, result_path):
        core_query_call_count["n"] += 1
        # First call: write a minimal result so the command can proceed
        import json as _json
        with open(result_path, "w") as f:
            _json.dump(_P1_DIRECT, f)
        return _P1_DIRECT

    monkeypatch.setattr(wiki_cli, "_run_core_query", counting_core)

    def fake_enrichment(question, index_path, auto, select):
        return 3  # Acquired 3 papers — would trigger re-query without --no-requery

    monkeypatch.setattr(wiki_cli, "_run_web_enrichment", fake_enrichment)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "query", "What is deep learning?",
        "--web", "--no-requery",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"query failed:\n{result.output}\nexc: {result.exception}"
    assert core_query_call_count["n"] == 1, (
        f"Expected exactly 1 core query call with --no-requery, got {core_query_call_count['n']}"
    )


@pytest.mark.unit
def test_query_no_p1_results(mock_run_helpers, mock_llm, wiki_env, monkeypatch):
    """P1 returns decision='direct': no P2 called, result still written with synthesis."""
    monkeypatch.chdir(wiki_env.root)

    mock_run_helpers["_run_index"].side_effect = _make_query_index_side_effect()
    mock_run_helpers["_run_config"].side_effect = _fake_run_config

    mock_llm["query-p1"] = _P1_DIRECT

    runner = CliRunner()
    result = runner.invoke(cli, [
        "query", "What is deep learning?",
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code == 0, f"query failed:\n{result.output}\nexc: {result.exception}"

    # read-batch should NOT be called (direct routing skips P2)
    render_calls = [
        c for c in mock_run_helpers["_run_render"].call_args_list
        if c.args and c.args[0] == "read-batch"
    ]
    assert len(render_calls) == 0, "Expected no read-batch call for direct routing"

    # Result file should exist with synthesis
    result_file = wiki_env.root / ".wiki" / "last-query-result.md"
    assert result_file.exists()
    data = json.loads(result_file.read_text())
    assert "synthesis" in data
    assert data["synthesis"] == _P1_DIRECT["synthesis"]


@pytest.mark.unit
def test_query_null_source_name_not_filtered(mock_run_helpers, mock_llm, wiki_env, monkeypatch):
    """Synthesis entry with source_name=None is never mistakenly self-citation filtered.

    The fix at line ~1381 uses `(... or "").strip()` so None doesn't raise AttributeError
    and doesn't accidentally match any real question.
    """
    monkeypatch.chdir(wiki_env.root)

    question = "What are the main fraud detection methods?"
    # Write a synthesis entry with source_name=None to the index
    syn_entry_null_name = {
        "id": "syn-generic",
        "type": "synthesis",
        "source_type": "query",
        "source_path": None,
        "wiki_path": str(wiki_env.wiki_dir / "syntheses" / "syn-generic.md"),
        "zotero_uri": None,
        "pdf_uri": None,
        "arxiv_id": None,
        "title": "Generic Synthesis",
        "citation": None,
        "year": None,
        "tags": ["fraud-detection"],
        "project": None,
        "source_name": None,  # None — must not cause AttributeError or be filtered
        "references": [],
        "status": "rendered",
        "locked": False,
        "ingested": "2026-01-01T00:00:00Z",
        "rendered": "2026-01-02T00:00:00Z",
        "last_improved": None,
    }

    # Write it to the index
    with open(wiki_env.index_path, "w") as f:
        f.write(json.dumps(syn_entry_null_name) + "\n")

    candidate_ids = ["syn-generic", "smithDeepLearning2024"]

    def index_side_effect(*args, **kw):
        if args and args[0] == "search":
            return _make_completed_process(stdout=json.dumps(candidate_ids), returncode=0)
        return _make_completed_process(returncode=0)

    mock_run_helpers["_run_index"].side_effect = index_side_effect
    mock_run_helpers["_run_config"].side_effect = _fake_run_config
    mock_run_helpers["_run_render"].return_value = _make_completed_process(stdout="", returncode=0)

    p1_needs_pages = {
        "routing": {"decision": "needs_pages", "coverage": "partial", "specificity": "high",
                    "source_diversity": "low", "question_type": "exploratory", "ambiguity": "low"},
        "needed_page_ids": candidate_ids,
    }
    mock_llm["query-p1"] = p1_needs_pages
    mock_llm["query-p2"] = {
        "query": question,
        "title": "Fraud Detection Overview",
        "synthesis": "[[syn-generic]] provides context.",
        "tags": ["fraud-detection"],
        "fragments": [],
    }

    runner = CliRunner()
    result = runner.invoke(cli, [
        "query", question,
        "--index", str(wiki_env.index_path),
    ])
    # Must NOT raise AttributeError from None.strip()
    assert result.exit_code == 0, f"query failed:\n{result.output}\nexc: {result.exception}"

    # syn-generic (with source_name=None) should NOT be self-citation filtered
    render_calls = [
        c for c in mock_run_helpers["_run_render"].call_args_list
        if c.args and c.args[0] == "read-batch"
    ]
    assert len(render_calls) >= 1, "Expected read-batch to be called"
    input_data = json.loads(render_calls[0].kwargs.get("input_data", "[]"))
    assert "syn-generic" in input_data, (
        "syn-generic with source_name=None should not be filtered by self-citation logic"
    )


# ---------------------------------------------------------------------------
# Integration tests — populated_index + mock_llm
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_query_full_p1_p2_flow(populated_index, mock_llm, monkeypatch):
    """Full P1→P2 flow with real index and stub pages. Result JSON has a title field."""
    import wiki_cli

    monkeypatch.chdir(populated_index.root)

    # Override _run_config for LLM config
    monkeypatch.setattr(wiki_cli, "_run_config", _fake_run_config)

    # Override _run_index search to return known IDs from populated_index
    real_run_index = wiki_cli._run_index

    def smart_run_index(*args, **kw):
        if args and args[0] == "search":
            return _make_completed_process(
                stdout=json.dumps(["jonesTransformers2023", "exp-churn-model-v1"]),
                returncode=0,
            )
        return real_run_index(*args, **kw)

    monkeypatch.setattr(wiki_cli, "_run_index", smart_run_index)

    # P1 requests the two rendered pages that have stub files on disk
    mock_llm["query-p1"] = {
        "routing": {
            "decision": "needs_pages",
            "coverage": "partial",
            "specificity": "high",
            "source_diversity": "low",
            "question_type": "comparative",
            "ambiguity": "low",
        },
        "needed_page_ids": ["jonesTransformers2023", "exp-churn-model-v1"],
    }
    mock_llm["query-p2"] = {
        "query": "What transformers work for tabular churn prediction?",
        "title": "Transformers and Churn Prediction Methods",
        "synthesis": "[[jonesTransformers2023]] covers transformers for tabular data, while [[exp-churn-model-v1]] provides a churn experiment baseline.",
        "tags": ["transformers", "churn"],
        "fragments": [],
    }

    runner = CliRunner()
    result = runner.invoke(cli, [
        "query", "What transformers work for tabular churn prediction?",
        "--index", str(populated_index.index_path),
    ])
    assert result.exit_code == 0, f"query failed:\n{result.output}\nexc: {result.exception}"

    # Result file must have title and sources fields
    result_file = populated_index.root / ".wiki" / "last-query-result.md"
    assert result_file.exists(), "Result file not written"
    data = json.loads(result_file.read_text())
    assert "title" in data, "Result JSON missing 'title' field"
    assert data["title"] == "Transformers and Churn Prediction Methods"


@pytest.mark.integration
def test_query_self_citation_real_index(populated_index, mock_llm, monkeypatch):
    """Self-citation with real index: syn-fraud-detection-methods excluded from P2 page reads."""
    import wiki_cli

    question = "What are the main fraud detection methods?"
    monkeypatch.chdir(populated_index.root)

    monkeypatch.setattr(wiki_cli, "_run_config", _fake_run_config)

    # Search returns all IDs including the syn entry
    real_run_index = wiki_cli._run_index

    def smart_run_index(*args, **kw):
        if args and args[0] == "search":
            return _make_completed_process(
                stdout=json.dumps([
                    "jonesTransformers2023",
                    "syn-fraud-detection-methods",
                    "exp-churn-model-v1",
                ]),
                returncode=0,
            )
        return real_run_index(*args, **kw)

    monkeypatch.setattr(wiki_cli, "_run_index", smart_run_index)

    # P1 requests all three including the self-citation
    mock_llm["query-p1"] = {
        "routing": {
            "decision": "needs_pages",
            "coverage": "partial",
            "specificity": "high",
            "source_diversity": "low",
            "question_type": "exploratory",
            "ambiguity": "low",
        },
        "needed_page_ids": [
            "jonesTransformers2023",
            "syn-fraud-detection-methods",
            "exp-churn-model-v1",
        ],
    }
    mock_llm["query-p2"] = {
        "query": question,
        "title": "Fraud Detection Overview",
        "synthesis": "[[jonesTransformers2023]] and [[exp-churn-model-v1]] are relevant.",
        "tags": ["fraud-detection"],
        "fragments": [],
    }

    # Track what read-batch receives
    captured_batch_input = []
    real_run_render = wiki_cli._run_render

    def tracking_run_render(*args, **kw):
        if args and args[0] == "read-batch":
            raw = kw.get("input_data", "[]")
            captured_batch_input.extend(json.loads(raw))
            return _make_completed_process(
                stdout="===PAGE: jonesTransformers2023===\nContent\n===END_PAGE===",
                returncode=0,
            )
        return real_run_render(*args, **kw)

    monkeypatch.setattr(wiki_cli, "_run_render", tracking_run_render)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "query", question,
        "--index", str(populated_index.index_path),
    ])
    assert result.exit_code == 0, f"query failed:\n{result.output}\nexc: {result.exception}"

    # syn entry must not appear in read-batch input
    assert "syn-fraud-detection-methods" not in captured_batch_input, (
        "Self-citation syn entry should have been excluded from read-batch"
    )
    # Other entries should remain
    assert "jonesTransformers2023" in captured_batch_input


@pytest.mark.integration
def test_query_result_has_sources(populated_index, mock_llm, monkeypatch):
    """Result JSON has a 'sources' field extracted from [[wikilink]] citations in synthesis."""
    import wiki_cli

    monkeypatch.chdir(populated_index.root)
    monkeypatch.setattr(wiki_cli, "_run_config", _fake_run_config)

    real_run_index = wiki_cli._run_index

    def smart_run_index(*args, **kw):
        if args and args[0] == "search":
            return _make_completed_process(
                stdout=json.dumps(["jonesTransformers2023"]),
                returncode=0,
            )
        return real_run_index(*args, **kw)

    monkeypatch.setattr(wiki_cli, "_run_index", smart_run_index)

    # P2 synthesis has two [[wikilink]] citations
    mock_llm["query-p1"] = {
        "routing": {
            "decision": "needs_pages",
            "coverage": "partial",
            "specificity": "high",
            "source_diversity": "low",
            "question_type": "comparative",
            "ambiguity": "low",
        },
        "needed_page_ids": ["jonesTransformers2023"],
    }
    mock_llm["query-p2"] = {
        "query": "What are transformers?",
        "title": "Transformer Overview",
        "synthesis": "See [[jonesTransformers2023]] and [[exp-churn-model-v1]] for details.",
        "tags": ["transformers"],
        "fragments": [],
    }

    real_run_render = wiki_cli._run_render

    def fake_run_render(*args, **kw):
        if args and args[0] == "read-batch":
            return _make_completed_process(
                stdout="===PAGE: jonesTransformers2023===\nContent\n===END_PAGE===",
                returncode=0,
            )
        return real_run_render(*args, **kw)

    monkeypatch.setattr(wiki_cli, "_run_render", fake_run_render)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "query", "What are transformers?",
        "--index", str(populated_index.index_path),
    ])
    assert result.exit_code == 0, f"query failed:\n{result.output}\nexc: {result.exception}"

    result_file = populated_index.root / ".wiki" / "last-query-result.md"
    data = json.loads(result_file.read_text())
    assert "sources" in data, "Result JSON missing 'sources' field"
    sources = data["sources"]
    assert "jonesTransformers2023" in sources
    assert "exp-churn-model-v1" in sources


# ---------------------------------------------------------------------------
# Smoke tests (subprocess)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_query_subprocess_help():
    """query --help exits 0 and shows --save, --web, --auto."""
    result = _run_cli_subprocess("query", "--help")
    assert result.returncode == 0
    assert "--save" in result.stdout
    assert "--web" in result.stdout
    assert "--auto" in result.stdout
