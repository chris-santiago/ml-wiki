import io
import json
import os
import subprocess
import sys

import pytest
from argparse import Namespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from wiki_index import (
    _split_by_payload,
    cmd_get_fragments_by_type,
    cmd_get_entries_batch,
    cmd_get_orphan_ids,
    load_aliases,
)


# ---------------------------------------------------------------------------
# _split_by_payload tests
# ---------------------------------------------------------------------------

def test_no_split_under_limit():
    groups = [{"moc_id": f"topic-{i}", "entries": []} for i in range(3)]
    result = _split_by_payload([groups], 1024 * 1024)
    assert result == [groups]


def test_splits_oversized_batch():
    big = {"moc_id": "topic-big", "data": "x" * 40000}
    result = _split_by_payload([[big, big]], 48 * 1024)
    assert len(result) == 2
    assert result[0] == [big]
    assert result[1] == [big]


def test_single_oversized_group_not_further_split():
    big = {"moc_id": "topic-big", "data": "x" * 60000}
    result = _split_by_payload([[big]], 48 * 1024)
    assert len(result) == 1
    assert result[0] == [big]


def test_preserves_batch_under_limit():
    small = [{"moc_id": f"topic-{i}"} for i in range(5)]
    result = _split_by_payload([small], 48 * 1024)
    assert len(result) == 1
    assert result[0] == small


def test_splits_first_batch_leaves_second_intact():
    big = {"moc_id": "topic-big", "data": "x" * 30000}
    result = _split_by_payload([[big, big], [big]], 48 * 1024)
    assert len(result) == 3
    assert result[0] == [big]
    assert result[1] == [big]
    assert result[2] == [big]


def test_empty_batches_list():
    assert _split_by_payload([], 48 * 1024) == []


def test_empty_batch():
    assert _split_by_payload([[]], 48 * 1024) == []


# ---------------------------------------------------------------------------
# cmd_get_fragments_by_type / cmd_get_entries_batch / cmd_get_orphan_ids tests
# ---------------------------------------------------------------------------

SAMPLE_INDEX = [
    {"id": "smith2023", "title": "Smith 2023", "type": "paper", "tags": ["nlp", "attention"], "status": "rendered"},
    {"id": "chen2024", "title": "Chen 2024", "type": "paper", "tags": ["nlp"], "status": "rendered"},
    {
        "id": "frag-smith2023-01", "type": "claim", "title": "Attention beats RNNs.",
        "tags": ["nlp", "attention"], "references": ["smith2023"], "project": None, "ingested": "2024-01-01T00:00:00Z"
    },
    {
        "id": "frag-smith2023-02", "type": "method", "title": "Multi-head attention.",
        "tags": ["attention"], "references": ["smith2023"], "project": None, "ingested": "2024-01-01T00:00:00Z"
    },
    {
        "id": "frag-chen2024-01", "type": "finding", "title": "GRU outperforms attention on small datasets.",
        "tags": ["nlp"], "references": ["chen2024"], "project": None, "ingested": "2024-01-01T00:00:00Z"
    },
]


@pytest.fixture
def index_file(tmp_path):
    path = tmp_path / "index.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in SAMPLE_INDEX))
    return str(path)


def test_get_fragments_by_type_flat(index_file, capsys):
    args = Namespace(types="claim,finding", group_by=None, index=index_file)
    cmd_get_fragments_by_type(args)
    out = json.loads(capsys.readouterr().out)
    ids = [f["id"] for f in out]
    assert "frag-smith2023-01" in ids   # claim — included
    assert "frag-chen2024-01" in ids    # finding — included
    assert "frag-smith2023-02" not in ids  # method — excluded


def test_get_fragments_by_type_grouped_by_tag(index_file, capsys):
    args = Namespace(types="claim,finding", group_by="tag", index=index_file)
    cmd_get_fragments_by_type(args)
    out = json.loads(capsys.readouterr().out)
    assert "nlp" in out
    nlp_ids = [f["id"] for f in out["nlp"]]
    assert "frag-smith2023-01" in nlp_ids   # claim with nlp tag
    assert "frag-chen2024-01" in nlp_ids    # finding with nlp tag


def test_get_fragments_by_type_multi_tag_duplication(index_file, capsys):
    # frag-smith2023-01 has tags ["nlp", "attention"] — must appear in both groups
    args = Namespace(types="claim", group_by="tag", index=index_file)
    cmd_get_fragments_by_type(args)
    out = json.loads(capsys.readouterr().out)
    assert "frag-smith2023-01" in [f["id"] for f in out.get("nlp", [])]
    assert "frag-smith2023-01" in [f["id"] for f in out.get("attention", [])]


def test_get_entries_batch(index_file, capsys, monkeypatch):
    ids = ["smith2023", "chen2024"]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(ids)))
    args = Namespace(index=index_file)
    cmd_get_entries_batch(args)
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 2
    result_ids = [e["id"] for e in out]
    assert "smith2023" in result_ids
    assert "chen2024" in result_ids
    for entry in out:
        assert set(entry.keys()) == {"id", "title", "tags"}


def test_get_entries_batch_skips_fragments(index_file, capsys, monkeypatch):
    ids = ["smith2023", "frag-smith2023-01"]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(ids)))
    args = Namespace(index=index_file)
    cmd_get_entries_batch(args)
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 1
    assert out[0]["id"] == "smith2023"


def test_get_entries_batch_skips_unknown_ids(index_file, capsys, monkeypatch):
    ids = ["smith2023", "does-not-exist"]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(ids)))
    args = Namespace(index=index_file)
    cmd_get_entries_batch(args)
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 1
    assert out[0]["id"] == "smith2023"


def test_get_orphan_ids(capsys, monkeypatch):
    report = {"orphan_entries": ["smith2023", "chen2024"], "orphan_files": [], "orphan_fragments": []}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(report)))
    cmd_get_orphan_ids(Namespace())
    out = json.loads(capsys.readouterr().out)
    assert out == ["smith2023", "chen2024"]


def test_get_orphan_ids_empty(capsys, monkeypatch):
    report = {"orphan_entries": [], "orphan_files": [], "orphan_fragments": []}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(report)))
    cmd_get_orphan_ids(Namespace())
    out = json.loads(capsys.readouterr().out)
    assert out == []


def test_get_orphan_ids_missing_field(capsys, monkeypatch):
    report = {"orphan_files": [], "orphan_fragments": []}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(report)))
    cmd_get_orphan_ids(Namespace())
    out = json.loads(capsys.readouterr().out)
    assert out == []


# ---------------------------------------------------------------------------
# get-tag-counts subcommand
# ---------------------------------------------------------------------------

WIKI_INDEX_PATH = os.path.join(os.path.dirname(__file__), "..", "wiki_index.py")


def test_get_tag_counts_empty_index(tmp_path):
    """Empty index returns empty dict."""
    index = tmp_path / "index.jsonl"
    index.write_text("")
    result = subprocess.run(
        [sys.executable, WIKI_INDEX_PATH, "get-tag-counts", "--index", str(index)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {}


def test_get_tag_counts_single_entry(tmp_path):
    """Single entry with tags returns correct counts."""
    index = tmp_path / "index.jsonl"
    entry = {"id": "paper-a", "title": "A", "tags": ["ml", "optimization"]}
    index.write_text(json.dumps(entry) + "\n")
    result = subprocess.run(
        [sys.executable, WIKI_INDEX_PATH, "get-tag-counts", "--index", str(index)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    counts = json.loads(result.stdout)
    assert counts["ml"] == 1
    assert counts["optimization"] == 1


def test_get_tag_counts_multiple_entries(tmp_path):
    """Tags shared across entries are counted correctly."""
    index = tmp_path / "index.jsonl"
    entries = [
        {"id": "paper-a", "title": "A", "tags": ["ml", "optimization"]},
        {"id": "paper-b", "title": "B", "tags": ["ml", "deep-learning"]},
        {"id": "paper-c", "title": "C", "tags": ["optimization"]},
    ]
    index.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    result = subprocess.run(
        [sys.executable, WIKI_INDEX_PATH, "get-tag-counts", "--index", str(index)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    counts = json.loads(result.stdout)
    assert counts["ml"] == 2
    assert counts["optimization"] == 2
    assert counts["deep-learning"] == 1


def test_get_tag_counts_entries_without_tags(tmp_path):
    """Entries with no tags are skipped without error."""
    index = tmp_path / "index.jsonl"
    entries = [
        {"id": "paper-a", "title": "A", "tags": ["ml"]},
        {"id": "paper-b", "title": "B"},  # no tags key
        {"id": "paper-c", "title": "C", "tags": None},  # null tags
    ]
    index.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    result = subprocess.run(
        [sys.executable, WIKI_INDEX_PATH, "get-tag-counts", "--index", str(index)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    counts = json.loads(result.stdout)
    assert counts == {"ml": 1}


# ---------------------------------------------------------------------------
# load_aliases validation tests
# ---------------------------------------------------------------------------

def _write_json(tmp_path, obj):
    p = tmp_path / "aliases.json"
    p.write_text(json.dumps(obj))
    return str(p)


def test_load_aliases_accepts_flat_map(tmp_path):
    path = _write_json(tmp_path, {"dl": "deep-learning", "cnn": "convnet"})
    assert load_aliases(path) == {"dl": "deep-learning", "cnn": "convnet"}


def test_load_aliases_allows_empty_string_removal_value(tmp_path):
    # An empty-string value marks a tag for removal (see apply_aliases) — valid.
    path = _write_json(tmp_path, {"junk-tag": ""})
    assert load_aliases(path) == {"junk-tag": ""}


def test_load_aliases_empty_map_ok(tmp_path):
    path = _write_json(tmp_path, {})
    assert load_aliases(path) == {}


def test_load_aliases_no_path_returns_empty():
    assert load_aliases(None) == {}


def test_load_aliases_rejects_wrapped_map(tmp_path):
    # The exact bug: a {"aliases": {...}} wrapper must be rejected, not applied as a no-op.
    path = _write_json(tmp_path, {"aliases": {"dl": "deep-learning"}})
    with pytest.raises(ValueError):
        load_aliases(path)


def test_load_aliases_rejects_non_dict(tmp_path):
    path = _write_json(tmp_path, ["dl", "deep-learning"])
    with pytest.raises(ValueError):
        load_aliases(path)


def test_load_aliases_rejects_non_string_value(tmp_path):
    path = _write_json(tmp_path, {"dl": 5})
    with pytest.raises(ValueError):
        load_aliases(path)


def test_load_aliases_rejects_empty_key(tmp_path):
    path = _write_json(tmp_path, {"": "deep-learning"})
    with pytest.raises(ValueError):
        load_aliases(path)
