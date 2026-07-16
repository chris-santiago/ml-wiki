---
name: publish
description: Rebuild and push the public branch (framework only, no personal content).
argument-hint: "[--dry-run]"
---

Run: `python3 scripts/publish_public.py [--dry-run]`

Pass through flags. Report the output to the user.

**Warning:** This force-pushes to `origin/public`. Confirm with the user before running without `--dry-run`.
