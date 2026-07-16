import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from wiki_validate import validate_render_output, ValidationWarning


def _make_valid_paper(overrides=None):
    base = {
        "summary": "A paper about X.",
        "key_claims": "- Claim 1\n- Claim 2\n- Claim 3",
        "methods": "They used Y.",
        "results": "- Result 1\n- Result 2\n- Result 3",
        "limitations": "- Limitation 1\n- Limitation 2",
        "tags": ["machine-learning", "transformers", "attention-mechanisms"],
        "fragments": [
            {"seq": 1, "type": "claim", "title": "X outperforms Y"},
            {"seq": 2, "type": "method", "title": "They use Z"},
        ],
    }
    if overrides:
        base.update(overrides)
    return base


def test_valid_paper_shallow():
    data = _make_valid_paper()
    warnings = validate_render_output(data, depth="shallow")
    assert warnings == []


def test_too_many_tags_truncated():
    data = _make_valid_paper({"tags": [f"tag-{i}" for i in range(15)]})
    warnings = validate_render_output(data, depth="shallow")
    assert len(data["tags"]) == 6
    assert any("truncated" in w.message.lower() for w in warnings)


def test_too_few_tags_warns():
    data = _make_valid_paper({"tags": ["ml"]})
    warnings = validate_render_output(data, depth="shallow")
    assert any("minimum" in w.message.lower() or "tag" in w.message.lower() for w in warnings)


def test_duplicate_tags_removed():
    data = _make_valid_paper({"tags": ["ml", "ml", "dl", "dl", "cnn"]})
    warnings = validate_render_output(data, depth="shallow")
    assert data["tags"] == ["ml", "dl", "cnn"]


def test_uppercase_tags_lowered():
    data = _make_valid_paper({"tags": ["Machine-Learning", "BERT", "cnn"]})
    warnings = validate_render_output(data, depth="shallow")
    assert all(t == t.lower() for t in data["tags"])


def test_too_many_fragments_shallow_truncated():
    frags = [{"seq": i, "type": "claim", "title": f"Claim {i}"} for i in range(1, 10)]
    data = _make_valid_paper({"fragments": frags})
    warnings = validate_render_output(data, depth="shallow")
    assert len(data["fragments"]) == 4
    assert any("truncated" in w.message.lower() for w in warnings)


def test_too_many_fragments_deep_truncated():
    frags = [{"seq": i, "type": "claim", "title": f"Claim {i}"} for i in range(1, 20)]
    data = _make_valid_paper({"fragments": frags})
    warnings = validate_render_output(data, depth="deep")
    assert len(data["fragments"]) == 12


def test_fragment_seqs_renumbered():
    data = _make_valid_paper({
        "fragments": [
            {"seq": 5, "type": "claim", "title": "A"},
            {"seq": 10, "type": "method", "title": "B"},
        ]
    })
    validate_render_output(data, depth="shallow")
    assert data["fragments"][0]["seq"] == 1
    assert data["fragments"][1]["seq"] == 2
