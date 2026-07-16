# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""Domain-specific post-validators for LLM JSON output.

These enforce rules too nuanced for JSON Schema: tag count ranges per
depth, fragment count ranges, tag format normalization, deduplication.
Validators mutate the data in place (truncate, deduplicate, renumber)
and return a list of warnings for logging.
"""

from dataclasses import dataclass

VALID_FRAGMENT_TYPES = frozenset({
    "claim", "method", "finding", "dataset", "metric",
    "question", "definition", "description",
})

TAG_LIMITS = {
    "shallow": (3, 6),
    "deep": (3, 6),
}

FRAGMENT_LIMITS = {
    "shallow": (2, 4),
    "deep": (6, 12),
}


@dataclass
class ValidationWarning:
    field: str
    message: str


def _fix_tags(data: dict, min_tags: int, max_tags: int) -> list[ValidationWarning]:
    warnings = []
    tags = data.get("tags", [])

    tags = [t.lower() for t in tags]
    before_dedup = len(tags)
    tags = list(dict.fromkeys(tags))
    if len(tags) < before_dedup:
        warnings.append(ValidationWarning("tags", f"Removed {before_dedup - len(tags)} duplicate tags"))

    if len(tags) > max_tags:
        warnings.append(ValidationWarning("tags", f"Truncated tags from {len(tags)} to {max_tags}"))
        tags = tags[:max_tags]

    if len(tags) < min_tags:
        warnings.append(ValidationWarning("tags", f"Only {len(tags)} tags (minimum {min_tags})"))

    data["tags"] = tags
    return warnings


FRAGMENT_REQUIRED_KEYS = {"seq", "type", "title"}


def _fix_fragments(data: dict, min_frags: int, max_frags: int) -> list[ValidationWarning]:
    warnings = []
    frags = data.get("fragments", [])

    if len(frags) > max_frags:
        warnings.append(ValidationWarning("fragments", f"Truncated fragments from {len(frags)} to {max_frags}"))
        frags = frags[:max_frags]

    if len(frags) < min_frags:
        warnings.append(ValidationWarning("fragments", f"Only {len(frags)} fragments (minimum {min_frags})"))

    for i, frag in enumerate(frags):
        frag["seq"] = i + 1
        extra = set(frag.keys()) - FRAGMENT_REQUIRED_KEYS
        for k in extra:
            del frag[k]
        if extra:
            warnings.append(ValidationWarning("fragments", f"Stripped extra keys from fragment {i+1}: {extra}"))

    data["fragments"] = frags
    return warnings


PROSE_KEYS = frozenset({
    "summary", "key_claims", "methods", "results", "limitations",
    "hypothesis", "setup", "implications", "synthesis",
})


def _coerce_prose_fields(data: dict) -> list[ValidationWarning]:
    """If the LLM returned an array where a string was expected, join it."""
    warnings = []
    for key in PROSE_KEYS:
        if key in data and isinstance(data[key], list):
            data[key] = "\n".join(f"- {item}" if not str(item).startswith("- ") else str(item) for item in data[key])
            warnings.append(ValidationWarning(key, f"Coerced array to markdown string"))
    return warnings


def validate_render_output(data: dict, depth: str = "shallow") -> list[ValidationWarning]:
    warnings = []

    warnings.extend(_coerce_prose_fields(data))

    tag_min, tag_max = TAG_LIMITS.get(depth, (3, 8))
    frag_min, frag_max = FRAGMENT_LIMITS.get(depth, (2, 4))

    warnings.extend(_fix_tags(data, tag_min, tag_max))
    warnings.extend(_fix_fragments(data, frag_min, frag_max))

    return warnings
