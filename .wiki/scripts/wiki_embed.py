# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "sentence-transformers",
# ]
# ///
"""
wiki_embed.py — semantic scoring for wiki connections, idea evidence, and tag clustering.

Subcommands:
  score-connections   Re-rank related_candidates by cosine similarity using MiniLM.
  score-idea          Rank fragments by cosine similarity against idea text from stdin.
  cluster-tags        Cluster tag slugs by complete-linkage cosine similarity.
  match-canonicals    Match unresolved tags to nearest canonical by cosine similarity.
"""
import argparse
import json
import os
import sys


INDEX_PATH = ".wiki/index.jsonl"
MODEL_NAME = "all-MiniLM-L6-v2"
DEFAULT_TOP_N = 8
TEXT_EXCERPT_CHARS = 600
CLUSTER_THRESHOLD = 0.75
AUTO_THRESHOLD = 0.90


def load_index(index_path: str) -> dict:
    entries = {}
    if not os.path.exists(index_path):
        return entries
    with open(index_path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    e = json.loads(line)
                    entries[e["id"]] = e
                except (json.JSONDecodeError, KeyError):
                    pass
    return entries


def build_text(entry: dict) -> str:
    """Build a text representation of an entry for embedding."""
    parts = []
    title = entry.get("title") or ""
    if title:
        parts.append(title)
    tags = entry.get("tags") or []
    if tags:
        parts.append(" ".join(tags))
    wiki_path = entry.get("wiki_path") or ""
    if wiki_path and os.path.exists(wiki_path):
        try:
            with open(wiki_path) as f:
                raw = f.read(TEXT_EXCERPT_CHARS * 3)
            lines = raw.splitlines()
            content_lines = []
            for line in lines:
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or stripped.startswith("<!--"):
                    continue
                if stripped.startswith("[[") or stripped.startswith("**References"):
                    break
                content_lines.append(stripped)
                if sum(len(l) for l in content_lines) >= TEXT_EXCERPT_CHARS:
                    break
            excerpt = " ".join(content_lines)
            if excerpt:
                parts.append(excerpt)
        except OSError:
            pass
    return " ".join(parts) or entry.get("id", "")


def cmd_score_idea(args):
    idea_text = sys.stdin.read().strip()
    if not idea_text:
        print("[]", flush=True)
        return

    index = load_index(args.index)
    exclude_id = args.exclude_id
    top_n = args.top_n

    fragments = []
    frag_ids = []
    wiki_entries = {}
    for eid, entry in index.items():
        if eid.startswith("frag-"):
            if exclude_id and exclude_id in (entry.get("references") or []):
                continue
            fragments.append(entry)
            frag_ids.append(eid)
        else:
            wiki_entries[eid] = entry

    if not fragments:
        print("[]", flush=True)
        return

    print(f"Encoding {len(fragments)} fragments with {MODEL_NAME}...", file=sys.stderr, flush=True)

    from sentence_transformers import SentenceTransformer
    import numpy as np

    model = SentenceTransformer(MODEL_NAME)
    frag_texts = [build_text(f) for f in fragments]
    all_texts = [idea_text] + frag_texts
    embeddings = model.encode(all_texts, batch_size=64, show_progress_bar=False, normalize_embeddings=True)

    query_vec = embeddings[0]
    frag_vecs = embeddings[1:]
    scores = np.dot(frag_vecs, query_vec)

    ranked = sorted(zip(frag_ids, fragments, scores), key=lambda x: -x[2])
    top_frags = ranked[:top_n]

    grouped = {}
    for frag_id, frag, score in top_frags:
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
                "id": frag_id,
                "type": frag.get("type", ""),
                "title": frag.get("title", ""),
                "score": round(float(score), 4),
            })

    results = sorted(grouped.values(), key=lambda g: -max(f["score"] for f in g["fragments"]))
    print(json.dumps(results, ensure_ascii=False), flush=True)


def cmd_score_connections(args):
    raw = sys.stdin.read().strip()
    if not raw:
        print("{}", flush=True)
        return
    try:
        connection_map = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    index = load_index(args.index)
    top_n = args.top_n

    # Collect all IDs that need embedding
    all_ids = set(connection_map.keys())
    for conns in connection_map.values():
        for cand in conns.get("related_candidates", []):
            all_ids.add(cand["id"])
    all_ids = sorted(all_ids)

    print(f"Encoding {len(all_ids)} entries with {MODEL_NAME}...", file=sys.stderr, flush=True)

    from sentence_transformers import SentenceTransformer
    import numpy as np

    model = SentenceTransformer(MODEL_NAME)
    texts = [build_text(index.get(eid, {"id": eid})) for eid in all_ids]
    id_to_idx = {eid: i for i, eid in enumerate(all_ids)}

    embeddings = model.encode(texts, batch_size=64, show_progress_bar=True, normalize_embeddings=True)

    print(f"Scoring connections...", file=sys.stderr, flush=True)

    result = {}
    for eid, conns in connection_map.items():
        candidates = conns.get("related_candidates", [])
        if not candidates:
            result[eid] = {
                "references": conns.get("references", []),
                "cited_by": conns.get("cited_by", []),
                "related": [],
            }
            continue

        if eid not in id_to_idx:
            result[eid] = {
                "references": conns.get("references", []),
                "cited_by": conns.get("cited_by", []),
                "related": [{"id": c["id"]} for c in candidates[:top_n]],
            }
            continue

        query_vec = embeddings[id_to_idx[eid]]
        cand_ids = [c["id"] for c in candidates if c["id"] in id_to_idx]
        if not cand_ids:
            result[eid] = {
                "references": conns.get("references", []),
                "cited_by": conns.get("cited_by", []),
                "related": [],
            }
            continue

        cand_matrix = np.array([embeddings[id_to_idx[cid]] for cid in cand_ids])
        sims = np.dot(query_vec, cand_matrix.T)
        ranked_indices = np.argsort(sims)[::-1][:top_n]
        related = [{"id": cand_ids[i]} for i in ranked_indices]

        result[eid] = {
            "references": conns.get("references", []),
            "cited_by": conns.get("cited_by", []),
            "related": related,
        }

    print(json.dumps(result), flush=True)


def cmd_cluster_tags(args):
    raw = sys.stdin.read().strip()
    if not raw:
        print(json.dumps({"clusters": [], "isolated": []}), flush=True)
        return

    try:
        tags = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    if not tags:
        print(json.dumps({"clusters": [], "isolated": []}), flush=True)
        return

    if len(tags) == 1:
        print(json.dumps({"clusters": [], "isolated": tags}), flush=True)
        return

    threshold = getattr(args, "threshold", CLUSTER_THRESHOLD)
    auto_threshold = getattr(args, "auto_threshold", AUTO_THRESHOLD)

    from sentence_transformers import SentenceTransformer
    import numpy as np

    model = SentenceTransformer(MODEL_NAME)
    texts = [t.replace("-", " ") for t in tags]
    embeddings = model.encode(texts, normalize_embeddings=True)
    sim_matrix = np.dot(embeddings, embeddings.T)

    cluster_members = [[i] for i in range(len(tags))]
    changed = True
    while changed:
        changed = False
        for i in range(len(cluster_members)):
            for j in range(i + 1, len(cluster_members)):
                min_sim = min(
                    float(sim_matrix[a][b])
                    for a in cluster_members[i]
                    for b in cluster_members[j]
                )
                if min_sim >= threshold:
                    cluster_members[i].extend(cluster_members[j])
                    cluster_members.pop(j)
                    changed = True
                    break
            if changed:
                break

    clusters = []
    isolated = []

    for members in cluster_members:
        member_tags = [tags[i] for i in members]
        if len(members) == 1:
            isolated.append(member_tags[0])
            continue

        pairs = [
            float(sim_matrix[members[ia]][members[ib]])
            for ia in range(len(members))
            for ib in range(ia + 1, len(members))
        ]
        min_sim = min(pairs)
        max_sim = max(pairs)
        tier = "auto" if min_sim >= auto_threshold else "llm"
        clusters.append({
            "tags": member_tags,
            "max_sim": round(max_sim, 4),
            "tier": tier,
        })

    print(json.dumps({"clusters": clusters, "isolated": isolated}), flush=True)


def cmd_match_canonicals(args):
    raw = sys.stdin.read().strip()
    if not raw:
        print(json.dumps({"matches": [], "unmatched": []}), flush=True)
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    unresolved = data.get("unresolved", [])
    canonicals = data.get("canonicals", [])

    if not unresolved:
        print(json.dumps({"matches": [], "unmatched": []}), flush=True)
        return
    if not canonicals:
        print(json.dumps({"matches": [], "unmatched": unresolved}), flush=True)
        return

    threshold = getattr(args, "threshold", 0.70)
    top_n = getattr(args, "top_n", 3)

    from sentence_transformers import SentenceTransformer
    import numpy as np

    model = SentenceTransformer(MODEL_NAME)
    all_tags = unresolved + canonicals
    texts = [t.replace("-", " ") for t in all_tags]
    embeddings = model.encode(texts, normalize_embeddings=True)

    unresolved_vecs = embeddings[: len(unresolved)]
    canonical_vecs = embeddings[len(unresolved) :]
    cross_sim = np.dot(unresolved_vecs, canonical_vecs.T)

    matches = []
    unmatched = []
    for i, tag in enumerate(unresolved):
        best_idx = int(np.argmax(cross_sim[i]))
        best_sim = float(cross_sim[i][best_idx])
        if best_sim >= threshold:
            matches.append({
                "tag": tag,
                "canonical": canonicals[best_idx],
                "similarity": round(best_sim, 4),
            })
        else:
            ranked = list(np.argsort(cross_sim[i])[::-1][:top_n])
            nearest = [
                {"canonical": canonicals[int(j)], "similarity": round(float(cross_sim[i][j]), 4)}
                for j in ranked
            ]
            unmatched.append({"tag": tag, "nearest": nearest})

    print(json.dumps({"matches": matches, "unmatched": unmatched}), flush=True)


def main():
    parser = argparse.ArgumentParser(description="Wiki semantic scoring")
    parser.add_argument("--index", default=INDEX_PATH)
    sub = parser.add_subparsers(dest="command")

    sc_p = sub.add_parser("score-connections", help="Re-rank related_candidates by MiniLM cosine similarity")
    sc_p.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)

    si_p = sub.add_parser("score-idea", help="Rank fragments by MiniLM cosine similarity against idea text from stdin")
    si_p.add_argument("--exclude-id", type=str, default=None)
    si_p.add_argument("--top-n", type=int, default=30)

    ct_p = sub.add_parser("cluster-tags", help="Cluster tag slugs by MiniLM cosine similarity")
    ct_p.add_argument("--threshold", type=float, default=CLUSTER_THRESHOLD,
                       help=f"Complete-linkage merge threshold (default: {CLUSTER_THRESHOLD})")
    ct_p.add_argument("--auto-threshold", type=float, default=AUTO_THRESHOLD,
                       help=f"Min sim for auto-merge tier (default: {AUTO_THRESHOLD})")

    mc_p = sub.add_parser("match-canonicals",
                           help="Match unresolved tags to nearest canonical by MiniLM cosine similarity")
    mc_p.add_argument("--threshold", type=float, default=0.70,
                       help="Minimum similarity to report a match (default: 0.70)")
    mc_p.add_argument("--top-n", type=int, default=3,
                       help="Nearest canonicals to include for unmatched tags (default: 3)")

    args = parser.parse_args()
    if args.command == "score-connections":
        cmd_score_connections(args)
    elif args.command == "score-idea":
        cmd_score_idea(args)
    elif args.command == "cluster-tags":
        cmd_cluster_tags(args)
    elif args.command == "match-canonicals":
        cmd_match_canonicals(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
