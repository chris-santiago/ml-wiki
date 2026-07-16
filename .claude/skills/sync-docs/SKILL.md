---
description: Reconcile all project documentation with current implementation. Dispatches readme-agent (user-facing README) and sync-docs-agent (internal design docs). Use when the user invokes /sync-docs after making changes to skills, agents, or scripts.
argument-hint: [--readme] [--design]
---

# /sync-docs [--readme] [--design]

Reconciles project documentation with the current implementation by dispatching two agents:

- **readme-agent** — rewrites or updates `README.md` (user-facing documentation)
- **sync-docs-agent** — reconciles `design/` docs and `COMMANDS.md` (internal documentation)

With no flags, both agents run. Use `--readme` or `--design` to run one at a time.

---

## Step 1: Parse flags

| Input | readme-agent | sync-docs-agent |
|-------|-------------|-----------------|
| `/sync-docs` | yes | yes |
| `/sync-docs --readme` | yes | no |
| `/sync-docs --design` | no | yes |

---

## Step 2: Extract preserved header (readme-agent only)

Skip this step if `--design` was passed (readme-agent not needed).

Read `README.md`. Extract everything above `## Setup` — this is the preserved header that the readme-agent will prepend to its output.

If `## Setup` is not found, the entire current README is treated as the header and the agent will append new content after it.

---

## Step 3: Detect readme mode

Skip this step if `--design` was passed.

Check whether the current `README.md` contains `## How To`:
- **Present** → Mode 2 (incremental update). The README has already been overhauled.
- **Absent** → Mode 1 (full overhaul). The README is still in the old format.

---

## Step 4: Dispatch agents

Dispatch the required agents. If both are needed, dispatch them in parallel (two Agent tool calls in a single message).

### readme-agent dispatch

Use `.claude/agents/readme-agent.md`. Pass the mode, preserved header, and (for Mode 2) the full current README.

**Mode 1 prompt:**

```
Mode 1: Full Overhaul.

Write the complete README from ## Setup onward. Prepend the preserved header below verbatim.

## Preserved Header
<preserved header text>
```

**Mode 2 prompt:**

```
Mode 2: Incremental Update.

Read the current README below. Compare against source files. Make surgical edits only where reality diverges from documentation. Prepend the preserved header verbatim.

## Preserved Header
<preserved header text>

## Current README
<full current README text>
```

### sync-docs-agent dispatch

Use `.claude/agents/sync-docs-agent.md`. No special input — it reads sources itself.

```
Reconcile design documentation with current implementation.
```

**Note:** The sync-docs-agent has its own commit step (Phase 6). This is an existing inconsistency — it commits directly while this skill does not. For now, let it commit its own changes. The readme-agent does NOT commit.

---

## Step 5: Report

After both agents complete, summarize:
- What the readme-agent changed (Mode 1: "README fully rewritten", Mode 2: list of sections updated)
- What the sync-docs-agent changed (or "no drift found")

Remind the user to review and commit README changes:

"README changes written but not committed. Review the diff with `git diff README.md`, then commit when ready."
