# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///

import argparse
import json
import os
import sys

import yaml

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.yaml")


def load_config(path=CONFIG_PATH):
    with open(path) as f:
        return yaml.safe_load(f)


def cmd_get(args):
    cfg = load_config()
    wiki = cfg.get("research_wiki", {})

    if args.key == "sources":
        sources = wiki.get("sources", [])
        if args.name:
            match = next((s for s in sources if s["name"] == args.name), None)
            if match is None:
                print(json.dumps({"error": f"source '{args.name}' not found"}), file=sys.stderr)
                sys.exit(1)
            print(json.dumps(match))
        elif args.type:
            filtered = [s for s in sources if s.get("type") == args.type]
            print(json.dumps(filtered))
        else:
            print(json.dumps(sources))

    elif args.key == "wiki_dir":
        print(json.dumps(wiki.get("wiki_dir", "./wiki")))

    elif args.key == "index_path":
        print(json.dumps(wiki.get("index_path", "./.wiki/index.jsonl")))

    elif args.key == "default_render_depth":
        print(json.dumps(wiki.get("default_render_depth", "shallow")))

    elif args.key == "tag_aliases":
        print(json.dumps(wiki.get("tag_aliases") or {}))

    elif args.key == "tag_blocklist":
        print(json.dumps(wiki.get("tag_blocklist") or []))

    elif args.key == "llm":
        print(json.dumps(wiki.get("llm") or {}))

    else:
        print(json.dumps({"error": f"unknown key '{args.key}'"}), file=sys.stderr)
        sys.exit(1)


def _valid_alias_map(obj):
    """A flat {tag: canonical} map: non-empty string keys, string values.

    An empty-string value is allowed (it marks a tag for removal). Rejects
    wrong-shaped input such as a {"aliases": {...}} wrapper.
    """
    return isinstance(obj, dict) and all(
        isinstance(k, str) and k.strip() and isinstance(v, str)
        for k, v in obj.items()
    )


def cmd_set_aliases(args):
    with open(args.aliases_file) as f:
        new_aliases = json.load(f)
    if not _valid_alias_map(new_aliases):
        print(
            'set-aliases: expected a flat {"tag": "canonical"} JSON object of '
            "tag strings; got malformed input.",
            file=sys.stderr,
        )
        sys.exit(1)
    cfg = load_config()
    cfg["research_wiki"]["tag_aliases"] = new_aliases
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    os.rename(tmp, CONFIG_PATH)
    print(json.dumps({"saved": len(new_aliases)}))


def cmd_merge_aliases(args):
    existing = {}
    if args.existing:
        with open(args.existing) as f:
            existing = json.load(f)
    with open(args.new_aliases) as f:
        new_aliases = json.load(f)
    merged = {**existing, **new_aliases}
    print(json.dumps(merged))


def cmd_validate(args):
    path = args.config or CONFIG_PATH
    errors = []

    try:
        with open(path) as f:
            cfg = yaml.safe_load(f)
    except FileNotFoundError:
        print(json.dumps({"valid": False, "errors": [f"config not found: {path}"]}))
        sys.exit(1)
    except yaml.YAMLError as e:
        print(json.dumps({"valid": False, "errors": [f"invalid YAML: {e}"]}))
        sys.exit(1)

    wiki = cfg.get("research_wiki", {})
    if not wiki:
        errors.append("missing 'research_wiki' key")

    wiki_dir = wiki.get("wiki_dir", "")
    if wiki_dir and not os.path.exists(wiki_dir):
        errors.append(f"wiki_dir not found: {wiki_dir}")

    for source in wiki.get("sources", []):
        name = source.get("name", "<unnamed>")
        bbt = source.get("bbt_export_path")
        if bbt:
            bbt_expanded = os.path.expanduser(bbt)
            if not os.path.exists(bbt_expanded):
                errors.append(f"source '{name}': bbt_export_path not found: {bbt}")
            else:
                try:
                    import json as _json
                    with open(bbt_expanded) as f:
                        data = _json.load(f)
                    if "items" not in data:
                        errors.append(f"source '{name}': BBT JSON missing 'items' array")
                except Exception as e:
                    errors.append(f"source '{name}': could not parse BBT JSON: {e}")

        zotero_storage = source.get("zotero_storage")
        if zotero_storage:
            expanded = os.path.expanduser(zotero_storage)
            if not os.path.exists(expanded):
                errors.append(f"source '{name}': zotero_storage not found: {zotero_storage} (optional, skipping)")

        journal_path = source.get("index_path")
        if journal_path:
            expanded = os.path.expanduser(journal_path)
            if not os.path.exists(expanded):
                errors.append(f"source '{name}': journal index_path not found: {journal_path}")

    if errors:
        print(json.dumps({"valid": False, "errors": errors}))
        sys.exit(1)
    else:
        print(json.dumps({"valid": True}))


def main():
    parser = argparse.ArgumentParser(
        description="Wiki config reader — PyYAML boundary for .wiki/config.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Subcommands:

  Read config:
    get sources             list all configured sources (JSON array)
    get sources --name X    get one source by name
    get sources --type X    filter sources by type (zotero, arxiv, ml-journal, paste)
    get wiki_dir            wiki content directory path
    get index_path          path to index.jsonl
    get default_render_depth  shallow or deep
    get tag_aliases         current alias map (non-canonical → canonical)
    get llm                 LLM config (base_url, models, params)

  Tag aliases:
    set-aliases <file>      overwrite tag_aliases in config.yaml from JSON file
    merge-aliases <file>    merge existing + new alias JSON → stdout (does not write config)

  Validation:
    validate                check that all config paths exist and are readable

Run `uv run wiki_config.py <subcommand> --help` for flags.""",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    get_p = sub.add_parser("get", help="Get a config value as JSON")
    get_p.add_argument("key", choices=["sources", "wiki_dir", "index_path", "default_render_depth", "tag_aliases", "tag_blocklist", "llm"])
    get_p.add_argument("--name", help="Filter sources by name")
    get_p.add_argument("--type", help="Filter sources by type")

    sa_p = sub.add_parser("set-aliases", help="Write tag_aliases map from JSON file to config.yaml")
    sa_p.add_argument("aliases_file", help="Path to JSON file containing alias map")

    val_p = sub.add_parser("validate", help="Validate config and referenced paths")
    val_p.add_argument("config", nargs="?", help="Path to config.yaml (default: .wiki/config.yaml)")

    ma_p = sub.add_parser("merge-aliases", help="Merge existing + new alias JSON files → stdout")
    ma_p.add_argument("--existing", help="Path to existing alias JSON (e.g. /tmp/existing_aliases.json)")
    ma_p.add_argument("new_aliases", help="Path to new alias JSON to merge in")

    args = parser.parse_args()
    if args.command == "get":
        cmd_get(args)
    elif args.command == "set-aliases":
        cmd_set_aliases(args)
    elif args.command == "validate":
        cmd_validate(args)
    elif args.command == "merge-aliases":
        cmd_merge_aliases(args)


if __name__ == "__main__":
    main()
