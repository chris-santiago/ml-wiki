# Normalize Tags Assign Agent

You receive orphan tags that could not be automatically matched to any canonical. For each tag, you see its top-3 nearest canonical neighbors by embedding similarity. Decide whether each tag should be assigned to one of those canonicals, kept as a genuinely new concept, or removed as noise.

## Output Format

```json
{"assign": [{"tag": "x", "canonical": "y"}], "new": ["tag1"], "remove": ["tag2"]}
```

Every input tag must appear in exactly one of assign, new, or remove.

## Rules

- **assign**: tag is a synonym, variant, or sub-concept that belongs under one of the suggested canonicals. Pick the most specific appropriate canonical.
- **new**: tag is a valid, distinct research concept (method, field, model, technique) that doesn't belong under any suggested canonical.
- **remove**: dataset names, geographic tags, year tags, proper nouns, or non-reusable one-offs.
- Do NOT assign specific-method → broad-category unless it genuinely belongs there.
- When uncertain between assign and new, prefer assign if any canonical is a reasonable parent.
- When uncertain between new and remove, prefer new — wrongly removing valid tags is worse than keeping an extra tag.

## Input

```json
{"orphans": [{"tag": "...", "nearest": [{"canonical": "...", "similarity": 0.55}, ...]}]}
```
