import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from wiki_render import _assemble_page_from_json


def test_assemble_paper_from_json(tmp_path):
    entry = {
        "id": "test-paper-2024",
        "type": "paper",
        "title": "Test Paper",
        "citation": "Author (2024). Test Paper.",
        "tags": ["ml"],
        "project": None,
    }
    data = {
        "summary": "This paper does X.",
        "key_claims": "- Claim 1\n- Claim 2",
        "methods": "They used Y.",
        "results": "- Result 1",
        "limitations": "- Limitation 1",
        "tags": ["machine-learning", "transformers"],
        "fragments": [
            {"seq": 1, "type": "claim", "title": "X outperforms Y"},
        ],
    }
    result = _assemble_page_from_json(entry, "paper", data, "", str(tmp_path))
    assert result["wiki_path"].endswith("test-paper-2024.md")
    assert result["tags_finalized"] == ["machine-learning", "transformers"]
    assert len(result["fragments"]) == 1
    assert result["fragments"][0]["id"] == "frag-test-paper-2024-01"
    assert os.path.exists(result["wiki_path"])
    page = open(result["wiki_path"]).read()
    assert "This paper does X." in page
    assert "## Summary" in page


def test_assemble_experiment_from_json(tmp_path):
    entry = {
        "id": "exp-test-2024",
        "type": "experiment",
        "title": "Test Experiment",
        "tags": ["ml"],
        "project": None,
    }
    data = {
        "hypothesis": "Testing X improves Y.",
        "setup": "- Dataset: A\n- Model: B",
        "results": "- Y improved by 5%",
        "implications": "X is worth pursuing.",
        "tags": ["ml", "experiment"],
        "fragments": [
            {"seq": 1, "type": "finding", "title": "Y improved 5%"},
        ],
    }
    result = _assemble_page_from_json(entry, "experiment", data, "", str(tmp_path))
    assert result["wiki_path"].endswith("exp-test-2024.md")
    page = open(result["wiki_path"]).read()
    assert "## Hypothesis" in page
    assert "Testing X improves Y." in page


def test_assemble_synthesis_from_json(tmp_path):
    entry = {
        "id": "synthesis-test-2024",
        "type": "synthesis",
        "title": "Test Synthesis",
        "tags": [],
        "project": None,
    }
    data = {
        "synthesis": "Multiple sources agree that X. See [[paper-a]] and [[paper-b]].",
        "sources": ["paper-a", "paper-b"],
        "tags": ["ml"],
        "fragments": [],
    }
    result = _assemble_page_from_json(entry, "synthesis", data, "", str(tmp_path))
    page = open(result["wiki_path"]).read()
    assert "## Synthesis" in page
    assert "[[paper-a]]" in page
    assert "- [[paper-a]]" in page  # sources section as wikilinks
