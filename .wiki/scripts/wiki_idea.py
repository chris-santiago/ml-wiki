# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""Idea sub-flow functions for the wiki CLI."""


def generate_idea_page(idea_id: str, title: str, tags: list[str], project: str | None, content: str) -> str:
    tag_list = ", ".join(tags) if tags else ""
    body = content.strip() if content.strip() else "[User fills in content here]"

    return f"""---
id: {idea_id}
type: idea
tags: [{tag_list}]
project: {project or 'null'}
---

# {title}

{body}

## Notes

<!-- end-notes -->

## Connections
"""


def merge_tags(existing: list[str], new: list[str]) -> list[str]:
    combined = list(existing)
    for t in new:
        if t not in combined:
            combined.append(t)
    return combined
