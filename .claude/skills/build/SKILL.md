---
name: build
description: Reconcile backlinks, check staleness, and regenerate MOC pages.
argument-hint: "[--rank fast|semantic|deep] [--force] [--map] [--min-entries N] [--concurrency N] [--only MOC_ID ...]"
---

Run: `uv run .wiki/scripts/wiki_cli.py build --fast [flags]`

**The skill always passes `--fast`** so routine `/build` is free: listings, connections, staleness banners, and TOPICS.md all update, but MOC prose is not regenerated via LLM. The mechanical layer (the lists of entries on each topic page) stays fresh on every build.

If the user explicitly wants LLM-regenerated MOC prose, they should call the CLI directly without `--fast`:

```
uv run .wiki/scripts/wiki_cli.py build [other flags]
```

Pass through all user arguments after `--fast`. If the user passes `--fast` themselves, the duplicate is harmless. Report the output to the user.
