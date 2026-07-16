# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""Shared utilities for the wiki CLI.

Pure functions for slug generation, source-type detection, citation
extraction, and tag normalization. No I/O, no subprocess calls.
"""

import re

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"})


def slugify(text: str, max_len: int = 60, prefix: str = "") -> str:
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text)
    text = text.strip('-')
    if max_len and len(text) > max_len:
        text = text[:max_len].rsplit('-', 1)[0]
    return prefix + text


def detect_source_type(input_str: str) -> str | None:
    lower = input_str.lower()
    if lower.startswith("http://") or lower.startswith("https://"):
        return "url"
    for ext in IMAGE_EXTENSIONS:
        if lower.endswith(ext):
            return "image"
    if lower.endswith(".pdf"):
        return "pdf"
    if lower.endswith(".md"):
        return "markdown"
    if lower.endswith(".txt"):
        return "text"
    return None


def extract_citations(text: str) -> list[str]:
    ids = re.findall(r'\[\[([^\]]+)\]\]', text)
    return list(dict.fromkeys(ids))


def normalize_tag(tag: str) -> str:
    tag = tag.lower().strip()
    tag = re.sub(r'[^a-z0-9\s-]', '', tag)
    tag = re.sub(r'[\s_]+', '-', tag)
    tag = re.sub(r'-+', '-', tag)
    return tag.strip('-')
