"""Tests for wiki_embed.py — semantic scoring for connections and idea evidence.

Pure functions (load_index, build_text) are tested directly.
Subcommands (score-idea, score-connections) require sentence-transformers and
numpy; those tests monkeypatch the imports so no GPU/model download is needed.

Tests that would require a real SentenceTransformer model download are marked
with a comment: "# would need real model" for future expansion.
"""
import io
import json
import os
import sys
import types
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wiki_embed


# ---------------------------------------------------------------------------
# load_index
# ---------------------------------------------------------------------------


def test_load_index_missing_file(tmp_path):
    result = wiki_embed.load_index(str(tmp_path / "nonexistent.jsonl"))
    assert result == {}


def test_load_index_empty_file(tmp_path):
    index = tmp_path / "index.jsonl"
    index.write_text("")
    assert wiki_embed.load_index(str(index)) == {}


def test_load_index_single_entry(tmp_path):
    index = tmp_path / "index.jsonl"
    entry = {"id": "paper-alpha", "title": "Alpha Paper", "tags": ["ml"]}
    index.write_text(json.dumps(entry) + "\n")
    result = wiki_embed.load_index(str(index))
    assert "paper-alpha" in result
    assert result["paper-alpha"]["title"] == "Alpha Paper"


def test_load_index_multiple_entries(tmp_path):
    index = tmp_path / "index.jsonl"
    entries = [
        {"id": "paper-a", "title": "Paper A", "tags": []},
        {"id": "paper-b", "title": "Paper B", "tags": ["nlp"]},
        {"id": "frag-1", "title": "Fragment 1", "tags": []},
    ]
    index.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    result = wiki_embed.load_index(str(index))
    assert len(result) == 3
    assert set(result.keys()) == {"paper-a", "paper-b", "frag-1"}


def test_load_index_skips_blank_lines(tmp_path):
    index = tmp_path / "index.jsonl"
    entry = {"id": "paper-x", "title": "X", "tags": []}
    index.write_text("\n" + json.dumps(entry) + "\n\n")
    result = wiki_embed.load_index(str(index))
    assert "paper-x" in result


def test_load_index_skips_invalid_json(tmp_path):
    index = tmp_path / "index.jsonl"
    good = json.dumps({"id": "good-entry", "title": "Good", "tags": []})
    index.write_text("not-valid-json\n" + good + "\n{\"no-id\": true}\n")
    result = wiki_embed.load_index(str(index))
    # Only the valid entry with an "id" field survives
    assert "good-entry" in result
    assert len(result) == 1


def test_load_index_skips_entry_missing_id(tmp_path):
    index = tmp_path / "index.jsonl"
    index.write_text(json.dumps({"title": "No ID here"}) + "\n")
    result = wiki_embed.load_index(str(index))
    assert result == {}


# ---------------------------------------------------------------------------
# build_text
# ---------------------------------------------------------------------------


def test_build_text_title_only():
    entry = {"id": "e1", "title": "My Paper", "tags": []}
    text = wiki_embed.build_text(entry)
    assert "My Paper" in text


def test_build_text_title_and_tags():
    entry = {"id": "e1", "title": "My Paper", "tags": ["nlp", "transformers"]}
    text = wiki_embed.build_text(entry)
    assert "My Paper" in text
    assert "nlp" in text
    assert "transformers" in text


def test_build_text_no_title_falls_back_to_id():
    entry = {"id": "fallback-id", "title": None, "tags": []}
    text = wiki_embed.build_text(entry)
    assert "fallback-id" in text


def test_build_text_empty_entry_returns_id():
    entry = {"id": "bare-id"}
    text = wiki_embed.build_text(entry)
    assert "bare-id" in text


def test_build_text_includes_wiki_page_excerpt(tmp_path):
    page = tmp_path / "page.md"
    page.write_text(
        "# My Paper\n\n"
        "<!-- comment -->\n"
        "This is the abstract content of the paper.\n"
        "[[Related Paper]]\n"
        "**References**\n"
    )
    entry = {"id": "e1", "title": "My Paper", "tags": [], "wiki_path": str(page)}
    text = wiki_embed.build_text(entry)
    assert "abstract content" in text


def test_build_text_wiki_page_stops_at_references(tmp_path):
    page = tmp_path / "page.md"
    page.write_text(
        "# Title\n\nGood content here.\n**References** and stuff after.\n"
    )
    entry = {"id": "e1", "title": "Title", "tags": [], "wiki_path": str(page)}
    text = wiki_embed.build_text(entry)
    assert "Good content here." in text
    assert "stuff after" not in text


def test_build_text_wiki_page_stops_at_wikilink(tmp_path):
    page = tmp_path / "page.md"
    page.write_text("# Title\n\nGood text.\n[[SomeLink]] ignored after this.\n")
    entry = {"id": "e1", "title": "Title", "tags": [], "wiki_path": str(page)}
    text = wiki_embed.build_text(entry)
    assert "Good text." in text
    assert "ignored after this" not in text


def test_build_text_skips_comments_and_headings(tmp_path):
    page = tmp_path / "page.md"
    page.write_text(
        "# Heading skipped\n"
        "<!-- comment skipped -->\n"
        "Real content here.\n"
    )
    entry = {"id": "e1", "title": "T", "tags": [], "wiki_path": str(page)}
    text = wiki_embed.build_text(entry)
    assert "Heading skipped" not in text
    assert "comment skipped" not in text
    assert "Real content here." in text


def test_build_text_missing_wiki_path_is_fine():
    entry = {"id": "e1", "title": "T", "tags": [], "wiki_path": "/nonexistent/path.md"}
    # Should not raise; OSError is silently caught
    text = wiki_embed.build_text(entry)
    assert "T" in text


def test_build_text_respects_excerpt_length(tmp_path):
    page = tmp_path / "page.md"
    # Write a page that is far longer than TEXT_EXCERPT_CHARS
    page.write_text("word " * 2000 + "\n")
    entry = {"id": "e1", "title": "T", "tags": [], "wiki_path": str(page)}
    text = wiki_embed.build_text(entry)
    # Result should be bounded; "word " * 2000 is 10000 chars; excerpt cap is 600
    # Plus title, tags — should be well under 1500 chars total
    assert len(text) < 2000


# ---------------------------------------------------------------------------
# cmd_score_idea — monkeypatched sentence_transformers
# ---------------------------------------------------------------------------


def _make_fake_st_module(embeddings_matrix):
    """Return a fake sentence_transformers module with a controllable model."""
    fake_model = MagicMock()
    fake_model.encode.return_value = embeddings_matrix

    fake_st = types.ModuleType("sentence_transformers")
    fake_st.SentenceTransformer = MagicMock(return_value=fake_model)
    return fake_st, fake_model


def _make_index_jsonl(tmp_path, entries):
    index = tmp_path / "index.jsonl"
    index.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return str(index)


def test_score_idea_empty_stdin_returns_empty_list(tmp_path, monkeypatch, capsys):
    index_path = _make_index_jsonl(tmp_path, [])
    args = Namespace(index=index_path, exclude_id=None, top_n=30)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    wiki_embed.cmd_score_idea(args)
    out = capsys.readouterr().out
    assert json.loads(out) == []


def test_score_idea_no_fragments_returns_empty_list(tmp_path, monkeypatch, capsys):
    entries = [{"id": "paper-a", "title": "Paper A", "tags": [], "type": "paper"}]
    index_path = _make_index_jsonl(tmp_path, entries)
    args = Namespace(index=index_path, exclude_id=None, top_n=30)
    monkeypatch.setattr(sys, "stdin", io.StringIO("some idea text"))
    wiki_embed.cmd_score_idea(args)
    out = capsys.readouterr().out
    assert json.loads(out) == []


def test_score_idea_ranks_fragments_by_score(tmp_path, monkeypatch, capsys):
    """Verify score-idea groups fragments by parent entry and sorts by max score."""
    entries = [
        {"id": "paper-a", "title": "Paper A", "tags": ["ml"], "type": "paper"},
        {
            "id": "frag-1",
            "title": "Fragment 1",
            "tags": [],
            "type": "fragment",
            "references": ["paper-a"],
        },
        {
            "id": "frag-2",
            "title": "Fragment 2",
            "tags": [],
            "type": "fragment",
            "references": ["paper-a"],
        },
    ]
    index_path = _make_index_jsonl(tmp_path, entries)

    # Embedding matrix: row 0 = query, row 1 = frag-1, row 2 = frag-2
    # After normalize_embeddings=True, dot product IS cosine similarity.
    embeddings = np.array([
        [1.0, 0.0],   # query
        [0.9, 0.1],   # frag-1  — higher similarity to query
        [0.0, 1.0],   # frag-2  — lower similarity
    ], dtype=np.float32)

    fake_st, fake_model = _make_fake_st_module(embeddings)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.setitem(sys.modules, "numpy", np)

    args = Namespace(index=index_path, exclude_id=None, top_n=30)
    monkeypatch.setattr(sys, "stdin", io.StringIO("idea about ml"))

    wiki_embed.cmd_score_idea(args)
    out = capsys.readouterr().out
    results = json.loads(out)

    assert len(results) == 1
    assert results[0]["id"] == "paper-a"
    frag_ids = [f["id"] for f in results[0]["fragments"]]
    assert "frag-1" in frag_ids
    assert "frag-2" in frag_ids
    # frag-1 should appear before frag-2 (higher score)
    assert frag_ids.index("frag-1") < frag_ids.index("frag-2")


def test_score_idea_exclude_id_filters_fragments(tmp_path, monkeypatch, capsys):
    """Fragments whose references include exclude_id should be dropped."""
    entries = [
        {"id": "paper-a", "title": "Paper A", "tags": [], "type": "paper"},
        {"id": "paper-b", "title": "Paper B", "tags": [], "type": "paper"},
        {
            "id": "frag-1",
            "title": "Fragment 1",
            "tags": [],
            "type": "fragment",
            "references": ["paper-a"],  # excluded
        },
        {
            "id": "frag-2",
            "title": "Fragment 2",
            "tags": [],
            "type": "fragment",
            "references": ["paper-b"],  # kept
        },
    ]
    index_path = _make_index_jsonl(tmp_path, entries)

    embeddings = np.array([
        [1.0, 0.0],  # query
        [0.9, 0.1],  # only frag-2 survives (frag-1 filtered before encoding)
    ], dtype=np.float32)

    fake_st, _ = _make_fake_st_module(embeddings)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.setitem(sys.modules, "numpy", np)

    args = Namespace(index=index_path, exclude_id="paper-a", top_n=30)
    monkeypatch.setattr(sys, "stdin", io.StringIO("idea text"))

    wiki_embed.cmd_score_idea(args)
    out = capsys.readouterr().out
    results = json.loads(out)

    entry_ids = [r["id"] for r in results]
    assert "paper-b" in entry_ids
    assert "paper-a" not in entry_ids


def test_score_idea_top_n_limits_fragments(tmp_path, monkeypatch, capsys):
    """top_n caps the number of fragments ranked before grouping."""
    # Create 5 fragments all referencing the same parent
    entries = [
        {"id": "paper-a", "title": "Paper A", "tags": [], "type": "paper"},
    ]
    for i in range(5):
        entries.append({
            "id": f"frag-{i}",
            "title": f"Fragment {i}",
            "tags": [],
            "type": "fragment",
            "references": ["paper-a"],
        })
    index_path = _make_index_jsonl(tmp_path, entries)

    # 6 rows: query + 5 frags
    embeddings = np.zeros((6, 2), dtype=np.float32)
    embeddings[0] = [1.0, 0.0]
    for i in range(5):
        embeddings[i + 1] = [0.9 - i * 0.1, 0.0]

    fake_st, _ = _make_fake_st_module(embeddings)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.setitem(sys.modules, "numpy", np)

    args = Namespace(index=index_path, exclude_id=None, top_n=3)
    monkeypatch.setattr(sys, "stdin", io.StringIO("some idea"))

    wiki_embed.cmd_score_idea(args)
    out = capsys.readouterr().out
    results = json.loads(out)

    # Only 3 fragments should appear (top_n=3)
    total_frags = sum(len(r["fragments"]) for r in results)
    assert total_frags == 3


def test_score_idea_fragment_with_no_wiki_entry_is_skipped(tmp_path, monkeypatch, capsys):
    """Fragments referencing an unknown parent should not appear in results."""
    entries = [
        {
            "id": "frag-orphan",
            "title": "Orphan Fragment",
            "tags": [],
            "type": "fragment",
            "references": ["nonexistent-paper"],
        },
    ]
    index_path = _make_index_jsonl(tmp_path, entries)

    embeddings = np.array([[1.0, 0.0], [0.9, 0.1]], dtype=np.float32)
    fake_st, _ = _make_fake_st_module(embeddings)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.setitem(sys.modules, "numpy", np)

    args = Namespace(index=index_path, exclude_id=None, top_n=30)
    monkeypatch.setattr(sys, "stdin", io.StringIO("some idea"))

    wiki_embed.cmd_score_idea(args)
    out = capsys.readouterr().out
    assert json.loads(out) == []


# ---------------------------------------------------------------------------
# cmd_score_connections — monkeypatched sentence_transformers
# ---------------------------------------------------------------------------


def test_score_connections_empty_stdin(tmp_path, monkeypatch, capsys):
    args = Namespace(index=str(tmp_path / "index.jsonl"), top_n=8)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    wiki_embed.cmd_score_connections(args)
    out = capsys.readouterr().out
    assert json.loads(out) == {}


def test_score_connections_invalid_json_exits(tmp_path, monkeypatch):
    args = Namespace(index=str(tmp_path / "index.jsonl"), top_n=8)
    monkeypatch.setattr(sys, "stdin", io.StringIO("not-json"))
    with pytest.raises(SystemExit) as exc_info:
        wiki_embed.cmd_score_connections(args)
    assert exc_info.value.code == 1


def test_score_connections_no_candidates(tmp_path, monkeypatch, capsys):
    """Entries with empty related_candidates produce empty related lists."""
    index_path = _make_index_jsonl(tmp_path, [
        {"id": "paper-a", "title": "Paper A", "tags": [], "type": "paper"},
    ])
    connection_map = {
        "paper-a": {
            "references": ["paper-b"],
            "cited_by": [],
            "related_candidates": [],
        }
    }
    fake_st, _ = _make_fake_st_module(np.array([[1.0, 0.0]], dtype=np.float32))
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.setitem(sys.modules, "numpy", np)

    args = Namespace(index=str(index_path), top_n=8)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(connection_map)))

    wiki_embed.cmd_score_connections(args)
    out = capsys.readouterr().out
    result = json.loads(out)

    assert "paper-a" in result
    assert result["paper-a"]["related"] == []
    assert result["paper-a"]["references"] == ["paper-b"]


def test_score_connections_ranks_candidates(tmp_path, monkeypatch, capsys):
    """Verify connections are re-ranked by cosine similarity."""
    entries = [
        {"id": "paper-a", "title": "Paper A", "tags": ["ml"], "type": "paper"},
        {"id": "paper-b", "title": "Paper B", "tags": ["nlp"], "type": "paper"},
        {"id": "paper-c", "title": "Paper C", "tags": ["cv"], "type": "paper"},
    ]
    index_path = _make_index_jsonl(tmp_path, entries)

    connection_map = {
        "paper-a": {
            "references": [],
            "cited_by": [],
            "related_candidates": [
                {"id": "paper-b"},
                {"id": "paper-c"},
            ],
        }
    }

    # IDs will be sorted: paper-a, paper-b, paper-c → indices 0, 1, 2
    # paper-a is [1, 0]; paper-b is [0.9, 0.1] (more similar); paper-c is [0, 1] (less similar)
    embeddings = np.array([
        [1.0, 0.0],   # paper-a (query)
        [0.9, 0.1],   # paper-b
        [0.0, 1.0],   # paper-c
    ], dtype=np.float32)

    fake_st, _ = _make_fake_st_module(embeddings)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.setitem(sys.modules, "numpy", np)

    args = Namespace(index=str(index_path), top_n=8)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(connection_map)))

    wiki_embed.cmd_score_connections(args)
    out = capsys.readouterr().out
    result = json.loads(out)

    related = result["paper-a"]["related"]
    related_ids = [r["id"] for r in related]
    assert related_ids[0] == "paper-b"
    assert related_ids[1] == "paper-c"


def test_score_connections_top_n_limits_related(tmp_path, monkeypatch, capsys):
    """top_n caps the number of related entries per source."""
    candidates = [{"id": f"paper-{i}"} for i in range(1, 6)]
    entries = [{"id": f"paper-{i}", "title": f"Paper {i}", "tags": [], "type": "paper"}
               for i in range(6)]
    index_path = _make_index_jsonl(tmp_path, entries)

    connection_map = {
        "paper-0": {
            "references": [],
            "cited_by": [],
            "related_candidates": candidates,
        }
    }

    # 6 entries: paper-0 .. paper-5, sorted → indices 0..5
    embeddings = np.zeros((6, 2), dtype=np.float32)
    embeddings[0] = [1.0, 0.0]
    for i in range(1, 6):
        embeddings[i] = [0.9 - i * 0.05, 0.0]

    fake_st, _ = _make_fake_st_module(embeddings)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.setitem(sys.modules, "numpy", np)

    args = Namespace(index=str(index_path), top_n=3)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(connection_map)))

    wiki_embed.cmd_score_connections(args)
    out = capsys.readouterr().out
    result = json.loads(out)

    assert len(result["paper-0"]["related"]) == 3


# ---------------------------------------------------------------------------
# CLI help / argument parsing (subprocess)
# ---------------------------------------------------------------------------


def test_help_output():
    import subprocess
    script = str(Path(__file__).resolve().parent.parent / "wiki_embed.py")
    proc = subprocess.run(
        [sys.executable, script, "--help"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    assert "score-connections" in proc.stdout
    assert "score-idea" in proc.stdout


def test_no_subcommand_exits_nonzero():
    import subprocess
    script = str(Path(__file__).resolve().parent.parent / "wiki_embed.py")
    proc = subprocess.run(
        [sys.executable, script],
        capture_output=True, text=True,
    )
    assert proc.returncode != 0


# ---------------------------------------------------------------------------
# cmd_cluster_tags — monkeypatched sentence_transformers
# ---------------------------------------------------------------------------


def test_cluster_tags_empty_input(monkeypatch, capsys):
    """Empty tag list returns empty clusters and isolated."""
    args = Namespace(index=".wiki/index.jsonl")
    monkeypatch.setattr(sys, "stdin", io.StringIO("[]"))
    wiki_embed.cmd_cluster_tags(args)
    out = capsys.readouterr().out
    result = json.loads(out)
    assert result == {"clusters": [], "isolated": []}


def test_cluster_tags_single_tag(monkeypatch, capsys):
    """Single tag goes straight to isolated without model call."""
    args = Namespace(index=".wiki/index.jsonl")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(["ml"])))
    wiki_embed.cmd_cluster_tags(args)
    out = capsys.readouterr().out
    result = json.loads(out)
    assert result["clusters"] == []
    assert result["isolated"] == ["ml"]


def test_cluster_tags_groups_similar_slugs(monkeypatch, capsys):
    """Two similar tags cluster together; a dissimilar one is isolated."""
    tags = ["regression-discontinuity", "regression-discontinuity-design", "k-means"]

    embeddings = np.array(
        [[1.0, 0.0, 0.0],
         [0.9747, 0.2236, 0.0],
         [0.0, 0.0, 1.0]],
        dtype=np.float32,
    )
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings = embeddings / norms

    fake_st, _ = _make_fake_st_module(embeddings)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.setitem(sys.modules, "numpy", np)

    args = Namespace(index=".wiki/index.jsonl")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(tags)))
    wiki_embed.cmd_cluster_tags(args)
    out = capsys.readouterr().out
    result = json.loads(out)

    assert len(result["clusters"]) == 1
    assert set(result["clusters"][0]["tags"]) == {
        "regression-discontinuity",
        "regression-discontinuity-design",
    }
    assert result["isolated"] == ["k-means"]


def test_cluster_tags_auto_tier_above_090(monkeypatch, capsys):
    """Cluster with min pairwise sim >= 0.90 gets tier='auto' (AUTO_THRESHOLD=0.90)."""
    tags = ["tag-a", "tag-b"]

    embeddings = np.array(
        [[1.0, 0.0],
         [0.98, 0.2]],
        dtype=np.float32,
    )
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings = embeddings / norms

    fake_st, _ = _make_fake_st_module(embeddings)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.setitem(sys.modules, "numpy", np)

    args = Namespace(index=".wiki/index.jsonl")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(tags)))
    wiki_embed.cmd_cluster_tags(args)
    out = capsys.readouterr().out
    result = json.loads(out)

    assert result["clusters"][0]["tier"] == "auto"


def test_cluster_tags_llm_tier_between_075_and_090(monkeypatch, capsys):
    """Cluster with min pairwise sim in [0.75, 0.90) gets tier='llm'."""
    tags = ["tag-a", "tag-b"]

    embeddings = np.array(
        [[1.0, 0.0],
         [0.8, 0.6]],
        dtype=np.float32,
    )
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings = embeddings / norms

    fake_st, _ = _make_fake_st_module(embeddings)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.setitem(sys.modules, "numpy", np)

    args = Namespace(index=".wiki/index.jsonl")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(tags)))
    wiki_embed.cmd_cluster_tags(args)
    out = capsys.readouterr().out
    result = json.loads(out)

    assert result["clusters"][0]["tier"] == "llm"


def test_cluster_tags_complete_linkage_blocks_chaining(monkeypatch, capsys):
    """A->B and B->C above threshold but A->C below: complete-linkage keeps A and C separate."""
    tags = ["tag-a", "tag-b", "tag-c"]

    # sim(A,B)~=0.82, sim(B,C)~=0.95, sim(A,C)~=0.60
    embeddings = np.array(
        [[1.0, 0.0],
         [0.82, 0.57],
         [0.60, 0.80]],
        dtype=np.float32,
    )
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings = embeddings / norms

    fake_st, _ = _make_fake_st_module(embeddings)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.setitem(sys.modules, "numpy", np)

    args = Namespace(index=".wiki/index.jsonl")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(tags)))
    wiki_embed.cmd_cluster_tags(args)
    out = capsys.readouterr().out
    result = json.loads(out)

    cluster_tag_sets = [set(c["tags"]) for c in result["clusters"]]
    assert {"tag-a", "tag-b"} in cluster_tag_sets
    assert "tag-c" in result["isolated"]


def test_cluster_tags_dashes_converted_to_spaces(monkeypatch):
    """Slug dashes are replaced with spaces before encoding."""
    tags = ["my-tag", "other-tag"]
    embeddings = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    fake_st, fake_model = _make_fake_st_module(embeddings)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.setitem(sys.modules, "numpy", np)

    args = Namespace(index=".wiki/index.jsonl")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(tags)))
    wiki_embed.cmd_cluster_tags(args)

    encode_call_args = fake_model.encode.call_args[0][0]
    assert "my tag" in encode_call_args
    assert "my-tag" not in encode_call_args
    assert "other tag" in encode_call_args
    assert "other-tag" not in encode_call_args


def test_cluster_tags_threshold_arg(monkeypatch, capsys):
    """--threshold overrides CLUSTER_THRESHOLD for merge decisions."""
    tags = ["tag-a", "tag-b"]

    # sim ~= 0.8 — above default 0.75 but below 0.85
    embeddings = np.array([[1.0, 0.0], [0.8, 0.6]], dtype=np.float32)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings = embeddings / norms

    fake_st, _ = _make_fake_st_module(embeddings)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.setitem(sys.modules, "numpy", np)

    args = Namespace(index=".wiki/index.jsonl", threshold=0.85, auto_threshold=0.90)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(tags)))
    wiki_embed.cmd_cluster_tags(args)
    out = capsys.readouterr().out
    result = json.loads(out)

    # With threshold=0.85, sim ~0.8 should NOT cluster
    assert result["clusters"] == []
    assert set(result["isolated"]) == {"tag-a", "tag-b"}


def test_cluster_tags_auto_threshold_arg(monkeypatch, capsys):
    """--auto-threshold overrides AUTO_THRESHOLD for tier assignment."""
    tags = ["tag-a", "tag-b"]

    # sim ~= 0.92 — above 0.90 (default auto) but below 0.95
    embeddings = np.array([[1.0, 0.0], [0.92, 0.39]], dtype=np.float32)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings = embeddings / norms

    fake_st, _ = _make_fake_st_module(embeddings)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.setitem(sys.modules, "numpy", np)

    # With auto_threshold=0.95, sim ~0.92 should be tier=llm
    args = Namespace(index=".wiki/index.jsonl", threshold=0.75, auto_threshold=0.95)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(tags)))
    wiki_embed.cmd_cluster_tags(args)
    out = capsys.readouterr().out
    result = json.loads(out)
    assert result["clusters"][0]["tier"] == "llm"


# ---------------------------------------------------------------------------
# cmd_match_canonicals — monkeypatched sentence_transformers
# ---------------------------------------------------------------------------


def test_match_canonicals_empty_input(monkeypatch, capsys):
    """Empty stdin returns empty matches and unmatched."""
    args = Namespace(index=".wiki/index.jsonl", threshold=0.70, top_n=3)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    wiki_embed.cmd_match_canonicals(args)
    out = capsys.readouterr().out
    assert json.loads(out) == {"matches": [], "unmatched": []}


def test_match_canonicals_no_canonicals(monkeypatch, capsys):
    """No canonicals → all unresolved go to unmatched as plain strings."""
    data = {"unresolved": ["tag-a", "tag-b"], "canonicals": []}
    args = Namespace(index=".wiki/index.jsonl", threshold=0.70, top_n=3)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(data)))
    wiki_embed.cmd_match_canonicals(args)
    out = capsys.readouterr().out
    result = json.loads(out)
    assert result["matches"] == []
    assert set(result["unmatched"]) == {"tag-a", "tag-b"}


def test_match_canonicals_no_unresolved(monkeypatch, capsys):
    """No unresolved tags → empty result."""
    data = {"unresolved": [], "canonicals": ["canon-a"]}
    args = Namespace(index=".wiki/index.jsonl", threshold=0.70, top_n=3)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(data)))
    wiki_embed.cmd_match_canonicals(args)
    out = capsys.readouterr().out
    result = json.loads(out)
    assert result == {"matches": [], "unmatched": []}


def test_match_canonicals_above_threshold(monkeypatch, capsys):
    """Tag above threshold matches; tag below goes to unmatched."""
    data = {"unresolved": ["close-tag", "far-tag"], "canonicals": ["canon-a"]}

    # close-tag similar to canon-a; far-tag dissimilar
    embeddings = np.array(
        [[0.95, 0.31],   # close-tag
         [0.0, 1.0],     # far-tag
         [1.0, 0.0]],    # canon-a
        dtype=np.float32,
    )
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings = embeddings / norms

    fake_st, _ = _make_fake_st_module(embeddings)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.setitem(sys.modules, "numpy", np)

    args = Namespace(index=".wiki/index.jsonl", threshold=0.70, top_n=3)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(data)))
    wiki_embed.cmd_match_canonicals(args)
    out = capsys.readouterr().out
    result = json.loads(out)

    assert len(result["matches"]) == 1
    assert result["matches"][0]["tag"] == "close-tag"
    assert result["matches"][0]["canonical"] == "canon-a"
    assert len(result["unmatched"]) == 1
    assert result["unmatched"][0]["tag"] == "far-tag"
    assert len(result["unmatched"][0]["nearest"]) == 1  # only 1 canonical available


def test_match_canonicals_threshold_arg(monkeypatch, capsys):
    """Higher --threshold rejects a match that default would accept."""
    data = {"unresolved": ["tag-a"], "canonicals": ["canon-a"]}

    # sim ~= 0.72 — above default 0.70 but below 0.80
    embeddings = np.array(
        [[0.72, 0.69],  # tag-a
         [1.0, 0.0]],   # canon-a
        dtype=np.float32,
    )
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings = embeddings / norms

    fake_st, _ = _make_fake_st_module(embeddings)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.setitem(sys.modules, "numpy", np)

    args = Namespace(index=".wiki/index.jsonl", threshold=0.80, top_n=3)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(data)))
    wiki_embed.cmd_match_canonicals(args)
    out = capsys.readouterr().out
    result = json.loads(out)

    assert result["matches"] == []
    assert len(result["unmatched"]) == 1
    assert result["unmatched"][0]["tag"] == "tag-a"


def test_match_canonicals_picks_best_canonical(monkeypatch, capsys):
    """When a tag is similar to two canonicals, argmax picks the highest."""
    data = {"unresolved": ["tag-a"], "canonicals": ["canon-weak", "canon-strong"]}

    # tag-a more similar to canon-strong
    embeddings = np.array(
        [[0.9, 0.44],   # tag-a
         [0.5, 0.87],   # canon-weak
         [1.0, 0.0]],   # canon-strong
        dtype=np.float32,
    )
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings = embeddings / norms

    fake_st, _ = _make_fake_st_module(embeddings)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.setitem(sys.modules, "numpy", np)

    args = Namespace(index=".wiki/index.jsonl", threshold=0.70, top_n=3)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(data)))
    wiki_embed.cmd_match_canonicals(args)
    out = capsys.readouterr().out
    result = json.loads(out)

    assert len(result["matches"]) == 1
    assert result["matches"][0]["canonical"] == "canon-strong"


def test_match_canonicals_dashes_to_spaces(monkeypatch):
    """Dashes are replaced with spaces before encoding for both unresolved and canonicals."""
    data = {"unresolved": ["my-tag"], "canonicals": ["my-canon"]}

    embeddings = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    fake_st, fake_model = _make_fake_st_module(embeddings)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.setitem(sys.modules, "numpy", np)

    args = Namespace(index=".wiki/index.jsonl", threshold=0.70, top_n=3)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(data)))
    wiki_embed.cmd_match_canonicals(args)

    encode_call_args = fake_model.encode.call_args[0][0]
    assert "my tag" in encode_call_args
    assert "my-tag" not in encode_call_args
    assert "my canon" in encode_call_args
    assert "my-canon" not in encode_call_args
