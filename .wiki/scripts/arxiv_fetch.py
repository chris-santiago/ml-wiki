# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""
arXiv metadata fetcher and PDF downloader.

Subcommands:
  fetch <arxiv-id>                    Fetch metadata and emit index-ready JSON entry
  download-pdf <arxiv-id> <dest-path> Download PDF to dest-path
  normalize <arxiv-id>                Print canonical arXiv ID (strip prefix/version/URL)
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


ATOM_API = "https://export.arxiv.org/api/query?id_list={}"
PDF_URL = "https://arxiv.org/pdf/{}"
ABS_URL = "https://arxiv.org/abs/{}"

# XML namespaces used in the Atom feed
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}

# Stop words excluded from the first-content-word slug component
_STOP = frozenset({
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "and", "or",
    "with", "via", "from", "by", "is", "are", "was", "be", "as", "into",
    "through", "about", "towards", "toward", "using", "based",
})


# ---------------------------------------------------------------------------
# ID normalization
# ---------------------------------------------------------------------------

def normalize_id(raw: str) -> str:
    """Return a canonical bare arXiv ID, e.g. '2312.00752'."""
    s = raw.strip()

    # Strip common URL prefixes
    for prefix in ("https://arxiv.org/abs/", "http://arxiv.org/abs/",
                   "https://arxiv.org/pdf/", "http://arxiv.org/pdf/",
                   "https://ar5iv.labs.arxiv.org/html/"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break

    # Strip arXiv: or arxiv: prefix
    s = re.sub(r"^[Aa]r[Xx]iv:", "", s)

    # Strip .pdf suffix
    s = re.sub(r"\.pdf$", "", s, flags=re.IGNORECASE)

    # Strip version suffix (v1, v2, ...)
    s = re.sub(r"v\d+$", "", s)

    # Validate: new-format YYMM.NNNNN or old-format cs/0601002
    if not re.match(r"^\d{4}\.\d{4,5}$", s) and \
       not re.match(r"^[a-z\-]+(\.[A-Z]{2})?/\d{7}$", s):
        raise ValueError(f"Cannot parse arXiv ID from: {raw!r}")

    return s


# ---------------------------------------------------------------------------
# Slug generation
# ---------------------------------------------------------------------------

def _slug_surname(author_str: str) -> str:
    """Extract lowercase surname from 'Firstname Lastname' or 'Lastname, Firstname'."""
    a = author_str.strip()
    if "," in a:
        surname = a.split(",")[0]
    else:
        parts = a.split()
        surname = parts[-1] if parts else a
    # lowercase, letters only
    return re.sub(r"[^a-z]", "", surname.lower())


def _slug_first_word(title: str) -> str:
    """Return the first non-stop lowercase word from the title."""
    words = re.findall(r"[a-zA-Z]+", title)
    for w in words:
        lw = w.lower()
        if lw not in _STOP:
            return lw
    return words[0].lower() if words else "paper"


def make_slug(first_author: str, year: int, title: str) -> str:
    """Generate a BibTeX-style citekey: surname + year + firstword."""
    surname = _slug_surname(first_author)
    word = _slug_first_word(title)
    return f"{surname}{year}{word}"


# ---------------------------------------------------------------------------
# Atom XML parsing
# ---------------------------------------------------------------------------

def _text(el, path: str, ns: dict) -> str:
    node = el.find(path, ns)
    return node.text.strip() if node is not None and node.text else ""


def parse_atom(xml_bytes: bytes) -> dict:
    """Parse arXiv Atom feed XML and return a metadata dict."""
    root = ET.fromstring(xml_bytes)

    total = root.find("opensearch:totalResults", NS)
    if total is not None and total.text and total.text.strip() == "0":
        raise ValueError("arXiv returned 0 results — ID not found or invalid")

    entry = root.find("atom:entry", NS)
    if entry is None:
        raise ValueError("No <entry> element in arXiv Atom response")

    # Title — collapse internal whitespace
    title = re.sub(r"\s+", " ", _text(entry, "atom:title", NS))

    # Authors
    authors = []
    for a in entry.findall("atom:author", NS):
        name = _text(a, "atom:name", NS)
        if name:
            authors.append(name)

    # Published year
    published = _text(entry, "atom:published", NS)
    year_match = re.search(r"(\d{4})", published)
    year = int(year_match.group(1)) if year_match else None

    # Abstract
    abstract = re.sub(r"\s+", " ", _text(entry, "atom:summary", NS))

    # Canonical arXiv ID from <id> URL
    id_url = _text(entry, "atom:id", NS)
    raw_id = id_url.split("/abs/")[-1] if "/abs/" in id_url else id_url
    arxiv_id = normalize_id(raw_id)

    # Categories
    categories = []
    for c in entry.findall("atom:category", NS):
        term = c.get("term", "")
        if term:
            categories.append(term)

    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "authors": authors,
        "year": year,
        "abstract": abstract,
        "categories": categories,
    }


# ---------------------------------------------------------------------------
# Citation builder
# ---------------------------------------------------------------------------

def build_citation(meta: dict) -> str:
    authors = meta["authors"]
    year = meta["year"] or "n.d."
    if not authors:
        author_str = "Unknown"
    elif len(authors) == 1:
        author_str = authors[0]
    elif len(authors) <= 3:
        author_str = ", ".join(authors[:-1]) + " & " + authors[-1]
    else:
        author_str = authors[0] + " et al."
    return f"{author_str} ({year})"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def fetch_url(url: str, binary: bool = False):
    req = urllib.request.Request(url, headers={"User-Agent": "ml-wiki/1.0 (research tool)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read() if binary else resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} fetching {url}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error fetching {url}: {e.reason}") from e


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_normalize(args):
    try:
        print(normalize_id(args.arxiv_id))
    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def cmd_fetch(args):
    try:
        arxiv_id = normalize_id(args.arxiv_id)
    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

    url = ATOM_API.format(arxiv_id)
    try:
        xml_bytes = fetch_url(url, binary=True)
    except RuntimeError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

    try:
        meta = parse_atom(xml_bytes)
    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

    first_author = meta["authors"][0] if meta["authors"] else "unknown"
    year = meta["year"] or 0
    slug = make_slug(first_author, year, meta["title"])
    citation = build_citation(meta)

    # Emit index-compatible entry stub (source_path left blank — fill after download)
    entry = {
        "id": slug,
        "type": "paper",
        "source_type": "pdf",
        "source_path": None,
        "wiki_path": None,
        "zotero_uri": None,
        "pdf_uri": ABS_URL.format(arxiv_id),
        "arxiv_id": arxiv_id,
        "title": meta["title"],
        "citation": citation,
        "year": year,
        "authors": meta["authors"],
        "abstract": meta["abstract"],
        "categories": meta["categories"],
        "tags": [],
        "project": None,
        "source_name": "arxiv",
        "references": [],
        "status": "stub",
        "locked": False,
        "ingested": now_iso(),
        "rendered": None,
    }
    print(json.dumps(entry, ensure_ascii=False))


def cmd_download_pdf(args):
    try:
        arxiv_id = normalize_id(args.arxiv_id)
    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

    dest = args.dest_path
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)

    url = PDF_URL.format(arxiv_id)
    try:
        data = fetch_url(url, binary=True)
    except RuntimeError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

    tmp = dest + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.rename(tmp, dest)
    print(json.dumps({"ok": True, "path": dest, "bytes": len(data)}))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="arxiv_fetch.py",
        description="Fetch arXiv metadata and download PDFs",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # normalize
    norm_p = sub.add_parser("normalize", help="Print canonical arXiv ID")
    norm_p.add_argument("arxiv_id", metavar="arxiv-id")

    # fetch
    fetch_p = sub.add_parser("fetch", help="Fetch metadata and emit index-ready JSON")
    fetch_p.add_argument("arxiv_id", metavar="arxiv-id")

    # download-pdf
    dl_p = sub.add_parser("download-pdf", help="Download PDF to dest-path")
    dl_p.add_argument("arxiv_id", metavar="arxiv-id")
    dl_p.add_argument("dest_path", metavar="dest-path")

    args = parser.parse_args()
    dispatch = {"normalize": cmd_normalize, "fetch": cmd_fetch, "download-pdf": cmd_download_pdf}
    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
