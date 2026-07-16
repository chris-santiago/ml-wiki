# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Date normalization
# ---------------------------------------------------------------------------

def _parse_year(date_str):
    """Extract a display year (int) from a BBT date string. Returns None if unparseable."""
    if not date_str:
        return None
    s = date_str.strip()
    # YYYY-MM-DD or YYYY-M-D
    m = re.match(r'^(\d{4})-\d{1,2}-\d{1,2}$', s)
    if m:
        return int(m.group(1))
    # YYYY-MM
    m = re.match(r'^(\d{4})-\d{2}$', s)
    if m:
        return int(m.group(1))
    # YYYY
    m = re.match(r'^(\d{4})$', s)
    if m:
        return int(m.group(1))
    # MM/YYYY or M/YYYY
    m = re.match(r'^\d{1,2}/(\d{4})$', s)
    if m:
        return int(m.group(1))
    # Month D, YYYY or Month DD, YYYY
    m = re.match(r'^[A-Za-z]+ \d{1,2},\s*(\d{4})$', s)
    if m:
        return int(m.group(1))
    # Fallback: any 4-digit year in the string
    m = re.search(r'(\d{4})', s)
    if m:
        return int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Citation construction
# ---------------------------------------------------------------------------

def _format_authors(creators):
    authors = [c for c in creators if c.get("creatorType") == "author"]
    if not authors:
        editors = [c for c in creators if c.get("creatorType") == "editor"]
        authors = editors
    if not authors:
        return "Unknown"

    def fmt(c, idx):
        last = c.get("lastName", "")
        first = c.get("firstName", "")
        initial = f"{first[0]}." if first else ""
        if idx == 0:
            return f"{last}, {initial}".strip(", ") if last else initial
        return f"{initial} {last}".strip()

    if len(authors) <= 3:
        parts = [fmt(a, i) for i, a in enumerate(authors)]
        if len(parts) == 1:
            return parts[0]
        return ", ".join(parts[:-1]) + " & " + parts[-1]
    else:
        return fmt(authors[0], 0) + " et al."


def _build_citation(item):
    creators = item.get("creators", [])
    authors = _format_authors(creators)
    year = _parse_year(item.get("date", ""))
    year_str = f"({year})" if year else ""
    title = item.get("title", "Untitled")
    itype = item.get("itemType", "")

    if itype == "journalArticle":
        journal = item.get("publicationTitle") or item.get("journalAbbreviation") or "Unknown Journal"
        volume = item.get("volume", "")
        issue = item.get("issue", "")
        pages = item.get("pages", "")
        vol_issue = f"{volume}({issue})" if volume and issue else volume or issue
        location = ", ".join(filter(None, [vol_issue, pages]))
        return f"{authors} {year_str}. {title}. *{journal}*" + (f", {location}." if location else ".")

    elif itype == "conferencePaper":
        proceedings = item.get("proceedingsTitle") or item.get("conferenceName") or "Unknown Proceedings"
        pages = item.get("pages", "")
        return f"{authors} {year_str}. {title}. *{proceedings}*" + (f", {pages}." if pages else ".")

    elif itype == "preprint":
        repo = item.get("repository") or item.get("archiveID") or item.get("libraryCatalog") or "Preprint"
        return f"{authors} {year_str}. {title}. *{repo}*."

    elif itype == "book":
        publisher = item.get("publisher", "")
        return f"{authors} {year_str}. *{title}*." + (f" {publisher}." if publisher else "")

    elif itype == "bookSection":
        book_title = item.get("bookTitle", "Unknown Book")
        pages = item.get("pages", "")
        publisher = item.get("publisher", "")
        parts = [f"In *{book_title}*"]
        if pages:
            parts.append(f"pp. {pages}")
        if publisher:
            parts.append(publisher)
        return f"{authors} {year_str}. {title}. " + ", ".join(parts) + "."

    else:
        return f"{authors} {year_str}. {title}."


# ---------------------------------------------------------------------------
# Tag normalization
# ---------------------------------------------------------------------------

def _normalize_tags(raw_tags):
    tags = []
    for t in raw_tags:
        tag = t.get("tag", "").strip().lower()
        if tag:
            slug = re.sub(r'\s+', '-', tag)
            slug = re.sub(r'[^\w\-]', '', slug)
            if slug:
                tags.append(slug)
    return list(dict.fromkeys(tags))  # deduplicate, preserve order


# ---------------------------------------------------------------------------
# URI construction
# ---------------------------------------------------------------------------

def _extract_pdf_attachment(item):
    """Return first PDF attachment or None."""
    for att in item.get("attachments", []):
        path = att.get("path", "")
        title = att.get("title", "")
        if path.lower().endswith(".pdf") or "pdf" in title.lower():
            return att
    return None


def _pdf_uri(attachment):
    if not attachment:
        return None
    select = attachment.get("select", "")
    # select = zotero://select/library/items/KEY → open-pdf URI
    m = re.search(r'/items/([A-Z0-9]+)$', select)
    if m:
        return f"zotero://open-pdf/library/items/{m.group(1)}"
    return None


# ---------------------------------------------------------------------------
# Entry normalization
# ---------------------------------------------------------------------------

def _normalize_entry(item, source_name=None):
    citekey = item.get("citationKey", "")
    pdf_att = _extract_pdf_attachment(item)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "id": citekey,
        "type": "paper",
        "source_type": "zotero",
        "source_path": (pdf_att.get("path") if pdf_att else None),
        "wiki_path": None,
        "zotero_uri": item.get("select"),
        "pdf_uri": _pdf_uri(pdf_att),
        "title": item.get("title", "Untitled"),
        "citation": _build_citation(item),
        "year": _parse_year(item.get("date", "")),
        "tags": _normalize_tags(item.get("tags", [])),
        "project": None,
        "source_name": source_name,
        "references": [],
        "status": "stub",
        "locked": False,
        "ingested": now,
        "rendered": None,
    }


# ---------------------------------------------------------------------------
# BBT loading
# ---------------------------------------------------------------------------

def _load_bbt(path):
    expanded = os.path.expanduser(path)
    with open(expanded) as f:
        data = json.load(f)
    items = data.get("items", [])
    # Filter out bare attachment items (no citationKey)
    return [item for item in items if item.get("citationKey")]


def _load_index_ids(index_path):
    ids = set()
    expanded = os.path.expanduser(index_path)
    if not os.path.exists(expanded):
        return ids
    with open(expanded) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entry = json.loads(line)
                    ids.add(entry.get("id", ""))
                except json.JSONDecodeError:
                    pass
    return ids


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_lookup(args):
    items = _load_bbt(args.bbt)
    found = any(item.get("citationKey") == args.citekey for item in items)
    sys.exit(0 if found else 1)


def cmd_parse(args):
    items = _load_bbt(args.bbt)
    source_name = getattr(args, "source_name", None)
    if args.citekey:
        item = next((i for i in items if i.get("citationKey") == args.citekey), None)
        if item is None:
            print(json.dumps({"error": f"citekey not found: {args.citekey}"}), file=sys.stderr)
            sys.exit(1)
        print(json.dumps(_normalize_entry(item, source_name=source_name), ensure_ascii=False))
    else:
        entries = [_normalize_entry(item, source_name=source_name) for item in items]
        print(json.dumps(entries, ensure_ascii=False))


def cmd_diff(args):
    items = _load_bbt(args.bbt)
    existing_ids = _load_index_ids(args.index)
    new_entries = [
        _normalize_entry(item)
        for item in items
        if item.get("citationKey") not in existing_ids
    ]
    print(json.dumps(new_entries, ensure_ascii=False))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="BetterBibTeX JSON parser")
    sub = parser.add_subparsers(dest="command", required=True)

    lookup_p = sub.add_parser("lookup", help="Boolean citekey check (exit 0=found, 1=not found)")
    lookup_p.add_argument("--bbt", required=True, help="Path to BBT JSON export")
    lookup_p.add_argument("citekey", help="Citekey to look up")

    parse_p = sub.add_parser("parse", help="Return normalized entries as JSON")
    parse_p.add_argument("--bbt", required=True, help="Path to BBT JSON export")
    parse_p.add_argument("--citekey", help="Return single entry by citekey")
    parse_p.add_argument("--source-name", dest="source_name", help="Inject source_name into each entry")

    diff_p = sub.add_parser("diff", help="Return entries not yet in the index")
    diff_p.add_argument("--bbt", required=True, help="Path to BBT JSON export")
    diff_p.add_argument("--index", required=True, help="Path to index.jsonl")

    args = parser.parse_args()
    if args.command == "lookup":
        cmd_lookup(args)
    elif args.command == "parse":
        cmd_parse(args)
    elif args.command == "diff":
        cmd_diff(args)


if __name__ == "__main__":
    main()
