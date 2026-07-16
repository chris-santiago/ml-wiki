"""Shared pytest fixtures and helpers for the wiki CLI test suite."""

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).resolve().parent.parent  # .wiki/scripts/
REPO_ROOT = SCRIPTS_DIR.parent.parent               # repo root (two levels up from .wiki/scripts/)
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
CLI_PATH = str(SCRIPTS_DIR / "wiki_cli.py")

# Ensure scripts are importable
sys.path.insert(0, str(SCRIPTS_DIR))

# ---------------------------------------------------------------------------
# Helper functions (module-level, usable without fixtures)
# ---------------------------------------------------------------------------


def load_fixture(name: str) -> dict:
    """Load a fixture JSON file from the fixtures directory."""
    path = FIXTURES_DIR / f"{name}.json"
    with open(path) as f:
        return json.load(f)


def load_fixture_with(name: str, **overrides) -> dict:
    """Load a fixture then deep-merge keyword overrides into the result."""
    data = load_fixture(name)
    _deep_merge(data, overrides)
    return data


def _deep_merge(base: dict, overrides: dict) -> None:
    """Recursively merge overrides into base in place."""
    for key, value in overrides.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def run_cli_subprocess(*args) -> CompletedProcess:
    """Run the wiki CLI as a subprocess and return the CompletedProcess."""
    return subprocess.run(
        [sys.executable, CLI_PATH, *args],
        capture_output=True,
        text=True,
    )


def _make_completed_process(stdout: str = "", stderr: str = "", returncode: int = 0) -> CompletedProcess:
    """Create a CompletedProcess instance for use in mocks."""
    return CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# WikiEnv dataclass
# ---------------------------------------------------------------------------


@dataclass
class WikiEnv:
    root: Path
    wiki_dir: Path
    index_path: Path
    config_path: Path
    sources_dir: Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def wiki_env(tmp_path) -> WikiEnv:
    """Create a complete tmp directory structure for wiki tests."""
    # wiki/ subdirs
    wiki_dir = tmp_path / "wiki"
    for sub in ("_pages", "topics", "projects", "syntheses", "images", "assets", "experiments", "ideas"):
        (wiki_dir / sub).mkdir(parents=True, exist_ok=True)
    # wiki/sources/arxiv
    (wiki_dir / "sources" / "arxiv").mkdir(parents=True, exist_ok=True)

    # .wiki/ subdirs
    dot_wiki = tmp_path / ".wiki"
    (dot_wiki / "sources").mkdir(parents=True, exist_ok=True)

    # Symlinks into real scripts/schemas/agents dirs
    (dot_wiki / "scripts").symlink_to(SCRIPTS_DIR)
    schemas_dir = SCRIPTS_DIR.parent / "schemas"
    agents_dir = SCRIPTS_DIR.parent / "agents"
    (dot_wiki / "schemas").symlink_to(schemas_dir)
    (dot_wiki / "agents").symlink_to(agents_dir)

    # Empty index
    index_path = dot_wiki / "index.jsonl"
    index_path.write_text("")

    # Minimal config.yaml (written as raw string — PyYAML is not imported here)
    config_path = dot_wiki / "config.yaml"
    config_path.write_text(
        f"""\
research_wiki:
  wiki_dir: {wiki_dir}
  index_path: {index_path}
  default_render_depth: shallow
  llm:
    base_url: https://localhost:9999
    api_key_env: TEST_API_KEY
    models:
      nano: test-nano
      mini: test-mini
      full: test-full
    default_params:
      max_tokens: 4096
      temperature: 0
    model_overrides: {{}}
  tag_aliases: {{}}
  sources: []
"""
    )

    return WikiEnv(
        root=tmp_path,
        wiki_dir=wiki_dir,
        index_path=index_path,
        config_path=config_path,
        sources_dir=dot_wiki / "sources",
    )


# ---------------------------------------------------------------------------
# Shared index entry builder
# ---------------------------------------------------------------------------

_INGESTED_TS = "2026-01-01T00:00:00Z"
_RENDERED_TS = "2026-01-02T00:00:00Z"


def _make_entry(
    id: str,
    type: str,
    source_type: str | None,
    status: str,
    wiki_path: str | None,
    tags: list,
    project: str | None = None,
    source_name: str | None = None,
    locked: bool = False,
    references: list | None = None,
    title: str = "",
) -> dict:
    return {
        "id": id,
        "type": type,
        "source_type": source_type,
        "source_path": None,
        "wiki_path": wiki_path,
        "zotero_uri": None,
        "pdf_uri": None,
        "arxiv_id": None,
        "title": title or id,
        "citation": None,
        "year": None,
        "tags": tags,
        "project": project,
        "source_name": source_name,
        "references": references if references is not None else [],
        "status": status,
        "locked": locked,
        "ingested": _INGESTED_TS,
        "rendered": _RENDERED_TS if status == "rendered" else None,
        "last_improved": None,
    }


def _stub_page(entry_id: str, entry_type: str) -> str:
    """Generate a minimal stub wiki page."""
    return f"""\
---
id: {entry_id}
type: {entry_type}
---

# {entry_id}

## Summary

Stub summary for {entry_id}.

## Notes

<!-- end-notes -->

## Connections
"""


@pytest.fixture
def populated_index(wiki_env: WikiEnv) -> WikiEnv:
    """Extend wiki_env with 8 pre-built index entries and stub wiki pages."""
    wiki_dir = wiki_env.wiki_dir

    # Build relative wiki paths (relative to repo root for index entries)
    def wp(rel: str) -> str:
        return str(wiki_dir / rel)

    entries = [
        _make_entry(
            id="smithDeepLearning2024",
            type="paper",
            source_type="zotero",
            status="stub",
            wiki_path=None,
            tags=[],
            title="Deep Learning",
        ),
        _make_entry(
            id="jonesTransformers2023",
            type="paper",
            source_type="pdf",
            status="rendered",
            wiki_path=wp("_pages/jonesTransformers2023.md"),
            tags=["transformers", "tabular-data"],
            title="Transformers for Tabular Data",
        ),
        _make_entry(
            id="exp-churn-model-v1",
            type="experiment",
            source_type="ml-journal",
            status="rendered",
            wiki_path=wp("_pages/exp-churn-model-v1.md"),
            tags=["churn", "classification"],
            project="ato",
            title="Churn Model v1",
        ),
        _make_entry(
            id="syn-fraud-detection-methods",
            type="synthesis",
            source_type="query",
            status="rendered",
            wiki_path=wp("syntheses/syn-fraud-detection-methods.md"),
            tags=["fraud-detection"],
            source_name="What are the main fraud detection methods?",
            references=["smithDeepLearning2024"],
            title="Fraud Detection Methods",
        ),
        _make_entry(
            id="idea-merchant-encoder",
            type="idea",
            source_type="manual",
            status="rendered",
            wiki_path=wp("ideas/idea-merchant-encoder.md"),
            tags=["sequence-modeling"],
            title="Merchant Encoder Idea",
        ),
        _make_entry(
            id="topic-deep-learning",
            type="topic-moc",
            source_type="moc",
            status="rendered",
            wiki_path=wp("topics/topic-deep-learning.md"),
            tags=["deep-learning"],
            title="Deep Learning (Topic)",
        ),
        _make_entry(
            id="project-ato",
            type="project-moc",
            source_type="moc",
            status="rendered",
            wiki_path=wp("projects/project-ato.md"),
            tags=[],
            project="ato",
            title="ATO Project",
        ),
        _make_entry(
            id="img-confusion-matrix",
            type="image",
            source_type="image",
            status="rendered",
            wiki_path=wp("images/img-confusion-matrix.md"),
            tags=["visualization"],
            title="Confusion Matrix",
        ),
    ]

    # Write entries to index
    with open(wiki_env.index_path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")

    # Create stub wiki pages on disk for all rendered entries
    for entry in entries:
        if entry["status"] == "rendered" and entry["wiki_path"]:
            page_path = Path(entry["wiki_path"])
            page_path.parent.mkdir(parents=True, exist_ok=True)
            page_path.write_text(_stub_page(entry["id"], entry["type"]))

    return wiki_env


@pytest.fixture
def mock_llm(monkeypatch) -> dict:
    """Monkeypatch wiki_llm.llm_call with a controllable fake.

    Returns the `responses` dict so tests can pre-populate it:

        def test_something(mock_llm):
            mock_llm["render-shallow"] = {"summary": "...", ...}
    """
    import wiki_llm  # importable via sys.path.insert above

    responses: dict = {}

    def fake_llm_call(agent, input_data, schema, config, **kwargs):
        if agent in responses:
            value = responses[agent]
            if isinstance(value, list):
                return value.pop(0)
            return value
        # Fall back to fixture file
        fixture_path = FIXTURES_DIR / f"{agent}.json"
        if fixture_path.exists():
            with open(fixture_path) as f:
                return json.load(f)
        raise ValueError(f"mock_llm: no response registered for agent '{agent}' and no fixture file found at {fixture_path}")

    async def fake_llm_batch(agent, items, schema, config, **kwargs):
        out = []
        for item_id, _ in items:
            try:
                result = fake_llm_call(agent, "", schema, config)
                out.append((item_id, result, None))
            except Exception as e:
                out.append((item_id, None, str(e)))
        return out

    def fake_validate_schema(data, schema_name):
        pass  # no-op

    monkeypatch.setattr(wiki_llm, "llm_call", fake_llm_call)
    monkeypatch.setattr(wiki_llm, "llm_batch", fake_llm_batch)
    monkeypatch.setattr(wiki_llm, "validate_schema", fake_validate_schema)

    return responses


@pytest.fixture
def mock_run_helpers(monkeypatch) -> dict:
    """Monkeypatch all _run_* helpers in wiki_cli with MagicMocks.

    Each mock returns a successful CompletedProcess(returncode=0).
    Returns a dict of mocks keyed by name so tests can inspect calls
    or override return values:

        def test_something(mock_run_helpers):
            mock_run_helpers["_run_index"].return_value = _make_completed_process(
                stdout='{"added": "foo"}', returncode=0
            )
    """
    import wiki_cli  # importable via sys.path.insert above

    _ok = _make_completed_process(stdout="", stderr="", returncode=0)

    names = [
        "_run_index",
        "_run_render",
        "_run_config",
        "_run_arxiv",
        "_run_zotero",
        "_run_embed",
        "_run_self",
    ]

    mocks: dict[str, MagicMock] = {}
    for name in names:
        m = MagicMock(return_value=_ok)
        monkeypatch.setattr(wiki_cli, name, m)
        mocks[name] = m

    return mocks
