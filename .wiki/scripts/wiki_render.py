# /// script
# requires-python = ">=3.11"
# dependencies = ["pymupdf"]
# ///

import argparse
import asyncio
import contextlib
import json
import os
import re
import sys
from datetime import datetime, timezone


@contextlib.contextmanager
def _suppress_mupdf_stderr():
    """Redirect fd 2 to /dev/null around MuPDF calls that emit C-level warnings."""
    fd = sys.stderr.fileno()
    saved = os.dup(fd)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, fd)
    os.close(devnull)
    try:
        yield
    finally:
        os.dup2(saved, fd)
        os.close(saved)

VALID_FRAGMENT_TYPES = {"claim", "method", "finding", "dataset", "metric", "question", "definition", "description"}
STALENESS_BANNER_MARKER = "<!-- wiki-staleness-banner -->"
NOTES_START = "## Notes"
NOTES_END = "<!-- end-notes -->"
CONNECTIONS_START = "## Connections"

REQUIRED_BLOCKS = {
    "paper": {"SUMMARY", "KEY_CLAIMS", "METHODS", "RESULTS", "LIMITATIONS", "TAGS_FINALIZED", "FRAGMENTS"},
    "experiment": {"HYPOTHESIS", "SETUP", "RESULTS", "IMPLICATIONS", "TAGS_FINALIZED", "FRAGMENTS"},
    "synthesis": {"SYNTHESIS", "SOURCES", "TAGS_FINALIZED", "FRAGMENTS"},
    "moc": {"NARRATIVE", "SUBGROUPS", "TENSIONS", "EVOLUTION", "LISTING"},
}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(text, max_words=6):
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    words = text.split()[:max_words]
    return "-".join(w for w in words if w)


def _scan_journal(journal_path, uuid):
    expanded = os.path.expanduser(journal_path)
    experiments = []
    target_entry = None
    with open(expanded) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    e = json.loads(line)
                    if e.get("type") == "experiment":
                        experiments.append(e)
                        if e.get("id") == uuid:
                            target_entry = e
                except json.JSONDecodeError:
                    pass
    return target_entry, experiments


def _format_experiment_text(entry, all_experiments, uuid):
    parts = [f"Experiment: {entry.get('id', '')}"]
    for key in ("description", "metric", "result", "verdict"):
        val = entry.get(key)
        if val:
            parts.append(f"{key.title()}: {val}")

    linked_id = entry.get("linked_hypothesis_id")
    if linked_id:
        siblings = [e for e in all_experiments
                    if e.get("linked_hypothesis_id") == linked_id
                    and e.get("id") != uuid]
        if siblings:
            parts.append(f"\nSibling experiments (linked_hypothesis_id={linked_id}):")
            for sib in siblings:
                sib_parts = [f"  - id: {sib.get('id', '')}"]
                for key in ("description", "verdict", "result"):
                    val = sib.get(key)
                    if val:
                        truncated = val[:200] + ("..." if len(val) > 200 else "")
                        sib_parts.append(f"    {key}: {truncated}")
                parts.append("\n".join(sib_parts))

    return "\n".join(parts)


def resolve_wiki_path(entry_id):
    if entry_id.startswith("topic-"):
        return f"wiki/topics/{entry_id}.md"
    elif entry_id.startswith("project-"):
        return f"wiki/projects/{entry_id}.md"
    elif entry_id.startswith("syn-") or entry_id.startswith("synthesis-"):
        return f"wiki/syntheses/{entry_id}.md"
    elif entry_id.startswith("img-"):
        return f"wiki/images/{entry_id}.md"
    elif entry_id.startswith("exp-"):
        return f"wiki/experiments/{entry_id}.md"
    elif entry_id.startswith("idea-"):
        return f"wiki/ideas/{entry_id}.md"
    else:
        return f"wiki/_pages/{entry_id}.md"


# ---------------------------------------------------------------------------
# Source extraction
# ---------------------------------------------------------------------------

def cmd_extract_source(args):
    source_type = args.source_type
    source_path = args.source_path

    if source_type in ("pdf", "zotero"):
        try:
            import fitz  # pymupdf
            fitz.TOOLS.mupdf_display_errors(False)
        except ImportError:
            print("ERROR: pymupdf not available. Install with: uv add pymupdf", file=sys.stderr)
            sys.exit(1)
        expanded = os.path.expanduser(source_path)
        if not os.path.exists(expanded):
            print(f"ERROR: file not found: {source_path}", file=sys.stderr)
            sys.exit(1)
        with _suppress_mupdf_stderr():
            doc = fitz.open(expanded)
            text = "\n\n".join(page.get_text() for page in doc)
        print(text)

    elif source_type in ("text", "markdown"):
        expanded = os.path.expanduser(source_path)
        if not os.path.exists(expanded):
            print(f"ERROR: file not found: {source_path}", file=sys.stderr)
            sys.exit(1)
        with open(expanded) as f:
            print(f.read())

    elif source_type == "url":
        # URLs require prior fetching — return a clear message
        print(f"[URL source: {source_path}]\nURL content must be fetched and cached before extraction.", file=sys.stderr)
        sys.exit(1)

    elif source_type == "ml-journal":
        sep = source_path.rfind(":")
        if sep == -1:
            print(f"ERROR: ml-journal source_path must be '<journal_path>:<uuid>', got: {source_path}", file=sys.stderr)
            sys.exit(1)
        journal_path = source_path[:sep]
        uuid = source_path[sep + 1:]

        expanded = os.path.expanduser(journal_path)
        if not os.path.exists(expanded):
            print(f"ERROR: journal file not found: {journal_path}", file=sys.stderr)
            sys.exit(1)

        target_entry, experiments = _scan_journal(journal_path, uuid)
        if target_entry is None:
            print(f"ERROR: entry '{uuid}' not found in journal", file=sys.stderr)
            sys.exit(1)

        print(_format_experiment_text(target_entry, experiments, uuid))

    else:
        print(f"ERROR: unknown source_type: {source_type}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Notes extraction
# ---------------------------------------------------------------------------

def cmd_extract_notes(args):
    wiki_path = args.wiki_path
    if not wiki_path or not os.path.exists(wiki_path):
        print("")  # No existing page — return empty
        return
    with open(wiki_path) as f:
        content = f.read()

    start_idx = content.find(NOTES_START)
    end_idx = content.find(NOTES_END)
    if start_idx == -1 or end_idx == -1:
        print("")
        return

    notes_content = content[start_idx + len(NOTES_START):end_idx].strip()
    print(notes_content)


def cmd_extract_zone(args):
    """Extract user zone: content between frontmatter and ## Notes."""
    wiki_path = args.wiki_path
    if not wiki_path or not os.path.exists(wiki_path):
        print("")
        return
    with open(wiki_path) as f:
        content = f.read()

    # Skip frontmatter
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            body = parts[2]

    # Take everything before ## Notes
    notes_idx = body.find(NOTES_START)
    if notes_idx != -1:
        body = body[:notes_idx]

    print(body.strip())


# ---------------------------------------------------------------------------
# Agent output parsing
# ---------------------------------------------------------------------------

def _find_block(raw, marker):
    raw_stripped = raw.strip()
    if raw_stripped.startswith('{'):
        try:
            data = json.loads(raw_stripped)
            return data.get(marker.lower())
        except json.JSONDecodeError:
            pass
    start_tag = f"==={marker}==="
    start = raw.find(start_tag)
    if start == -1:
        return None
    end = raw.find("===END===", start + len(start_tag))
    if end == -1:
        return None
    return raw[start + len(start_tag):end].strip()


def _json_to_blocks(data):
    """Convert a JSON dict to the blocks dict expected by render commands."""
    blocks = {}
    for k, v in data.items():
        key = k.upper()
        if isinstance(v, str):
            blocks[key] = v
        elif isinstance(v, list):
            if v and isinstance(v[0], dict):
                blocks[key] = json.dumps(v)  # FRAGMENTS: downstream does json.loads()
            else:
                blocks[key] = ", ".join(str(x) for x in v)  # SOURCES/TAGS: downstream splits on ","
        elif v is not None:
            blocks[key] = str(v)
    return blocks


def parse_agent_output(text):
    """Parse agent output. Accepts JSON objects or ===BLOCK_NAME=== delimiter format."""
    text_stripped = text.strip()
    if text_stripped.startswith('{'):
        try:
            data = json.loads(text_stripped)
            return _json_to_blocks(data), True
        except json.JSONDecodeError:
            pass

    blocks = {}
    current_block = None
    current_lines = []

    for line in text_stripped.splitlines():
        if re.match(r'^===([A-Z_]+)===$', line.strip()):
            block_name = line.strip()[3:-3]
            if block_name == "END":
                if current_block:
                    blocks[current_block] = "\n".join(current_lines).strip()
                return blocks, True  # has END marker
            if current_block:
                blocks[current_block] = "\n".join(current_lines).strip()
            current_block = block_name
            current_lines = []
        elif current_block is not None:
            current_lines.append(line)

    # No END marker found
    if current_block:
        blocks[current_block] = "\n".join(current_lines).strip()
    return blocks, False


def validate_blocks(blocks, entry_type, has_end, entry_id=""):
    """Validate parsed blocks. Returns list of errors."""
    errors = []
    if not has_end:
        errors.append("missing ===END=== marker (incomplete generation)")

    required = REQUIRED_BLOCKS.get(entry_type, set())
    missing = required - set(blocks.keys())
    if missing:
        errors.append(f"missing required blocks: {sorted(missing)}")

    for block_name, content in blocks.items():
        if not content.strip():
            errors.append(f"block {block_name} is empty")

    if "TAGS_FINALIZED" in blocks:
        tags_raw = blocks["TAGS_FINALIZED"]
        if not tags_raw.strip():
            errors.append("TAGS_FINALIZED is empty")

    if "FRAGMENTS" in blocks:
        try:
            frags = json.loads(_strip_code_fence(blocks["FRAGMENTS"]))
            for frag in frags:
                ftype = frag.get("type", "")
                if ftype not in VALID_FRAGMENT_TYPES:
                    errors.append(f"invalid fragment type '{ftype}' in FRAGMENTS")
                for field in ("seq", "type", "title"):
                    if field not in frag:
                        errors.append(f"fragment missing required field '{field}'")
        except json.JSONDecodeError as e:
            errors.append(f"FRAGMENTS is not valid JSON: {e}")

    return errors


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------

def render_paper_page(entry, blocks, preserved_notes):
    front_matter = _front_matter(entry)
    citation_line = f"**Citation:** {entry.get('citation', '')}" if entry.get("citation") else ""
    pdf_line = f"**PDF:** [Open PDF]({entry['pdf_uri']})" if entry.get("pdf_uri") else ""
    zotero_line = f"**Zotero:** [View entry]({entry['zotero_uri']})" if entry.get("zotero_uri") else ""
    links = "\n".join(filter(None, [citation_line, pdf_line, zotero_line]))

    return f"""{front_matter}
# {entry.get('title', 'Untitled')}

{links}

## Summary

{blocks.get('SUMMARY', '')}

## Key Claims

{blocks.get('KEY_CLAIMS', '')}

## Methods

{blocks.get('METHODS', '')}

## Results

{blocks.get('RESULTS', '')}

## Limitations

{blocks.get('LIMITATIONS', '')}

## Notes

<!-- user-owned section — preserved across re-renders -->

{preserved_notes}

{NOTES_END}

## Connections

<!-- managed by /build — do not edit manually -->
"""


def render_experiment_page(entry, blocks, preserved_notes):
    front_matter = _front_matter(entry)
    source_line = f"**Source:** [View in ml-journal]({entry.get('id', '')})" if entry.get("source_name") else ""

    return f"""{front_matter}
# {entry.get('title', 'Untitled')}

{source_line}

## Hypothesis

{blocks.get('HYPOTHESIS', '')}

## Setup

{blocks.get('SETUP', '')}

## Results

{blocks.get('RESULTS', '')}

## Implications

{blocks.get('IMPLICATIONS', '')}

## Notes

<!-- user-owned section — preserved across re-renders -->

{preserved_notes}

{NOTES_END}

## Connections

<!-- managed by /build — do not edit manually -->
"""


def render_synthesis_page(entry, blocks, preserved_notes):
    front_matter = _front_matter(entry)
    query_line = f"**Query:** \"{entry.get('title', '')}\""
    date_line = f"**Generated:** {now_iso()[:10]}"

    sources_raw = blocks.get('SOURCES', '')
    source_links = "\n".join(
        f"- [[{sid.strip()}]]"
        for sid in sources_raw.split(",")
        if sid.strip()
    )

    return f"""{front_matter}
# {entry.get('title', 'Untitled')}

{query_line}
{date_line}

## Synthesis

{blocks.get('SYNTHESIS', '')}

## Sources

{source_links}

## Notes

<!-- user-owned section — preserved across re-renders -->

{preserved_notes}

{NOTES_END}

## Connections

<!-- managed by /build — do not edit manually -->
"""


def render_moc_page(entry, blocks):
    front_matter = _front_matter(entry)
    title = entry.get("title", entry.get("id", "Untitled"))

    return f"""{front_matter}
# {title}

## Overview

{blocks.get('NARRATIVE', '')}

## Subgroups

{blocks.get('SUBGROUPS', '')}

## Tensions & Open Questions

{blocks.get('TENSIONS', '')}

## Evolution

{blocks.get('EVOLUTION', '')}

## Entries

{blocks.get('LISTING', '')}
"""


def render_image_page(entry, image_path, description):
    front_matter = _front_matter(entry)
    title = entry.get("title", entry.get("id", "Untitled"))
    filename = os.path.basename(image_path)

    return f"""{front_matter}
# {title}

![[{filename}]]

## Description

{description}

## Notes

<!-- user-owned section — preserved across re-renders -->

{NOTES_END}

## Connections

<!-- managed by /build — do not edit manually -->
"""


def cmd_assemble_image(args):
    with open(args.entry_json) as f:
        entry = json.load(f)

    entry_id = entry.get("id", "")
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()] or entry.get("tags") or []
    entry["tags"] = tags

    output_path = resolve_wiki_path(entry_id)
    page_content = render_image_page(entry, args.image_path, args.description)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tmp = output_path + ".tmp"
    with open(tmp, "w") as f:
        f.write(page_content)
    os.rename(tmp, output_path)

    frag_id = f"frag-{entry_id}-01"
    fragments = [{
        "id": frag_id,
        "type": "description",
        "title": f"Description: {entry.get('title', entry_id)}",
        "tags": tags,
        "project": entry.get("project"),
        "references": [entry_id],
        "ingested": now_iso(),
    }]

    if args.fragments_out:
        with open(args.fragments_out, "w") as f:
            json.dump(fragments, f, ensure_ascii=False)

    result = {
        "wiki_path": output_path,
        "tags_finalized": tags,
        "fragments": fragments,
    }
    print(json.dumps(result, ensure_ascii=False))


def _front_matter(entry):
    year = entry.get("year")
    tags = entry.get("tags") or []
    project = entry.get("project")
    source_name = entry.get("source_name")

    lines = [
        "---",
        f"id: {entry.get('id', '')}",
        f"type: {entry.get('type', '')}",
    ]
    if year:
        lines.append(f"year: {year}")
    if tags:
        lines.append(f"tags: [{', '.join(tags)}]")
    if project:
        lines.append(f"project: {project}")
    if source_name:
        lines.append(f"source_name: {source_name}")
    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

_RENDERERS = {
    "paper": render_paper_page,
    "experiment": render_experiment_page,
    "synthesis": render_synthesis_page,
    "moc": render_moc_page,
}


def _strip_code_fence(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _assemble_page(entry, entry_type, blocks, preserved_notes):
    entry_id = entry.get("id", "")
    tags_finalized = [t.strip() for t in blocks.get("TAGS_FINALIZED", "").split(",") if t.strip()]

    fragments_raw = json.loads(_strip_code_fence(blocks.get("FRAGMENTS", "[]")))
    fragments = []
    for frag in fragments_raw:
        frag_id = f"frag-{entry_id}-{frag['seq']:02d}"
        fragments.append({
            "id": frag_id,
            "type": frag["type"],
            "title": frag["title"],
            "tags": tags_finalized,
            "project": entry.get("project"),
            "references": [entry_id],
            "ingested": now_iso(),
        })

    renderer = _RENDERERS.get(entry_type, render_paper_page)
    if entry_type == "moc":
        page_content = renderer(entry, blocks)
    else:
        page_content = renderer(entry, blocks, preserved_notes)

    output_path = resolve_wiki_path(entry_id)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tmp = output_path + ".tmp"
    with open(tmp, "w") as f:
        f.write(page_content)
    os.rename(tmp, output_path)

    return {
        "wiki_path": output_path,
        "tags_finalized": tags_finalized,
        "fragments": fragments,
    }


def _assemble_page_from_json(entry, entry_type, data, preserved_notes, output_dir=None):
    """Assemble wiki page from validated JSON dict (lowercase keys)."""
    entry_id = entry.get("id", "")
    tags_finalized = data.get("tags", [])

    fragments = []
    for frag in data.get("fragments", []):
        frag_id = f"frag-{entry_id}-{frag['seq']:02d}"
        fragments.append({
            "id": frag_id,
            "type": frag["type"],
            "title": frag["title"],
            "tags": tags_finalized,
            "project": entry.get("project"),
            "references": [entry_id],
            "ingested": now_iso(),
        })

    # Remap to UPPERCASE keys for existing template functions
    key_map = {
        "paper": {"summary": "SUMMARY", "key_claims": "KEY_CLAIMS", "methods": "METHODS",
                  "results": "RESULTS", "limitations": "LIMITATIONS"},
        "experiment": {"hypothesis": "HYPOTHESIS", "setup": "SETUP",
                       "results": "RESULTS", "implications": "IMPLICATIONS"},
        "synthesis": {"synthesis": "SYNTHESIS"},
    }
    blocks = {}
    for json_key, block_key in key_map.get(entry_type, {}).items():
        if json_key in data:
            blocks[block_key] = data[json_key]

    if entry_type == "synthesis" and "sources" in data:
        blocks["SOURCES"] = ", ".join(data["sources"])

    renderer = _RENDERERS.get(entry_type, render_paper_page)
    if entry_type == "moc":
        page_content = renderer(entry, blocks)
    else:
        page_content = renderer(entry, blocks, preserved_notes)

    if output_dir:
        output_path = os.path.join(output_dir, os.path.basename(resolve_wiki_path(entry_id)))
    else:
        output_path = resolve_wiki_path(entry_id)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tmp = output_path + ".tmp"
    with open(tmp, "w") as f:
        f.write(page_content)
    os.rename(tmp, output_path)

    return {
        "wiki_path": output_path,
        "tags_finalized": tags_finalized,
        "fragments": fragments,
    }


def cmd_assemble(args):
    with open(args.agent_output) as f:
        agent_text = f.read()
    with open(args.entry_json) as f:
        entry = json.load(f)

    preserved_notes = ""
    if args.notes and os.path.exists(args.notes):
        with open(args.notes) as f:
            preserved_notes = f.read().strip()

    entry_id = entry.get("id", "")
    entry_type = args.type or entry.get("type", "paper")

    blocks, has_end = parse_agent_output(agent_text)
    errors = validate_blocks(blocks, entry_type, has_end, entry_id)
    if errors:
        error_payload = {"id": entry_id, "error": "agent output validation failed", "details": errors}
        print(json.dumps(error_payload), file=sys.stderr)
        sys.exit(1)

    result = _assemble_page(entry, entry_type, blocks, preserved_notes)

    if args.fragments_out:
        with open(args.fragments_out, "w") as f:
            json.dump(result["fragments"], f, ensure_ascii=False)

    print(json.dumps(result, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Batch operations
# ---------------------------------------------------------------------------

def cmd_prep_batch(args):
    raw = sys.stdin.read().strip()
    try:
        entry_list = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid JSON: {e}"}), file=sys.stderr)
        sys.exit(1)

    # Load source configs if provided
    source_configs = {}
    if args.source_configs:
        with open(args.source_configs) as f:
            for cfg in json.load(f):
                source_configs[cfg["name"]] = cfg

    manifest = []
    for entry in entry_list:
        eid = entry["id"]
        source_name = entry.get("source_name", "")
        source_type = entry.get("source_type", "")
        source_path = entry.get("source_path", "")
        wiki_path = entry.get("wiki_path")

        # Extract source text
        source_text = ""
        if source_type in ("pdf", "zotero") and source_path:
            try:
                import fitz
                fitz.TOOLS.mupdf_display_errors(False)
                expanded = os.path.expanduser(source_path)
                if os.path.exists(expanded):
                    with _suppress_mupdf_stderr():
                        doc = fitz.open(expanded)
                        source_text = "\n\n".join(page.get_text() for page in doc)
            except Exception as e:
                source_text = f"[extraction failed: {e}]"
        elif source_type in ("text", "markdown") and source_path:
            expanded = os.path.expanduser(source_path)
            if os.path.exists(expanded):
                with open(expanded) as f:
                    source_text = f.read()

        elif source_type == "ml-journal" and source_path:
            sep = source_path.rfind(":")
            if sep != -1:
                journal_path = source_path[:sep]
                uuid = source_path[sep + 1:]
                try:
                    target_entry, experiments = _scan_journal(journal_path, uuid)
                    if target_entry:
                        source_text = _format_experiment_text(target_entry, experiments, uuid)
                except FileNotFoundError:
                    pass

        # Extract preserved notes
        preserved_notes = ""
        if wiki_path and os.path.exists(wiki_path):
            with open(wiki_path) as f:
                content = f.read()
            start_idx = content.find(NOTES_START)
            end_idx = content.find(NOTES_END)
            if start_idx != -1 and end_idx != -1:
                preserved_notes = content[start_idx + len(NOTES_START):end_idx].strip()

        tags = entry.get("tags") or []
        focus = entry.get("project") or (tags[0] if tags else None)

        manifest.append({
            "id": eid,
            "source_text": source_text,
            "preserved_notes": preserved_notes,
            "focus": focus,
            "metadata": entry,
        })

    print(json.dumps(manifest, ensure_ascii=False))


def cmd_finalize_batch(args):
    raw = sys.stdin.read().strip()
    try:
        batch = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid JSON: {e}"}), file=sys.stderr)
        sys.exit(1)

    successes, failures = [], []

    for item in batch:
        eid = item.get("id", "")
        agent_output_file = item.get("agent_output_file")
        entry_json_file = item.get("entry_json_file")
        notes_file = item.get("notes_file")

        if not agent_output_file or not entry_json_file:
            failures.append({"id": eid, "error": "missing agent_output_file or entry_json_file"})
            continue

        try:
            with open(agent_output_file) as f:
                agent_text = f.read()
            with open(entry_json_file) as f:
                entry = json.load(f)

            preserved_notes = ""
            if notes_file and os.path.exists(notes_file):
                with open(notes_file) as f:
                    preserved_notes = f.read().strip()

            entry_type = entry.get("type", "paper")
            blocks, has_end = parse_agent_output(agent_text)
            errors = validate_blocks(blocks, entry_type, has_end, eid)
            if errors:
                failures.append({"id": eid, "error": "; ".join(errors)})
                continue

            result = _assemble_page(entry, entry_type, blocks, preserved_notes)
            successes.append({"id": eid, **result})

        except Exception as e:
            failures.append({"id": eid, "error": str(e)})

    print(json.dumps({"successes": successes, "failures": failures}, ensure_ascii=False))


def cmd_update_connections_batch(args):
    raw = sys.stdin.read().strip()
    try:
        connection_map = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid JSON: {e}"}), file=sys.stderr)
        sys.exit(1)

    # connection_map: {entry_id: {references, cited_by, related}}
    # We need wiki_paths — load index to get them
    index_path = args.index or ".wiki/index.jsonl"
    entries = {}
    if os.path.exists(index_path):
        with open(index_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    e = json.loads(line)
                    entries[e["id"]] = e

    updated = 0
    for eid, conns in connection_map.items():
        entry = entries.get(eid, {})
        wiki_path = entry.get("wiki_path") or resolve_wiki_path(eid)
        if not os.path.exists(wiki_path):
            continue

        with open(wiki_path) as f:
            content = f.read()

        conn_idx = content.find(f"\n{CONNECTIONS_START}")
        if conn_idx == -1:
            conn_idx = content.find(CONNECTIONS_START)
        if conn_idx == -1:
            continue

        references = conns.get("references", [])
        cited_by = conns.get("cited_by", [])
        related = conns.get("related", conns.get("related_candidates", []))
        if isinstance(related, list) and related and isinstance(related[0], dict):
            related_ids = [r.get("id") or r.get("entry_id", "") for r in related]
        else:
            related_ids = related

        connections_section = f"""{CONNECTIONS_START}

<!-- managed by /build — do not edit manually -->

**References:** {", ".join(f"[[{r}]]" for r in references) if references else "—"}
**Cited by:** {", ".join(f"[[{r}]]" for r in cited_by) if cited_by else "—"}
**Related:** {", ".join(f"[[{r}]]" for r in related_ids) if related_ids else "—"}
"""
        new_content = content[:conn_idx].rstrip() + "\n\n" + connections_section

        tmp = wiki_path + ".tmp"
        with open(tmp, "w") as f:
            f.write(new_content)
        os.rename(tmp, wiki_path)
        updated += 1

    print(json.dumps({"updated": updated}))


def cmd_update_banners_batch(args):
    raw = sys.stdin.read().strip()
    try:
        banner_list = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid JSON: {e}"}), file=sys.stderr)
        sys.exit(1)

    updated = 0
    for item in banner_list:
        wiki_path = item.get("wiki_path")
        banner_text = item.get("banner_text")
        if not wiki_path or not os.path.exists(wiki_path):
            continue

        with open(wiki_path) as f:
            content = f.read()

        # Remove any existing banner
        if STALENESS_BANNER_MARKER in content:
            # Remove the banner line and the blank line after it
            content = re.sub(
                r'\n?' + re.escape(STALENESS_BANNER_MARKER) + r'.*?\n\n?',
                '\n',
                content,
                flags=re.DOTALL
            )

        # Find insertion point — after front matter
        if banner_text:
            full_banner = f"\n{STALENESS_BANNER_MARKER}\n{banner_text}\n"
            fm_end = content.find("---", 3)
            if fm_end != -1:
                insert_at = content.find("\n", fm_end) + 1
                content = content[:insert_at] + full_banner + content[insert_at:]
            else:
                content = full_banner + content

        tmp = wiki_path + ".tmp"
        with open(tmp, "w") as f:
            f.write(content)
        os.rename(tmp, wiki_path)
        updated += 1

    print(json.dumps({"updated": updated}))


EVIDENCE_REVIEW_START = "## Evidence Review"


def cmd_splice_idea(args):
    wiki_path = args.wiki_path
    if not os.path.exists(wiki_path):
        print(json.dumps({"error": f"file not found: {wiki_path}"}), file=sys.stderr)
        sys.exit(1)

    with open(args.agent_output) as f:
        agent_text = f.read()

    blocks, has_end = parse_agent_output(agent_text)
    if not has_end:
        print(json.dumps({"error": "agent output missing ===END=== marker"}), file=sys.stderr)
        sys.exit(1)

    with open(wiki_path) as f:
        content = f.read()

    notes_end_idx = content.find(NOTES_END)
    if notes_end_idx == -1:
        print(json.dumps({"error": f"missing {NOTES_END} marker in {wiki_path}"}), file=sys.stderr)
        sys.exit(1)
    after_notes = notes_end_idx + len(NOTES_END)

    conn_idx = content.find(f"\n{CONNECTIONS_START}", after_notes)
    if conn_idx == -1:
        conn_idx = content.find(CONNECTIONS_START, after_notes)
    connections_section = ""
    if conn_idx != -1:
        connections_section = content[conn_idx:].lstrip("\n")

    supporting = blocks.get("SUPPORTING", "No supporting evidence found.")
    contradicting = blocks.get("CONTRADICTING", "No contradicting evidence found.")
    gaps = blocks.get("GAPS", "No gaps identified.")
    suggestions = blocks.get("SUGGESTIONS", "No suggestions at this time.")
    sources = blocks.get("SOURCES", "")

    source_links = ", ".join(f"[[{s.strip()}]]" for s in sources.split(",") if s.strip()) if sources else "—"

    evidence_section = f"""{EVIDENCE_REVIEW_START}

<!-- managed by /idea improve — replaced on each run -->

### Supporting Evidence
{supporting}

### Contradicting Evidence
{contradicting}

### Gaps
{gaps}

### Suggestions
{suggestions}

### Sources Consulted
{source_links}
"""

    before_evidence = content[:after_notes]
    new_content = before_evidence + "\n\n" + evidence_section
    if connections_section:
        new_content = new_content.rstrip("\n") + "\n\n" + connections_section
    else:
        new_content = new_content.rstrip("\n") + "\n\n" + CONNECTIONS_START + "\n"

    tmp = wiki_path + ".tmp"
    with open(tmp, "w") as f:
        f.write(new_content)
    os.rename(tmp, wiki_path)

    result = {
        "spliced": True,
        "blocks_found": list(blocks.keys()),
    }
    if "TAGS_FINALIZED" in blocks:
        result["tags_finalized"] = [t.strip() for t in blocks["TAGS_FINALIZED"].split(",") if t.strip()]
    if "FRAGMENTS" in blocks:
        try:
            result["fragments"] = json.loads(_strip_code_fence(blocks["FRAGMENTS"]))
        except json.JSONDecodeError:
            result["fragments_parse_error"] = blocks["FRAGMENTS"]

    print(json.dumps(result, ensure_ascii=False))


def cmd_read_batch(args):
    raw = sys.stdin.read().strip()
    try:
        entry_list = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid JSON: {e}"}), file=sys.stderr)
        sys.exit(1)

    # Build index lookup for entries that don't include wiki_path inline
    index_path = getattr(args, "index", None) or ".wiki/index.jsonl"
    index_map = {}
    if os.path.exists(index_path):
        with open(index_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        e = json.loads(line)
                        index_map[e["id"]] = e.get("wiki_path")
                    except (json.JSONDecodeError, KeyError):
                        pass

    results = []
    for item in entry_list:
        # Accept both plain string IDs and {"id": ...} objects
        if isinstance(item, str):
            eid = item
            wiki_path = None
        else:
            eid = item.get("id", "")
            wiki_path = item.get("wiki_path")
        if not wiki_path:
            wiki_path = index_map.get(eid)
        if wiki_path and os.path.exists(wiki_path):
            with open(wiki_path) as f:
                content = f.read()
            results.append({"id": eid, "content": content})
        else:
            results.append({"id": eid, "content": None})

    fmt = getattr(args, "format", "json") or "json"
    if fmt == "text":
        for r in results:
            if r["content"] is None:
                continue
            print(f"===PAGE: {r['id']}===")
            print(r["content"])
            print("===END_PAGE===")
    else:
        print(json.dumps(results, ensure_ascii=False))


def cmd_read_journal_refs(args):
    if args.journals:
        with open(args.journals) as f:
            sources = json.load(f)
        # Accept either plain path strings or source objects with journal_path key
        journal_paths = [s["journal_path"] if isinstance(s, dict) else s for s in sources]
    else:
        raw = sys.stdin.read().strip()
        try:
            sources = json.loads(raw)
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"invalid JSON: {e}"}), file=sys.stderr)
            sys.exit(1)
        journal_paths = [s["journal_path"] if isinstance(s, dict) else s for s in sources]

    all_refs = []
    for path in journal_paths:
        expanded = os.path.expanduser(path)
        if not os.path.exists(expanded):
            continue
        with open(expanded) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entry = json.loads(line)
                        refs = entry.get("references", [])
                        if refs:
                            all_refs.append({
                                "experiment_id": entry.get("id", ""),
                                "references": refs,
                            })
                    except json.JSONDecodeError:
                        pass

    print(json.dumps(all_refs, ensure_ascii=False))


def cmd_generate_listings_batch(args):
    raw = sys.stdin.read().strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid JSON: {e}"}), file=sys.stderr)
        sys.exit(1)

    # Accept list-moc-groups output format: {"tags": {...}, "projects": {...}}
    # or legacy format: {"groups": {...}, "entries": [...]}
    if "tags" in data or "projects" in data:
        moc_groups = {"tags": data.get("tags", {}), "projects": data.get("projects", {})}
        # Load entry metadata from index
        entry_metadata = {}
        index_path = getattr(args, "index", ".wiki/index.jsonl")
        try:
            with open(index_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        e = json.loads(line)
                        entry_metadata[e["id"]] = e
        except FileNotFoundError:
            pass
    else:
        moc_groups = data.get("groups", {})
        entry_metadata = {e["id"]: e for e in data.get("entries", [])}
    written = []

    for group_type, groups in moc_groups.items():
        for group_name, entry_ids in groups.items():
            if group_type == "tags":
                moc_id = f"topic-{group_name}"
                output_path = f"wiki/topics/{moc_id}.md"
                title = f"Topic: {group_name.replace('-', ' ').title()}"
            else:
                moc_id = f"project-{group_name}"
                output_path = f"wiki/projects/{moc_id}.md"
                title = f"Project: {group_name.replace('-', ' ').title()}"

            if not args.force:
                moc_entry = entry_metadata.get(moc_id, {})
                if moc_entry.get("status") == "rendered" and moc_entry.get("wiki_path"):
                    continue

            lines = [f"# {title}\n", "## Entries\n"]
            for eid in entry_ids:
                entry = entry_metadata.get(eid, {})
                entry_title = entry.get("title", eid)
                year = entry.get("year", "")
                status = entry.get("status", "stub")
                source_name = entry.get("source_name", "")
                year_str = f" ({year})" if year else ""
                source_str = f" [{source_name}]" if source_name else ""
                status_str = " ⚠️ stub" if status == "stub" else ""
                lines.append(f"- [[{eid}]] — {entry_title}{year_str}{source_str}{status_str}")

            content = "\n".join(lines) + "\n"
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            tmp = output_path + ".tmp"
            with open(tmp, "w") as f:
                f.write(content)
            os.rename(tmp, output_path)
            written.append({"id": moc_id, "wiki_path": output_path, "title": title})

    print(json.dumps({"written": written}, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Top-level index and map generation
# ---------------------------------------------------------------------------

def cmd_generate_topic_index(args):
    raw = sys.stdin.read().strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid JSON: {e}"}), file=sys.stderr)
        sys.exit(1)

    tags = data.get("tags", {})

    major, minor, specialist = {}, {}, {}
    for tag, entries in tags.items():
        count = len(entries) if isinstance(entries, list) else int(entries)
        if count >= 10:
            major[tag] = count
        elif count >= 3:
            minor[tag] = count
        else:
            specialist[tag] = count

    def moc_lines(tag_dict):
        lines = []
        for tag in sorted(tag_dict):
            count = tag_dict[tag]
            label = tag.replace("-", " ").title()
            lines.append(f"- [[topic-{tag}]] — {label} ({count})")
        return "\n".join(lines) if lines else "_None_"

    specialist_tags = sorted(specialist)
    specialist_section = ", ".join(f"`{t}`" for t in specialist_tags)
    if not specialist_section:
        specialist_section = "_None_"

    content = f"""---
id: topics-index
type: moc
---
# Topics Index

Navigational index of all research topics, grouped by coverage depth.

## Major Topics (10+ entries)

{moc_lines(major)}

## Minor Topics (3–9 entries)

{moc_lines(minor)}

## Specialist Tags (1–2 entries)

{specialist_section}
"""

    output_path = "wiki/TOPICS.md"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tmp = output_path + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.rename(tmp, output_path)

    print(json.dumps({
        "written": output_path,
        "major": len(major),
        "minor": len(minor),
        "specialist": len(specialist),
    }, ensure_ascii=False))


def cmd_generate_map(args):
    raw = sys.stdin.read().strip()
    map_content = _find_block(raw, "MAP")
    if map_content is None:
        print(json.dumps({"error": "no ===MAP=== block found in agent output"}), file=sys.stderr)
        sys.exit(1)
    content = "---\nid: map\ntype: moc\n---\n" + map_content + "\n"

    output_path = "wiki/MAP.md"
    tmp = output_path + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.rename(tmp, output_path)

    print(json.dumps({"written": output_path}, ensure_ascii=False))


# ---------------------------------------------------------------------------
# ml-journal helpers
# ---------------------------------------------------------------------------

def cmd_list_experiments(args):
    journal_path = args.journal
    expanded = os.path.expanduser(journal_path)
    if not os.path.exists(expanded):
        print(f"ERROR: journal file not found: {journal_path}", file=sys.stderr)
        sys.exit(1)

    experiments = []
    with open(expanded) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    e = json.loads(line)
                    if e.get("type") == "experiment":
                        experiments.append(e)
                except json.JSONDecodeError:
                    pass

    stubs = []
    for e in experiments:
        uuid = e.get("id", "")
        description = e.get("description", "")
        slug_base = _slugify(description)
        short_id = uuid[:6] if len(uuid) >= 6 else uuid
        entry_id = f"exp-{slug_base}-{short_id}" if slug_base else f"exp-{short_id}"

        stubs.append({
            "id": entry_id,
            "type": "experiment",
            "source_type": "ml-journal",
            "source_path": f"{journal_path}:{uuid}",
            "title": description,
            "project": e.get("project"),
            "status": "stub",
            "tags": [],
            "source_name": args.source_name or None,
        })

    print(json.dumps(stubs, ensure_ascii=False))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Wiki render engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Subcommands by workflow stage:

  Source extraction  (/ingest, /render):
    extract-source     pull text from PDF, arXiv, Zotero, ml-journal, paste
    list-experiments   list journal experiments as stub-ready index entries
    extract-notes      read preserved Notes section from an existing page

  Page assembly  (/render):
    assemble           build a single wiki page from agent output + entry JSON
    assemble-image     write an image wiki page directly (no agent output)
    prep-batch         read source text for a batch of stub entries
    finalize-batch     assemble pages for a completed render batch

  Build operations  (/build, /build --fast):
    read-journal-refs       collect experiment refs from journal JSONLs
    read-batch              read current page content for a list of entries
    update-connections-batch  rewrite Connections sections from connection map
    update-banners-batch    add/remove staleness banners
    generate-listings-batch write mechanical MOC listing pages (--fast mode)
    generate-topic-index    write wiki/TOPICS.md from moc-groups JSON (stdin)
    generate-map            write wiki/MAP.md from build-agent output (stdin)

Run `uv run wiki_render.py <subcommand> --help` for flags.""",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # extract-source
    es_p = sub.add_parser("extract-source", help="Extract text from a source file")
    es_p.add_argument("source_path")
    es_p.add_argument("--source-type", required=True,
                      choices=["zotero", "pdf", "markdown", "text", "url", "ml-journal"])
    es_p.add_argument("--zotero-storage", help="Zotero storage root (optional, for validation)")

    # extract-notes
    en_p = sub.add_parser("extract-notes", help="Extract preserved Notes section from a wiki page")
    en_p.add_argument("wiki_path", nargs="?")

    # extract-zone
    ez_p = sub.add_parser("extract-zone", help="Extract user zone (between frontmatter and ## Notes)")
    ez_p.add_argument("wiki_path", nargs="?")

    # assemble
    asm_p = sub.add_parser("assemble", help="Assemble finished wiki page from agent output")
    asm_p.add_argument("--agent-output", required=True)
    asm_p.add_argument("--entry-json", required=True)
    asm_p.add_argument("--notes")
    asm_p.add_argument("--type", choices=["paper", "experiment", "synthesis", "moc"])
    asm_p.add_argument("--fragments-out", help="Write fragments JSON array to this file")

    # assemble-image
    ai_p = sub.add_parser("assemble-image", help="Write image wiki page directly from args (no agent output)")
    ai_p.add_argument("--image-path", required=True, help="Path to image asset (e.g. wiki/assets/img.png)")
    ai_p.add_argument("--description", required=True, help="Description text for the image")
    ai_p.add_argument("--tags", help="Comma-separated tags (overrides entry-json tags)")
    ai_p.add_argument("--entry-json", required=True, help="Entry JSON file")
    ai_p.add_argument("--fragments-out", help="Write fragments JSON array to this file")

    # prep-batch
    pb_p = sub.add_parser("prep-batch", help="Prep entries for batch render (reads source text)")
    pb_p.add_argument("--source-configs")

    # finalize-batch
    sub.add_parser("finalize-batch", help="Assemble pages for a batch of rendered entries")

    # update-connections-batch
    ucb_p = sub.add_parser("update-connections-batch", help="Update Connections sections from connection map")
    ucb_p.add_argument("--index", help="Path to index.jsonl (for wiki_path lookup)")

    # update-banners-batch
    sub.add_parser("update-banners-batch", help="Add/remove staleness banners")

    # read-batch
    rb_p = sub.add_parser("read-batch", help="Read page content for a list of entries")
    rb_p.add_argument("--index", help="Path to index.jsonl (default: .wiki/index.jsonl)")
    rb_p.add_argument("--format", choices=["json", "text"], default="json",
                       help="Output format: json (default) or text (delimited plain text)")

    # read-journal-refs
    rjr_p = sub.add_parser("read-journal-refs", help="Parse journal JSONLs, return experiment refs")
    rjr_p.add_argument("--journals", help="Path to journal sources JSON file (from wiki_config.py get sources)")

    # generate-listings-batch
    p = sub.add_parser("generate-listings-batch", help="Generate mechanical MOC listing pages (--fast mode)")
    p.add_argument("--index", default=".wiki/index.jsonl", help="Index path for entry metadata lookup")
    p.add_argument("--force", action="store_true", help="Overwrite existing rendered MOC pages (default: skip them)")

    # generate-topic-index
    sub.add_parser("generate-topic-index", help="Write wiki/TOPICS.md tiered tag index from moc-groups JSON on stdin")

    # generate-map
    sub.add_parser("generate-map", help="Write wiki/MAP.md from build-agent Mode 3 output on stdin")

    # splice-idea
    spi_p = sub.add_parser("splice-idea", help="Splice improve agent output into idea page (preserves user zone + notes)")
    spi_p.add_argument("wiki_path", help="Path to the idea wiki page")
    spi_p.add_argument("--agent-output", required=True, help="Path to agent output file")

    # list-experiments
    le_p = sub.add_parser("list-experiments", help="List all experiments in a journal as stub-ready index entries")
    le_p.add_argument("--journal", required=True, help="Path to journal JSONL file")
    le_p.add_argument("--source-name", help="Source name to inject into each entry stub")

    args = parser.parse_args()
    dispatch = {
        "extract-source": cmd_extract_source,
        "extract-notes": cmd_extract_notes,
        "extract-zone": cmd_extract_zone,
        "assemble": cmd_assemble,
        "assemble-image": cmd_assemble_image,
        "prep-batch": cmd_prep_batch,
        "finalize-batch": cmd_finalize_batch,
        "update-connections-batch": cmd_update_connections_batch,
        "update-banners-batch": cmd_update_banners_batch,
        "read-batch": cmd_read_batch,
        "read-journal-refs": cmd_read_journal_refs,
        "generate-listings-batch": cmd_generate_listings_batch,
        "generate-topic-index": cmd_generate_topic_index,
        "generate-map": cmd_generate_map,
        "splice-idea": cmd_splice_idea,
        "list-experiments": cmd_list_experiments,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
