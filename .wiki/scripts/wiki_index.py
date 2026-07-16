# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

DEFAULT_INDEX = os.path.join(os.path.dirname(__file__), "..", "index.jsonl")

VALID_TYPES = {"paper", "experiment", "synthesis", "moc", "image", "idea"}
VALID_SOURCE_TYPES = {"zotero", "pdf", "markdown", "text", "url", "query", "ml-journal", "image"}
VALID_STATUSES = {"stub", "rendered"}
VALID_FRAGMENT_TYPES = {"claim", "method", "finding", "dataset", "metric", "question", "definition", "description"}


# ---------------------------------------------------------------------------
# Index I/O
# ---------------------------------------------------------------------------

def load_index(path):
    expanded = os.path.expanduser(path)
    if not os.path.exists(expanded):
        return []
    entries = []
    with open(expanded) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def save_index(path, entries):
    expanded = os.path.expanduser(path)
    tmp = expanded + ".tmp"
    os.makedirs(os.path.dirname(expanded), exist_ok=True)
    with open(tmp, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    os.rename(tmp, expanded)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_alias_map(alias_map, source):
    """Reject anything that is not a flat {tag: canonical} map.

    Keys must be non-empty tag strings; values must be strings (an empty string
    means "drop this tag", per apply_aliases). This guards against wrong-shaped
    input such as a {"aliases": {...}} wrapper, which would otherwise be accepted
    and applied as a silent no-op.
    """
    if not isinstance(alias_map, dict):
        raise ValueError(
            f'alias map from {source} must be a JSON object mapping "tag" -> '
            f'"canonical"; got {type(alias_map).__name__}'
        )
    for k, v in alias_map.items():
        if not isinstance(k, str) or not k.strip():
            raise ValueError(
                f"alias map from {source} has a non-string or empty key: {k!r}"
            )
        if not isinstance(v, str):
            raise ValueError(
                f'alias map from {source} maps "{k}" to a non-string value '
                f"({type(v).__name__}); expected a canonical tag string"
            )


def load_aliases(path):
    if not path:
        return {}
    with open(path) as f:
        alias_map = json.load(f)
    _validate_alias_map(alias_map, path)
    return alias_map


def apply_aliases(tags, alias_map):
    if not alias_map:
        return tags
    return sorted({v for t in tags if (v := alias_map.get(t, t))})


def _member_hash(member_ids):
    """Stable 16-char hex hash of sorted member entry IDs."""
    return hashlib.sha256(json.dumps(sorted(member_ids)).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Path routing (pure function — no index read)
# ---------------------------------------------------------------------------

def wiki_path(entry_id):
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
# Phase 2a: Core CRUD
# ---------------------------------------------------------------------------

def cmd_wiki_path(args):
    print(wiki_path(args.id))


def cmd_create_entry(args):
    entry_type = args.type
    if entry_type not in VALID_TYPES:
        print(json.dumps({"error": f"invalid type '{entry_type}', must be one of {sorted(VALID_TYPES)}"}), file=sys.stderr)
        sys.exit(1)

    source_type = args.source_type
    if source_type not in VALID_SOURCE_TYPES:
        print(json.dumps({"error": f"invalid source_type '{source_type}', must be one of {sorted(VALID_SOURCE_TYPES)}"}), file=sys.stderr)
        sys.exit(1)

    status = args.status or "stub"
    if status not in VALID_STATUSES:
        print(json.dumps({"error": f"invalid status '{status}', must be one of {sorted(VALID_STATUSES)}"}), file=sys.stderr)
        sys.exit(1)

    tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
    references = [r.strip() for r in args.references.split(",")] if args.references else []
    year = int(args.year) if args.year else None

    entry = {
        "id": args.id,
        "type": entry_type,
        "source_type": source_type,
        "source_path": args.source_path,
        "wiki_path": args.wiki_path,
        "zotero_uri": args.zotero_uri,
        "pdf_uri": args.pdf_uri,
        "arxiv_id": args.arxiv_id,
        "title": args.title or "",
        "citation": args.citation,
        "year": year,
        "tags": tags,
        "project": args.project,
        "source_name": args.source_name,
        "references": references,
        "status": status,
        "locked": args.locked or False,
        "ingested": now_iso(),
        "rendered": None,
        "last_improved": None,
    }
    print(json.dumps(entry, ensure_ascii=False))


def cmd_add(args):
    raw = sys.stdin.read().strip()
    if not raw:
        print(json.dumps({"error": "no input on stdin"}), file=sys.stderr)
        sys.exit(1)
    try:
        entry = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid JSON: {e}"}), file=sys.stderr)
        sys.exit(1)

    entries = load_index(args.index)
    existing_ids = {e["id"] for e in entries}
    if entry["id"] in existing_ids:
        print(json.dumps({"error": f"ID collision: '{entry['id']}' already exists"}), file=sys.stderr)
        sys.exit(2)

    entries.append(entry)
    save_index(args.index, entries)
    print(json.dumps({"added": entry["id"]}))


def cmd_get(args):
    entries = load_index(args.index)
    entry = next((e for e in entries if e["id"] == args.id), None)
    if entry is None:
        print(json.dumps({"error": f"not found: '{args.id}'"}), file=sys.stderr)
        sys.exit(1)
    print(json.dumps(entry, ensure_ascii=False))


def cmd_update(args):
    entries = load_index(args.index)
    idx = next((i for i, e in enumerate(entries) if e["id"] == args.id), None)
    if idx is None:
        print(json.dumps({"error": f"not found: '{args.id}'"}), file=sys.stderr)
        sys.exit(1)

    entry = entries[idx]
    if args.status is not None:
        entry["status"] = args.status
    if args.tags is not None:
        entry["tags"] = [t.strip() for t in args.tags.split(",")]
    if args.wiki_path is not None:
        entry["wiki_path"] = args.wiki_path
    if args.rendered is not None:
        entry["rendered"] = now_iso() if args.rendered == "now" else args.rendered
    if args.locked is not None:
        entry["locked"] = args.locked
    if args.references is not None:
        entry["references"] = [r.strip() for r in args.references.split(",")]
    if args.project is not None:
        entry["project"] = args.project
    if args.source_path is not None:
        entry["source_path"] = args.source_path
    if args.type is not None:
        entry["type"] = args.type
    if args.last_improved is not None:
        entry["last_improved"] = now_iso() if args.last_improved == "now" else args.last_improved

    entries[idx] = entry
    save_index(args.index, entries)
    print(json.dumps(entry, ensure_ascii=False))


def cmd_delete(args):
    entries = load_index(args.index)
    before = len(entries)
    entries = [e for e in entries if e["id"] != args.id]
    if len(entries) == before:
        print(json.dumps({"error": f"not found: '{args.id}'"}), file=sys.stderr)
        sys.exit(1)
    save_index(args.index, entries)
    print(json.dumps({"deleted": args.id}, ensure_ascii=False))


def cmd_exists(args):
    entries = load_index(args.index)
    entry = next((e for e in entries if e["id"] == args.id), None)
    if entry is None:
        sys.exit(1)
    # Check for collision from different source
    if args.source_path and entry.get("source_path") != args.source_path:
        print(json.dumps(entry, ensure_ascii=False))
        sys.exit(2)
    print(json.dumps(entry, ensure_ascii=False))
    sys.exit(0)


def cmd_list(args):
    entries = load_index(args.index)
    # Filter out fragments
    results = [e for e in entries if not e["id"].startswith("frag-")]

    if args.status:
        results = [e for e in results if e.get("status") == args.status]
    if args.type:
        results = [e for e in results if e.get("type") == args.type]
    if args.tag:
        results = [e for e in results if args.tag in (e.get("tags") or [])]
    if args.project:
        results = [e for e in results if e.get("project") == args.project]
    if args.source_name:
        results = [e for e in results if e.get("source_name") == args.source_name]

    fmt = args.format or "json"
    if fmt == "json":
        print(json.dumps(results, ensure_ascii=False))
    elif fmt == "ids":
        for e in results:
            print(e["id"])
    elif fmt == "count":
        print(len(results))


# ---------------------------------------------------------------------------
# Phase 2b: Batch + Fragments
# ---------------------------------------------------------------------------

def cmd_add_batch(args):
    raw = sys.stdin.read().strip()
    try:
        new_entries = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid JSON: {e}"}), file=sys.stderr)
        sys.exit(1)

    entries = load_index(args.index)
    existing_ids = {e["id"] for e in entries}
    added, skipped = [], []

    for entry in new_entries:
        eid = entry.get("id", "")
        if eid in existing_ids:
            skipped.append({"id": eid, "reason": "collision"})
        else:
            entries.append(entry)
            existing_ids.add(eid)
            added.append(eid)

    save_index(args.index, entries)
    print(json.dumps({"added": len(added), "skipped": skipped}))


def cmd_update_batch(args):
    entries = load_index(args.index)

    if args.input:
        with open(args.input) as f:
            updates = json.load(f)
        entry_map = {e["id"]: e for e in entries}
        updated = []
        for upd in updates:
            eid = upd.get("id")
            if eid in entry_map:
                patch = upd.get("updates", {})
                if isinstance(patch.get("tags"), str):
                    patch["tags"] = [t.strip() for t in patch["tags"].split(",") if t.strip()]
                entry_map[eid].update(patch)
                updated.append(eid)
        entries = list(entry_map.values())
        save_index(args.index, entries)
        print(json.dumps({"updated": len(updated)}))

    elif args.filter and args.set:
        key, val = args.filter.split("=", 1)
        set_key, set_val = args.set.split("=", 1)
        if set_val == "now":
            set_val = now_iso()
        count = 0
        for entry in entries:
            if str(entry.get(key, "")) == val:
                entry[set_key] = set_val
                count += 1
        save_index(args.index, entries)
        print(json.dumps({"updated": count}))

    else:
        print(json.dumps({"error": "provide --input or --filter and --set"}), file=sys.stderr)
        sys.exit(1)


def cmd_add_fragments_batch(args):
    raw = sys.stdin.read().strip()
    try:
        fragments = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid JSON: {e}"}), file=sys.stderr)
        sys.exit(1)

    invalid = []
    for frag in fragments:
        ftype = frag.get("type", "")
        if ftype not in VALID_FRAGMENT_TYPES:
            invalid.append({"id": frag.get("id"), "error": f"invalid fragment type '{ftype}'"})

    if invalid:
        print(json.dumps({"error": "invalid fragment types", "fragments": invalid}), file=sys.stderr)
        sys.exit(1)

    # Ensure required fields
    required = {"id", "type", "title", "tags", "project", "references", "ingested"}
    for frag in fragments:
        missing = required - set(frag.keys())
        if missing:
            frag.setdefault("tags", [])
            frag.setdefault("project", None)
            frag.setdefault("references", [])
            frag.setdefault("ingested", now_iso())

    entries = load_index(args.index)
    existing_ids = {e["id"] for e in entries}
    added = 0
    for frag in fragments:
        if frag["id"] not in existing_ids:
            entries.append(frag)
            existing_ids.add(frag["id"])
            added += 1

    save_index(args.index, entries)
    print(json.dumps({"added": added}))


def cmd_delete_fragments(args):
    entries = load_index(args.index)
    prefix = f"frag-{args.source_id}-"
    before = len(entries)
    entries = [e for e in entries if not e["id"].startswith(prefix)]
    save_index(args.index, entries)
    print(json.dumps({"deleted": before - len(entries)}))


def cmd_delete_fragments_batch(args):
    raw = sys.stdin.read().strip()
    try:
        ids_to_delete = set(json.loads(raw))
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid JSON: {e}"}), file=sys.stderr)
        sys.exit(1)

    entries = load_index(args.index)
    before = len(entries)
    entries = [e for e in entries if e["id"] not in ids_to_delete]
    save_index(args.index, entries)
    print(json.dumps({"deleted": before - len(entries)}))


# ---------------------------------------------------------------------------
# Phase 2c: Analysis + Lint
# ---------------------------------------------------------------------------

def cmd_search(args):
    entries = load_index(args.index)
    query_terms = args.query.lower().split()
    limit = args.limit or 100

    def score(e):
        text = " ".join([
            e.get("title", ""),
            " ".join(e.get("tags", [])),
        ]).lower()
        return sum(1 for t in query_terms if t in text)

    wiki_entries = [e for e in entries if not e["id"].startswith("frag-")]
    fragments = [e for e in entries if e["id"].startswith("frag-")]

    scored_entries = [(e, s) for e in wiki_entries if (s := score(e)) > 0]
    scored_entries.sort(key=lambda x: -x[1])

    scored_frags = [(f, s) for f in fragments if (s := score(f)) > 0]
    scored_frags.sort(key=lambda x: -x[1])

    result = {
        "entries": [e for e, _ in scored_entries[:limit]],
        "fragments": [f for f, _ in scored_frags[:limit]],
    }
    print(json.dumps(result, ensure_ascii=False))


def cmd_resolve_connections(args):
    entries = load_index(args.index)
    wiki_entries = [e for e in entries if not e["id"].startswith("frag-")]
    fragments = [e for e in entries if e["id"].startswith("frag-")]
    alias_map = load_aliases(args.tag_aliases)

    # Load journal refs if provided
    journal_refs = {}  # experiment_id -> [wiki_entry_ids]
    if args.journal_refs:
        with open(args.journal_refs) as f:
            refs_list = json.load(f)
        for item in refs_list:
            journal_refs[item["experiment_id"]] = item.get("references", [])

    # Build tag and fragment-type maps (with alias normalization)
    entry_tags = {e["id"]: {alias_map.get(t, t) for t in (e.get("tags") or [])} for e in wiki_entries}
    frag_types_by_source = {}  # source_id -> set of fragment types
    for frag in fragments:
        for src in frag.get("references", []):
            frag_types_by_source.setdefault(src, set()).add(frag.get("type", ""))

    # Build cited-by map
    cited_by = {}  # entry_id -> set of entry_ids that reference it
    for e in wiki_entries:
        for ref_id in (e.get("references") or []):
            cited_by.setdefault(ref_id, set()).add(e["id"])
    # Also include journal refs as cited-by
    for exp_id, refs in journal_refs.items():
        for ref_id in refs:
            cited_by.setdefault(ref_id, set()).add(exp_id)

    rendered = [e for e in wiki_entries if e.get("status") == "rendered"]
    scope_ids = None
    if args.id:
        entry = next((e for e in wiki_entries if e["id"] == args.id), None)
        if entry:
            neighbor_ids = set(entry.get("references") or [])
            neighbor_ids.update(cited_by.get(args.id, set()))
            scope_ids = {args.id} | neighbor_ids

    connection_map = {}
    target_entries = rendered if not scope_ids else [e for e in rendered if e["id"] in scope_ids]

    for entry in target_entries:
        eid = entry["id"]
        tags_a = entry_tags.get(eid, set())
        frag_types_a = frag_types_by_source.get(eid, set())

        related_candidates = []
        for other in rendered:
            oid = other["id"]
            if oid == eid:
                continue
            tags_b = entry_tags.get(oid, set())
            frag_types_b = frag_types_by_source.get(oid, set())
            shared_tags = len(tags_a & tags_b)
            shared_frag_types = len(frag_types_a & frag_types_b) if frag_types_a and frag_types_b else 0
            if shared_tags >= 2 or (shared_frag_types >= 1 and shared_tags >= 1):
                related_candidates.append({
                    "id": oid,
                    "shared_tags": list(tags_a & tags_b),
                    "shared_frag_types": list(frag_types_a & frag_types_b),
                })

        connection_map[eid] = {
            "references": list(entry.get("references") or []),
            "cited_by": sorted(cited_by.get(eid, set())),
            "related_candidates": related_candidates,
        }

    print(json.dumps(connection_map, ensure_ascii=False))


def cmd_check_stale(args):
    entries = load_index(args.index)
    wiki_entries = {e["id"]: e for e in entries if not e["id"].startswith("frag-")}

    stale = []

    syntheses = [e for e in wiki_entries.values() if e.get("type") == "synthesis"]
    for syn in syntheses:
        syn_rendered = syn.get("rendered") or syn.get("ingested", "")
        syn_tags = set(syn.get("tags") or [])
        stale_entries = []
        for eid, entry in wiki_entries.items():
            if eid == syn["id"]:
                continue
            entry_ingested = entry.get("ingested", "")
            if entry_ingested <= syn_rendered:
                continue
            shared = len(syn_tags & set(entry.get("tags") or []))
            if shared >= 2:
                stale_entries.append(eid)
        if stale_entries:
            stale.append({"id": syn["id"], "stale_entries": stale_entries})

    ideas = [e for e in wiki_entries.values() if e.get("type") == "idea"]
    for idea in ideas:
        last_improved = idea.get("last_improved")
        idea_tags = set(idea.get("tags") or [])
        if last_improved is None:
            stale.append({"id": idea["id"], "stale_entries": ["never_improved"]})
            continue
        stale_entries = []
        for eid, entry in wiki_entries.items():
            if eid == idea["id"]:
                continue
            entry_ingested = entry.get("ingested", "")
            if entry_ingested <= last_improved:
                continue
            shared = len(idea_tags & set(entry.get("tags") or []))
            if shared >= 2:
                stale_entries.append(eid)
        if stale_entries:
            stale.append({"id": idea["id"], "stale_entries": stale_entries})

    print(json.dumps(stale, ensure_ascii=False))


def cmd_score_idea(args):
    raw = sys.stdin.read().strip()
    try:
        input_data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid JSON: {e}"}), file=sys.stderr)
        sys.exit(1)

    search_terms = [t.lower() for t in input_data.get("search_terms", [])]
    idea_tags = set(input_data.get("tags", []))
    exclude_id = input_data.get("exclude_id")
    top_n = args.top_n or 30

    entries = load_index(args.index)
    fragments = [e for e in entries if e["id"].startswith("frag-")]

    scored = []
    for frag in fragments:
        source_ids = frag.get("references", [])
        if exclude_id and any(sid == exclude_id for sid in source_ids):
            continue

        frag_tags = set(frag.get("tags") or [])
        shared_tag_count = len(idea_tags & frag_tags)

        frag_title = (frag.get("title") or "").lower()
        keyword_match_count = sum(1 for t in search_terms if t in frag_title)

        score = (shared_tag_count * 3) + keyword_match_count
        if score > 0:
            scored.append((frag, score))

    scored.sort(key=lambda x: -x[1])
    top_frags = scored[:top_n]

    wiki_entries = {e["id"]: e for e in entries if not e["id"].startswith("frag-")}
    grouped = {}
    for frag, sc in top_frags:
        for src_id in frag.get("references", []):
            entry = wiki_entries.get(src_id)
            if entry is None:
                continue
            if src_id not in grouped:
                grouped[src_id] = {
                    "id": src_id,
                    "title": entry.get("title", ""),
                    "type": entry.get("type", ""),
                    "tags": entry.get("tags", []),
                    "year": entry.get("year"),
                    "fragments": [],
                }
            grouped[src_id]["fragments"].append({
                "id": frag["id"],
                "type": frag.get("type", ""),
                "title": frag.get("title", ""),
                "score": sc,
            })

    results = sorted(grouped.values(), key=lambda g: -max(f["score"] for f in g["fragments"]))
    print(json.dumps(results, ensure_ascii=False))


def cmd_get_unresolved_tags(args):
    """Read list-moc-groups output from stdin, return {tag: count} for tags not in existing aliases."""
    raw = sys.stdin.read().strip()
    try:
        groups = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)
    existing = load_aliases(args.aliases) if args.aliases else {}
    tags = groups.get("tags", {})
    unresolved = {tag: len(ids) for tag, ids in tags.items() if tag not in existing}
    print(json.dumps(unresolved))


def cmd_get_tag_counts(args):
    """Read full index, return {tag: count} for all tags."""
    entries = load_index(args.index)
    counts = {}
    for entry in entries:
        for tag in entry.get("tags") or []:
            counts[tag] = counts.get(tag, 0) + 1
    print(json.dumps(counts))


def cmd_get_canonical_tags(args):
    """Return tags with N+ entries (non-fragment only)."""
    entries = load_index(args.index)
    non_frag = [e for e in entries if not e["id"].startswith("frag-")]
    tag_counts = {}
    for e in non_frag:
        for t in (e.get("tags") or []):
            tag_counts[t] = tag_counts.get(t, 0) + 1
    threshold = args.min_count or 10
    frequent = sorted(t for t, c in tag_counts.items() if c >= threshold)
    print(json.dumps(frequent))


def cmd_normalize_tags(args):
    try:
        alias_map = load_aliases(args.aliases)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"Invalid alias map: {e}", file=sys.stderr)
        sys.exit(1)
    entries = load_index(args.index)
    entries_updated = 0
    fragments_updated = 0

    for entry in entries:
        old_tags = entry.get("tags") or []
        new_tags = apply_aliases(old_tags, alias_map)
        if new_tags != sorted(set(old_tags)):
            entry["tags"] = new_tags
            if entry["id"].startswith("frag-"):
                fragments_updated += 1
            else:
                entries_updated += 1

    save_index(args.index, entries)
    print(json.dumps({"entries_updated": entries_updated, "fragments_updated": fragments_updated}))


def cmd_sync_frontmatter_tags(args):
    """Sync frontmatter tags in wiki markdown files with the index."""
    entries = load_index(args.index)
    updated = 0
    skipped = 0
    missing = 0
    for entry in entries:
        if entry["id"].startswith("frag-"):
            continue
        wp = entry.get("wiki_path")
        if not wp:
            continue
        if not os.path.isfile(wp):
            missing += 1
            continue
        tags = entry.get("tags") or []
        tag_line = f"tags: [{', '.join(tags)}]"
        with open(wp) as f:
            content = f.read()
        # Match frontmatter tags line between --- delimiters
        lines = content.split("\n")
        if len(lines) < 3 or lines[0].rstrip() != "---":
            skipped += 1
            continue
        end_idx = None
        tag_idx = None
        for i in range(1, len(lines)):
            if lines[i].rstrip() == "---":
                end_idx = i
                break
            if lines[i].startswith("tags:"):
                tag_idx = i
        if end_idx is None:
            skipped += 1
            continue
        if tag_idx is None:
            # Insert tags line before closing ---
            lines.insert(end_idx, tag_line)
            updated += 1
        else:
            if lines[tag_idx].rstrip() == tag_line:
                continue
            lines[tag_idx] = tag_line
            updated += 1
        tmp = wp + ".tmp"
        with open(tmp, "w") as f:
            f.write("\n".join(lines))
        os.rename(tmp, wp)
    print(json.dumps({"updated": updated, "skipped": skipped, "missing": missing}))


def cmd_list_moc_groups(args):
    entries = load_index(args.index)
    wiki_entries = [e for e in entries if not e["id"].startswith("frag-")]
    alias_map = load_aliases(args.tag_aliases)

    tags_map = {}
    projects_map = {}
    for entry in wiki_entries:
        if entry.get("type") == "moc":
            continue
        for tag in (entry.get("tags") or []):
            canonical = alias_map.get(tag, tag)
            if canonical:
                tags_map.setdefault(canonical, []).append(entry["id"])
        project = entry.get("project")
        if project:
            projects_map.setdefault(project, []).append(entry["id"])

    print(json.dumps({"tags": tags_map, "projects": projects_map}, ensure_ascii=False))


def cmd_filter_dirty_mocs(args):
    raw = sys.stdin.read().strip()
    try:
        moc_groups = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid JSON: {e}"}), file=sys.stderr)
        sys.exit(1)

    if args.force:
        print(raw)
        return

    entries = load_index(args.index)
    index = {e["id"]: e for e in entries}

    def slugify(name):
        return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

    def is_dirty(group_type, group_name, member_ids):
        prefix = "topic" if group_type == "tag" else "project"
        moc_id = f"{prefix}-{slugify(group_name)}"
        moc_entry = index.get(moc_id, {})
        last_built = moc_entry.get("last_built")
        if not last_built:
            return True
        if _member_hash(member_ids) != moc_entry.get("member_hash", ""):
            return True
        for eid in member_ids:
            entry_rendered = index.get(eid, {}).get("rendered") or \
                             index.get(eid, {}).get("ingested", "")
            if entry_rendered > last_built:
                return True
        return False

    dirty_tags = {
        tag: ids
        for tag, ids in moc_groups.get("tags", {}).items()
        if is_dirty("tag", tag, ids)
    }
    dirty_projects = {
        proj: ids
        for proj, ids in moc_groups.get("projects", {}).items()
        if is_dirty("project", proj, ids)
    }
    print(json.dumps({"tags": dirty_tags, "projects": dirty_projects}, ensure_ascii=False))


def _build_frags_by_ref(entries):
    frags_by_ref = {}
    for e in entries:
        if e["id"].startswith("frag-"):
            for ref in e.get("references", []):
                frags_by_ref.setdefault(ref, []).append(e.get("title", ""))
    return frags_by_ref


def cmd_prep_rank_batches(args):
    """Partition entries with related_candidates into JSON batch files for the build ranking step."""
    with open(args.connection_map) as f:
        conn_map = json.load(f)

    entries = load_index(args.index)
    index = {e["id"]: e for e in entries}
    frags_by_ref = _build_frags_by_ref(entries)

    with_candidates = []
    for eid, conn in conn_map.items():
        if not conn.get("related_candidates"):
            continue
        entry = index.get(eid, {})
        enriched_candidates = []
        for cand in conn["related_candidates"]:
            cid = cand["id"]
            centry = index.get(cid, {})
            enriched_candidates.append({
                "id": cid,
                "title": centry.get("title", ""),
                "shared_tags": cand.get("shared_tags", []),
                "shared_frag_types": cand.get("shared_frag_types", []),
                "fragment_titles_sample": frags_by_ref.get(cid, [])[:3],
                "_score": len(cand.get("shared_tags", [])),
            })
        enriched_candidates.sort(key=lambda c: c["_score"], reverse=True)
        max_candidates = args.max_candidates or 25
        enriched_candidates = enriched_candidates[:max_candidates]
        for c in enriched_candidates:
            del c["_score"]
        with_candidates.append({
            "id": eid,
            "title": entry.get("title", ""),
            "tags": entry.get("tags", []),
            "fragment_titles": frags_by_ref.get(eid, []),
            "related_candidates": enriched_candidates,
        })

    batch_size = args.batch_size or 10
    batches = [with_candidates[i:i + batch_size] for i in range(0, len(with_candidates), batch_size)]

    out_prefix = args.out_prefix or "/tmp/build_batch_"
    written = []
    for i, batch in enumerate(batches):
        path = f"{out_prefix}{i:02d}.json"
        with open(path, "w") as f:
            json.dump(batch, f, ensure_ascii=False)
        written.append(path)

    print(json.dumps({
        "total_entries": len(with_candidates),
        "batches": len(batches),
        "batch_files": written,
    }, ensure_ascii=False))


def cmd_fast_rank_connections(args):
    """Rank related_candidates by shared_tag count (mechanical, no LLM).
    Reads connection map JSON from stdin, writes ranked map to stdout."""
    raw = sys.stdin.read().strip()
    if not raw:
        print("{}", flush=True)
        return
    try:
        connection_map = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    top_n = args.top_n
    result = {}
    for eid, conns in connection_map.items():
        candidates = conns.get("related_candidates", [])
        ranked = sorted(candidates, key=lambda c: len(c.get("shared_tags", [])), reverse=True)[:top_n]
        result[eid] = {
            "references": conns.get("references", []),
            "cited_by": conns.get("cited_by", []),
            "related": [{"id": c["id"]} for c in ranked],
        }
    print(json.dumps(result), flush=True)


def _split_by_payload(batches, max_bytes):
    """Subdivide count-based batches so no batch exceeds max_bytes of JSON."""
    result = []
    for batch in batches:
        current, current_size = [], 0
        for group in batch:
            size = len(json.dumps(group, ensure_ascii=False).encode())
            if current_size + size > max_bytes and current:
                result.append(current)
                current, current_size = [group], size
            else:
                current.append(group)
                current_size += size
        if current:
            result.append(current)
    return result


def cmd_prep_moc_batches(args):
    """Prepare per-group data for MOC narrative generation.

    Reads moc_groups.json from stdin, loads the index, and writes:
    - One entry JSON file per qualifying group to /tmp/moc_entry_<id>.json
    - Batch data files (inline prompt data) to /tmp/moc_data_batch_<n>.json

    Outputs a JSON manifest: {batch_files, entry_json_map, total_groups}
    """
    raw = sys.stdin.read().strip()
    try:
        moc_groups = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    entries_list = load_index(args.index)
    index = {e["id"]: e for e in entries_list}

    frags_by_ref = _build_frags_by_ref(entries_list)

    min_entries = args.min_entries
    out_prefix = args.out_prefix or "/tmp/moc_data_batch_"
    entry_dir = args.entry_dir or "/tmp"

    tags_map = moc_groups.get("tags", {})
    projects_map = moc_groups.get("projects", {})

    def slugify(name):
        return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

    def build_group_data(group_type, group_name, member_ids):
        moc_id = f"{'topic' if group_type == 'tag' else 'project'}-{slugify(group_name)}"
        existing = index.get(moc_id, {})
        if existing.get("title"):
            moc_title = existing["title"]
        else:
            title_words = group_name.replace("-", " ").replace("_", " ").title()
            prefix = "Topic" if group_type == "tag" else "Project"
            moc_title = f"{prefix}: {title_words}"
        entry_data = []
        for eid in member_ids:
            e = index.get(eid, {})
            entry_data.append({
                "id": eid,
                "title": e.get("title", eid),
                "type": e.get("type", "paper"),
                "year": e.get("year", ""),
                "tags": e.get("tags", []),
                "status": e.get("status", "stub"),
                "source_name": e.get("source_name", ""),
                "fragment_titles": frags_by_ref.get(eid, []),
            })
        return {
            "group_type": group_type,
            "group_name": group_name,
            "moc_id": moc_id,
            "moc_title": moc_title,
            "entries": entry_data,
        }

    # Gather all qualifying groups
    small_groups, medium_groups, large_groups = [], [], []
    for tag, members in tags_map.items():
        if len(members) < min_entries:
            continue
        gd = build_group_data("tag", tag, members)
        n = len(members)
        if n <= 5:
            small_groups.append(gd)
        elif n <= 10:
            medium_groups.append(gd)
        else:
            large_groups.append(gd)

    for proj, members in projects_map.items():
        if len(members) < min_entries:
            continue
        gd = build_group_data("project", proj, members)
        n = len(members)
        if n <= 5:
            small_groups.append(gd)
        elif n <= 10:
            medium_groups.append(gd)
        else:
            large_groups.append(gd)

    all_groups = small_groups + medium_groups + large_groups

    # Write entry JSON files for finalize-batch
    entry_json_map = {}
    for gd in all_groups:
        moc_id = gd["moc_id"]
        existing = index.get(moc_id, {})
        entry_obj = {
            "id": moc_id,
            "type": "moc",
            "title": gd["moc_title"],
            "tags": [gd["group_name"]] if gd["group_type"] == "tag" else [],
            "project": gd["group_name"] if gd["group_type"] == "project" else None,
            "source_type": None,
            "source_path": None,
            "source_name": existing.get("source_name"),
            "status": "rendered",
            "wiki_path": existing.get("wiki_path") or wiki_path(moc_id),
            "locked": False,
            "references": [e["id"] for e in gd["entries"]],
        }
        path = os.path.join(entry_dir, f"moc_entry_{moc_id}.json")
        with open(path, "w") as f:
            json.dump(entry_obj, f, ensure_ascii=False)
        entry_json_map[moc_id] = path

    # Partition into batches
    small_batch_size = args.small_batch_size or 10
    medium_batch_size = args.medium_batch_size or 5

    count_batches = []
    for i in range(0, len(small_groups), small_batch_size):
        count_batches.append(small_groups[i:i + small_batch_size])
    for i in range(0, len(medium_groups), medium_batch_size):
        count_batches.append(medium_groups[i:i + medium_batch_size])
    for g in large_groups:
        count_batches.append([g])

    max_bytes = args.max_payload_kb * 1024
    batches = _split_by_payload(count_batches, max_bytes)

    batch_files = []
    for i, batch in enumerate(batches):
        path = f"{out_prefix}{i:02d}.json"
        with open(path, "w") as f:
            json.dump(batch, f, ensure_ascii=False)
        batch_files.append(path)

    # Optionally write the MOC manifest for update-moc-entries
    moc_manifest = []
    for gd in all_groups:
        moc_id = gd["moc_id"]
        moc_manifest.append({
            "id": moc_id,
            "wiki_path": wiki_path(moc_id),
            "title": gd["moc_title"],
            "tags": [gd["group_name"]] if gd["group_type"] == "tag" else [],
            "project": gd["group_name"] if gd["group_type"] == "project" else None,
            "references": [e["id"] for e in gd["entries"]],
        })

    if args.moc_manifest_out:
        with open(args.moc_manifest_out, "w") as f:
            json.dump(moc_manifest, f, ensure_ascii=False)

    print(json.dumps({
        "total_groups": len(all_groups),
        "batches": len(batches),
        "batch_files": batch_files,
        "entry_json_map": entry_json_map,
        "moc_manifest_out": args.moc_manifest_out,
    }, ensure_ascii=False))


def cmd_update_moc_entries(args):
    raw = sys.stdin.read().strip()
    try:
        moc_manifest = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid JSON: {e}"}), file=sys.stderr)
        sys.exit(1)

    entries = load_index(args.index)
    entry_map = {e["id"]: (i, e) for i, e in enumerate(entries)}
    updated, created = [], []

    for moc in moc_manifest:
        moc_id = moc["id"]
        if moc_id in entry_map:
            idx, existing = entry_map[moc_id]
            ts = now_iso()
            refs = moc.get("references", existing.get("references", []))
            existing.update({
                "wiki_path": moc.get("wiki_path", existing.get("wiki_path")),
                "rendered": ts,
                "last_built": ts,
                "member_hash": _member_hash(refs),
                "references": refs,
                "title": moc.get("title", existing.get("title", "")),
            })
            entries[idx] = existing
            updated.append(moc_id)
        else:
            ts = now_iso()
            refs = moc.get("references", [])
            new_entry = {
                "id": moc_id,
                "type": "moc",
                "source_type": "markdown",
                "source_path": None,
                "wiki_path": moc.get("wiki_path"),
                "zotero_uri": None,
                "pdf_uri": None,
                "title": moc.get("title", ""),
                "citation": None,
                "year": None,
                "tags": moc.get("tags", []),
                "project": moc.get("project"),
                "source_name": None,
                "references": refs,
                "status": "rendered",
                "locked": False,
                "ingested": ts,
                "rendered": ts,
                "last_built": ts,
                "member_hash": _member_hash(refs),
            }
            entries.append(new_entry)
            created.append(moc_id)

    save_index(args.index, entries)
    print(json.dumps({"updated": updated, "created": created}))


def cmd_lint_structural(args):
    entries = load_index(args.index)
    wiki_dir = args.wiki_dir or "wiki"

    wiki_entries = {e["id"]: e for e in entries if not e["id"].startswith("frag-")}
    fragments = [e for e in entries if e["id"].startswith("frag-")]
    all_ids = set(wiki_entries.keys())

    # Build inbound reference map
    inbound = {eid: set() for eid in all_ids}
    for eid, entry in wiki_entries.items():
        for ref in (entry.get("references") or []):
            if ref in inbound:
                inbound[ref].add(eid)

    report = {
        "orphan_entries": [],
        "orphan_files": [],
        "orphan_fragments": [],
        "missing_links": [],
        "broken_paths": [],
        "stale_stubs": [],
        "tag_gaps": [],
        "untagged": [],
        "unsupported_claims": [],
        "stale_syntheses": [],
    }

    # Orphan entries (no inbound refs, not moc/synthesis)
    for eid, entry in wiki_entries.items():
        if entry.get("type") not in ("moc", "synthesis") and not inbound[eid]:
            report["orphan_entries"].append(eid)

    # Orphan files (files on disk not in index)
    tracked_paths = {e.get("wiki_path") for e in wiki_entries.values() if e.get("wiki_path")}
    for subdir in ("_pages", "topics", "projects", "syntheses", "images", "experiments"):
        subdir_path = os.path.join(wiki_dir, subdir)
        if not os.path.exists(subdir_path):
            continue
        for fname in os.listdir(subdir_path):
            if fname.endswith(".md"):
                rel = os.path.join(wiki_dir, subdir, fname)
                if rel not in tracked_paths:
                    report["orphan_files"].append(rel)

    # Orphan fragments (source entry doesn't exist)
    for frag in fragments:
        for src in (frag.get("references") or []):
            if src not in all_ids:
                report["orphan_fragments"].append(frag["id"])
                break

    # Missing links (references to nonexistent IDs)
    for eid, entry in wiki_entries.items():
        for ref in (entry.get("references") or []):
            if ref not in all_ids:
                report["missing_links"].append({"entry": eid, "missing_ref": ref})

    # Broken paths (wiki_path points to nonexistent file)
    for eid, entry in wiki_entries.items():
        wp = entry.get("wiki_path")
        if wp and not os.path.exists(wp):
            report["broken_paths"].append(eid)

    # Stale stubs (stub entries, sorted by ingested date)
    stubs = [e for e in wiki_entries.values() if e.get("status") == "stub"]
    stubs.sort(key=lambda e: e.get("ingested", ""))
    report["stale_stubs"] = [{"id": e["id"], "ingested": e.get("ingested")} for e in stubs]

    # Tag gaps (tags used by only one entry)
    tag_counts = {}
    for entry in wiki_entries.values():
        for tag in (entry.get("tags") or []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    report["tag_gaps"] = [tag for tag, count in tag_counts.items() if count == 1]

    # Untagged rendered entries
    report["untagged"] = [
        eid for eid, e in wiki_entries.items()
        if e.get("status") == "rendered" and not (e.get("tags") or [])
    ]

    # Unsupported claims (claim fragments referenced by only one source)
    claim_frags = [f for f in fragments if f.get("type") == "claim"]
    for frag in claim_frags:
        refs = frag.get("references") or []
        if len(refs) <= 1:
            report["unsupported_claims"].append(frag["id"])

    # Stale syntheses (synthesis with newer related entries at 2+ shared tags)
    syntheses = [e for e in wiki_entries.values() if e.get("type") == "synthesis"]
    for syn in syntheses:
        syn_rendered = syn.get("rendered") or syn.get("ingested", "")
        syn_tags = set(syn.get("tags") or [])
        stale = []
        for eid, entry in wiki_entries.items():
            if eid == syn["id"]:
                continue
            if entry.get("ingested", "") <= syn_rendered:
                continue
            if len(syn_tags & set(entry.get("tags") or [])) >= 2:
                stale.append(eid)
        if stale:
            report["stale_syntheses"].append({"id": syn["id"], "newer_related": stale})

    print(json.dumps(report, ensure_ascii=False))


def cmd_get_entries_batch(args):
    raw = sys.stdin.read().strip()
    ids = json.loads(raw)
    entries = load_index(args.index)
    id_set = set(ids)
    results = [
        {"id": e["id"], "title": e.get("title", ""), "tags": e.get("tags", [])}
        for e in entries
        if e["id"] in id_set and not e["id"].startswith("frag-")
    ]
    print(json.dumps(results, ensure_ascii=False))


def cmd_get_orphan_ids(_args):
    raw = sys.stdin.read().strip()
    report = json.loads(raw)
    print(json.dumps(report.get("orphan_entries", []), ensure_ascii=False))


def cmd_get_fragments_by_type(args):
    entries = load_index(args.index)
    types = {t.strip() for t in args.types.split(",")}
    fragments = [
        e for e in entries
        if e["id"].startswith("frag-") and e.get("type") in types
    ]
    if args.group_by == "tag":
        grouped = {}
        for frag in fragments:
            for tag in frag.get("tags", []):
                grouped.setdefault(tag, []).append(frag)
        print(json.dumps(grouped, ensure_ascii=False))
    else:
        print(json.dumps(fragments, ensure_ascii=False))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def add_index_arg(parser):
    parser.add_argument("--index", default=DEFAULT_INDEX, help="Path to index.jsonl")


def main():
    parser = argparse.ArgumentParser(
        description="Wiki JSONL index engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Subcommands by workflow stage:

  Index CRUD:
    create-entry   build entry JSON from flags (stdout)
    add            add entry from stdin to index  [exit 2 on collision]
    get            fetch entry by ID
    update         set fields on an existing entry
    delete         remove entry from index
    exists         check presence by ID  [exit 0/1/2]
    list           filter entries by status/type/tag/project
    add-batch      bulk-add from JSON array on stdin
    update-batch   bulk-update by filter or input file
    wiki-path      resolve wiki file path from ID (pure, no I/O)

  Fragments:
    add-fragments-batch    bulk-add fragments from stdin
    delete-fragments       remove all fragments for a source ID
    delete-fragments-batch remove fragments by ID list from stdin

  Build prep  (/build, /build --fast):
    resolve-connections    compute backlinks and related_candidates
    prep-rank-batches      partition connection map for LLM ranking
    fast-rank-connections  rank candidates by shared-tag count (no LLM)
    prep-moc-batches       prepare per-group data for MOC narrative gen
    update-moc-entries     upsert MOC entries from manifest (stdin)

  MOC & tags:
    list-moc-groups        emit tag→ids and project→ids maps
    get-unresolved-tags    find tags absent from alias map (stdin)
    normalize-tags         apply alias map to all entry/fragment tags
    sync-frontmatter-tags  sync wiki page frontmatter tags with index

  Query & lint  (/query, /lint):
    search                 keyword search across titles and tags
    check-stale            find synthesis entries with stale sources
    lint-structural        run all mechanical lint checks
    get-fragments-by-type  filter fragments by type, optionally grouped by tag
    get-entries-batch      return {id,title,tags} for a JSON array of IDs (stdin)
    get-orphan-ids         extract orphan entry IDs from lint-structural report (stdin)

Run `uv run wiki_index.py <subcommand> --help` for flags.""",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # wiki-path
    wp = sub.add_parser("wiki-path", help="Resolve file path from entry ID (pure function)")
    wp.add_argument("id")

    # create-entry
    ce = sub.add_parser("create-entry", help="Build entry JSON from CLI flags (prints to stdout)")
    ce.add_argument("--id", required=True)
    ce.add_argument("--type", required=True)
    ce.add_argument("--source-type", required=True)
    ce.add_argument("--source-path")
    ce.add_argument("--title")
    ce.add_argument("--tags")
    ce.add_argument("--project")
    ce.add_argument("--source-name")
    ce.add_argument("--zotero-uri")
    ce.add_argument("--pdf-uri")
    ce.add_argument("--arxiv-id")
    ce.add_argument("--citation")
    ce.add_argument("--year")
    ce.add_argument("--status", default="stub")
    ce.add_argument("--wiki-path")
    ce.add_argument("--references")
    ce.add_argument("--locked", action="store_true", default=False)
    add_index_arg(ce)

    # add
    add_p = sub.add_parser("add", help="Add entry JSON from stdin to index (exit 2 on collision)")
    add_index_arg(add_p)

    # get
    get_p = sub.add_parser("get", help="Get entry by ID")
    get_p.add_argument("id")
    add_index_arg(get_p)

    # update
    upd_p = sub.add_parser("update", help="Update entry fields")
    upd_p.add_argument("id")
    upd_p.add_argument("--status")
    upd_p.add_argument("--type")
    upd_p.add_argument("--tags")
    upd_p.add_argument("--wiki-path")
    upd_p.add_argument("--rendered")
    upd_p.add_argument("--locked", type=lambda x: x.lower() == "true" if isinstance(x, str) else bool(x), default=None)
    upd_p.add_argument("--references")
    upd_p.add_argument("--project")
    upd_p.add_argument("--source-path")
    upd_p.add_argument("--last-improved")

    # delete
    del_p = sub.add_parser("delete", help="Remove an entry from the index by ID")
    del_p.add_argument("id")
    add_index_arg(del_p)
    add_index_arg(upd_p)

    # exists
    ex_p = sub.add_parser("exists", help="Check if ID exists (exit 0=found, 1=not found, 2=collision)")
    ex_p.add_argument("id")
    ex_p.add_argument("--source-path", help="If provided, exit 2 when source_path differs")
    add_index_arg(ex_p)

    # list
    ls_p = sub.add_parser("list", help="List entries with optional filters")
    ls_p.add_argument("--status")
    ls_p.add_argument("--type")
    ls_p.add_argument("--tag")
    ls_p.add_argument("--project")
    ls_p.add_argument("--source-name")
    ls_p.add_argument("--format", choices=["json", "ids", "count"], default="json")
    add_index_arg(ls_p)

    # add-batch
    ab_p = sub.add_parser("add-batch", help="Add JSON array from stdin to index")
    add_index_arg(ab_p)

    # update-batch
    ub_p = sub.add_parser("update-batch", help="Update multiple entries")
    ub_p.add_argument("--filter", help="field=value filter")
    ub_p.add_argument("--set", help="field=value to set")
    ub_p.add_argument("--input", help="Path to JSON array of {id, updates} objects")
    add_index_arg(ub_p)

    # add-fragments-batch
    afb_p = sub.add_parser("add-fragments-batch", help="Add fragments from stdin")
    add_index_arg(afb_p)

    # delete-fragments
    df_p = sub.add_parser("delete-fragments", help="Delete all fragments for a source ID")
    df_p.add_argument("source_id")
    add_index_arg(df_p)

    # delete-fragments-batch
    dfb_p = sub.add_parser("delete-fragments-batch", help="Delete fragments by ID list from stdin")
    add_index_arg(dfb_p)

    # search
    srch_p = sub.add_parser("search", help="Keyword search across titles, fragment titles, and tags")
    srch_p.add_argument("--query", required=True)
    srch_p.add_argument("--limit", type=int, default=100)
    add_index_arg(srch_p)

    # resolve-connections
    rc_p = sub.add_parser("resolve-connections", help="Compute backlinks and related entries")
    rc_p.add_argument("--journal-refs", help="Path to journal refs JSON file")
    rc_p.add_argument("--id", help="Scope to single entry + neighbors")
    rc_p.add_argument("--tag-aliases", help="Path to tag alias JSON file (non-canonical → canonical)")
    add_index_arg(rc_p)

    # check-stale
    cs_p = sub.add_parser("check-stale", help="Find stale synthesis entries")
    add_index_arg(cs_p)

    # score-idea
    si_p = sub.add_parser("score-idea", help="Score fragments by relevance to an idea (search terms + tags via stdin)")
    si_p.add_argument("--top-n", type=int, default=30, help="Max fragments to return (default: 30)")
    add_index_arg(si_p)

    # list-moc-groups
    lmg_p = sub.add_parser("list-moc-groups", help="Return tag→ids and project→ids maps")
    lmg_p.add_argument("--tag-aliases", help="Path to tag alias JSON file (non-canonical → canonical)")
    add_index_arg(lmg_p)

    # prep-rank-batches
    prb_p = sub.add_parser("prep-rank-batches", help="Partition connection map into batch files for build ranking")
    prb_p.add_argument("--connection-map", required=True, help="Path to connection_map.json")
    prb_p.add_argument("--out-prefix", default="/tmp/build_batch_", help="Output file prefix (default: /tmp/build_batch_)")
    prb_p.add_argument("--batch-size", type=int, default=10, help="Entries per batch (default: 10)")
    prb_p.add_argument("--max-candidates", type=int, default=25, help="Max candidates per entry, ranked by shared tag count (default: 25)")
    add_index_arg(prb_p)

    # fast-rank-connections
    frc_p = sub.add_parser("fast-rank-connections", help="Rank related_candidates by shared_tag count (no LLM)")
    frc_p.add_argument("--top-n", type=int, default=8, help="Number of related entries to keep (default: 8)")

    # prep-moc-batches
    pmb_p = sub.add_parser("prep-moc-batches", help="Prepare per-group data for MOC narrative generation")
    pmb_p.add_argument("--min-entries", type=int, default=10, help="Minimum group size to include (default: 10)")
    pmb_p.add_argument("--out-prefix", default="/tmp/moc_data_batch_", help="Batch file prefix (default: /tmp/moc_data_batch_)")
    pmb_p.add_argument("--entry-dir", default="/tmp", help="Directory for per-MOC entry JSON files (default: /tmp)")
    pmb_p.add_argument("--small-batch-size", type=int, default=10, help="Groups per batch for small groups ≤5 (default: 10)")
    pmb_p.add_argument("--medium-batch-size", type=int, default=5, help="Groups per batch for medium groups 6-10 (default: 5)")
    pmb_p.add_argument("--max-payload-kb", type=int, default=48, help="Split batches exceeding this JSON payload size in KB (default: 48)")
    pmb_p.add_argument("--moc-manifest-out", default=None, help="Write update-moc-entries manifest to this path")
    add_index_arg(pmb_p)

    # get-unresolved-tags
    gut_p = sub.add_parser("get-unresolved-tags", help="Return {tag: count} for tags not yet in alias map (reads list-moc-groups output from stdin)")
    gut_p.add_argument("--aliases", help="Path to existing alias JSON (non-canonical → canonical)")

    # get-tag-counts
    gtc_p = sub.add_parser("get-tag-counts", help="Return {tag: count} for all tags in index")
    add_index_arg(gtc_p)

    # get-canonical-tags
    gct_p = sub.add_parser("get-canonical-tags", help="Tags with N+ entries (non-fragment)")
    gct_p.add_argument("--min-count", type=int, default=10, help="Minimum entry count (default: 10)")
    add_index_arg(gct_p)

    # normalize-tags
    nt_p = sub.add_parser("normalize-tags", help="Apply alias map to all entry and fragment tags in-place")
    nt_p.add_argument("--aliases", required=True, help="Path to JSON alias map (non-canonical → canonical)")
    add_index_arg(nt_p)

    # sync-frontmatter-tags
    sft_p = sub.add_parser("sync-frontmatter-tags", help="Sync wiki page frontmatter tags with index tags")
    add_index_arg(sft_p)

    # filter-dirty-mocs
    fdm_p = sub.add_parser("filter-dirty-mocs",
        help="Filter MOC groups to only those needing narrative regeneration")
    fdm_p.add_argument("--force", action="store_true",
        help="Emit all groups regardless of dirty state")
    add_index_arg(fdm_p)

    # update-moc-entries
    ume_p = sub.add_parser("update-moc-entries", help="Create/update MOC index entries from stdin")
    add_index_arg(ume_p)

    # lint-structural
    lint_p = sub.add_parser("lint-structural", help="Run all mechanical lint checks")
    lint_p.add_argument("--wiki-dir", default="wiki")
    add_index_arg(lint_p)

    # get-fragments-by-type
    gft_p = sub.add_parser(
        "get-fragments-by-type",
        help="Return fragments filtered by type, optionally grouped by tag"
    )
    gft_p.add_argument("--types", required=True, help="Comma-separated types (e.g. claim,finding)")
    gft_p.add_argument("--group-by", dest="group_by", choices=["tag"], default=None)
    add_index_arg(gft_p)

    # get-entries-batch
    geb_p = sub.add_parser(
        "get-entries-batch",
        help="Return {id, title, tags} for a JSON array of IDs from stdin"
    )
    add_index_arg(geb_p)

    # get-orphan-ids
    sub.add_parser(
        "get-orphan-ids",
        help="Extract orphan_entries ID list from lint-structural report (stdin)"
    )

    args = parser.parse_args()
    dispatch = {
        "wiki-path": cmd_wiki_path,
        "create-entry": cmd_create_entry,
        "add": cmd_add,
        "get": cmd_get,
        "update": cmd_update,
        "exists": cmd_exists,
        "list": cmd_list,
        "add-batch": cmd_add_batch,
        "update-batch": cmd_update_batch,
        "add-fragments-batch": cmd_add_fragments_batch,
        "delete-fragments": cmd_delete_fragments,
        "delete-fragments-batch": cmd_delete_fragments_batch,
        "search": cmd_search,
        "resolve-connections": cmd_resolve_connections,
        "check-stale": cmd_check_stale,
        "score-idea": cmd_score_idea,
        "list-moc-groups": cmd_list_moc_groups,
        "filter-dirty-mocs": cmd_filter_dirty_mocs,
        "update-moc-entries": cmd_update_moc_entries,
        "lint-structural": cmd_lint_structural,
        "get-fragments-by-type": cmd_get_fragments_by_type,
        "get-entries-batch": cmd_get_entries_batch,
        "get-orphan-ids": cmd_get_orphan_ids,
        "get-unresolved-tags": cmd_get_unresolved_tags,
        "get-tag-counts": cmd_get_tag_counts,
        "get-canonical-tags": cmd_get_canonical_tags,
        "normalize-tags": cmd_normalize_tags,
        "sync-frontmatter-tags": cmd_sync_frontmatter_tags,
        "delete": cmd_delete,
        "prep-rank-batches": cmd_prep_rank_batches,
        "fast-rank-connections": cmd_fast_rank_connections,
        "prep-moc-batches": cmd_prep_moc_batches,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
