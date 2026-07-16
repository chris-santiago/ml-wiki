# ml-wiki

A personal research wiki that ingests papers, Zotero libraries, PDFs, markdown notes, images, and ML experiment logs into a structured directory of interlinked markdown pages. It provides Map of Content (MOC) pages, semantic fragment search, contradiction detection, and staleness tracking -- all orchestrated through Claude Code slash commands.

After setup, use `/ingest --sync` to populate from your sources, `/render --all-stubs` to generate wiki pages, and `/build` to create topic indexes and cross-references.

---

## How It Works

ml-wiki uses a three-layer architecture where each layer has a single responsibility:

| Layer | Lives in | Responsibility |
|---|---|---|
| **CLI layer** (orchestration) | `.wiki/scripts/wiki_cli.py` | Python Click CLI that orchestrates everything. Each subcommand replaces a former SKILL.md procedure -- resolves config, calls scripts, dispatches LLM, validates output, and writes results. |
| **LLM layer** (judgment) | `.wiki/scripts/wiki_llm.py` + `.wiki/agents/*.md` | OpenRouter API calls with JSON output. Agent prompts request structured JSON, validated by jsonschema schemas in `.wiki/schemas/` and domain-specific post-validators in `wiki_validate.py`. |
| **Script layer** (mechanical) | `.wiki/scripts/wiki_index.py`, `wiki_render.py`, etc. | Does the work. Python scripts for all file I/O, index CRUD, source extraction, page assembly, and batch operations. No script calls another -- they communicate via stdin/stdout/files/exit codes. |

Skills still exist as thin wrappers (10-line SKILL.md files that call `uv run .wiki/scripts/wiki_cli.py <command>`) for slash-command access inside Claude Code. Three skills remain native Claude Code operations: `howto`, `process`, and `sync-docs`.

---

## Prerequisites

- **Claude Code** -- the [Anthropic CLI for Claude](https://docs.anthropic.com/en/docs/claude-code). Provides the slash-command interface and runs the 3 native skills (`/howto`, `/process`, `/sync-docs`).
- **uv** -- the Python package manager. Scripts use [PEP 723 inline metadata](https://peps.python.org/pep-0723/), so there is no `requirements.txt` or `pyproject.toml`. Running `uv run .wiki/scripts/wiki_cli.py` resolves and installs dependencies automatically.
- **OpenRouter API key** -- set the `OPENROUTER_API_KEY` environment variable. All LLM calls (render, build, query, lint, normalize-tags, idea improve) go through OpenRouter. The model tiers are configured in `.wiki/config.yaml`.
- **Zotero + BetterBibTeX plugin** (for the primary ingest path) -- you need BetterBibTeX JSON exports (not Better CSL JSON, which uses a different schema). Each Zotero collection is exported as a `.json` file into `sources/`.
- **Python 3.11+**

---

## Setup

### 1. Clone and initialize

```bash
git clone <repo-url>
cd ml-wiki
```

Run `/wiki-init` inside Claude Code to interactively generate `.wiki/config.yaml`:

```
/wiki-init
```

It asks for:
- Zotero library names and BBT JSON export paths (you can have multiple)
- Optional: ml-journal names and JSONL paths (for ML experiment logs)
- Optional: Zotero storage path (for PDF extraction)

The command scaffolds all required directories (`wiki/_pages/`, `wiki/topics/`, `wiki/projects/`, `.wiki/scripts/`, `.wiki/sources/`, `.wiki/schemas/`), creates an empty index if one does not exist, and validates that configured paths are reachable.

### 2. Configuration

The config file lives at `.wiki/config.yaml`:

```yaml
research_wiki:
  wiki_dir: ./wiki
  index_path: ./.wiki/index.jsonl
  default_render_depth: shallow
  llm:
    base_url: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY
    models:
      nano: openai/gpt-5.4-nano      # Fast: render-shallow, query-p1, build-rank, normalize-tags, search-terms
      mini: openai/gpt-5.4-mini      # Mid: render-deep, build-map, lint, idea-improve
      full: openai/gpt-5.4           # Best: build-moc, query-p2
    default_params:
      max_tokens: 16384
      temperature: 0
    model_overrides: {}               # Per-agent overrides (e.g. "render-deep": "anthropic/claude-sonnet-4")
  tag_blocklist: []                   # Tags excluded from normalize-tags proposals
  tag_aliases:
    # Cumulative map of non-canonical -> canonical tags.
    # Auto-managed by /normalize-tags. Do not hand-edit.
    lstm-networks: lstm
    nlp: natural-language-processing
    # ... (1700+ entries)
  sources:
    - name: my-papers
      type: zotero
      bbt_export_path: ./sources/MyPapers.json
      zotero_storage: /Users/you/Zotero/storage
    - name: my-references
      type: zotero
      bbt_export_path: ./sources/MyReferences.json
      zotero_storage: /Users/you/Zotero/storage
    - name: my-experiments
      type: ml-journal
      journal_path: /path/to/.project-log/journal.jsonl
```

`wiki_config.py` is the PyYAML boundary. It reads `config.yaml` and emits JSON to stdout. All other scripts are config-unaware -- they receive values as CLI flags from `wiki_cli.py`. This keeps the YAML dependency isolated.

### 3. Populate from sources

```
/ingest --sync
```

This diffs all configured Zotero and ml-journal sources against the index and adds new entries as stubs. Then render them:

```
/render --all-stubs
```

---

## Philosophy

### Why a wiki, not RAG

Most LLM-and-document workflows are retrieval-based: you upload files, the LLM retrieves relevant chunks at query time, and generates an answer from scratch. Nothing accumulates. Ask a subtle question that requires synthesizing five papers, and the LLM has to find and piece together the fragments every time.

This system takes a different approach. Instead of re-deriving knowledge on every query, the LLM incrementally builds a persistent wiki -- a structured, interlinked collection of markdown files that sits between you and the raw sources. When you add a new paper, the LLM reads it, extracts the key knowledge, and integrates it into the existing wiki. The cross-references are already there. The contradictions have already been flagged. The synthesis already reflects everything you have read.

### The maintenance problem

Personal knowledge bases die not because people stop reading but because the bookkeeping becomes unbearable. Updating cross-references, keeping summaries current, noting when new evidence contradicts old claims, maintaining consistency across dozens of pages. The maintenance burden grows faster than the value.

LLMs solve this. They do not get bored, do not forget to update a cross-reference, and can touch every file in the wiki in one pass. The cost of maintenance drops to near zero, so the wiki stays alive.

### Separation of concerns

Each tool in the stack does exactly one thing:

- **Zotero** is the library -- it manages PDFs, citations, and bibliographic metadata
- **ml-journal** is the experiment log -- it tracks what you tried and what happened
- **The wiki** is the synthesis layer -- it captures *why*, connecting papers to claims to experiments to open questions
- **Obsidian** is the viewer -- it renders the wiki for human browsing

None of these systems write to each other. The wiki reads from Zotero and ml-journal but never modifies them. Obsidian reads the wiki but never authors in it. Data flows in one direction: sources -> index -> wiki -> viewer.

### Two speeds

The system has a clear cost boundary. Operations that touch only the JSONL index -- ingesting, syncing, filtering, diffing -- are fast and free. Operations that invoke the LLM -- rendering, querying, narrative generation -- cost time and tokens. These are always decoupled so you are never blocked. You can ingest your entire Zotero library in seconds; you render papers on demand, when you are ready to engage with them.

### Compounding knowledge

Query results feed back into the wiki. When you synthesize an answer across multiple papers and experiments, that synthesis is valuable -- it should not vanish into chat history. `/save` files it as a first-class wiki page. Your explorations compound in the knowledge base just like ingested sources do. Over time, the wiki becomes a layered artifact: raw summaries at the bottom, cross-references in the middle, and your evolving synthesis on top.

---

## Core Concepts

### Entry types

| Type | Prefix | Directory | Created by | Description |
|---|---|---|---|---|
| **paper** | (none) | `wiki/_pages/` | `/ingest` + `/render` | A research paper, imported from Zotero, PDF, or pasted text |
| **experiment** | `exp-` | `wiki/experiments/` | `/ingest --sync` (ml-journal source) | An ML experiment record with hypothesis, setup, results |
| **synthesis** | `synthesis-` | `wiki/syntheses/` | `/save` or `/ingest` (authored markdown) | Your own analysis, query answer, or blog post |
| **image** | `img-` | `wiki/images/` | `/ingest` (image file) | A diagram or figure with vision-generated caption and description |
| **idea** | `idea-` | `wiki/ideas/` | `/idea create` or `/idea ingest` | A forward-looking proposal, research plan, or methodology design |
| **moc** | `topic-` or `project-` | `wiki/topics/` or `wiki/projects/` | `/build` | Map of Content -- navigational page grouping entries by tag or project |

### Fragments

Fragments are the atomic units of knowledge in the wiki. Each one is a self-contained sentence or two that captures a single claim, method, finding, dataset, metric, question, or definition from a source.

- **Created during `/render`**: The LLM reads the source material and extracts 2-4 fragments (shallow) or 6-12 fragments (deep). These are written to the index as `frag-<entry-id>-<seq>` records.
- **Power search**: `/query` runs keyword search across fragment titles and tags. Because each fragment title is a self-contained statement (e.g., "Attention mechanisms outperform CNNs for sequential fraud detection on the Kaggle IEEE-CIS dataset"), the search engine can match on meaning rather than just keywords.
- **Power lint**: `/lint` detects contradictions by comparing `claim` and `finding` fragment titles across entries that share a tag.
- **Not stable across re-renders**: When a page is re-rendered, old fragments are deleted and new ones are created. Reference the source entry ID, not fragment IDs.

Fragment types (closed list): `claim`, `method`, `finding`, `dataset`, `metric`, `question`, `definition`, `description`.

### The JSONL index

The index at `.wiki/index.jsonl` is the single source of truth for what the wiki contains. It holds two kinds of records:

<details>
<summary>Entry record example</summary>

```json
{
  "id": "fischerDeepLearning2024",
  "type": "paper",
  "source_type": "zotero",
  "source_path": "/Users/you/Zotero/storage/.../fischer2024.pdf",
  "wiki_path": "wiki/_pages/fischerDeepLearning2024.md",
  "title": "Deep Learning for Fraud Detection",
  "citation": "Fischer, A. (2024). Deep Learning...",
  "year": 2024,
  "tags": ["deep-learning", "fraud-detection"],
  "project": null,
  "source_name": "my-papers",
  "references": [],
  "status": "rendered",
  "locked": false,
  "ingested": "2026-05-02T05:07:54Z",
  "rendered": "2026-05-02T05:21:37Z",
  "last_improved": null
}
```

</details>

<details>
<summary>Fragment record example</summary>

```json
{
  "id": "frag-fischerDeepLearning2024-01",
  "type": "method",
  "title": "GRU with mean-pooled FastText embeddings for sequential fraud detection.",
  "tags": ["deep-learning", "fraud-detection", "gru"],
  "references": ["fischerDeepLearning2024"],
  "ingested": "2026-05-02T05:21:31Z"
}
```

</details>

All index mutations use atomic writes (write to `.tmp`, then `os.rename()`).

### Page structure

Every rendered wiki page follows a consistent structure:

```markdown
---
id: fischerDeepLearning2024
type: paper
year: 2024
source_name: my-papers
---
# Paper Title

**Citation:** Fischer, A. (2024). Deep Learning...
**PDF:** [Open PDF](zotero://open-pdf/library/items/ABC123)

## Summary
[LLM-generated content]

## Key Claims
[LLM-generated content]

## Methods / Results / Limitations
[LLM-generated content]

## Notes
<!-- user-owned section -- preserved across re-renders -->
Your notes go here. This section survives /render and /render --force.
<!-- end-notes -->

## Connections
<!-- managed by /build -- do not edit manually -->
**References:** [[cited-entry-1]], [[cited-entry-2]]
**Cited by:** [[topic-fraud-detection]]
**Related:** [[related-entry-1]], [[related-entry-2]]
```

Important boundaries:
- Everything between `## Notes` and `<!-- end-notes -->` is yours. It survives all re-renders.
- Everything from `## Connections` to EOF is managed by `/build`. Do not edit manually.
- Staleness banners (marked with `<!-- wiki-staleness-banner -->`) are added/removed automatically by `/build`.

### Locking

`/lock <id>` prevents `/render` from overwriting a page -- use it when you have manually edited generated sections. `/build` still updates the `## Connections` section and staleness banners on locked pages. `/unlock <id>` reverses it. The `## Notes` section is always preserved regardless of lock state.

### Staleness tracking

`/build` detects stale entries -- syntheses whose source entries have been re-rendered since the synthesis was written, and ideas that have never been reviewed or whose shared-tag entries have been updated. Stale pages get a banner:

- Syntheses: `> **Stale synthesis** -- N new entries with shared tags since last render. Run /render <id> to refresh.`
- Unreviewed ideas: `> **Unreviewed idea** -- Run /idea improve <id> to generate an evidence review.`
- Stale ideas: `> **Stale idea** -- N new entries with shared tags since last improve. Run /idea improve <id> to refresh.`

Stale syntheses can be refreshed programmatically via the `synthesis-refresh` CLI command (see Scripts Reference), which reads the existing synthesis, gathers new evidence, and dispatches an LLM to integrate it.

### MOC pages

Map of Content pages are navigational pages that group entries by tag or project. They are generated by `/build` for any tag with 10+ entries (configurable via `--min-entries`), or any project with entries.

A full MOC page (generated by `/build` without `--fast`) contains:
- **Narrative**: A paragraph-level overview of the topic
- **Subgroups**: How entries cluster within the topic
- **Tensions**: Where papers disagree or offer competing approaches
- **Evolution**: How the field has developed over time
- **Listing**: A formatted list of every entry in the group

A fast MOC page (generated by `/build --fast`) contains only the listing. `/build --fast` skips groups that already have a rendered narrative page, making it safe to run without clobbering existing LLM-generated content.

`wiki/TOPICS.md` is a tiered alphabetical index: Major (10+ entries), Minor (3-9), Specialist (1-2). `wiki/MAP.md` (generated with `--map`) clusters all major topics into 8-12 thematic research domains.

---

## Canonical Workflows

### Batch ingest (new Zotero papers)

The standard sequence after exporting a Zotero collection:

```
/ingest --sync              # Register new entries as stubs (fast, no LLM)
/render --all-stubs         # Generate wiki pages for every stub (LLM)
/normalize-tags             # Consolidate tag drift across the new batch
/build                      # Reconcile backlinks, staleness, and MOC pages
```

Each step is independent and idempotent. `/ingest` has no LLM cost. `/normalize-tags` uses LLM calls for tag clustering and junk detection (mini + nano tiers). `/render` is the expensive step. `/build` runs after any render batch to sync the navigational layer.

### Single paper

For a one-off addition:

```
/process fischerDeepLearning2024
```

This runs ingest + render + partial backlink update in one step. Run `/build` afterwards if you want full MOC regeneration.

### Query and save

```
/query "What loss functions work best for heavily imbalanced fraud detection?"
/save
```

The answer is saved as a synthesis page in `wiki/syntheses/` with extracted fragments. Or combine into one step:

```
/query "What loss functions work best for heavily imbalanced fraud detection?" --save
```

### Web-enriched query

```
/query "What are recent advances in tabular deep learning?" --web
```

After answering, `--web` searches arXiv for relevant papers not yet in the wiki, shows a selection panel, and ingests your picks. The query then re-runs against the enriched index.

### Idea workflow

```
/idea create "GRU-Mamba hybrid for fraud sequence modeling"
# ... fill in your content in the wiki page ...
/idea improve idea-gru-mamba-hybrid
```

Or ingest an existing document and immediately critique it:

```
/idea ingest plans/my-research-plan.md --improve
```

The improve pipeline searches the wiki (and optionally the web) for supporting and contradicting evidence, then splices a structured critique into the idea page.

### Maintenance pass

After a period of active ingestion:

```
/normalize-tags             # Resolve tag drift
/build                      # Full rebuild: connections + MOCs + banners
/lint                       # Structural + semantic audit
```

---

## Commands

All commands are Claude Code slash commands. Run them in a Claude Code session. Most delegate to `wiki_cli.py` -- the CLI handles all orchestration, LLM calls, and output validation.

### /ingest

Register a source in the JSONL index. Fast, no LLM cost.

```
/ingest fischerDeepLearning2024          # Zotero citekey
/ingest paper.pdf                        # Local PDF
/ingest notes.md                         # Markdown (asks: synthesis or reference?)
/ingest screenshot.png                   # Image (vision pass for captioning)
/ingest import/                          # Batch-ingest all files in a directory
/ingest --sync                           # Diff all configured sources against index
/ingest --sync --source my-papers      # Sync a single source
```

Source type detection is automatic: Zotero citekeys are matched against configured BBT exports, files are classified by extension, and ambiguous inputs get one clarifying question.

<details>
<summary>Flags</summary>

| Flag | Effect |
|---|---|
| `--text "<content>"` | Ingest pasted text directly (no file needed) |
| `--id <slug>` | Explicit entry ID override |
| `--title "<title>"` | Entry title |
| `--tags "t1,t2"` | Comma-separated tags |
| `--project <name>` | Project name |
| `--source-kind reference\|synthesis` | For markdown: skip the interactive question |
| `--sync` | Diff all configured sources and bulk-add stubs |
| `--source <name>` | With `--sync`: only sync this source |
| `--caption "<text>"` | Image caption (non-interactive) |
| `--description "<text>"` | Image description (non-interactive) |

</details>

<details>
<summary>Special cases</summary>

- **Markdown files**: You are asked whether it is an authored synthesis (your own writing) or a reference document. Syntheses get a `synthesis-` prefix and route to `wiki/syntheses/`. If the source has relative figure/image links, it is symlinked rather than copied so figure references resolve correctly.
- **Images**: The file is copied to `wiki/assets/`, Claude Code's vision is used to generate a caption and description, and you confirm before the entry is created.
- **Pasted text**: If the input is not a file path, the content is saved to `.wiki/sources/<id>.txt`.
- **Directory ingest**: Each file is ingested individually with full interactive flow. Successfully ingested files are removed from the directory; failed or skipped files are left in place.
- **Sync**: Diffs all configured Zotero sources (via `zotero_reader.py diff`) and ml-journal sources (via `wiki_render.py list-experiments`) against the index and bulk-adds new stubs.

</details>

---

### /render

Generate a markdown wiki page from source material. This is where LLM cost lives.

```
/render fischerDeepLearning2024                        # Shallow render (default)
/render fischerDeepLearning2024 --depth deep           # Comprehensive extraction
/render fischerDeepLearning2024 --focus "fraud detection"  # Steer extraction emphasis
/render fischerDeepLearning2024 --force                # Render even if locked
/render --all-stubs                                    # Batch render every stub
/render --tag fraud-detection                          # Batch render stubs with a tag
/render --all-stubs --concurrency 5                    # Limit parallel API calls
/render --fragments-only --type synthesis              # Extract fragments from syntheses without overwriting pages
/render syn-my-entry --fragments-only                  # Single entry fragment extraction
```

<details>
<summary>Render depths</summary>

| Depth | Model tier | Output |
|---|---|---|
| **shallow** (default) | `nano` | 3-5 sentence summary, 3-4 key claims, 2-4 fragments |
| **deep** | `mini` | Detailed methods/results, 6-12 fragments across all types |

</details>

Batch mode (`--all-stubs` / `--tag`) dispatches LLM calls concurrently. Use `--concurrency N` to control parallelism (default 10).

The `## Notes` section is always preserved across re-renders, even with `--force`. Idea entries are rejected -- they use `/idea improve` instead.

<details>
<summary>Fragments-only mode</summary>

`--fragments-only` extracts fragments and tags from an entry without overwriting the wiki page. Designed for authored syntheses (ingested markdown) that need searchable fragments but whose page content should not be regenerated.

- Bypasses the lock check -- no `--force` needed on locked entries
- Reads the wiki page as source text (not the original source file)
- Query syntheses (from `/query --save`) are automatically skipped -- their source documents already have fragments
- Combine with `--type synthesis` to batch all ingested markdown syntheses at once

</details>

---

### /process

Convenience wrapper: ingest + render + partial backlink update in one step.

```
/process fischerDeepLearning2024
/process fischerDeepLearning2024 --depth deep --focus "transformers"
```

Use for one-off additions. For bulk operations, use `/ingest` and `/render` separately, then `/build`. Does not regenerate MOC pages.

---

### /build

Reconcile the entire wiki: backlinks, staleness banners, and MOC pages.

The slash command **always passes `--fast`** (listing-only MOCs, no LLM prose), so routine `/build` is free. Mechanical layer (entry lists, connections, staleness, TOPICS.md) updates on every run. To regenerate MOC prose with the LLM, call the CLI directly without `--fast`.

```
/build                              # mechanical-only (always --fast under the hood)
/build --rank deep                  # mechanical MOCs + LLM-ranked connections
/build --map                        # also generate thematic MAP.md
/build --force                      # rebuild all MOC groups, even up-to-date ones
/build --min-entries 5              # lower the topic page threshold (default: 10)
/build --concurrency 25             # cap parallel API calls (default: 50)
/build --only topic-fraud-detection # rebuild a single MOC page (skips ranking/staleness)
/build --only topic-fraud-detection --only topic-deep-learning  # multiple targets

# Full LLM-narrative MOC regeneration (rare, expensive — call CLI directly):
uv run .wiki/scripts/wiki_cli.py build
```

<details>
<summary>Ranking modes (--rank)</summary>

| Flag | Behavior | Cost |
|---|---|---|
| `--rank fast` | Top-8 by shared tag count. Stdlib only, instant. | None |
| `--rank semantic` (default) | MiniLM cosine similarity via `wiki_embed.py`. ~80s one-time model download (~80MB), then ~80s encode/score. | No LLM calls |
| `--rank deep` | Parallel LLM calls reading entry content. Most accurate, highest cost and latency. | LLM |

</details>

<details>
<summary>MOC generation modes</summary>

| Flag | Behavior |
|---|---|
| `--fast` (slash-command default) | Listing-only pages, no LLM calls. Skips groups with existing narrative pages unless `--force` is also passed. The `/build` slash command always passes this. |
| (no flag, CLI only) | LLM-generated narrative pages (Narrative, Subgroups, Tensions, Evolution sections). Only dirty groups (membership changed or entries re-rendered) are rebuilt unless `--force` is passed. Invoke via `uv run .wiki/scripts/wiki_cli.py build`. |
| `--map` | Additionally generates `wiki/MAP.md` thematic landscape overview (one extra LLM call). |
| `--min-entries N` | Minimum entries for a tag to get a topic page (default: 10). Lower it to generate MOC pages for smaller clusters. |
| `--concurrency N` | Max parallel API calls for MOC and deep-rank dispatch (default: 50). |
| `--only MOC_ID ...` | Rebuild only the named MOC pages (e.g. `topic-fraud-detection`). Skips connection ranking, staleness checks, and topic index generation. Repeatable -- pass multiple `--only` flags for multiple targets. |

</details>

<details>
<summary>Full build pipeline</summary>

1. Resolve the full connection map (shared tags, shared fragments, backlinks, journal refs)
2. Rank related links for each entry using the selected ranking mode
3. Write `## Connections` sections on every page (including locked pages)
4. Detect stale synthesis and idea entries and add/remove staleness banners
5. Load tag aliases from config and group entries by canonical tag
6. Filter to dirty MOC groups (unless `--force`)
7. Generate MOC pages (full narratives or listings depending on `--fast`)
8. Write `wiki/TOPICS.md` (tiered alphabetical index)
9. Optionally write `wiki/MAP.md` (`--map`)

</details>

---

### /query

Answer a research question from the wiki's knowledge base.

```
/query "What loss functions work best for heavily imbalanced fraud detection?"
/query "How do state-space models compare to transformers?" --save
/query "What are recent advances in tabular deep learning?" --web
/query "What are recent advances in tabular deep learning?" --web --auto
/query "What are recent advances in tabular deep learning?" --web --auto --no-requery
/query "What are recent advances in tabular deep learning?" --web --select "1,3,5"
```

| Flag | Effect |
|---|---|
| `--save` | File the final answer as a synthesis wiki page |
| `--web` | After answering, search arXiv for relevant uningested papers and offer to ingest them, then re-query |
| `--auto` | With `--web`: ingest all suggestions without prompting |
| `--no-requery` | With `--web`: ingest without re-running the query |
| `--select "1,3"` | With `--web`: non-interactive paper selection by index |

<details>
<summary>Query pipeline</summary>

1. **Pass 1** (`nano` tier): Keyword search across fragments, generate semantic rephrasings, evaluate routing rubric. Either answers directly from fragments or identifies which full pages need reading.
2. **Pass 2** (`full` tier, only if needed): Reads full wiki pages and synthesizes a comprehensive answer with inline citations. Contradictions are surfaced explicitly -- conflicting claims are called out, not blended.

The result is saved to `.wiki/last-query-result.md` as JSON for follow-up with `/save` (which reads it via `json.load`).

With `--web`, after the answer is displayed, a panel shows arXiv papers not yet in the wiki. You select which to ingest (or `--auto` ingests all, or `--select` picks by index). Each selected paper is downloaded, ingested, and rendered via `/web-ingest` + `/render`. Then the query re-runs against the enriched index (unless `--no-requery`). Web-acquired entries become first-class wiki entries -- they are never injected directly into synthesis.

</details>

---

### /save

File the last query result as a synthesis wiki page.

```
/save
/save --id synthesis-loss-functions-imbalanced-fraud
```

Creates a synthesis page in `wiki/syntheses/` with fragments extracted from the answer. The `synthesis-` prefix is added automatically if omitted.

---

### /idea

Create, ingest, and improve wiki idea entries. Ideas are forward-looking proposals that can be iteratively improved with evidence-based critique.

```
/idea create "GRU-Mamba hybrid for fraud modeling"
/idea create "GRU-Mamba hybrid" --tags "gru,mamba" --project ato
/idea ingest plans/research-plan.md
/idea ingest plans/research-plan.md --improve
/idea ingest plans/research-plan.md --improve --local
/idea improve idea-gru-mamba-hybrid
/idea improve idea-gru-mamba-hybrid --local
```

| Subcommand | Effect |
|---|---|
| `create "<title>"` | Create a new empty idea page at `wiki/ideas/idea-<slug>.md` |
| `ingest <path>` | Create an idea from an existing markdown file or pasted text |
| `ingest <path> --improve` | Ingest and immediately run the improve pipeline |
| `improve <id>` | Web search + wiki evidence + structured critique (default) |
| `improve <id> --local` | Wiki-only evidence, skip web search |

<details>
<summary>Improve pipeline</summary>

1. Extract search terms from the idea text
2. Fragment-aware search across the wiki index (`score-idea`)
3. Web evidence pass: search arXiv, ingest top results (unless `--local`)
4. Dispatch `idea-improve` agent with idea text + evidence
5. Splice structured critique (Supporting, Contradicting, Gaps, Suggestions) into the idea page
6. Update index with merged tags, references, and fragments

The improve pipeline preserves the user's original idea text and appends the evidence review below it.

</details>

---

### /web-ingest

Fetch an arXiv paper by ID or URL, download the PDF, and add a stub entry.

```
/web-ingest 2312.00752
/web-ingest https://arxiv.org/abs/2312.00752
/web-ingest arXiv:2312.00752
/web-ingest 2312.00752 --yes                   # Skip confirmation
/web-ingest 2312.00752 --dry-run               # Fetch metadata only
```

| Flag | Effect |
|---|---|
| `--yes` / `-y` | Skip the confirmation prompt |
| `--dry-run` | Fetch metadata only, do not download or add |
| `--tags "t1,t2"` | Comma-separated tags to apply |
| `--project <name>` | Project name to apply |

arXiv only -- no other web sources. Shows a confirmation screen with title/authors/abstract before downloading. Run `/render <id>` afterwards to generate the wiki page.

---

### /normalize-tags

Scan all tags, identify duplicates and synonyms, propose a canonical mapping, and apply it.

```
/normalize-tags
/normalize-tags --dry-run
/normalize-tags --apply /path/to/aliases.json
/normalize-tags --promote 5                      # promote aliased tags with 5+ entries
/normalize-tags --promote 5 --dry-run            # preview promotions
```

| Flag | Effect |
|---|---|
| `--dry-run` | Show proposals without applying. Prints the final aliases file path for later use with `--apply`. |
| `--apply <file>` | Apply a previously generated alias map from a JSON file (skip LLM). |
| `--promote N` | Promote aliased tags with N+ entries to independent canonicals (removes them from the alias map). Combinable with `--dry-run` to preview. |

Run after batch ingestion when tag drift accumulates. The pipeline uses a multi-stage approach: MiniLM cosine similarity clusters similar tags, then LLM calls confirm ambiguous merges (`mini` tier for clustering confirmation, `nano` tier for junk detection) and match unresolved tags to canonical forms. Singleton-to-singleton mappings are automatically blocked.

The alias map is cumulative -- each run adds to the existing map in `config.yaml`. Previously resolved tags are never re-evaluated. Aliases are loaded by `/build` during MOC grouping, so running `/normalize-tags` before `/build` ensures synonym consolidation flows into MOC pages.

---

### /lint

Audit the wiki for structural problems and semantic inconsistencies.

```
/lint                       # Full: structural + contradiction detection + cross-link suggestions
/lint --mechanical-only     # Structural checks only (no LLM cost)
/lint --fix                 # Structural checks + auto-repair orphan fragments and broken paths
/lint --concurrency 5       # Limit parallel API calls for contradiction checks
/lint --min-frags 10        # Raise minimum fragments per tag for contradiction check (default 5)
```

<details>
<summary>Structural checks</summary>

- Index entries with no corresponding wiki file
- Wiki files with no corresponding index entry
- Orphan fragments (source entry not in index)
- Entries with `status: rendered` but no `wiki_path`
- Entries with `wiki_path` pointing to missing files
- Invalid `type`, `source_type`, or `status` values
- Tags that are not lowercase-slugified
- MOC entries that have fragments
- Duplicate entry IDs

</details>

<details>
<summary>Semantic checks (LLM-powered)</summary>

- **Contradiction detection**: Compares `claim` and `finding` fragment titles across entries that share a tag. Flags direct contradictions, scope conflicts, and temporal contradictions.
- **Cross-link suggestions**: Identifies orphan entries (no inbound references) that should be linked to related entries.

</details>

---

### /lock and /unlock

```
/lock fischerDeepLearning2024       # Prevent /render from overwriting
/unlock fischerDeepLearning2024     # Allow /render to overwrite again
```

Locking prevents `/render` from overwriting generated content. `/build` still updates `## Connections` and staleness banners. `## Notes` is always preserved regardless.

---

### /wiki-init

Initialize or reconfigure the wiki.

```
/wiki-init
/wiki-init --non-interactive
```

Run once during first-time setup, when adding sources, or after a fresh clone. Interactively generates `.wiki/config.yaml` (including the `llm` section with OpenRouter defaults), scaffolds directories, and creates an empty index. If `config.yaml` already exists, shows the current config and asks before overwriting. Use `--non-interactive` to create a minimal config without prompts.

---

### /sync-docs

Reconcile project documentation with current implementation.

```
/sync-docs              # Both README and design docs
/sync-docs --readme     # README only
/sync-docs --design     # Design docs only
```

Dispatches the readme-agent (for `README.md`) and/or the sync-docs-agent (for `design/` docs and `COMMANDS.md`).

---

### /publish

Rebuild and force-push the `public` branch, stripping all personal content (wiki pages, index, sources, design docs) while preserving the framework (scripts, agents, schemas, skills, config template).

```
/publish              # Rebuild and push
/publish --dry-run    # Show what would be removed/rewritten without pushing
```

Works entirely in a temporary clone so the real working tree is never touched. Confirms before force-pushing.

---

## How To

### Adding Content

<details>
<summary>How do I add a paper from Zotero?</summary>

Export your Zotero collection as BetterBibTeX JSON into `sources/`. Then:

```
/ingest --sync --source my-library
/render <citekey>
```

Or sync all sources at once with `/ingest --sync`, then batch render with `/render --all-stubs`.

</details>

<details>
<summary>How do I add a paper from arXiv?</summary>

```
/web-ingest 2312.00752
/render <id>
```

Or use the full URL: `/web-ingest https://arxiv.org/abs/2312.00752`. The command fetches metadata, shows a confirmation screen, and downloads the PDF. You can also add tags and a project during confirmation.

</details>

<details>
<summary>How do I add my own writing (blog post, analysis, notes)?</summary>

```
/ingest my-analysis.md
```

When asked, choose "authored synthesis." The file is placed in `wiki/syntheses/` with a `synthesis-` prefix and is automatically locked so `/render` will not overwrite it. If your markdown has relative image links, the file is symlinked (not copied) so figure references resolve correctly.

</details>

<details>
<summary>How do I make my authored syntheses searchable?</summary>

After ingesting a markdown file as a synthesis, extract fragments so `/query` and `/lint` can find it:

```
/render syn-my-analysis --fragments-only
```

Or batch all ingested syntheses at once:

```bash
uv run .wiki/scripts/wiki_cli.py render --fragments-only --type synthesis --concurrency 20
```

This extracts fragments and tags without touching the wiki page content. Query syntheses (from `/query --save`) are automatically skipped since their source documents already have fragments.

</details>

<details>
<summary>How do I add an image or diagram?</summary>

```
/ingest screenshot.png
```

The image is copied to `wiki/assets/`, Claude Code's vision generates a caption and description, and you confirm before the entry is created. The resulting page at `wiki/images/img-<slug>.md` embeds the image with its description.

</details>

<details>
<summary>How do I ingest a batch of loose files?</summary>

```bash
mkdir -p import
cp ~/Downloads/*.pdf import/
```

```
/ingest import/
```

Each file is ingested individually with full interactive flow. Successfully ingested files are removed from the directory. Failed or skipped files are left in place.

</details>

<details>
<summary>How do I record a research idea?</summary>

```
/idea create "My research idea title" --tags "tag-a,tag-b"
```

Fill in the content at `wiki/ideas/idea-<slug>.md`, then run `/idea improve idea-<slug>` to get an evidence-based critique. Or ingest an existing document: `/idea ingest plans/my-plan.md --improve`.

</details>

### Querying & Synthesis

<details>
<summary>How do I ask the wiki a question?</summary>

```
/query "What loss functions work best for imbalanced fraud detection?"
```

The answer draws from fragment search results. If fragment data is insufficient, full wiki pages are read and synthesized. Contradictions between sources are surfaced explicitly.

</details>

<details>
<summary>How do I save a query answer as a wiki page?</summary>

```
/query "My question" --save
```

Or run `/save` after viewing the answer. The result becomes a synthesis page in `wiki/syntheses/` with its own fragments.

</details>

<details>
<summary>How do I discover papers I might be missing?</summary>

```
/query "My question" --web
```

After the answer, a panel shows arXiv papers on the topic that are not in the wiki. Select which to ingest, and the query re-runs against the enriched index.

For fully automatic discovery and re-query:

```
/query "My question" --web --auto
```

</details>

### Maintenance & Housekeeping

<details>
<summary>How do I run a full maintenance pass?</summary>

```
/normalize-tags
/build
/lint
```

`/normalize-tags` resolves tag drift. `/build` reconciles connections, staleness, and MOC pages. `/lint` audits for structural and semantic problems.

</details>

<details>
<summary>How do I re-render a page with better quality?</summary>

```
/render <id> --depth deep
```

Deep renders use the `mini` model tier and produce 6-12 fragments with detailed methods and results. Your `## Notes` section is preserved.

</details>

<details>
<summary>How do I protect a page I have manually edited?</summary>

```
/lock <id>
```

`/render` will refuse to overwrite it (unless `--force` is used). `/build` still updates connections and banners.

</details>

<details>
<summary>How do I fix structural problems in the wiki?</summary>

```
/lint --fix
```

This auto-repairs orphan fragments and broken `wiki_path` references. Orphan wiki files (no index entry) are reported for manual review but not deleted.

</details>

<details>
<summary>What does a staleness banner mean?</summary>

It means source entries referenced by a synthesis or idea have been re-rendered since the synthesis/idea was last written. The information may be outdated. Run `/render <id>` (for syntheses) or `/idea improve <id>` (for ideas) to refresh. For syntheses, you can also use the `synthesis-refresh` CLI command to programmatically integrate new evidence.

</details>

### Customization & Extension

<details>
<summary>How do I add a new Zotero source?</summary>

Run `/wiki-init` to reconfigure, or edit `.wiki/config.yaml` directly:

```yaml
sources:
  - name: my-new-library
    type: zotero
    bbt_export_path: ./sources/MyLibrary.json
    zotero_storage: /Users/you/Zotero/storage
```

Then `/ingest --sync --source my-new-library`.

</details>

<details>
<summary>How do I add ML experiment logs?</summary>

Add an ml-journal source to `.wiki/config.yaml`:

```yaml
sources:
  - name: my-experiments
    type: ml-journal
    journal_path: /path/to/.project-log/journal.jsonl
```

Then `/ingest --sync --source my-experiments`. Experiment entries get `exp-` prefixes and route to `wiki/experiments/`.

</details>

<details>
<summary>How do I add a new entry type?</summary>

1. Add the type to `VALID_TYPES` in `wiki_index.py`
2. Add a template in `wiki_render.py`'s `REQUIRED_BLOCKS` dict
3. Add a corresponding agent prompt in `.wiki/agents/render-*.md`
4. Add a JSON schema in `.wiki/schemas/render-<type>.json`
5. Add path routing in the `wiki_path()` function if the new type needs its own directory

</details>

<details>
<summary>How do I add a new source type?</summary>

1. Add the source to `.wiki/config.yaml` under `sources`
2. If it needs custom parsing, add a reader script in `.wiki/scripts/` with PEP 723 inline metadata
3. Update `wiki_cli.py`'s `ingest` command to handle the new source type in its detection logic
4. Add a `source_type` value to `VALID_SOURCE_TYPES` in `wiki_index.py`

</details>

<details>
<summary>How do I change the LLM models?</summary>

Edit the `llm.models` section in `.wiki/config.yaml`:

```yaml
llm:
  models:
    nano: openai/gpt-5.4-nano
    mini: openai/gpt-5.4-mini
    full: openai/gpt-5.4
```

Or override a specific agent without changing the tier default:

```yaml
llm:
  model_overrides:
    render-deep: anthropic/claude-sonnet-4
    query-p2: anthropic/claude-opus-4
```

Any model available on OpenRouter can be used. The `api_key_env` field controls which environment variable holds the API key.

</details>

---

## Directory Structure

```
wiki/
  _pages/               # Paper pages (Zotero, PDF, pasted text)
  experiments/          # ML experiment pages (from ml-journal)
  ideas/                # Idea pages (proposals, research plans)
  topics/               # topic-<tag>.md MOC pages
  projects/             # project-<name>.md MOC pages
  syntheses/            # synthesis-<slug>.md authored/query pages
  images/               # img-<slug>.md image pages
  sources/              # arXiv PDFs downloaded by /web-ingest
  assets/               # Original image files (png, jpg, etc.)
  TOPICS.md             # Tiered alphabetical index of all topic MOCs
  MAP.md                # Thematic landscape overview (8-12 research domains)

.wiki/
  scripts/              # Python scripts (the mechanical + orchestration layers)
    wiki_cli.py         # Click CLI orchestrator — replaces SKILL.md procedures
    wiki_llm.py         # OpenRouter LLM client (sync + async batch), JSON extraction, schema validation
    wiki_validate.py    # Domain-specific post-validators (tag/fragment count enforcement, prose coercion)
    wiki_util.py        # Pure utilities (slugify, source-type detection, citation extraction, tag normalization)
    wiki_build.py       # Build helpers (listing construction, banner generation, candidate pre-filtering)
    wiki_idea.py        # Idea helpers (page generation, tag merging)
    wiki_index.py       # JSONL CRUD engine, path routing, search, fragments, lint
    wiki_render.py      # Source extraction, page assembly, batch operations
    wiki_config.py      # YAML -> JSON config bridge (PyYAML boundary)
    wiki_embed.py       # Sentence-transformer embedding, cosine scoring, tag clustering, canonical matching
    arxiv_fetch.py      # arXiv API client (normalize, fetch, download-pdf)
    zotero_reader.py    # BetterBibTeX JSON parser
    COMMANDS.md         # Full subcommand reference, organized by workflow stage
    tests/              # Test suite (294 tests, run via uv run --group test pytest)
  agents/               # LLM agent prompt templates (JSON output)
    render-shallow.md   # Fast structured summary (nano tier)
    render-deep.md      # Comprehensive extraction (mini tier)
    build-moc.md        # MOC narrative generation (full tier)
    build-rank.md       # Related link ranking (nano tier)
    build-map.md        # Thematic map generation (mini tier)
    query-p1.md         # Query narrowing pass (nano tier)
    query-p2.md         # Query synthesis pass (full tier)
    lint.md             # Contradiction detection + cross-link suggestions (mini tier)
    normalize-tags.md   # Tag deduplication and synonym resolution (nano tier)
    normalize-tags-cluster.md  # Tag cluster confirmation (mini tier)
    normalize-tags-junk.md     # Junk tag detection (nano tier)
    normalize-tags-assign.md   # Orphan tag assignment (mini tier)
    idea-improve.md     # Idea critique with evidence review (mini tier)
    search-terms.md     # Search term extraction (nano tier)
    synthesis-refresh.md  # Synthesis refresh with new evidence (full tier)
  schemas/              # JSON schemas for LLM output validation
    render-paper.json
    render-experiment.json
    render-synthesis.json
    build-moc.json
    build-rank.json
    build-map.json
    query-p1.json
    query-p2.json
    lint-contradictions.json
    lint-crosslinks.json
    normalize-tags.json
    normalize-tags-cluster.json
    normalize-tags-junk.json
    normalize-tags-assign.json
    idea-improve.json
    search-terms.json
    synthesis-refresh.json
  sources/              # Pasted text storage (git-tracked)
  config.yaml           # Wiki configuration (includes LLM model tiers)
  index.jsonl           # JSONL index (entries + fragments)

.claude/
  agents/               # Claude Code agent prompts (for native skills only)
    readme-agent.md     # README generation (dispatched by /sync-docs)
    sync-docs-agent.md  # Design doc reconciliation (dispatched by /sync-docs)
  skills/               # Skill definitions (thin wrappers calling wiki_cli.py)
    ingest/SKILL.md
    render/SKILL.md
    process/SKILL.md    # Native Claude Code skill (reads flow reference files)
    build/SKILL.md
    query/SKILL.md
    save/SKILL.md
    idea/SKILL.md
    web-ingest/SKILL.md
    normalize-tags/SKILL.md
    lint/SKILL.md
    lock/SKILL.md
    unlock/SKILL.md
    wiki-init/SKILL.md
    publish/SKILL.md    # Rebuild and push public branch
    sync-docs/SKILL.md  # Native Claude Code skill (dispatches agents)
    howto/SKILL.md      # Native Claude Code skill (answers how-to questions)

scripts/
  publish_public.py     # Public branch builder (temp clone, strip personal content)

sources/                # Zotero BetterBibTeX JSON exports (read-only input)

pyproject.toml          # Test dependency group and pytest config (scripts use PEP 723 inline metadata, not this)
```

---

## Scripts Reference

All scripts use PEP 723 inline metadata and are run via `uv run`. The CLI layer (`wiki_cli.py`) imports `wiki_util`, `wiki_build`, `wiki_idea`, `wiki_validate`, `wiki_llm`, and `wiki_render` -- these are the only cross-script imports in the system. All other scripts communicate via stdin/stdout/files/exit codes.

| Script | Purpose | Dependencies |
|---|---|---|
| `wiki_cli.py` | Click CLI orchestrator. Each subcommand replaces a former SKILL.md procedure. | click, httpx, jsonschema, openai |
| `wiki_llm.py` | OpenRouter LLM client: sync calls, async batch dispatch, JSON extraction, schema validation, retry logic. | jsonschema, openai |
| `wiki_validate.py` | Domain-specific post-validators: tag/fragment count enforcement, prose field coercion, deduplication. | stdlib only |
| `wiki_util.py` | Pure utilities: slug generation, source-type detection, citation extraction, tag normalization. | stdlib only |
| `wiki_build.py` | Build helpers: listing block construction, staleness banner generation, candidate pre-filtering. | stdlib only |
| `wiki_idea.py` | Idea helpers: page generation, tag merging. | stdlib only |
| `wiki_index.py` | JSONL CRUD, path routing, search, fragment management, connection resolution, MOC prep, staleness checks, structural lint, idea scoring | stdlib only |
| `wiki_render.py` | Source extraction (PDF/text/markdown/ml-journal), page assembly from JSON output, batch operations, connection/banner updates, idea splicing, experiment listing | pymupdf |
| `wiki_config.py` | Reads `config.yaml`, emits JSON to stdout. The single PyYAML boundary. Tag alias merge and persistence. | pyyaml |
| `wiki_embed.py` | Sentence-transformer embedding, cosine similarity scoring, tag clustering, and canonical tag matching. Used by `--rank semantic`, `/idea improve` evidence ranking, and `/normalize-tags` tag deduplication. Downloads `all-MiniLM-L6-v2` (~80MB) on first run. | sentence-transformers |
| `arxiv_fetch.py` | arXiv API client: normalize IDs, fetch metadata as index-ready JSON, download PDFs. The only script with network access (besides `wiki_llm.py`). | stdlib only |
| `zotero_reader.py` | Parse BetterBibTeX JSON exports. Boolean citekey lookup, single-entry parse, diff against index. | stdlib only |

<details>
<summary>wiki_cli.py subcommands</summary>

| Subcommand | Description |
|---|---|
| `lock` | Lock an entry |
| `unlock` | Unlock an entry |
| `save` | File query result as synthesis page |
| `web-ingest` | Fetch arXiv paper, download PDF, add stub |
| `ingest` | Register source (single file, text, directory, or sync) |
| `init` | Scaffold directories, generate config |
| `render` | Single or batch render with LLM |
| `query` | Two-pass research question answering |
| `build` | Full wiki reconciliation pipeline |
| `lint` | Structural + semantic audit |
| `normalize-tags` | Tag drift resolution with LLM proposals |
| `idea create` | Create empty idea page |
| `idea ingest` | Create idea from file/text |
| `idea improve` | Evidence-based critique pipeline |
| `synthesis-refresh` | Refresh a stale synthesis with new evidence (full tier) |

</details>

<details>
<summary>wiki_index.py subcommands</summary>

| Category | Subcommand | Description |
|---|---|---|
| **Index CRUD** | `create-entry` | Build entry JSON from CLI flags (stdout) |
| | `add` | Add entry from stdin (exit 2 on collision) |
| | `get` | Fetch entry by ID |
| | `update` | Set fields on an existing entry |
| | `delete` | Remove entry from index |
| | `exists` | Check presence (exit 0=found, 1=not found, 2=collision) |
| | `list` | Filter by status/type/tag/project |
| | `add-batch` | Bulk-add from JSON array on stdin |
| | `update-batch` | Bulk-update by filter or input file |
| | `wiki-path` | Resolve wiki file path from ID (pure function, no I/O) |
| **Fragments** | `add-fragments-batch` | Bulk-add fragments from stdin |
| | `delete-fragments` | Remove all fragments for a source ID |
| | `delete-fragments-batch` | Remove fragments by ID list from stdin |
| **Build prep** | `resolve-connections` | Compute backlinks and related candidates |
| | `prep-rank-batches` | Partition connection map for LLM ranking |
| | `fast-rank-connections` | Rank candidates by shared-tag count (no LLM) |
| | `prep-moc-batches` | Prepare per-group data for MOC narrative gen |
| | `filter-dirty-mocs` | Filter MOC groups to only those needing rebuild |
| | `update-moc-entries` | Upsert MOC entries from manifest (stdin) |
| **MOC & tags** | `list-moc-groups` | Emit tag->ids and project->ids maps |
| | `get-unresolved-tags` | Find tags absent from alias map (stdin) |
| | `get-tag-counts` | Return {tag: count} for all tags in index |
| | `get-canonical-tags` | Tags with N+ entries (non-fragment, default 10) |
| | `normalize-tags` | Apply alias map to all entry/fragment tags |
| | `sync-frontmatter-tags` | Sync wiki page frontmatter tags with index tags |
| **Query & lint** | `search` | Keyword search across titles and tags |
| | `score-idea` | Score fragments by relevance to an idea |
| | `check-stale` | Find stale synthesis/idea entries |
| | `lint-structural` | Run all mechanical lint checks |
| | `get-fragments-by-type` | Filter fragments by type, grouped by tag |
| | `get-entries-batch` | Return {id, title, tags} for IDs from stdin |
| | `get-orphan-ids` | Extract orphan entry IDs from lint report |

</details>

<details>
<summary>wiki_render.py subcommands</summary>

| Category | Subcommand | Description |
|---|---|---|
| **Source extraction** | `extract-source` | Pull text from PDF, arXiv, Zotero, ml-journal, text, markdown |
| | `extract-zone` | Extract user zone (between frontmatter and `## Notes`) |
| | `list-experiments` | List journal experiments as stub-ready index entries |
| | `extract-notes` | Read preserved Notes section from an existing page |
| **Page assembly** | `assemble` | Build a wiki page from agent output + entry JSON |
| | `assemble-image` | Write an image wiki page directly (no agent output) |
| | `prep-batch` | Read source text for a batch of stub entries |
| | `finalize-batch` | Assemble pages for a completed render batch |
| | `splice-idea` | Splice improve agent output into idea page |
| **Build operations** | `read-journal-refs` | Collect experiment refs from journal JSONLs |
| | `read-batch` | Read page content for a list of entries |
| | `update-connections-batch` | Rewrite Connections sections from connection map |
| | `update-banners-batch` | Add/remove staleness banners |
| | `generate-listings-batch` | Write mechanical MOC listing pages (`--fast` mode) |
| | `generate-topic-index` | Write `wiki/TOPICS.md` from moc-groups JSON |
| | `generate-map` | Write `wiki/MAP.md` from build-agent output |
| **Utilities** | `extract-agent-block` | Pull `===MARKER===...===END===` from stdin |

</details>

<details>
<summary>wiki_embed.py subcommands</summary>

| Subcommand | Description |
|---|---|
| `score-connections` | Re-rank related candidates by MiniLM cosine similarity |
| `score-idea` | Rank fragments by MiniLM cosine similarity against idea text from stdin |
| `cluster-tags` | Cluster tag slugs by MiniLM cosine similarity |
| `match-canonicals` | Match unresolved tags to nearest canonical by MiniLM cosine similarity |

</details>

<details>
<summary>wiki_config.py subcommands</summary>

| Subcommand | Description |
|---|---|
| `get sources` | List all configured sources (JSON array) |
| `get sources --name X` | Get one source by name |
| `get sources --type X` | Filter sources by type |
| `get wiki_dir` | Wiki content directory path |
| `get index_path` | Path to index.jsonl |
| `get default_render_depth` | Default render depth (shallow or deep) |
| `get tag_aliases` | Current alias map |
| `get llm` | LLM config (base_url, models, params, overrides) |
| `set-aliases <file>` | Overwrite tag_aliases from JSON file |
| `merge-aliases <file>` | Merge existing + new alias JSON -> stdout |
| `validate` | Check that all config paths exist |

</details>

<details>
<summary>Exit codes with special meanings</summary>

| Script | Subcommand | Exit code | Meaning |
|---|---|---|---|
| `wiki_index.py` | `add` | 2 | ID collision (not 1) |
| `wiki_index.py` | `exists` | 0 | Found |
| `wiki_index.py` | `exists` | 1 | Not found |
| `wiki_index.py` | `exists` | 2 | Source path mismatch |
| `zotero_reader.py` | `lookup` | 0 | Found |
| `zotero_reader.py` | `lookup` | 1 | Not found |

</details>

For a full subcommand reference organized by workflow stage with examples, see `.wiki/scripts/COMMANDS.md`.

### Test suite

295 tests live in `.wiki/scripts/tests/`. The repo includes a `pyproject.toml` that defines a `test` dependency group (pytest, plus all script dependencies needed for import-based testing). Run tests with:

```bash
uv run --group test pytest
```

Scripts still use PEP 723 inline metadata for their runtime dependencies -- the `pyproject.toml` exists solely for test configuration and is not used at runtime.

---

## Known Gotchas

- **BetterBibTeX JSON only** -- Zotero sources must use BetterBibTeX JSON export, not Better CSL JSON. The schema is different.
- **`wiki_index.py add` exits with code 2 on ID collision**, not 1. Exit 1 means "not found" (for `exists`).
- **`assemble` rejects incomplete agent output in delimiter format** -- the `===END===` marker must be present or assembly fails. `assemble` also accepts JSON directly (used by `/save` and `/render`), in which case no delimiter markers are needed.
- **pymupdf** is resolved by `uv run` from the `# /// script` block in `wiki_render.py`. If `import fitz` fails, `uv` will install it.
- **`## Connections` runs from the header to EOF** -- anything you add after it will be overwritten by `/build`.
- **`<!-- end-notes -->` is required** -- without it, the Notes/Connections boundary is ambiguous and notes may be lost on re-render.
- **MOC entries do not get fragments** -- they are navigational, not source material.
- **Fragment IDs are not stable** -- they change on re-render. Reference the source entry ID, not fragment IDs.
- **`zotero_reader.py lookup` is a boolean check** (exit 0/1). Use `parse --citekey` to retrieve entry data.
- **Tag aliases are cumulative** -- the `tag_aliases` map in `config.yaml` grows with each `/normalize-tags` run. It is auto-managed; do not hand-edit it.
- **`/build --fast` skips groups that already have a rendered narrative page.** This makes it safe to run without clobbering existing LLM-generated narratives. Use `--force` alongside `--fast` to override.
- **`/render` rejects idea entries.** Ideas use `/idea improve` instead of `/render`.
- **Idea pages have a user zone above `## Notes`** -- the original idea text lives there and is preserved by the improve pipeline. The evidence review is spliced below it.
- **`/build` updates locked pages** -- locking only prevents `/render` from overwriting. Connections and staleness banners are always updated.
- **`wiki_embed.py` downloads `all-MiniLM-L6-v2` on first run** (~80MB). Subsequent runs reuse the cached model.
- **`arxiv_fetch.py` and `wiki_llm.py` have network access** -- all other scripts operate on local files only.
- **`OPENROUTER_API_KEY` must be set** -- all LLM commands will fail without it. The env var name is configurable via `llm.api_key_env` in config.
- **`wiki_cli.py` imports sibling modules** -- `wiki_util`, `wiki_build`, `wiki_idea`, `wiki_validate`, `wiki_llm`, and `wiki_render` are imported at runtime. This is the only cross-script coupling in the system; all other scripts remain fully independent.
- **`/normalize-tags` has LLM cost** -- despite being a tag management command, it dispatches LLM calls at the `mini` tier (cluster confirmation) and `nano` tier (junk detection) during its multi-stage pipeline.
- **Default topic page threshold is 10 entries** -- only tags with 10+ entries get MOC pages by default. Use `--min-entries` on `/build` to lower the threshold.
