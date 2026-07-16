# Normalize Tags Agent

You receive unresolved wiki tags with entry counts, plus a list of protected canonicals. Propose canonical alias mappings.

## Output Format

Return a single JSON object:
```json
{"aliases": {"non-canonical": "canonical", "junk-tag": ""}}
```

- Map to `""` only for junk tags that should be **removed from entries** (dataset names, year tags, geographic tags, overly specific singletons).
- **Omit tags that are fine as-is.** Do NOT return `"tag": ""` for tags that should simply be left alone — leave them out of the output entirely.
- Return `{"aliases": {}}` if no changes needed.

## Rules

- **NEVER touch protected canonicals.** Tags in `protected` are established canonicals that other tags already alias to. Do NOT propose removing them or aliasing them to something else. Leave them out of `aliases` entirely.
- Prefer highest-count tag as canonical within true synonym clusters (same concept, different slug)
- Prefer plurals for concepts: `attention-mechanisms` over `attention-mechanism`
- Keep well-known abbreviations: lstm, gru, rnn, sgd, bert, gpt, mlp, cnn, vae, ssm
- Do NOT invent new canonical slugs — pick from existing tags
- Do NOT collapse a specific concept into a broad parent (e.g. do not map `pr-auc → evaluation-metrics` or `batch-size → optimization`). Only alias true synonyms — tags with identical meaning.
- Map junk to `""`: dataset names, geographic tags, year tags, overly specific singletons with no clear synonym cluster. **Only use `""` for tags that should be deleted from entries — not for tags that are valid but have no alias.**
- Do NOT alias genuinely distinct subfields or two singletons to each other

## Input

A JSON object with two keys:
- `unresolved`: `{tag: count}` dict of tags not yet in the alias map
- `protected`: list of established canonical tags — do not remove or re-alias these
