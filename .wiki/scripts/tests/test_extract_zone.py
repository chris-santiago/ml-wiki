import os
import subprocess
import sys
import tempfile

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..")
RENDER_SCRIPT = os.path.join(SCRIPTS_DIR, "wiki_render.py")


def _run_extract_zone(wiki_path):
    result = subprocess.run(
        [sys.executable, RENDER_SCRIPT, "extract-zone", wiki_path],
        capture_output=True, text=True,
    )
    return result


def test_extracts_zone_between_frontmatter_and_notes():
    content = """---
id: idea-test
type: idea
tags: [ml]
---

# My Idea

This is the idea content.

It has multiple paragraphs.

## Notes

Some user notes here.

<!-- end-notes -->

## Connections
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(content)
        path = f.name
    try:
        result = _run_extract_zone(path)
        assert result.returncode == 0
        output = result.stdout.strip()
        assert "# My Idea" in output
        assert "multiple paragraphs" in output
        assert "## Notes" not in output
        assert "user notes" not in output
        assert "## Connections" not in output
    finally:
        os.unlink(path)


def test_returns_empty_for_missing_file():
    result = _run_extract_zone("/tmp/nonexistent_wiki_page.md")
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_returns_all_body_if_no_notes_section():
    content = """---
id: test
type: paper
---

# Title

Body content here.
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(content)
        path = f.name
    try:
        result = _run_extract_zone(path)
        assert result.returncode == 0
        output = result.stdout.strip()
        assert "# Title" in output
        assert "Body content" in output
    finally:
        os.unlink(path)


def test_handles_no_frontmatter():
    content = """# Just a heading

Some content.

## Notes

Notes here.

<!-- end-notes -->
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(content)
        path = f.name
    try:
        result = _run_extract_zone(path)
        assert result.returncode == 0
        output = result.stdout.strip()
        assert "# Just a heading" in output
        assert "Some content" in output
        assert "## Notes" not in output
    finally:
        os.unlink(path)
