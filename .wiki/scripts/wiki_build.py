# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""Build sub-phase functions for the wiki CLI.

Helper functions for the /build command: LISTING block construction,
staleness banner generation, and candidate pre-filtering. Called by
wiki_cli.py's build command.
"""


def build_listing(entries: list[dict], stale_ids: set[str]) -> str:
    """Build MOC LISTING block from index data. Pure Python, no LLM."""
    lines = []
    for e in sorted(entries, key=lambda x: x.get("title", "").lower()):
        stale = "⚠️ " if e["id"] in stale_ids else ""
        year = f" ({e['year']})" if e.get("year") else ""
        lines.append(f"- {stale}[[{e['id']}]] — {e['title']}{year} ({e['type']}, {e['status']})")
    return "\n".join(lines)


def build_banner_list(stale_list: list[dict], entry_lookup: dict[str, dict]) -> list[dict]:
    """Build banner JSON array from stale list. Pure Python, no LLM."""
    banners = []
    for item in stale_list:
        entry_id = item["id"]
        stale_entries = item.get("stale_entries", [])
        entry = entry_lookup.get(entry_id, {})
        wiki_path = entry.get("wiki_path", "")

        if not wiki_path:
            continue

        if entry_id.startswith("syn-") or entry_id.startswith("synthesis-"):
            n = len(stale_entries)
            text = f"> **Stale synthesis** — {n} new entries with shared tags since last render. Run `/render {entry_id}` to refresh."
        elif entry_id.startswith("idea-"):
            if stale_entries == ["never_improved"]:
                text = f"> **Unreviewed idea** — Run `/idea improve {entry_id}` to generate an evidence review."
            else:
                n = len(stale_entries)
                text = f"> **Stale idea** — {n} new entries with shared tags since last improve. Run `/idea improve {entry_id}` to refresh."
        else:
            continue

        banners.append({"wiki_path": wiki_path, "banner_text": text})

    return banners


def prefilter_rank_candidates(target: dict, candidates: list[dict], min_shared: int = 2) -> list[dict]:
    """Conservative pre-filter: remove candidates sharing only broad tags."""
    BROAD_TAGS = {
        "machine-learning", "deep-learning", "neural-networks",
        "optimization", "computational-efficiency",
    }
    target_tags = set(target.get("tags", []))
    result = []
    for c in candidates:
        shared = target_tags & set(c.get("tags", []))
        non_broad = shared - BROAD_TAGS
        if len(shared) >= min_shared or non_broad:
            result.append(c)
    return result
