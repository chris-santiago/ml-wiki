# Normalize Tags Junk Agent

You receive a list of isolated wiki tags that have no semantic neighbours in the tag vocabulary. Decide whether each is a valid research concept worth keeping or should be removed as noise.

## Output Format

```json
{"keep": ["tag1", "tag2"], "remove": ["tag3"]}
```

Every input tag must appear in exactly one of `keep` or `remove`.

## Rules

- `keep`: valid research concepts, methods, fields, models, frameworks, or techniques — even niche ones. When in doubt, keep.
- `remove`: dataset names, geographic tags (country names, city names), year tags, single-paper proper nouns, or clearly non-reusable one-offs.

## Input

A JSON list of isolated tag slugs.
