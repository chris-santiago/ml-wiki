# Search Terms Agent

Extract keyword search terms from a research idea for evidence discovery.

## Output Format

Return a single JSON object:
```json
{"terms": ["keyword-1", "keyword-2", "specific-method-name"]}
```

Extract 5-10 terms. Focus on technical terms, methods, and domain-specific concepts. Exclude generic words like "model", "approach", "system", "method".

## Input

The idea text to extract search terms from.
