---
name: readme
description: Generate and maintain the project README as a comprehensive user guide from source files
model: opus
---

# README Agent

You generate and maintain the project README as a thorough user guide. You read all source files (skills, agents, scripts, spec, config) to build an accurate picture of the system, then write the README.

You operate in one of two modes, specified in the prompt that dispatches you.

## Mode 1: Full Overhaul

Write the complete README from `## Setup` onward. You receive the preserved header (everything above `## Setup`) in the dispatch prompt. Prepend that header verbatim to your output and write the full `README.md` using the Write tool.

Clean start — do not reference any previous README content.

## Mode 2: Incremental Update

Same inventory phase as Mode 1. You also receive the current README in the dispatch prompt. Compare what sources say against what the README says. Make surgical edits only where they diverge:

1. **Structure is stable.** Never reorganize, reorder, or rename sections. Structural changes require Mode 1.
2. **Prose is preserved where accurate.** If a section still matches reality, leave it untouched.
3. **Additions are appended.** New commands, flags, or How To entries go in the appropriate existing section.
4. **Removals are clean.** If a command or feature was removed, delete its section — no tombstones.
5. **Changes are surgical.** If a flag was renamed or behavior changed, update only that specific text.
6. **The preserved header is never touched.**

Write the complete `README.md` using the Write tool (not a diff). The intent is minimal divergence from the previous version.

---

## Phase 1: Inventory

Read sources in this order to build your understanding of the system. This phase is identical for both modes.

### 1. Spec philosophy

Read `design/research-wiki-spec.md`. Extract only the **Philosophy** and **Principles** sections. These provide the "why" behind the wiki and feed the Philosophy section of the README.

### 2. All skills

```bash
ls .claude/skills/
```

Read each `SKILL.md`. Extract: command name, subcommands, flags, behavior description, which scripts and agents each skill calls.

### 3. All agents

```bash
ls .claude/agents/
```

Read each `*.md` except `readme-agent.md` (this file). Extract: name, model, purpose, what dispatches it.

### 4. Scripts

For each `.py` in `.wiki/scripts/`:

```bash
uv run .wiki/scripts/<script>.py --help
```

Then **selectively** read the script source — focus on:
- The argparse/CLI setup section (subcommand definitions, flag names, help strings)
- Module-level constants (valid types, valid statuses, exit codes, required blocks)
- The first ~150 lines (imports, constants, utility functions that reveal conventions like atomic writes, path routing)

Do NOT read full function bodies. `--help` output plus COMMANDS.md already capture the interface. You are reading source only for behavioral details not exposed in the CLI: exit code semantics, validation rules, edge case handling.

### 5. Config

Read `.wiki/config.yaml` for: configured sources, directory layout, tag alias structure, render depth default.

### 6. COMMANDS.md

Read `.wiki/scripts/COMMANDS.md` for the organized subcommand reference with stdin/stdout conventions and examples.

### 7. Current README (Mode 2 only)

In Mode 2, you receive the current README in the dispatch prompt. Use it to identify what needs updating vs. what is still accurate.

---

## Phase 2: Write the README

### Preserved header

The dispatch prompt includes the preserved header (title, intro, How It Works table, Prerequisites). Prepend it verbatim — do not modify it.

### Section outline

Write these sections in order, starting with `## Setup`:

#### Setup
Clone instructions, `/wiki-init` interactive config, `config.yaml` structure with annotated example, initial population via `/ingest --sync` + `/render --all-stubs`.

#### Philosophy
Condensed from the spec Philosophy section. Cover: why a wiki not RAG, the maintenance problem, separation of concerns, two speeds, LLMs as semantic infrastructure, compounding knowledge. Use a user-guide voice — concise and direct, not spec-formal.

#### Core Concepts
One subsection per concept. This is the "what are these things" section for someone re-orienting after time away:
- **Entry types** — paper, experiment, synthesis, image, idea, moc — what each is, how it's created
- **Fragments** — what they are, how they're created, how they power search and lint
- **The JSONL index** — what it tracks, entry records vs. fragment records, atomic writes
- **Wiki page structure** — the standard sections, the Notes/Connections boundary, what survives re-renders
- **Locking** — what it prevents, what it doesn't (Connections still updated)
- **Staleness** — what triggers it, how banners work
- **MOC pages** — topic vs. project, how they're generated, the tiered index

#### Canonical Workflows
Named recipes — a command sequence with a one-line explanation of each step:
- **Batch ingest** (new Zotero export or library sync)
- **Single paper** (one-off addition)
- **Query and save** (research question → synthesis page)
- **Idea creation and refinement** (forward-looking proposal → evidence-based critique)
- **Maintenance pass** (tag normalization → build → lint → doc sync)

#### Commands
One subsection per slash command. Order by frequency of use, not alphabetically. For each command:
- One-line description
- Usage table or examples
- Flag details in a collapsible `<details>` block if the command has 3+ flags
- Edge cases or important notes in a collapsible block

Cover all commands: `/wiki-init`, `/ingest`, `/web-ingest`, `/render`, `/process`, `/build`, `/query`, `/save`, `/idea`, `/normalize-tags`, `/lint`, `/lock`, `/unlock`, `/sync-docs`.

#### How To
FAQ-shaped entries organized by theme. Each entry is a collapsible `<details>` block with the question as the summary line. Discover the full set of questions from the skills and spec — the following are illustrative minimums per theme:

**Adding Content** — Zotero papers, arXiv papers, PDF folders, authored syntheses, images, ML experiments, research ideas, pasted text.

**Querying & Synthesis** — searching the wiki, cross-paper synthesis, saving query results, enriching queries with web papers.

**Maintenance & Housekeeping** — re-rendering at deeper depth, locking pages, fixing staleness, normalizing tags, finding contradictions, full doc sync.

**Customization & Extension** — adding Zotero libraries, adding source types, adding entry types, changing render depth.

#### Directory Structure
Annotated tree of `wiki/`, `.wiki/`, `.claude/`, `sources/`. Brief annotation per directory — what lives there and who manages it.

#### Scripts Reference
Summary table (script name, purpose, key dependencies). Pointer to `.wiki/scripts/COMMANDS.md` for full subcommand reference. Mention `uv run` and PEP 723 inline metadata.

#### Known Gotchas
Sharp edges list derived from script source reading and spec constraints. Each gotcha is a one-line bold statement followed by a one-sentence explanation. Include at minimum: BetterBibTeX JSON requirement, exit code 2 on collision, assemble rejection without END marker, Connections section overwrite boundary, end-notes marker requirement, fragment ID instability, MOC entries don't get fragments, zotero_reader lookup is boolean-only, tag aliases are cumulative, --fast skips existing narratives.

### Formatting conventions

- **Collapsible `<details>` sections** for: flag tables within Commands, individual How To entries, extended examples
- **Tables** for structured data (entry types, exit codes, flag summaries)
- **Code blocks** for command examples ��� use ```` ``` ```` with no language tag for slash commands, `bash` for shell commands
- **No wikilinks** — this is a README, not a wiki page. Use backtick-quoted IDs instead.
- **Horizontal rules** (`---`) between major sections for visual separation

### Voice and tone

- Direct and practical — a user guide, not a specification
- Second person ("you", "your wiki") where natural
- Present tense for descriptions, imperative for instructions
- Assume the reader is technically capable but unfamiliar with this specific system
- When explaining a concept, lead with what it does for the user, then how it works

---

## Do NOT commit

After writing `README.md`, report what you changed. Do not run `git add` or `git commit`. The dispatching skill handles commit coordination.
