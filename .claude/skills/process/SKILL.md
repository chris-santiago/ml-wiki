---
description: Ingest, render, and update backlinks for a single source in one step. Use when the user invokes /process with a citekey, file path, or URL. Convenience wrapper around /ingest + /render + partial backlink update.
argument-hint: <input> [--depth shallow|deep] [--focus "<hint>"]
---

# /process

Convenience command: ingest + render + partial backlink update in one step.

For one-off additions where you don't need the decoupled two-phase workflow. Use `/ingest` and `/render` separately for bulk operations.

## Execution

Run these three steps in order. **If any step fails, STOP.**

### Step 1: Ingest

```bash
uv run .wiki/scripts/wiki_cli.py ingest <input> [--tags T] [--project P]
```

Pass through any tags/project the user provided. If the entry is already ingested, note the existing entry ID and continue to Step 2.

### Step 2: Render

**If the entry ID starts with `syn-` (synthesis entry):** The ingested file IS the wiki page — rendering would overwrite the user's authored content with LLM-generated output. **Do NOT render.** Warn the user:

> "Skipping render — `<entry-id>` is a synthesis entry and your authored content is already in place. Rendering would overwrite it. Proceeding to backlink update."

Then skip directly to Step 3.

**Otherwise:**

```bash
uv run .wiki/scripts/wiki_cli.py render <entry-id> [--depth shallow|deep] [--focus "<hint>"]
```

Use the entry ID from Step 1. Pass through --depth and --focus if the user provided them.

### Step 3: Partial backlink update

```bash
uv run .wiki/scripts/wiki_index.py resolve-connections \
  --id <entry-id> \
  2>/dev/null > /tmp/<id>_connections.json
```

```bash
uv run .wiki/scripts/wiki_render.py update-connections-batch \
  --index .wiki/index.jsonl < /tmp/<id>_connections.json
```

## Report

"Processed `<id>` → `<wiki_path>`. Backlinks updated. Run `/build` for full MOC generation."
