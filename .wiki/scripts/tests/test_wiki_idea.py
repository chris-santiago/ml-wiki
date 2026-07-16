import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from wiki_idea import generate_idea_page, merge_tags


def test_generate_idea_page():
    page = generate_idea_page("idea-test", "Test Idea", ["ml"], None, "This is my idea content.")
    assert "id: idea-test" in page
    assert "# Test Idea" in page
    assert "This is my idea content." in page
    assert "## Notes" in page
    assert "<!-- end-notes -->" in page
    assert "## Connections" in page


def test_generate_idea_page_empty_content():
    page = generate_idea_page("idea-test", "Test Idea", [], None, "")
    assert "id: idea-test" in page
    assert "[User fills in content here]" in page


def test_merge_tags_adds_new():
    result = merge_tags(["ml", "fraud"], ["ml", "transformers"])
    assert "ml" in result
    assert "fraud" in result
    assert "transformers" in result


def test_merge_tags_never_removes():
    result = merge_tags(["ml", "fraud"], ["transformers"])
    assert "ml" in result
    assert "fraud" in result
    assert "transformers" in result
