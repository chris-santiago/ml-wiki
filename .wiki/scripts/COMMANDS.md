# Wiki Scripts Command Reference

> "Which script do I call, and when?" — organized by workflow stage and mapped to skills.
>
> For flag-level detail: `uv run <script> --help` or `uv run <script> <subcommand> --help`

---

## Scripts at a glance

| Script | What it does |
|--------|-------------|
| `wiki_cli.py` | **Orchestration CLI (Click).** Primary entry point. One subcommand per wiki command. Replaces SKILL.md orchestration. |
| `wiki_llm.py` | OpenRouter LLM client via `openai` SDK — prompt loading, JSON dispatch, schema validation, retry, async batch |
| `wiki_validate.py` | Domain-specific post-validators for LLM output (tag/fragment counts, types) |
| `wiki_util.py` | Shared pure functions — slugify, detect_source_type, extract_citations |
| `wiki_build.py` | Build helpers — LISTING block construction from index metadata |
| `wiki_idea.py` | Idea helpers — page template generation |
| `wiki_index.py` | JSONL index CRUD, search, connection graph, MOC prep, tag maintenance, lint |
| `wiki_render.py` | Source extraction, page assembly, batch build operations |
| `wiki_config.py` | Read/write `config.yaml` — the only script that touches PyYAML |
| `zotero_reader.py` | Parse BetterBibTeX JSON exports |
| `arxiv_fetch.py` | Fetch arXiv metadata and download PDFs |
| `wiki_embed.py` | Semantic re-ranking, idea evidence scoring, tag clustering, and canonical matching via MiniLM cosine similarity |

---

## 1. Ingestion — adding new entries

**Skill: `/ingest`**

### Discover what isn't indexed yet

```bash
# Zotero: entries in BBT export not yet in index
uv run .wiki/scripts/zotero_reader.py diff \
  --bbt sources/MyLib.json --index .wiki/index.jsonl

# arXiv: canonicalize an ID before ingesting
uv run .wiki/scripts/arxiv_fetch.py normalize 2301.12345
```

### Create and register an entry

```bash
# Build entry JSON (stdout) and pipe directly to add
uv run .wiki/scripts/wiki_index.py create-entry \
  --id 2301.12345 --type paper --source-type arxiv \
  --title "Attention Is All You Need" --tags transformer,attention --year 2023 \
  | uv run .wiki/scripts/wiki_index.py add

# Check for ID collision before adding (exit 0=found, 1=not found, 2=path mismatch)
uv run .wiki/scripts/wiki_index.py exists 2301.12345

# Fetch arXiv metadata as index-ready JSON and download PDF
uv run .wiki/scripts/arxiv_fetch.py fetch 2301.12345
uv run .wiki/scripts/arxiv_fetch.py download-pdf 2301.12345 wiki/sources/arxiv/2301.12345.pdf
```

### Extract source text for rendering

```bash
# PDF
uv run .wiki/scripts/wiki_render.py extract-source \
  wiki/sources/arxiv/2301.12345.pdf --source-type pdf

# arXiv abstract (no local PDF needed)
uv run .wiki/scripts/wiki_render.py extract-source \
  2301.12345 --source-type arxiv

# Zotero entry
uv run .wiki/scripts/wiki_render.py extract-source \
  <citekey> --source-type zotero

# ml-journal experiment
uv run .wiki/scripts/wiki_render.py extract-source \
  path/to/journal.jsonl:<uuid> --source-type ml-journal

# List all experiments in a journal as stub-ready entries
uv run .wiki/scripts/wiki_render.py list-experiments \
  --journal path/to/journal.jsonl --source-name my-journal
```

---

## 2. Rendering — turning stubs into wiki pages

**Skill: `/render`**

### Single-entry render

```bash
# Preserve existing Notes before re-render
uv run .wiki/scripts/wiki_render.py extract-notes wiki/_pages/2301.12345.md \
  > /tmp/notes.txt

# Assemble page from agent output (writes wiki file, returns result JSON)
uv run .wiki/scripts/wiki_render.py assemble \
  --agent-output /tmp/agent_out.txt \
  --entry-json /tmp/entry.json \
  --notes /tmp/notes.txt \
  --fragments-out /tmp/frags.json

# Update index: status → rendered, attach wiki_path
uv run .wiki/scripts/wiki_index.py update 2301.12345 --status rendered

# Add generated fragments to index
cat /tmp/frags.json | uv run .wiki/scripts/wiki_index.py add-fragments-batch
```

### Batch render (via `wiki_cli.py render --all-stubs`)

Batch rendering uses concurrent LLM calls via `llm_batch`. The pipeline is:

1. **Prep** (sequential): `_render_prep` per entry — validate, extract source, build prompt
2. **LLM** (concurrent): `llm_batch` dispatches async API calls grouped by schema
3. **Finish** (sequential): `_render_finish` per entry — validate, assemble, update index

```bash
# Render all stubs with default concurrency (10 parallel API calls)
uv run .wiki/scripts/wiki_cli.py render --all-stubs

# Render stubs tagged "transformers" with higher concurrency
uv run .wiki/scripts/wiki_cli.py render --tag transformers --concurrency 20

# Deep render all stubs
uv run .wiki/scripts/wiki_cli.py render --all-stubs --depth deep
```

The old subprocess-based batch path (`prep-batch` → `finalize-batch`) is retained in `wiki_render.py` and still used by `/build` for MOC page assembly:

```bash
# Legacy batch path (used by /build for MOC assembly)
uv run .wiki/scripts/wiki_render.py prep-batch \
  --source-configs /tmp/source_configs.json > /tmp/batch_data.json
cat /tmp/batch_output.txt | uv run .wiki/scripts/wiki_render.py finalize-batch
```

### Image pages

```bash
uv run .wiki/scripts/wiki_render.py assemble-image \
  --image-path wiki/assets/diagram.png \
  --description "Architecture diagram for model X" \
  --entry-json /tmp/img_entry.json \
  [--fragments-out /tmp/frags.json]
```

---

## 3. Tag maintenance

**Skill: `/normalize-tags`**

The normalize-tags pipeline is multi-stage: embedding-based clustering, auto-merge, LLM confirmation, and orphan assignment.

```bash
# Preview tag normalization proposals (dry run)
uv run .wiki/scripts/wiki_cli.py normalize-tags --dry-run

# Apply all proposals
uv run .wiki/scripts/wiki_cli.py normalize-tags

# Apply a saved alias map directly
uv run .wiki/scripts/wiki_cli.py normalize-tags --apply /tmp/aliases.json

# Promote aliased tags with 5+ entries to independent canonicals
uv run .wiki/scripts/wiki_cli.py normalize-tags --promote 5
```

### Lower-level tag operations

```bash
# Get current alias map from config
uv run .wiki/scripts/wiki_config.py get tag_aliases > /tmp/existing_aliases.json

# Get full tag counts across the index
uv run .wiki/scripts/wiki_index.py get-tag-counts

# Cluster tags by MiniLM cosine similarity (used by normalize-tags Stage 2)
echo '["transformers", "transformer", "attention"]' \
  | uv run .wiki/scripts/wiki_embed.py cluster-tags

# Get canonical (high-frequency) tags — tags with 10+ entries (non-fragment)
uv run .wiki/scripts/wiki_index.py get-canonical-tags --min-count 10

# Match unresolved tags to nearest canonical by cosine similarity
echo '{"unresolved": ["transformer", "attn"], "canonicals": ["transformers", "attention"]}' \
  | uv run .wiki/scripts/wiki_embed.py match-canonicals --threshold 0.70

# Find tags not yet covered by alias map
uv run .wiki/scripts/wiki_index.py list-moc-groups \
  | uv run .wiki/scripts/wiki_index.py get-unresolved-tags \
      --aliases /tmp/existing_aliases.json

# Merge new aliases into existing map
uv run .wiki/scripts/wiki_config.py merge-aliases \
  --existing /tmp/existing_aliases.json /tmp/new_aliases.json \
  > /tmp/merged_aliases.json

# Write merged map back to config.yaml
uv run .wiki/scripts/wiki_config.py set-aliases /tmp/merged_aliases.json

# Apply aliases to all index entries and fragments in-place
uv run .wiki/scripts/wiki_index.py normalize-tags --aliases /tmp/merged_aliases.json

# Sync tags from wiki page frontmatter back to index entries
uv run .wiki/scripts/wiki_index.py sync-frontmatter-tags [--index .wiki/index.jsonl]
```

---

## 4. Building — connections, MOCs, and navigation pages

**Skill: `/build`** (full LLM pass) · **`/build --fast`** (mechanical only)

```bash
# Full build with default settings (semantic ranking, 10+ entries for topic pages)
uv run .wiki/scripts/wiki_cli.py build

# Fast build with no LLM cost
uv run .wiki/scripts/wiki_cli.py build --fast --rank fast

# Control minimum entries for a tag to get a topic page (default: 10)
uv run .wiki/scripts/wiki_cli.py build --min-entries 5
```

### Compute connections

```bash
# Collect experiment refs from journal sources (input to resolve-connections)
uv run .wiki/scripts/wiki_render.py read-journal-refs \
  --journals /tmp/journal_sources.json > /tmp/journal_refs.json

# Compute full connection graph (backlinks + related_candidates)
uv run .wiki/scripts/wiki_index.py resolve-connections \
  --journal-refs /tmp/journal_refs.json \
  --tag-aliases /tmp/aliases.json \
  > /tmp/connection_map.json

# Rank candidates — semantic (slow, better quality)
uv run .wiki/scripts/wiki_embed.py score-connections --top-n 8 \
  < /tmp/connection_map.json > /tmp/ranked_map.json

# Rank candidates — tag-overlap only (fast, no LLM, used by --fast)
uv run .wiki/scripts/wiki_index.py fast-rank-connections --top-n 8 \
  < /tmp/connection_map.json > /tmp/ranked_map.json

# Rewrite Connections sections in all wiki pages
uv run .wiki/scripts/wiki_render.py update-connections-batch \
  [--index .wiki/index.jsonl] < /tmp/ranked_map.json
```

### MOC narrative generation

```bash
# Filter to MOC groups that need regeneration (membership changed or entries re-rendered since last build)
# --force emits all groups regardless of dirty state
uv run .wiki/scripts/wiki_index.py filter-dirty-mocs \
  [--force] < /tmp/moc_groups.json > /tmp/moc_groups_dirty.json

# Prep per-group data batches for the build agent
uv run .wiki/scripts/wiki_index.py prep-moc-batches \
  --out-prefix /tmp/moc_batch_ \
  --moc-manifest-out /tmp/moc_manifest.json

# (build agent runs on each batch file, writes narrative output)

# Upsert MOC entries from manifest — writes last_built timestamp and member_hash for incremental build tracking
cat /tmp/moc_manifest.json | uv run .wiki/scripts/wiki_index.py update-moc-entries

# --fast mode: generate mechanical listing pages (no agent narrative)
# --force overwrites existing rendered MOC pages (default: skip them)
uv run .wiki/scripts/wiki_render.py generate-listings-batch [--force]

# Regenerate TOPICS.md navigation index
uv run .wiki/scripts/wiki_index.py list-moc-groups \
  | uv run .wiki/scripts/wiki_render.py generate-topic-index

# Regenerate MAP.md from build-agent Mode 3 output
cat /tmp/map_agent_out.txt | uv run .wiki/scripts/wiki_render.py generate-map
```

### Staleness banners

```bash
# Add banners to stale synthesis pages, remove from fresh ones
uv run .wiki/scripts/wiki_index.py check-stale \
  | uv run .wiki/scripts/wiki_render.py update-banners-batch
```

---

## 5. Ideas — creating and improving proposals

**Skill: `/idea`**

### Create and manage idea entries

```bash
# Check for ID collision
uv run .wiki/scripts/wiki_index.py exists idea-my-proposal

# Create an idea entry (type=idea, source-type=text for new, markdown for file)
uv run .wiki/scripts/wiki_index.py create-entry \
  --id idea-my-proposal --type idea --source-type text \
  --title "My Research Proposal" --tags "ml,proposal" \
  --status rendered --wiki-path wiki/ideas/idea-my-proposal.md \
  | uv run .wiki/scripts/wiki_index.py add
```

### Evidence-based improve pipeline

```bash
# Score wiki fragments against idea text by semantic similarity (MiniLM cosine)
uv run .wiki/scripts/wiki_embed.py score-idea \
  --exclude-id idea-my-proposal --top-n 30 < /tmp/idea_text.txt

# (idea-improve-agent runs with idea text + evidence → agent output file)

# Splice agent output into idea page (replaces Evidence Review section)
uv run .wiki/scripts/wiki_render.py splice-idea \
  wiki/ideas/idea-my-proposal.md --agent-output /tmp/agent_out.txt

# Update idea metadata after improve
uv run .wiki/scripts/wiki_index.py update idea-my-proposal \
  --last-improved now --tags "ml,proposal,new-tag"

# Delete old fragments, add new ones
uv run .wiki/scripts/wiki_index.py delete-fragments idea-my-proposal
cat /tmp/frags.json | uv run .wiki/scripts/wiki_index.py add-fragments-batch
```

### Delete an entry

```bash
# Remove an entry from the index entirely
uv run .wiki/scripts/wiki_index.py delete idea-my-proposal
```

---

## 6. Querying and reading

**Skill: `/query`**

```bash
# Keyword search across entry titles, fragment titles, and tags
uv run .wiki/scripts/wiki_index.py search --query "attention transformer" --limit 20

# Fetch a single entry by ID
uv run .wiki/scripts/wiki_index.py get 2301.12345

# List all rendered papers tagged "transformers"
uv run .wiki/scripts/wiki_index.py list --status rendered --type paper --tag transformers

# Read current page content for a set of entries (JSON array of IDs on stdin)
# --format json (default) returns structured JSON; --format text returns delimited plain text
echo '["2301.12345", "1706.03762"]' \
  | uv run .wiki/scripts/wiki_render.py read-batch [--format {json,text}]

# Batch fetch entry objects by ID (JSON array of IDs on stdin)
echo '["2301.12345", "1706.03762"]' \
  | uv run .wiki/scripts/wiki_index.py get-entries-batch

# Find entries whose wiki_path points to a nonexistent file
uv run .wiki/scripts/wiki_index.py get-orphan-ids

# Get fragments by type, optionally grouped by tag
uv run .wiki/scripts/wiki_index.py get-fragments-by-type --types claim,finding
uv run .wiki/scripts/wiki_index.py get-fragments-by-type --types claim,finding --group-by tag
```

### Web acquisition (`/query --web`)

After answering, `--web` discovers uningested arXiv papers and can ingest them, then re-runs the query against the enriched index. The acquisition phase uses:

```bash
# Step 5.5: fetch metadata for each arXiv suggestion (check for existing slug before fetching)
uv run .wiki/scripts/wiki_index.py exists "<slug>"
uv run .wiki/scripts/arxiv_fetch.py fetch "<arxiv_id>" > /tmp/web_suggestion_<n>.json

# Step 6: download PDF and add stub for each selected paper
uv run .wiki/scripts/arxiv_fetch.py download-pdf "<arxiv_id>" "wiki/sources/arxiv/<arxiv_id>.pdf"
uv run .wiki/scripts/wiki_index.py add < /tmp/web_suggestion_<n>.json
# then invoke /process <slug> to render and update backlinks

# Step 7: re-run Steps 1–4 against the enriched index (no second web pass)
```

Web-acquired entries become first-class wiki entries. They are never injected directly into synthesis — only ingested papers are cited.

---

## 7. Synthesis Refresh

**Skill: (invoked via `/synthesis-refresh <id>`)**

Refreshes a stale synthesis with new evidence from recently added entries.

```bash
# Refresh a stale synthesis entry
uv run .wiki/scripts/wiki_cli.py synthesis-refresh syn-embedding-strategies

# Check which syntheses are stale before refreshing
uv run .wiki/scripts/wiki_index.py check-stale
```

The command:
1. Reads the existing `## Synthesis` section from the wiki page
2. Identifies new entries via `check-stale` (2+ shared tag threshold)
3. Reads page content for new entries via `read-batch`
4. Invokes the `synthesis-refresh` agent (model tier: full) to integrate new evidence
5. Updates the wiki page (replaces `## Synthesis` and `## Sources`, strips staleness banner)
6. Updates index (rendered timestamp, tags, references) and replaces fragments

---

## 8. Lint

**Skill: `/lint`**

```bash
# Run all mechanical structural checks
uv run .wiki/scripts/wiki_index.py lint-structural --wiki-dir wiki

# Full lint (structural + LLM semantic passes)
uv run .wiki/scripts/wiki_cli.py lint

# Structural checks only, no LLM
uv run .wiki/scripts/wiki_cli.py lint --mechanical-only

# Auto-fix orphan fragments and broken paths
uv run .wiki/scripts/wiki_cli.py lint --fix

# Control LLM concurrency and minimum fragment threshold for contradiction check
uv run .wiki/scripts/wiki_cli.py lint --concurrency 20 --min-frags 3
```

The contradiction detection pass uses `llm_batch` for per-tag concurrent dispatch. Each tag with `--min-frags` or more claim/finding fragments gets its own LLM call, dispatched concurrently up to `--concurrency` (default 10).

---

## 9. Utilities and config

```bash
# Resolve the wiki file path for any entry ID (pure function, no I/O)
uv run .wiki/scripts/wiki_index.py wiki-path 2301.12345
# → wiki/_pages/2301.12345.md

# Validate all paths in config.yaml
uv run .wiki/scripts/wiki_config.py validate

# Extract user zone content (between frontmatter and ## Notes)
uv run .wiki/scripts/wiki_render.py extract-zone wiki/_pages/2301.12345.md

# Zotero: boolean citekey lookup (exit 0=found, 1=not found)
uv run .wiki/scripts/zotero_reader.py lookup --bbt sources/MyLib.json vaswani2017attention

# Zotero: get normalized entry data for one citekey
uv run .wiki/scripts/zotero_reader.py parse --bbt sources/MyLib.json \
  --citekey vaswani2017attention
```

### Wiki CLI — primary entry point

All wiki commands are now available via `wiki_cli.py`:

```bash
# Most common operations
uv run .wiki/scripts/wiki_cli.py ingest <input>              # Register a source
uv run .wiki/scripts/wiki_cli.py render <id>                 # Generate wiki page
uv run .wiki/scripts/wiki_cli.py render --all-stubs          # Batch render (concurrent)
uv run .wiki/scripts/wiki_cli.py query "question"            # Answer from wiki
uv run .wiki/scripts/wiki_cli.py build                       # Reconcile connections + MOCs
uv run .wiki/scripts/wiki_cli.py lint                        # Structural + semantic audit
uv run .wiki/scripts/wiki_cli.py idea create "title"         # Create idea page
uv run .wiki/scripts/wiki_cli.py idea improve <id>           # Evidence-based critique
uv run .wiki/scripts/wiki_cli.py normalize-tags --dry-run    # Preview tag normalization
uv run .wiki/scripts/wiki_cli.py synthesis-refresh <id>      # Refresh stale synthesis
uv run .wiki/scripts/wiki_cli.py web-ingest 2301.12345       # Ingest arXiv paper

# Full help for any subcommand
uv run .wiki/scripts/wiki_cli.py <command> --help
```

---

## stdin/stdout conventions

Most commands that accept lists take **JSON on stdin** and emit **JSON to stdout**, making them composable:

```
source of IDs  →  filter/transform  →  consumer
list (--format ids)  |  jq …  |  read-batch
list-moc-groups      |  get-unresolved-tags
resolve-connections  |  score-connections  |  update-connections-batch
```

Exit codes with specific meanings:
- `add` — exit **2** on ID collision (not 1)
- `exists` — exit **0** found · **1** not found · **2** source_path mismatch
- `zotero_reader.py lookup` — exit **0** found · **1** not found
