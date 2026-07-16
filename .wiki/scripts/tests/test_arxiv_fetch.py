"""Tests for arxiv_fetch.py — normalize subcommand only.

The fetch and download-pdf subcommands require network access and are not
covered here. normalize is pure string parsing with no I/O.
"""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = str(Path(__file__).resolve().parent.parent / "arxiv_fetch.py")


def _run_arxiv(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# normalize — happy-path tests
# ---------------------------------------------------------------------------


def test_normalize_abs_url():
    """https://arxiv.org/abs/<id> → canonical bare ID."""
    result = _run_arxiv("normalize", "https://arxiv.org/abs/2301.12345")
    assert result.returncode == 0
    assert result.stdout.strip() == "2301.12345"


def test_normalize_pdf_url():
    """https://arxiv.org/pdf/<id> → canonical bare ID."""
    result = _run_arxiv("normalize", "https://arxiv.org/pdf/2301.12345")
    assert result.returncode == 0
    assert result.stdout.strip() == "2301.12345"


def test_normalize_bare_id():
    """A bare arXiv ID passes through unchanged."""
    result = _run_arxiv("normalize", "2301.12345")
    assert result.returncode == 0
    assert result.stdout.strip() == "2301.12345"


def test_normalize_versioned():
    """Version suffix (vN) is stripped from the ID."""
    result = _run_arxiv("normalize", "2301.12345v2")
    assert result.returncode == 0
    assert result.stdout.strip() == "2301.12345"


def test_normalize_arxiv_prefix():
    """arXiv: or arxiv: colon prefix is stripped."""
    result = _run_arxiv("normalize", "arXiv:2301.12345")
    assert result.returncode == 0
    assert result.stdout.strip() == "2301.12345"


# ---------------------------------------------------------------------------
# normalize — rejection test
# ---------------------------------------------------------------------------


def test_normalize_rejects_non_arxiv():
    """A non-arXiv URL must exit non-zero and emit an error on stderr."""
    result = _run_arxiv("normalize", "https://example.com/paper")
    assert result.returncode != 0
    assert result.stderr.strip() != ""
