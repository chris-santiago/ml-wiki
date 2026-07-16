# /// script
# requires-python = ">=3.11"
# dependencies = ["click", "httpx", "jsonschema", "openai"]
# ///

"""Wiki CLI — orchestration layer replacing Claude Code skills.

Each subcommand corresponds to a former SKILL.md file. Calls existing
scripts via subprocess; imports wiki_llm for LLM calls.
"""

import asyncio
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import click

SCRIPTS_DIR = Path(__file__).parent
INDEX_SCRIPT = SCRIPTS_DIR / "wiki_index.py"
RENDER_SCRIPT = SCRIPTS_DIR / "wiki_render.py"
ARXIV_SCRIPT = SCRIPTS_DIR / "arxiv_fetch.py"
CONFIG_SCRIPT = SCRIPTS_DIR / "wiki_config.py"
ZOTERO_SCRIPT = SCRIPTS_DIR / "zotero_reader.py"
EMBED_SCRIPT = SCRIPTS_DIR / "wiki_embed.py"

# Import wiki_util for slug generation and citation extraction
sys.path.insert(0, str(SCRIPTS_DIR))
from wiki_util import slugify, extract_citations, detect_source_type, normalize_tag


def _uv_run(script, *args, input_data=None):
    cmd = ["uv", "run", str(script), *args]
    return subprocess.run(cmd, capture_output=True, text=True, input=input_data)


def _run_index(*args, input_data=None):
    return _uv_run(INDEX_SCRIPT, *args, input_data=input_data)


def _run_render(*args, input_data=None):
    return _uv_run(RENDER_SCRIPT, *args, input_data=input_data)


def _run_arxiv(*args):
    return _uv_run(ARXIV_SCRIPT, *args)


def _run_config(*args):
    return _uv_run(CONFIG_SCRIPT, *args)


def _run_zotero(*args):
    return _uv_run(ZOTERO_SCRIPT, *args)


def _run_embed(*args, input_data=None):
    return _uv_run(EMBED_SCRIPT, *args, input_data=input_data)


def _run_self(*args):
    return _uv_run(Path(__file__), *args)


@click.group()
def cli():
    """Wiki CLI — manage your research wiki."""
    pass


@cli.command()
@click.argument("entry_id")
@click.option("--index", default=None, help="Path to index.jsonl (for testing)")
def lock(entry_id, index):
    """Lock an entry to prevent /render from overwriting it."""
    args = ["update", entry_id, "--locked", "true"]
    if index:
        args.extend(["--index", index])
    result = _run_index(*args)
    if result.returncode != 0:
        click.echo(result.stderr, err=True)
        sys.exit(result.returncode)
    click.echo(f"Locked {entry_id}")


@cli.command()
@click.argument("entry_id")
@click.option("--index", default=None, help="Path to index.jsonl (for testing)")
def unlock(entry_id, index):
    """Unlock an entry so /render can overwrite it."""
    args = ["update", entry_id, "--locked", "false"]
    if index:
        args.extend(["--index", index])
    result = _run_index(*args)
    if result.returncode != 0:
        click.echo(result.stderr, err=True)
        sys.exit(result.returncode)
    click.echo(f"Unlocked {entry_id}")


@cli.command()
@click.option("--id", "entry_id", default=None, help="Explicit slug override (synthesis- prefix auto-added)")
@click.option("--index", default=None, help="Path to index.jsonl (for testing)")
@click.option("--result-file", default=None, help="Path to query result file (default: .wiki/last-query-result.md)")
@click.option("--wiki-dir", default=None, help="Wiki content directory (default: ./wiki)")
def save(entry_id, index, result_file, wiki_dir):
    """File the last query result as a synthesis wiki page."""
    # Step 1: Determine result file path and verify it exists
    if result_file is None:
        result_file = os.path.join(".wiki", "last-query-result.md")
    if not os.path.exists(result_file):
        click.echo(f"Error: result file not found: {result_file}", err=True)
        click.echo("No query result to save. Run /query first.", err=True)
        sys.exit(1)

    with open(result_file) as f:
        try:
            result_data = json.load(f)
        except json.JSONDecodeError as e:
            click.echo(f"Error: could not parse result file as JSON: {e}", err=True)
            sys.exit(1)

    # Step 2: Extract fields from JSON result
    query_text = result_data.get("query", "")
    entry_title = result_data.get("title", "").strip() or query_text
    synthesis = result_data.get("synthesis", "")
    sources = result_data.get("sources", [])
    tags = result_data.get("tags", [])
    fragments = result_data.get("fragments", [])
    references = [s.strip() for s in sources if isinstance(sources, list) and s.strip()]

    # Step 3: Generate slug
    if entry_id:
        # Auto-prepend synthesis- if not already present
        if not entry_id.startswith("syn-"):
            slug = f"syn-{entry_id}"
        else:
            slug = entry_id
    else:
        slug = slugify(query_text, max_len=40, prefix="syn-")

    # Step 4: Check for collision
    exists_args = ["exists", slug]
    if index:
        exists_args.extend(["--index", index])
    exists_result = _run_index(*exists_args)
    if exists_result.returncode == 0:
        click.echo(f"Error: entry '{slug}' already exists in index.", err=True)
        sys.exit(1)

    # Step 5: Create entry stub JSON and run assemble
    entry_stub = {
        "id": slug,
        "type": "synthesis",
        "source_type": "query",
        "title": entry_title or slug,
        "status": "rendered",
        "wiki_path": None,
        "tags": [],
        "project": None,
        "source_name": query_text,  # verbatim query for self-citation filter
        "locked": False,
        "references": references,
    }

    # Use temp files for agent output (JSON), entry stub, and fragments output
    agent_out_fd, agent_out_path = tempfile.mkstemp(suffix=".json", prefix="save_agent_")
    entry_json_fd, entry_json_path = tempfile.mkstemp(suffix=".json", prefix="save_entry_")
    frags_fd, frags_path = tempfile.mkstemp(suffix=".json", prefix="save_frags_")
    try:
        os.close(agent_out_fd)
        os.close(entry_json_fd)
        os.close(frags_fd)

        # Write query result as JSON for assemble (parse_agent_output detects JSON)
        agent_data = {
            "synthesis": synthesis,
            "sources": sources,
            "tags_finalized": tags,
            "fragments": fragments,
        }
        with open(agent_out_path, "w") as f:
            json.dump(agent_data, f, ensure_ascii=False)

        with open(entry_json_path, "w") as f:
            json.dump(entry_stub, f)

        assemble_result = _run_render(
            "assemble",
            "--agent-output", agent_out_path,
            "--entry-json", entry_json_path,
            "--type", "synthesis",
            "--fragments-out", frags_path,
        )
        if assemble_result.returncode != 0:
            click.echo(f"Error: assemble failed:\n{assemble_result.stderr}", err=True)
            sys.exit(1)

        try:
            assemble_data = json.loads(assemble_result.stdout)
        except json.JSONDecodeError:
            click.echo(f"Error: could not parse assemble output: {assemble_result.stdout}", err=True)
            sys.exit(1)

        wiki_path_result = assemble_data.get("wiki_path", "")
        tags_finalized = assemble_data.get("tags_finalized", [])

        # Step 6: Register entry in index
        tags_str = ",".join(tags_finalized) if tags_finalized else ""
        refs_str = ",".join(references) if references else ""

        create_args = [
            "create-entry",
            "--id", slug,
            "--type", "synthesis",
            "--source-type", "query",
            "--title", entry_title or slug,
            "--status", "rendered",
            "--wiki-path", wiki_path_result,
        ]
        if tags_str:
            create_args.extend(["--tags", tags_str])
        if refs_str:
            create_args.extend(["--references", refs_str])

        create_result = _run_index(*create_args)
        if create_result.returncode != 0:
            click.echo(f"Error: create-entry failed:\n{create_result.stderr}", err=True)
            sys.exit(1)

        add_args = ["add"]
        if index:
            add_args.extend(["--index", index])
        add_result = _run_index(*add_args, input_data=create_result.stdout)

        if add_result.returncode == 2:
            # Collision fallback: use update
            update_args = ["update", slug, "--status", "rendered", "--wiki-path", wiki_path_result]
            if tags_str:
                update_args.extend(["--tags", tags_str])
            if index:
                update_args.extend(["--index", index])
            update_result = _run_index(*update_args)
            if update_result.returncode != 0:
                click.echo(f"Error: update failed:\n{update_result.stderr}", err=True)
                sys.exit(1)
        elif add_result.returncode != 0:
            click.echo(f"Error: add failed:\n{add_result.stderr}", err=True)
            sys.exit(1)

        # Step 7: Add fragments
        try:
            with open(frags_path) as f:
                frags_data = f.read()
        except (OSError, IOError):
            frags_data = "[]"

        frags_args = ["add-fragments-batch"]
        if index:
            frags_args.extend(["--index", index])
        _run_index(*frags_args, input_data=frags_data)

        # Step 8: Delete result file
        try:
            os.unlink(result_file)
        except OSError:
            pass

        n_frags = len(json.loads(frags_data)) if frags_data else 0
        tags_display = ", ".join(tags_finalized) if tags_finalized else "(none)"
        click.echo(f"Saved {slug} → {wiki_path_result}. {n_frags} fragments extracted. Tags: {tags_display}")

    finally:
        # Clean up temp files
        for path in (agent_out_path, entry_json_path, frags_path):
            try:
                os.unlink(path)
            except OSError:
                pass


@cli.command("web-ingest")
@click.argument("arxiv_input")
@click.option("--tags", default=None, help="Comma-separated tags")
@click.option("--project", default=None, help="Project name")
@click.option("--index", default=None, help="Path to index.jsonl")
@click.option("--dry-run", is_flag=True, help="Fetch metadata only, don't download or add")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def web_ingest(arxiv_input, tags, project, index, dry_run, yes):
    """Ingest an arXiv paper by ID or URL."""
    # Step 1: Normalize
    norm = _run_arxiv("normalize", arxiv_input)
    if norm.returncode != 0:
        click.echo(f"Could not parse arXiv ID from: {arxiv_input}", err=True)
        sys.exit(1)
    arxiv_id = norm.stdout.strip()

    # Step 2: Fetch metadata
    fetch = _run_arxiv("fetch", arxiv_id)
    if fetch.returncode != 0:
        click.echo(f"Failed to fetch metadata: {fetch.stderr}", err=True)
        sys.exit(1)
    entry = json.loads(fetch.stdout)
    entry_id = entry["id"]

    # Step 3: Collision check
    exists_args = ["exists", entry_id]
    if index:
        exists_args.extend(["--index", index])
    exists_result = _run_index(*exists_args)
    if exists_result.returncode == 0:
        click.echo(f"Already in index: {entry_id}", err=True)
        sys.exit(1)
    if exists_result.returncode == 2:
        click.echo(f"ID collision: {entry_id}", err=True)
        sys.exit(1)

    # Step 4: Display info
    click.echo(f"arXiv: {arxiv_id}")
    click.echo(f"Title: {entry.get('title', 'Unknown')}")
    click.echo(f"ID: {entry_id}")
    if entry.get("citation"):
        click.echo(f"Citation: {entry['citation']}")

    if dry_run:
        click.echo(json.dumps(entry, indent=2))
        return

    if not yes:
        if not click.confirm("Ingest this paper?"):
            return

    # Step 5: Download PDF
    pdf_dir = "wiki/sources/arxiv"
    os.makedirs(pdf_dir, exist_ok=True)
    pdf_path = f"{pdf_dir}/{arxiv_id}.pdf"
    dl = _run_arxiv("download-pdf", arxiv_id, pdf_path)
    if dl.returncode != 0:
        click.echo(f"PDF download failed: {dl.stderr}", err=True)
        sys.exit(1)
    entry["source_path"] = pdf_path

    # Step 6: Add to index
    add_args = ["add"]
    if index:
        add_args.extend(["--index", index])
    add_result = _run_index(*add_args, input_data=json.dumps(entry))
    if add_result.returncode != 0:
        click.echo(f"Failed to add entry: {add_result.stderr}", err=True)
        sys.exit(1)

    # Step 7: Apply tags/project
    if tags or project:
        update_args = ["update", entry_id]
        if tags:
            update_args.extend(["--tags", tags])
        if project:
            update_args.extend(["--project", project])
        if index:
            update_args.extend(["--index", index])
        _run_index(*update_args)

    click.echo(f"Ingested {entry_id} → {entry.get('title', 'Unknown')}")


@cli.command()
@click.argument("input_path", required=False)
@click.option("--text", "pasted_text", default=None, help="Pasted text content (instead of file path)")
@click.option("--id", "entry_id", default=None, help="Explicit entry ID")
@click.option("--title", default=None, help="Entry title")
@click.option("--tags", default=None, help="Comma-separated tags")
@click.option("--project", default=None, help="Project name")
@click.option("--source-kind", default=None, type=click.Choice(["reference", "synthesis"]), help="For markdown: defaults to synthesis; pass 'reference' for paper")
@click.option("--sync", is_flag=True, help="Sync all configured sources")
@click.option("--source", "source_filter", default=None, help="With --sync: only sync this source")
@click.option("--index", default=None, help="Path to index.jsonl")
@click.option("--wiki-dir", default=None, help="Wiki content directory")
@click.option("--sources-dir", default=None, help="Directory for pasted text storage")
@click.option("--caption", default=None, help="Image caption (non-interactive)")
@click.option("--description", default=None, help="Image description (non-interactive)")
def ingest(input_path, pasted_text, entry_id, title, tags, project, source_kind,
           sync, source_filter, index, wiki_dir, sources_dir, caption, description):
    """Register a source in the wiki index."""
    # --- Early returns for unimplemented modes ---
    if sync:
        config_result = _run_config("get", "sources")
        if config_result.returncode != 0:
            click.echo(f"Error reading config: {config_result.stderr}", err=True)
            sys.exit(1)
        sources = json.loads(config_result.stdout)
        if source_filter:
            sources = [s for s in sources if s["name"] == source_filter]

        index_path = index or ".wiki/index.jsonl"
        total_new = 0
        for src in sources:
            src_name = src.get("name", "")
            if src["type"] == "zotero":
                diff = _run_zotero("diff", "--bbt", src["bbt_export_path"], "--index", index_path)
                if diff.returncode == 0 and diff.stdout.strip():
                    new_entries = json.loads(diff.stdout)
                    if new_entries:
                        for e in new_entries:
                            e["source_name"] = src_name
                        add_args = ["add-batch"]
                        if index:
                            add_args.extend(["--index", index])
                        _run_index(*add_args, input_data=json.dumps(new_entries))
                        total_new += len(new_entries)

            elif src["type"] == "ml-journal":
                journal_path = src.get("journal_path") or src.get("index_path")
                if journal_path:
                    exps = _run_render("list-experiments", "--journal", journal_path, "--source-name", src_name)
                    if exps.returncode == 0 and exps.stdout.strip():
                        new_entries = json.loads(exps.stdout)
                        if new_entries:
                            add_args = ["add-batch"]
                            if index:
                                add_args.extend(["--index", index])
                            _run_index(*add_args, input_data=json.dumps(new_entries))
                            total_new += len(new_entries)

        click.echo(f"Synced {total_new} new entries across {len(sources)} sources.")
        return

    # Validate that at least one of input_path or --text is provided
    if not input_path and not pasted_text:
        click.echo("Error: provide an input path or --text", err=True)
        sys.exit(1)

    # Handle --text flag: source type is text, no file path needed
    if pasted_text:
        source_type = "text"
    else:
        if os.path.isdir(input_path):
            # --- Directory mode: ingest each file in the directory ---
            resolved_wiki_dir = wiki_dir or "wiki"
            resolved_index = index or ".wiki/index.jsonl"

            all_files = [
                f for f in os.listdir(input_path)
                if not f.startswith(".") and os.path.isfile(os.path.join(input_path, f))
            ]

            ingested = 0
            skipped = 0
            errors = 0

            for fname in sorted(all_files):
                fpath = os.path.join(input_path, fname)
                sub_args = ["ingest", fpath, "--index", resolved_index,
                            "--wiki-dir", resolved_wiki_dir]
                if tags:
                    sub_args.extend(["--tags", tags])
                if project:
                    sub_args.extend(["--project", project])

                sub_result = _run_self(*sub_args)

                if sub_result.returncode == 0:
                    ingested += 1
                    try:
                        os.unlink(fpath)
                    except OSError:
                        pass
                elif "Already in index" in sub_result.stderr:
                    skipped += 1
                else:
                    errors += 1
                    click.echo(f"  Error ingesting {fname}: {sub_result.stderr.strip()}", err=True)

            click.echo(
                f"Ingested {ingested} files. "
                f"Skipped {skipped} (already in index). "
                f"Left {errors} in place (errors)."
            )
            return

        source_type = detect_source_type(input_path)

        if source_type == "image":
            # --- Image mode ---
            resolved_wiki_dir = wiki_dir or "wiki"
            assets_dir = os.path.join(resolved_wiki_dir, "assets")
            os.makedirs(assets_dir, exist_ok=True)

            filename = os.path.basename(input_path)
            dest_path = os.path.join(assets_dir, filename)
            shutil.copy2(input_path, dest_path)

            stem = Path(input_path).stem.replace("_", "-")
            generated_id = entry_id if entry_id else ("img-" + slugify(stem))

            # Collision check
            exists_args = ["exists", generated_id]
            if index:
                exists_args.extend(["--index", index])
            exists_result = _run_index(*exists_args)
            if exists_result.returncode == 0:
                click.echo(f"Already in index: {generated_id}", err=True)
                sys.exit(1)
            if exists_result.returncode == 2:
                click.echo(f"ID collision: {generated_id}", err=True)
                sys.exit(1)

            # Caption / description
            is_tty = sys.stdin.isatty()
            resolved_caption = caption
            resolved_description = description
            if resolved_caption is None:
                if is_tty:
                    resolved_caption = click.prompt("Caption", default="Untitled image")
                else:
                    resolved_caption = "Untitled image"
            if resolved_description is None:
                if is_tty:
                    resolved_description = click.prompt("Description", default="No description provided.")
                else:
                    resolved_description = "No description provided."

            # Create entry
            create_args = [
                "create-entry",
                "--id", generated_id,
                "--type", "image",
                "--source-type", "image",
                "--source-path", dest_path,
                "--title", resolved_caption,
                "--status", "stub",
            ]
            if tags:
                create_args.extend(["--tags", tags])
            if project:
                create_args.extend(["--project", project])

            create_result = _run_index(*create_args)
            if create_result.returncode != 0:
                click.echo(f"Error: create-entry failed:\n{create_result.stderr}", err=True)
                sys.exit(1)

            add_args = ["add"]
            if index:
                add_args.extend(["--index", index])
            add_result = _run_index(*add_args, input_data=create_result.stdout)
            if add_result.returncode == 2:
                click.echo(f"Already in index: {generated_id}", err=True)
                sys.exit(1)
            if add_result.returncode != 0:
                click.echo(f"Error: add failed:\n{add_result.stderr}", err=True)
                sys.exit(1)

            # Get entry JSON for assemble-image
            get_args = ["get", generated_id]
            if index:
                get_args.extend(["--index", index])
            get_result = _run_index(*get_args)
            if get_result.returncode != 0:
                click.echo(f"Error: get failed:\n{get_result.stderr}", err=True)
                sys.exit(1)

            entry_json_fd, entry_json_path = tempfile.mkstemp(suffix=".json", prefix="img_entry_")
            frags_fd, frags_path = tempfile.mkstemp(suffix=".json", prefix="img_frags_")
            try:
                os.close(entry_json_fd)
                os.close(frags_fd)

                with open(entry_json_path, "w") as f:
                    f.write(get_result.stdout)

                assemble_args = [
                    "assemble-image",
                    "--image-path", dest_path,
                    "--description", resolved_description,
                    "--entry-json", entry_json_path,
                    "--fragments-out", frags_path,
                ]
                if tags:
                    assemble_args.extend(["--tags", tags])

                assemble_result = _run_render(*assemble_args)
                if assemble_result.returncode != 0:
                    click.echo(f"Error: assemble-image failed:\n{assemble_result.stderr}", err=True)
                    sys.exit(1)

                try:
                    assemble_data = json.loads(assemble_result.stdout)
                except json.JSONDecodeError:
                    click.echo(f"Error: could not parse assemble-image output: {assemble_result.stdout}", err=True)
                    sys.exit(1)

                wiki_path_result = assemble_data.get("wiki_path", "")

                # Update entry to rendered
                update_args = ["update", generated_id, "--status", "rendered", "--wiki-path", wiki_path_result]
                if index:
                    update_args.extend(["--index", index])
                update_result = _run_index(*update_args)
                if update_result.returncode != 0:
                    click.echo(f"Error: update failed:\n{update_result.stderr}", err=True)
                    sys.exit(1)

                # Add fragments
                try:
                    with open(frags_path) as f:
                        frags_data = f.read()
                except (OSError, IOError):
                    frags_data = "[]"

                frags_args = ["add-fragments-batch"]
                if index:
                    frags_args.extend(["--index", index])
                _run_index(*frags_args, input_data=frags_data)

                click.echo(f"Ingested image {generated_id} → {wiki_path_result}")

            finally:
                for path in (entry_json_path, frags_path):
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
            return

        # Unknown source type — try Zotero lookup
        if source_type is None:
            config_result = _run_config("get", "sources")
            zotero_sources = []
            if config_result.returncode == 0:
                try:
                    all_sources = json.loads(config_result.stdout)
                    zotero_sources = [s for s in all_sources if s.get("type") == "zotero"]
                except (json.JSONDecodeError, TypeError):
                    pass

            matched_source = None
            for src in zotero_sources:
                bbt_path = src.get("bbt_export_path", "")
                lookup = _run_zotero("lookup", "--bbt", bbt_path, input_path)
                if lookup.returncode == 0:
                    source_type = "zotero"
                    matched_source = src
                    break

            if source_type is None:
                click.echo(f"Error: Could not determine source type for: {input_path}", err=True)
                sys.exit(1)

    # --- Generate entry ID ---
    if entry_id:
        generated_id = entry_id
    elif source_type == "text":
        click.echo("Error: --id is required for text input", err=True)
        sys.exit(1)
    elif source_type == "url":
        click.echo("Error: --id is required for URL input", err=True)
        sys.exit(1)
    elif source_type == "pdf":
        stem = Path(input_path).stem
        generated_id = slugify(stem)
    elif source_type == "markdown":
        generated_id = Path(input_path).stem
    elif source_type == "zotero":
        generated_id = input_path  # citekey
    else:
        click.echo(f"Error: Cannot auto-generate ID for source type: {source_type}", err=True)
        sys.exit(1)

    if source_type == "markdown" and source_kind != "reference" and not generated_id.startswith("syn-"):
        generated_id = f"syn-{generated_id}"

    # --- Collision check ---
    exists_args = ["exists", generated_id]
    if index:
        exists_args.extend(["--index", index])
    exists_result = _run_index(*exists_args)
    if exists_result.returncode == 0:
        click.echo(f"Already in index: {generated_id}", err=True)
        sys.exit(1)
    if exists_result.returncode == 2:
        click.echo(f"ID collision: {generated_id}", err=True)
        sys.exit(1)

    # --- Resolve defaults ---
    resolved_wiki_dir = wiki_dir or "wiki"
    resolved_sources_dir = sources_dir or ".wiki/sources"

    # --- Handle pasted text: write to sources dir ---
    source_path = input_path or ""
    if pasted_text:
        os.makedirs(resolved_sources_dir, exist_ok=True)
        txt_path = os.path.join(resolved_sources_dir, f"{generated_id}.txt")
        with open(txt_path, "w") as f:
            f.write(pasted_text)
        source_path = txt_path

    # --- Add to index (branch by source type) ---
    if source_type == "zotero" and matched_source:
        bbt_path = matched_source.get("bbt_export_path", "")
        source_name = matched_source.get("name", "")
        parse_result = _run_zotero("parse", "--bbt", bbt_path, "--citekey", generated_id, "--source-name", source_name)
        if parse_result.returncode != 0:
            click.echo(f"Error: zotero parse failed:\n{parse_result.stderr}", err=True)
            sys.exit(1)
        add_args = ["add"]
        if index:
            add_args.extend(["--index", index])
        add_result = _run_index(*add_args, input_data=parse_result.stdout)
        if add_result.returncode == 2:
            click.echo(f"Already in index: {generated_id}", err=True)
            sys.exit(1)
        if add_result.returncode != 0:
            click.echo(f"Error: add failed:\n{add_result.stderr}", err=True)
            sys.exit(1)

    elif source_type == "markdown":
        if source_kind == "reference":
            entry_type = "paper"
            dest_dir = os.path.join(resolved_wiki_dir, "_pages")
        else:
            entry_type = "synthesis"
            dest_dir = os.path.join(resolved_wiki_dir, "syntheses")

        dest_path = os.path.join(dest_dir, f"{generated_id}.md")

        # Step 1: create-entry with status stub
        create_args = [
            "create-entry",
            "--id", generated_id,
            "--type", entry_type,
            "--source-type", "markdown",
            "--source-path", source_path,
            "--status", "stub",
        ]
        if title:
            create_args.extend(["--title", title])
        if tags:
            create_args.extend(["--tags", tags])
        if project:
            create_args.extend(["--project", project])

        create_result = _run_index(*create_args)
        if create_result.returncode != 0:
            click.echo(f"Error: create-entry failed:\n{create_result.stderr}", err=True)
            sys.exit(1)

        add_args = ["add"]
        if index:
            add_args.extend(["--index", index])
        add_result = _run_index(*add_args, input_data=create_result.stdout)
        if add_result.returncode == 2:
            click.echo(f"Already in index: {generated_id}", err=True)
            sys.exit(1)
        if add_result.returncode != 0:
            click.echo(f"Error: add failed:\n{add_result.stderr}", err=True)
            sys.exit(1)

        # Step 2: copy file to destination
        os.makedirs(dest_dir, exist_ok=True)
        shutil.copy2(source_path, dest_path)

        # Step 3: update to rendered status
        update_args = ["update", generated_id, "--status", "rendered", "--wiki-path", dest_path]
        if source_kind != "reference":
            update_args.extend(["--locked", "true"])
        if index:
            update_args.extend(["--index", index])
        update_result = _run_index(*update_args)
        if update_result.returncode != 0:
            click.echo(f"Error: update failed:\n{update_result.stderr}", err=True)
            sys.exit(1)

    else:
        # PDF, text, url
        if source_type == "pdf":
            entry_type = "paper"
        elif source_type == "text":
            entry_type = "paper"
        elif source_type == "url":
            entry_type = "paper"
        else:
            entry_type = "paper"

        create_args = [
            "create-entry",
            "--id", generated_id,
            "--type", entry_type,
            "--source-type", source_type,
            "--status", "stub",
        ]
        if source_path:
            create_args.extend(["--source-path", source_path])
        if title:
            create_args.extend(["--title", title])
        if tags:
            create_args.extend(["--tags", tags])
        if project:
            create_args.extend(["--project", project])

        create_result = _run_index(*create_args)
        if create_result.returncode != 0:
            click.echo(f"Error: create-entry failed:\n{create_result.stderr}", err=True)
            sys.exit(1)

        add_args = ["add"]
        if index:
            add_args.extend(["--index", index])
        add_result = _run_index(*add_args, input_data=create_result.stdout)
        if add_result.returncode == 2:
            click.echo(f"Already in index: {generated_id}", err=True)
            sys.exit(1)
        if add_result.returncode != 0:
            click.echo(f"Error: add failed:\n{add_result.stderr}", err=True)
            sys.exit(1)

        # Apply project via update if needed (tags already in create-entry)
        if project and source_type != "zotero":
            update_args = ["update", generated_id, "--project", project]
            if index:
                update_args.extend(["--index", index])
            _run_index(*update_args)

    click.echo(f"Ingested {generated_id}. Run /render {generated_id} to generate the wiki page.")


@cli.command()
@click.option("--non-interactive", is_flag=True, help="Skip prompts, create minimal config")
def init(non_interactive):
    """Initialize the wiki in this repo."""
    for d in ["wiki/_pages", "wiki/topics", "wiki/projects",
              "wiki/syntheses", "wiki/images", "wiki/assets",
              "wiki/sources/arxiv",
              ".wiki/scripts", ".wiki/sources", ".wiki/schemas"]:
        os.makedirs(d, exist_ok=True)

    config_path = ".wiki/config.yaml"
    if os.path.exists(config_path) and not non_interactive:
        if not click.confirm("Config already exists. Overwrite?"):
            click.echo("Aborted.")
            return

    sources = []
    if not non_interactive:
        click.echo("Configure Zotero sources (press Enter with empty name to finish):")
        while True:
            name = click.prompt("  Source name", default="", show_default=False)
            if not name:
                break
            bbt_path = click.prompt(f"  BBT JSON export path for '{name}'")
            zotero_storage = click.prompt("  Zotero storage path (optional)", default="", show_default=False)
            source = {"name": name, "type": "zotero", "bbt_export_path": bbt_path}
            if zotero_storage:
                source["zotero_storage"] = zotero_storage
            sources.append(source)

    import yaml
    config = {
        "research_wiki": {
            "wiki_dir": "./wiki",
            "index_path": "./.wiki/index.jsonl",
            "default_render_depth": "shallow",
            "llm": {
                "base_url": "https://openrouter.ai/api/v1",
                "api_key_env": "OPENROUTER_API_KEY",
                "models": {
                    "nano": "openai/gpt-5.4-nano",
                    "mini": "openai/gpt-5.4-mini",
                    "full": "openai/gpt-5.4",
                },
                "default_params": {"max_tokens": 8192, "temperature": 0},
                "model_overrides": {},
            },
            "tag_aliases": {},
            "sources": sources,
        }
    }

    tmp = config_path + ".tmp"
    with open(tmp, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    os.rename(tmp, config_path)

    index_path = ".wiki/index.jsonl"
    if not os.path.exists(index_path):
        with open(index_path, "w") as f:
            pass

    val = _run_config("validate")
    if val.returncode != 0:
        click.echo(f"Warning: {val.stdout}", err=True)

    click.echo("Wiki initialized. Run `ingest --sync` to populate from your sources.")


def _render_prep(entry_id, depth, force, focus, index_path, wiki_dir, fragments_only=False):
    """Phase 1: validate entry, extract source, build LLM prompt. Returns prep dict or None."""
    get_args = ["get", entry_id]
    if index_path:
        get_args.extend(["--index", index_path])
    get_result = _run_index(*get_args)
    if get_result.returncode != 0:
        click.echo(f"  {entry_id}: entry not found", err=True)
        return None
    entry = json.loads(get_result.stdout)

    if entry.get("type") == "idea":
        click.echo(f"  {entry_id}: skipped (idea — use /idea improve)", err=True)
        return None

    if fragments_only:
        if entry.get("source_type") == "query":
            click.echo(f"  {entry_id}: skipped (query synthesis — source docs already have fragments)", err=True)
            return None
    else:
        if entry.get("locked") and not force:
            click.echo(f"  {entry_id}: skipped (locked)", err=True)
            return None

        if entry.get("status") == "rendered" and not force:
            click.echo(f"  {entry_id}: skipped (already rendered)", err=True)
            return None

    if depth is None:
        config_result = _run_config("get", "default_render_depth")
        depth = json.loads(config_result.stdout) if config_result.returncode == 0 else "shallow"

    notes_result = _run_render("extract-notes", entry.get("wiki_path") or "")
    preserved_notes = notes_result.stdout.strip() if notes_result.returncode == 0 else ""

    del_args = ["delete-fragments", entry_id]
    if index_path:
        del_args.extend(["--index", index_path])
    _run_index(*del_args)

    source_type = entry.get("source_type", "text")
    source_path = entry.get("source_path", "")

    if fragments_only:
        wp = entry.get("wiki_path", "")
        if wp and os.path.exists(wp):
            with open(wp, encoding="utf-8") as _f:
                source_text = _f.read()
        else:
            click.echo(f"  {entry_id}: no wiki page to extract fragments from", err=True)
            return None
    elif source_type == "query":
        wp = entry.get("wiki_path", "")
        if wp and os.path.exists(wp):
            with open(wp, encoding="utf-8") as _f:
                source_text = _f.read()
        else:
            click.echo(f"  {entry_id}: source extraction failed (query entry has no wiki page)", err=True)
            return None
    else:
        extract_args = ["extract-source", source_path or "", "--source-type", source_type]
        source_name = entry.get("source_name")
        if source_name:
            src_config = _run_config("get", "sources", "--name", source_name)
            if src_config.returncode == 0:
                src_data = json.loads(src_config.stdout)
                zs = src_data.get("zotero_storage")
                if zs:
                    extract_args.extend(["--zotero-storage", zs])
        source_result = _run_render(*extract_args)
        if source_result.returncode != 0:
            click.echo(f"  {entry_id}: source extraction failed — {source_result.stderr.strip()}", err=True)
            return None
        source_text = source_result.stdout

    resolved_focus = focus or entry.get("project") or (entry.get("tags", []) or [None])[0]

    meta_lines = [
        f"ID: {entry_id}",
        f"Type: {entry.get('type', 'paper')}",
        f"Title: {entry.get('title', 'Untitled')}",
    ]
    if entry.get("citation"):
        meta_lines.append(f"Citation: {entry['citation']}")
    if entry.get("tags"):
        meta_lines.append(f"Tags: {', '.join(entry['tags'])}")
    if entry.get("project"):
        meta_lines.append(f"Project: {entry['project']}")
    if resolved_focus:
        meta_lines.append(f"Focus: {resolved_focus}")
    meta_lines.append(f"Depth: {depth}")

    alias_result = _run_config("get", "tag_aliases")
    alias_targets = set()
    if alias_result.returncode == 0:
        alias_map = json.loads(alias_result.stdout)
        alias_targets = set(v for v in alias_map.values() if v)
    idx_arg = index_path or ".wiki/index.jsonl"
    freq_result = _run_index("get-canonical-tags", "--min-count", "10", "--index", idx_arg)
    freq_tags = set()
    if freq_result.returncode == 0:
        freq_tags = set(json.loads(freq_result.stdout))
    blocklist_result = _run_config("get", "tag_blocklist")
    blocklist = set()
    if blocklist_result.returncode == 0:
        blocklist = set(json.loads(blocklist_result.stdout))
    canonical_tags = sorted((alias_targets | freq_tags) - blocklist)
    if canonical_tags:
        meta_lines.append(f"Canonical tags: {', '.join(canonical_tags)}")

    user_msg = "## Entry Metadata\n" + "\n".join(meta_lines) + "\n\n## Source Text\n" + source_text

    entry_type = entry.get("type", "paper")
    agent_name = f"render-{depth}"
    schema_name = "render-paper" if fragments_only else f"render-{entry_type}"

    return {
        "id": entry_id,
        "entry": entry,
        "agent_name": agent_name,
        "schema_name": schema_name,
        "user_msg": user_msg,
        "preserved_notes": preserved_notes,
        "entry_type": entry_type,
        "depth": depth,
    }


def _render_finish(prep, result_data, index_path, fragments_only=False):
    """Phase 3: validate, assemble, update index, add fragments."""
    from wiki_validate import validate_render_output
    from wiki_llm import validate_schema

    entry_id = prep["id"]

    if fragments_only:
        tags_finalized = result_data.get("tags", [])
        fragments = []
        for frag in result_data.get("fragments", []):
            frag_id = f"frag-{entry_id}-{frag['seq']:02d}"
            fragments.append({
                "id": frag_id,
                "type": frag["type"],
                "title": frag["title"],
                "tags": tags_finalized,
                "project": prep["entry"].get("project"),
                "references": [entry_id],
                "ingested": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })

        tags_str = ",".join(tags_finalized)
        update_args = ["update", entry_id, "--tags", tags_str]
        if index_path:
            update_args.extend(["--index", index_path])
        _run_index(*update_args)

        if fragments:
            frag_args = ["add-fragments-batch"]
            if index_path:
                frag_args.extend(["--index", index_path])
            _run_index(*frag_args, input_data=json.dumps(fragments))

        click.echo(f"  {entry_id}: OK — {len(fragments)} fragments, tags: {tags_str} (page unchanged)")
        return

    warnings = validate_render_output(result_data, depth=prep["depth"])
    try:
        validate_schema(result_data, prep["schema_name"])
    except Exception as e:
        raise RuntimeError(f"Schema validation failed: {e}")
    for w in warnings:
        click.echo(f"  Warning: {w.field}: {w.message}", err=True)

    from wiki_render import _assemble_page_from_json
    assemble_result = _assemble_page_from_json(prep["entry"], prep["entry_type"],
                                                result_data, prep["preserved_notes"])

    tags_str = ",".join(assemble_result["tags_finalized"])
    update_args = ["update", entry_id, "--status", "rendered", "--rendered", "now",
                   "--wiki-path", assemble_result["wiki_path"], "--tags", tags_str]
    if index_path:
        update_args.extend(["--index", index_path])
    _run_index(*update_args)

    if assemble_result["fragments"]:
        frags_json = json.dumps(assemble_result["fragments"])
        frag_args = ["add-fragments-batch"]
        if index_path:
            frag_args.extend(["--index", index_path])
        _run_index(*frag_args, input_data=frags_json)

    n_frags = len(assemble_result["fragments"])
    click.echo(f"  {entry_id}: OK — {assemble_result['wiki_path']} ({n_frags} fragments, tags: {tags_str})")


@cli.command()
@click.argument("entry_id", required=False)
@click.option("--depth", type=click.Choice(["shallow", "deep"]), default=None, help="Render depth")
@click.option("--force", is_flag=True, help="Render even if locked")
@click.option("--focus", default=None, help="Focus hint for extraction")
@click.option("--all-stubs", is_flag=True, help="Batch render all stub entries")
@click.option("--tag", default=None, help="Batch render stubs with this tag")
@click.option("--type", "entry_type", default=None, help="Batch filter by entry type")
@click.option("--fragments-only", is_flag=True, help="Extract fragments and tags only; do not overwrite the wiki page")
@click.option("--concurrency", default=10, type=int, help="Max parallel API calls for batch render")
@click.option("--index", default=None, help="Path to index.jsonl")
@click.option("--wiki-dir", default=None, help="Wiki content directory")
def render(entry_id, depth, force, focus, all_stubs, tag, entry_type, fragments_only, concurrency, index, wiki_dir):
    """Generate a markdown wiki page from a source entry."""
    index_path = index

    if all_stubs or tag or (fragments_only and not entry_id):
        list_args = ["list", "--format", "json"]
        if not fragments_only:
            list_args.extend(["--status", "stub"])
        if tag:
            list_args.extend(["--tag", tag])
        if entry_type:
            list_args.extend(["--type", entry_type])
        if index_path:
            list_args.extend(["--index", index_path])
        list_result = _run_index(*list_args)
        if list_result.returncode != 0:
            click.echo(f"Error listing entries: {list_result.stderr}", err=True)
            sys.exit(1)
        entries = json.loads(list_result.stdout) if list_result.stdout.strip() else []
        if not entries:
            click.echo("No entries to process.")
            return

        label = "Extracting fragments from" if fragments_only else "Rendering"
        click.echo(f"{label} {len(entries)} entries...")

        # Phase 1: Prep all entries (sequential — source extraction is fast)
        preps = []
        skipped = 0
        for entry in entries:
            prep = _render_prep(entry["id"], depth, force, focus, index_path, wiki_dir,
                                fragments_only=fragments_only)
            if prep is None:
                skipped += 1
                continue
            preps.append(prep)

        if not preps:
            click.echo(f"All {len(entries)} entries skipped.")
            return

        # Phase 2: Batch LLM calls, grouped by schema (concurrent)
        llm_config = json.loads(_run_config("get", "llm").stdout)
        from wiki_llm import llm_batch as _llm_batch
        from collections import defaultdict

        by_schema = defaultdict(list)
        prep_map = {}
        for p in preps:
            by_schema[p["schema_name"]].append(p)
            prep_map[p["id"]] = p

        all_results = []
        for schema_name, group in by_schema.items():
            items = [(p["id"], p["user_msg"]) for p in group]
            agent_name = group[0]["agent_name"]
            results = asyncio.run(_llm_batch(agent_name, items, schema_name,
                                             llm_config, concurrency=concurrency,
                                             skip_validation=fragments_only))
            all_results.extend(results)

        # Phase 3: Finish all (sequential — index writes + page assembly)
        success = 0
        errors = 0
        for item_id, result_data, error in all_results:
            if error:
                click.echo(f"  {item_id}: FAILED — {error}", err=True)
                errors += 1
                continue
            try:
                _render_finish(prep_map[item_id], result_data, index_path,
                               fragments_only=fragments_only)
                success += 1
            except Exception as e:
                click.echo(f"  {item_id}: FAILED — {e}", err=True)
                errors += 1

        click.echo(f"Processed {success}/{len(preps)} entries. {skipped} skipped, {errors} failed.")
        return

    if not entry_id:
        click.echo("Error: provide an entry ID or use --all-stubs/--tag", err=True)
        sys.exit(1)

    # Single-entry render via phase functions
    index_path = index
    prep = _render_prep(entry_id, depth, force, focus, index_path, wiki_dir,
                        fragments_only=fragments_only)
    if prep is None:
        sys.exit(1)

    llm_config = json.loads(_run_config("get", "llm").stdout)
    from wiki_llm import llm_call as _llm_call, LLMAPIError
    try:
        result_data = _llm_call(prep["agent_name"], prep["user_msg"], prep["schema_name"],
                                llm_config, skip_validation=True)
    except LLMAPIError as e:
        click.echo(f"LLM call failed: {e}", err=True)
        sys.exit(1)

    _render_finish(prep, result_data, index_path, fragments_only=fragments_only)


def _write_query_result(data, result_path):
    """Write query result as JSON."""
    with open(result_path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _run_web_enrichment(question, index_path, auto, select):
    """Search arXiv, display suggestions, optionally ingest. Returns count of acquired papers."""
    import urllib.parse
    import urllib.request
    import xml.etree.ElementTree as ET

    # Extract concise technical search terms via LLM; fall back to raw question on failure
    search_terms = None
    try:
        from wiki_llm import llm_call as _llm_call
        llm_config = json.loads(_run_config("get", "llm").stdout)
        terms_result = _llm_call("search-terms", question, "search-terms", llm_config)
        search_terms = terms_result.get("terms", [])[:5]
    except Exception:
        pass

    if search_terms:
        terms_clause = " AND ".join(f"all:{t}" for t in search_terms)
        raw_query = f"(cat:cs.LG OR cat:cs.AI OR cat:stat.ML) AND ({terms_clause})"
    else:
        raw_query = question

    encoded = urllib.parse.quote(raw_query)
    url = f"http://export.arxiv.org/api/query?search_query={encoded}&max_results=5"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            xml_data = resp.read().decode()
    except Exception as e:
        click.echo(f"arXiv search failed: {e}", err=True)
        return 0

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml_data)
    entries = root.findall("atom:entry", ns)

    suggestions = []
    for entry in entries:
        title = entry.findtext("atom:title", "", ns).strip().replace("\n", " ")
        entry_id_url = entry.findtext("atom:id", "", ns)
        arxiv_id = entry_id_url.split("/abs/")[-1] if "/abs/" in entry_id_url else ""
        if not arxiv_id:
            continue

        norm = _run_arxiv("normalize", arxiv_id)
        if norm.returncode != 0:
            continue
        arxiv_id = norm.stdout.strip()

        fetch = _run_arxiv("fetch", arxiv_id)
        if fetch.returncode != 0:
            continue
        meta = json.loads(fetch.stdout)
        slug = meta["id"]

        exists = _run_index("exists", slug, "--index", index_path)
        if exists.returncode == 0:
            continue

        summary = entry.findtext("atom:summary", "", ns).strip().replace("\n", " ")[:150]
        suggestions.append({
            "arxiv_id": arxiv_id,
            "slug": slug,
            "title": meta.get("title", title),
            "citation": meta.get("citation", ""),
            "summary": summary,
        })

    if not suggestions:
        click.echo("No uningested arXiv papers found.")
        return 0

    click.echo("\n--- Web — not in your wiki ---\n")
    for i, s in enumerate(suggestions, 1):
        click.echo(f"{i}. {s['title']}")
        click.echo(f"   arXiv: {s['arxiv_id']}")
        if s['summary']:
            click.echo(f"   > {s['summary']}...")
        click.echo()

    if auto:
        selected = list(range(len(suggestions)))
    elif select:
        selected = [int(x.strip()) - 1 for x in select.split(",") if x.strip().isdigit()]
        selected = [i for i in selected if 0 <= i < len(suggestions)]
    else:
        click.echo("Use --auto to ingest all, or --select '1,3' to pick specific papers.")
        return 0

    acquired = 0
    for idx in selected:
        s = suggestions[idx]
        click.echo(f"Ingesting {s['slug']}...")
        ingest_args = ["web-ingest", s["arxiv_id"], "--yes"]
        if index_path != ".wiki/index.jsonl":
            ingest_args.extend(["--index", index_path])
        r = _run_self(*ingest_args)
        if r.returncode == 0:
            acquired += 1
            click.echo(f"  {s['slug']}: OK")
        else:
            click.echo(f"  {s['slug']}: FAILED — {r.stderr.strip()}", err=True)

    return acquired


def _run_core_query(question, index_path, result_path):
    """Execute core query: search → P1 → optional P2 → write result. Returns output dict or None."""
    # Step 1: Search
    search_result = _run_index("search", "--query", question, "--limit", "25", "--index", index_path)
    if search_result.returncode != 0:
        click.echo(f"Search failed: {search_result.stderr}", err=True)
        return None
    search_data = search_result.stdout

    # Step 2: Call P1
    llm_config = json.loads(_run_config("get", "llm").stdout)
    from wiki_llm import llm_call as _llm_call, LLMAPIError, validate_schema
    from wiki_validate import _coerce_prose_fields

    user_msg = f"## Question\n{question}\n\n## Search Results\n{search_data}"

    try:
        p1_result = _llm_call("query-p1", user_msg, "query-p1", llm_config, skip_validation=True)
    except LLMAPIError as e:
        click.echo(f"P1 LLM call failed: {e}", err=True)
        return None

    _coerce_prose_fields(p1_result)
    try:
        validate_schema(p1_result, "query-p1")
    except Exception as e:
        click.echo(f"P1 schema validation failed: {e}", err=True)
        return None

    # Step 3: Route
    routing = p1_result.get("routing", {})
    decision = routing.get("decision", "needs_pages")
    click.echo(f"P1 routing: {decision}")

    if decision == "direct":
        synthesis = p1_result.get("synthesis", "")
        sources = extract_citations(synthesis)
        p1_result["sources"] = sources
        _write_query_result(p1_result, result_path)
        return p1_result

    # Step 4: Needs pages
    needed_ids = p1_result.get("needed_page_ids", [])
    if not needed_ids:
        click.echo("P1 returned needs_pages but no page IDs", err=True)
        return None

    # Filter out any saved query-synthesis whose title matches this exact question
    # (prevents a previously saved result from citing itself on re-query)
    q_normalized = question.strip().lower()
    if os.path.exists(index_path):
        index_map = {}
        with open(index_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        e = json.loads(line)
                        index_map[e["id"]] = e
                    except (json.JSONDecodeError, KeyError):
                        pass
        needed_ids = [
            eid for eid in needed_ids
            if not (
                index_map.get(eid, {}).get("source_type") == "query"
                and (index_map.get(eid, {}).get("source_name") or "").strip().lower() == q_normalized
            )
        ]

    click.echo(f"Reading {len(needed_ids)} full pages...")
    pages_result = _run_render("read-batch", "--format", "text", "--index", index_path,
                                input_data=json.dumps(needed_ids))
    if pages_result.returncode != 0:
        click.echo(f"read-batch failed: {pages_result.stderr}", err=True)
        return None

    # Step 5: Call P2
    p2_user_msg = f"## Question\n{question}\n\n## Full Page Text\n{pages_result.stdout}"

    try:
        p2_result = _llm_call("query-p2", p2_user_msg, "query-p2", llm_config, skip_validation=True)
    except LLMAPIError as e:
        click.echo(f"P2 LLM call failed: {e}", err=True)
        return None

    _coerce_prose_fields(p2_result)
    try:
        validate_schema(p2_result, "query-p2")
    except Exception as e:
        click.echo(f"P2 schema validation failed: {e}", err=True)
        return None

    synthesis = p2_result.get("synthesis", "")
    sources = extract_citations(synthesis)
    p2_result["sources"] = sources

    _write_query_result(p2_result, result_path)
    return p2_result


@cli.command()
@click.argument("question")
@click.option("--save", "save_result", is_flag=True, help="Save result as synthesis page")
@click.option("--web", is_flag=True, help="Search arXiv for related papers")
@click.option("--auto", is_flag=True, help="With --web: ingest all suggestions without prompting")
@click.option("--no-requery", is_flag=True, help="With --web: ingest without re-running query")
@click.option("--select", default=None, help="With --web: comma-separated indices to ingest (non-interactive)")
@click.option("--index", default=None, help="Path to index.jsonl")
def query(question, save_result, web, auto, no_requery, select, index):
    """Answer a research question from the wiki index."""
    index_path = index or ".wiki/index.jsonl"
    result_path = ".wiki/last-query-result.md"

    final_output = _run_core_query(question, index_path, result_path)
    if final_output is None:
        sys.exit(1)

    click.echo("\n" + final_output.get("synthesis", "(no synthesis)") + "\n")
    sources = final_output.get("sources", [])
    if sources:
        click.echo("Sources: " + ", ".join(sources))

    if web:
        acquired = _run_web_enrichment(question, index_path, auto, select)
        if acquired > 0 and not no_requery:
            click.echo(f"\n{acquired} papers ingested. Re-running query...")
            final_output = _run_core_query(question, index_path, result_path)
            if final_output:
                click.echo("\n" + final_output.get("synthesis", "(no synthesis)") + "\n")
                sources = final_output.get("sources", [])
                if sources:
                    click.echo("Sources: " + ", ".join(sources))

    if save_result:
        save_args = ["save", "--result-file", result_path]
        if index:
            save_args.extend(["--index", index])
        save_r = _run_self(*save_args)
        if save_r.returncode == 0:
            click.echo(save_r.stdout.strip())
        else:
            click.echo(f"Save failed: {save_r.stderr}", err=True)


def _build_data_prep(index_path, tmp_dir):
    """Phase 1: Get journal sources, read refs, resolve connections."""
    src_result = _run_config("get", "sources", "--type", "ml-journal")
    journal_sources = src_result.stdout if src_result.returncode == 0 else "[]"
    journal_sources_path = os.path.join(tmp_dir, "journal_sources.json")
    with open(journal_sources_path, "w") as f:
        f.write(journal_sources)

    journal_refs_path = os.path.join(tmp_dir, "journal_refs.json")
    refs_result = _run_render("read-journal-refs", "--journals", journal_sources_path)
    with open(journal_refs_path, "w") as f:
        f.write(refs_result.stdout if refs_result.returncode == 0 else "[]")

    tag_aliases_result = _run_config("get", "tag_aliases")
    tag_aliases_path = os.path.join(tmp_dir, "tag_aliases.json")
    with open(tag_aliases_path, "w") as f:
        f.write(tag_aliases_result.stdout if tag_aliases_result.returncode == 0 else "{}")

    conn_result = _run_index("resolve-connections",
                              "--journal-refs", journal_refs_path,
                              "--tag-aliases", tag_aliases_path,
                              "--index", index_path)
    if conn_result.returncode != 0:
        click.echo(f"resolve-connections failed: {conn_result.stderr}", err=True)
        sys.exit(1)

    conn_path = os.path.join(tmp_dir, "connection_map.json")
    with open(conn_path, "w") as f:
        f.write(conn_result.stdout)


def _build_ranking(rank_mode, index_path, tmp_dir, concurrency=50):
    """Phase 2: Rank related links."""
    conn_path = os.path.join(tmp_dir, "connection_map.json")
    ranked_path = os.path.join(tmp_dir, "connection_map_ranked.json")

    with open(conn_path) as f:
        conn_data = f.read()

    if rank_mode == "fast":
        result = _run_index("fast-rank-connections", "--top-n", "8", input_data=conn_data)
    elif rank_mode == "semantic":
        result = _run_embed("score-connections", "--top-n", "8", input_data=conn_data)
    elif rank_mode == "deep":
        ranked_json = _build_ranking_deep(conn_data, index_path, concurrency=concurrency)
        with open(ranked_path, "w") as f:
            f.write(ranked_json)
        _run_render("update-connections-batch", "--index", index_path, input_data=ranked_json)
        return

    if result.returncode != 0:
        click.echo(f"Ranking failed: {result.stderr}", err=True)
        sys.exit(1)

    with open(ranked_path, "w") as f:
        f.write(result.stdout)

    _run_render("update-connections-batch", "--index", index_path, input_data=result.stdout)


def _build_ranking_deep(conn_data, index_path, concurrency=50):
    """Deep ranking via LLM: pre-filter then concurrent dispatch via llm_batch."""
    from wiki_build import prefilter_rank_candidates
    from wiki_llm import llm_batch as _llm_batch, validate_schema
    from wiki_validate import _coerce_prose_fields

    conn_map = json.loads(conn_data)
    llm_config = json.loads(_run_config("get", "llm").stdout)

    ranked_map = {}
    items = []
    for entry_id, entry_data in conn_map.items():
        target = entry_data.get("entry", {})
        candidates = entry_data.get("candidates", [])

        if not candidates:
            ranked_map[entry_id] = []
            continue

        filtered = prefilter_rank_candidates(target, candidates)
        if not filtered:
            ranked_map[entry_id] = []
            continue

        items.append((entry_id, json.dumps({"target": target, "candidates": filtered})))

    if items:
        results = asyncio.run(_llm_batch("build-rank", items, "build-rank", llm_config,
                                          concurrency=concurrency, skip_validation=True))
        for entry_id, result_data, error in results:
            if error or result_data is None:
                click.echo(f"  Rank failed for {entry_id}: {error}", err=True)
                ranked_map[entry_id] = []
                continue
            try:
                _coerce_prose_fields(result_data)
                validate_schema(result_data, "build-rank")
                ranked_map[entry_id] = [{"id": r["id"]} for r in result_data.get("related", [])]
            except Exception as e:
                click.echo(f"  Rank failed for {entry_id}: {e}", err=True)
                ranked_map[entry_id] = []

    return json.dumps(ranked_map)


def _build_staleness(index_path, tmp_dir):
    """Phase 3: Check stale entries and apply banners."""
    from wiki_build import build_banner_list

    stale_result = _run_index("check-stale", "--index", index_path)
    if stale_result.returncode != 0:
        click.echo(f"check-stale failed: {stale_result.stderr}", err=True)
        sys.exit(1)

    stale_path = os.path.join(tmp_dir, "stale_list.json")
    with open(stale_path, "w") as f:
        f.write(stale_result.stdout)

    stale_list = json.loads(stale_result.stdout) if stale_result.stdout.strip() else []
    if not stale_list:
        click.echo("  No stale entries.")
        return

    entry_lookup = {}
    for item in stale_list:
        get_result = _run_index("get", item["id"], "--index", index_path)
        if get_result.returncode == 0:
            entry_lookup[item["id"]] = json.loads(get_result.stdout)

    banners = build_banner_list(stale_list, entry_lookup)
    if banners:
        _run_render("update-banners-batch", input_data=json.dumps(banners))
        click.echo(f"  {len(banners)} banners updated.")
    else:
        click.echo("  No banners to update.")


def _write_moc_agent_output(data, output_path):
    """Write MOC agent JSON result to file for finalize-batch (parse_agent_output detects JSON)."""
    with open(output_path, "w") as f:
        json.dump(data, f, ensure_ascii=False)


def _build_moc_generation(fast, force, index_path, tmp_dir, min_entries=10, only=(), concurrency=50):
    """Phase 4: Generate MOC pages."""
    from wiki_build import build_listing

    # Step 8: Get MOC groups
    tag_aliases_path = os.path.join(tmp_dir, "tag_aliases.json")
    if not os.path.exists(tag_aliases_path):
        tag_aliases_result = _run_config("get", "tag_aliases")
        with open(tag_aliases_path, "w") as f:
            f.write(tag_aliases_result.stdout if tag_aliases_result.returncode == 0 else "{}")

    groups_result = _run_index("list-moc-groups", "--tag-aliases", tag_aliases_path, "--index", index_path)
    if groups_result.returncode != 0:
        click.echo(f"list-moc-groups failed: {groups_result.stderr}", err=True)
        sys.exit(1)

    moc_groups_path = os.path.join(tmp_dir, "moc_groups.json")
    with open(moc_groups_path, "w") as f:
        f.write(groups_result.stdout)

    if fast:
        raw_groups = json.loads(groups_result.stdout)
        filtered = {"tags": {}, "projects": {}}
        for tag, ids in raw_groups.get("tags", {}).items():
            if len(ids) >= min_entries:
                filtered["tags"][tag] = ids
        for proj, ids in raw_groups.get("projects", {}).items():
            if len(ids) >= min_entries:
                filtered["projects"][proj] = ids
        fast_result = _run_render("generate-listings-batch", "--index", index_path,
                                   input_data=json.dumps(filtered))
        if fast_result.returncode != 0:
            click.echo(f"generate-listings-batch failed: {fast_result.stderr}", err=True)
        else:
            click.echo("  MOC listing pages generated (fast mode).")
        return

    # Step 8.5: Filter to target groups
    if only:
        import re as _re
        only_set = set(only)
        raw_groups = json.loads(groups_result.stdout)
        filtered = {"tags": {}, "projects": {}}
        _slug = lambda n: _re.sub(r"[^a-z0-9]+", "-", n.lower()).strip("-")
        for tag, ids in raw_groups.get("tags", {}).items():
            if f"topic-{_slug(tag)}" in only_set:
                filtered["tags"][tag] = ids
        for proj, ids in raw_groups.get("projects", {}).items():
            if f"project-{_slug(proj)}" in only_set:
                filtered["projects"][proj] = ids
        dirty_groups_json = json.dumps(filtered)
    else:
        filter_args = ["filter-dirty-mocs", "--index", index_path]
        if force:
            filter_args.append("--force")
        dirty_result = _run_index(*filter_args, input_data=groups_result.stdout)
        if dirty_result.returncode != 0:
            click.echo(f"filter-dirty-mocs failed: {dirty_result.stderr}", err=True)
            sys.exit(1)
        dirty_groups_json = dirty_result.stdout

    dirty_groups = json.loads(dirty_groups_json) if dirty_groups_json.strip() else {"tags": {}, "projects": {}}
    dirty_count = len(dirty_groups.get("tags", {})) + len(dirty_groups.get("projects", {}))

    if dirty_count == 0:
        click.echo("  All MOC groups up to date, skipped.")
        return

    # Step 9: Prep batches
    prep_result = _run_index("prep-moc-batches", "--min-entries", str(min_entries),
                              "--max-payload-kb", "48", "--index", index_path,
                              input_data=dirty_groups_json)
    if prep_result.returncode != 0:
        click.echo(f"prep-moc-batches failed: {prep_result.stderr}", err=True)
        sys.exit(1)

    manifest = json.loads(prep_result.stdout)
    batch_files = manifest.get("batch_files", [])
    entry_json_map = manifest.get("entry_json_map", {})

    # Dispatch LLM calls concurrently via llm_batch
    from wiki_llm import llm_batch as _llm_batch, validate_schema
    from wiki_validate import _coerce_prose_fields

    llm_config = json.loads(_run_config("get", "llm").stdout)
    success_count = 0
    error_count = 0

    # Collect groups needing dispatch; resume already-rendered ones.
    items = []
    item_meta = {}  # moc_id -> (group, output_path)
    for batch_file in batch_files:
        with open(batch_file) as f:
            batch_data = json.loads(f.read())
        for group in batch_data:
            moc_id = group.get("moc_id", "")
            output_path = os.path.join(tmp_dir, f"moc_{moc_id}_output.json")
            if os.path.exists(output_path):
                click.echo(f"  {moc_id}: skipped (resuming)")
                success_count += 1
                continue
            items.append((moc_id, json.dumps(group)))
            item_meta[moc_id] = (group, output_path)

    if items:
        results = asyncio.run(_llm_batch("build-moc", items, "build-moc", llm_config,
                                          concurrency=concurrency, skip_validation=True))
        for moc_id, result_data, error in results:
            if error or result_data is None:
                error_count += 1
                click.echo(f"  {moc_id}: FAILED — {error}", err=True)
                continue
            try:
                _coerce_prose_fields(result_data)
                validate_schema(result_data, "build-moc")
                group, output_path = item_meta[moc_id]
                entries = group.get("entries", [])
                result_data["listing"] = build_listing(entries, stale_ids=set())
                with open(output_path, "w") as f:
                    json.dump(result_data, f, ensure_ascii=False)
                success_count += 1
                click.echo(f"  {moc_id}: OK")
            except Exception as e:
                error_count += 1
                click.echo(f"  {moc_id}: FAILED — {e}", err=True)

    # Finalize: build manifest for finalize-batch
    finalize_items = []
    for batch_file in batch_files:
        with open(batch_file) as f:
            batch_data = json.loads(f.read())
        for group in batch_data:
            moc_id = group.get("moc_id", "")
            output_path = os.path.join(tmp_dir, f"moc_{moc_id}_output.json")
            entry_json = entry_json_map.get(moc_id, "")
            if os.path.exists(output_path) and entry_json:
                with open(output_path) as f:
                    result_data = json.loads(f.read())

                agent_output_path = os.path.join(tmp_dir, f"moc_{moc_id}_agent_output.txt")
                _write_moc_agent_output(result_data, agent_output_path)

                finalize_items.append({
                    "id": moc_id,
                    "agent_output_file": agent_output_path,
                    "entry_json_file": entry_json,
                    "notes_file": None,
                })

    if finalize_items:
        finalize_result = _run_render("finalize-batch", input_data=json.dumps(finalize_items))
        if finalize_result.returncode != 0:
            click.echo(f"finalize-batch failed: {finalize_result.stderr}", err=True)
        else:
            try:
                finalize_data = json.loads(finalize_result.stdout)
                update_items = []
                for item in finalize_data.get("successes", []):
                    mid = item["id"]
                    entry_json_path = entry_json_map.get(mid, "")
                    if entry_json_path and os.path.exists(entry_json_path):
                        with open(entry_json_path) as f:
                            entry = json.loads(f.read())
                        update_items.append({
                            "id": mid,
                            "wiki_path": item.get("wiki_path", ""),
                            "title": entry.get("title", ""),
                            "tags": entry.get("tags", []),
                            "project": entry.get("project"),
                            "references": entry.get("references", []),
                        })

                if update_items:
                    _run_index("update-moc-entries", "--index", index_path,
                                input_data=json.dumps(update_items))
            except (json.JSONDecodeError, KeyError) as e:
                click.echo(f"MOC update manifest error: {e}", err=True)

    click.echo(f"  {success_count} MOC groups rebuilt. {error_count} failures.")


def _build_topic_map(generate_map, index_path, tmp_dir, min_entries=10):
    """Phase 5: Generate TOPICS.md and optionally MAP.md."""
    moc_groups_path = os.path.join(tmp_dir, "moc_groups.json")
    if not os.path.exists(moc_groups_path):
        click.echo("  Skipping topic map (no moc_groups.json).")
        return

    with open(moc_groups_path) as f:
        moc_data = f.read()

    # Always: generate TOPICS.md
    topics_result = _run_render("generate-topic-index", input_data=moc_data)
    if topics_result.returncode != 0:
        click.echo(f"generate-topic-index failed: {topics_result.stderr}", err=True)
    else:
        click.echo("  TOPICS.md written.")

    if not generate_map:
        return

    # Filter to tags with 3+ entries for map
    moc_groups = json.loads(moc_data)
    tags = moc_groups.get("tags", {})
    map_input = [
        {"tag": tag, "moc_id": f"topic-{tag}", "count": len(ids)}
        for tag, ids in tags.items()
        if len(ids) >= min_entries
    ]

    if not map_input:
        click.echo(f"  No topics with {min_entries}+ entries for MAP.md.")
        return

    # Call build-map agent
    from wiki_llm import llm_call as _llm_call, LLMAPIError, validate_schema
    from wiki_validate import _coerce_prose_fields

    llm_config = json.loads(_run_config("get", "llm").stdout)
    user_msg = json.dumps(map_input)

    try:
        result = _llm_call("build-map", user_msg, "build-map", llm_config, skip_validation=True)
        _coerce_prose_fields(result)
        validate_schema(result, "build-map")
    except (LLMAPIError, Exception) as e:
        click.echo(f"MAP.md generation failed: {e}", err=True)
        return

    map_result = _run_render("generate-map", input_data=json.dumps(result))
    if map_result.returncode != 0:
        click.echo(f"generate-map failed: {map_result.stderr}", err=True)
    else:
        click.echo("  MAP.md written.")


@cli.command()
@click.option("--rank", "rank_mode", type=click.Choice(["fast", "semantic", "deep"]), default="semantic", help="Ranking strategy (default: semantic)")
@click.option("--fast", is_flag=True, help="Listing-only MOC pages, no LLM")
@click.option("--force", is_flag=True, help="Rebuild all MOC groups")
@click.option("--map", "generate_map", is_flag=True, help="Also generate wiki/MAP.md")
@click.option("--min-entries", "min_entries", type=int, default=10, help="Minimum entries for a tag to get a topic page (default: 10)")
@click.option("--concurrency", default=50, type=int, help="Max parallel API calls for MOC and deep-rank dispatch (default: 50)")
@click.option("--index", default=None, help="Path to index.jsonl")
@click.option("--only", multiple=True, default=(), help="Rebuild only these MOC IDs (e.g. topic-sequence-modeling)")
def build(rank_mode, fast, force, generate_map, min_entries, concurrency, index, only):
    """Reconcile backlinks, check staleness, and regenerate MOC pages."""
    index_path = index or ".wiki/index.jsonl"
    tmp_dir = tempfile.mkdtemp(prefix="wiki_build_")

    try:
        if not only:
            click.echo("Phase 1: Data prep...")
            _build_data_prep(index_path, tmp_dir)

            click.echo(f"Phase 2: Ranking ({rank_mode})...")
            _build_ranking(rank_mode, index_path, tmp_dir, concurrency=concurrency)

            click.echo("Phase 3: Staleness...")
            _build_staleness(index_path, tmp_dir)

        click.echo("Phase 4: MOC generation...")
        _build_moc_generation(fast, force, index_path, tmp_dir, min_entries, only=only, concurrency=concurrency)

        if not only:
            click.echo("Phase 5: Topic map...")
            _build_topic_map(generate_map, index_path, tmp_dir, min_entries)

        click.echo("Build complete.")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@cli.command()
@click.option("--mechanical-only", is_flag=True, help="Structural checks only, no LLM")
@click.option("--fix", is_flag=True, help="Auto-repair orphan fragments and broken paths")
@click.option("--index", default=None, help="Path to index.jsonl")
@click.option("--min-frags", default=5, type=int, help="Min fragments per tag for contradiction check")
@click.option("--concurrency", default=10, type=int, help="Max parallel API calls for contradiction check")
def lint(mechanical_only, fix, index, min_frags, concurrency):
    """Audit the wiki for structural and semantic issues."""
    index_path = index or ".wiki/index.jsonl"
    wiki_dir = "wiki"

    # Step 1: Structural lint
    click.echo("Structural checks...")
    struct_result = _run_index("lint-structural", "--wiki-dir", wiki_dir, "--index", index_path)
    if struct_result.returncode != 0:
        click.echo(f"lint-structural failed: {struct_result.stderr}", err=True)
        sys.exit(1)

    report = json.loads(struct_result.stdout) if struct_result.stdout.strip() else {}
    issues = report.get("issues", [])
    click.echo(f"  {len(issues)} structural issues found.")
    for issue in issues[:10]:
        click.echo(f"  - {issue}")
    if len(issues) > 10:
        click.echo(f"  ... and {len(issues) - 10} more.")

    # Step 2: Auto-fix if --fix
    if fix and issues:
        orphan_result = _run_index("get-orphan-ids", input_data=struct_result.stdout)
        if orphan_result.returncode == 0 and orphan_result.stdout.strip():
            orphan_ids = json.loads(orphan_result.stdout)
            if orphan_ids:
                _run_index("delete-fragments-batch", "--index", index_path,
                           input_data=json.dumps(orphan_ids))
                click.echo(f"  Fixed {len(orphan_ids)} orphan fragments.")

    if mechanical_only:
        return

    # Step 3: LLM Pass 1 — Contradiction detection (async batched per-tag)
    click.echo("Pass 1: Contradiction detection...")
    frags_result = _run_index("get-fragments-by-type", "--types", "claim,finding",
                               "--group-by", "tag", "--index", index_path)
    if frags_result.returncode != 0 or not frags_result.stdout.strip():
        click.echo("  No claim/finding fragments to check.")
    else:
        frags_by_tag = json.loads(frags_result.stdout)
        filtered = {tag: frags for tag, frags in frags_by_tag.items() if len(frags) >= min_frags}

        if filtered:
            from wiki_llm import llm_batch
            llm_config = json.loads(_run_config("get", "llm").stdout)

            items = [
                (tag, f"Pass 1: Contradiction Detection\n\n{json.dumps({tag: frags})}")
                for tag, frags in filtered.items()
            ]
            click.echo(f"  Checking {len(items)} tags (≥{min_frags} fragments each)...")

            results = asyncio.run(
                llm_batch("lint", items, "lint-contradictions", llm_config, concurrency=concurrency)
            )

            all_contradictions = []
            errors = 0
            for tag, result, error in results:
                if error:
                    errors += 1
                elif result:
                    all_contradictions.extend(result.get("contradictions", []))

            if all_contradictions:
                click.echo(f"  {len(all_contradictions)} contradictions found:")
                for c in all_contradictions:
                    click.echo(f"    {c['frag_id_a']} vs {c['frag_id_b']}: {c['explanation']}")
            else:
                click.echo("  No contradictions found.")
            if errors:
                click.echo(f"  ({errors}/{len(items)} tags failed)", err=True)
        else:
            click.echo(f"  Not enough fragments per tag for comparison (need ≥{min_frags}).")

    # Step 4: LLM Pass 2 — Cross-link suggestions
    click.echo("Pass 2: Cross-link suggestions...")
    orphan_result = _run_index("get-orphan-ids", input_data=struct_result.stdout)
    if orphan_result.returncode != 0 or not orphan_result.stdout.strip():
        click.echo("  No orphan entries to check.")
    else:
        orphan_ids = json.loads(orphan_result.stdout)
        if orphan_ids:
            entries_result = _run_index("get-entries-batch", "--index", index_path,
                                         input_data=json.dumps(orphan_ids))
            if entries_result.returncode == 0 and entries_result.stdout.strip():
                from wiki_llm import llm_call as _llm_call, LLMAPIError, validate_schema
                from wiki_validate import _coerce_prose_fields
                llm_config = json.loads(_run_config("get", "llm").stdout)

                user_msg = f"Pass 2: Cross-Link Suggestions\n\n{entries_result.stdout}"
                try:
                    result = _llm_call("lint", user_msg, "lint-crosslinks", llm_config, skip_validation=True)
                    _coerce_prose_fields(result)
                    validate_schema(result, "lint-crosslinks")
                    crosslinks = result.get("crosslinks", [])
                    if crosslinks:
                        click.echo(f"  {len(crosslinks)} cross-link suggestions:")
                        for cl in crosslinks:
                            click.echo(f"    {cl['entry_a']} <-> {cl['entry_b']}: {cl['explanation']}")
                    else:
                        click.echo("  No cross-link suggestions.")
                except (LLMAPIError, Exception) as e:
                    click.echo(f"  Pass 2 failed: {e}", err=True)
        else:
            click.echo("  No orphan entries.")


@cli.command("normalize-tags")
@click.option("--dry-run", is_flag=True, help="Show proposals without applying")
@click.option("--apply", "apply_file", default=None, help="Apply alias map from JSON file (merged into the existing map by default)")
@click.option("--replace", is_flag=True, help="With --apply: replace the whole alias map instead of merging into it")
@click.option("--promote", type=int, default=None, help="Promote aliased tags with N+ entries to their own canonical")
@click.option("--index", default=None, help="Path to index.jsonl")
def normalize_tags(dry_run, apply_file, replace, promote, index):
    """Resolve tag drift, synonyms, and duplicates across the index."""
    index_path = index or ".wiki/index.jsonl"

    if apply_file:
        # Normalize the index tags using exactly the supplied map (a surgical delta),
        # then persist aliases. By default the persisted map is the supplied map MERGED
        # into the existing config aliases, so applying a partial/subset map is additive
        # and never clobbers the accumulated vocabulary. --replace restores full-replace.
        result = _run_index("normalize-tags", "--aliases", apply_file, "--index", index_path)
        if result.returncode != 0:
            click.echo(result.stderr.strip() or "Failed to apply alias map.", err=True)
            sys.exit(1)

        if replace:
            _run_config("set-aliases", apply_file)
            click.echo(f"Replaced alias map with {apply_file}.")
            return

        existing_result = _run_config("get", "tag_aliases")
        existing_json = existing_result.stdout if existing_result.returncode == 0 else "{}"
        tmp_dir = tempfile.mkdtemp()
        try:
            existing_path = os.path.join(tmp_dir, "existing_aliases.json")
            with open(existing_path, "w") as f:
                f.write(existing_json.strip() or "{}")
            merge_result = _run_config("merge-aliases", "--existing", existing_path, apply_file)
            if merge_result.returncode != 0:
                click.echo(f"merge-aliases failed: {merge_result.stderr}", err=True)
                sys.exit(1)
            merged_path = os.path.join(tmp_dir, "merged_aliases.json")
            with open(merged_path, "w") as f:
                f.write(merge_result.stdout)
            _run_config("set-aliases", merged_path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        click.echo(f"Merged aliases from {apply_file} into the existing map.")
        return

    if promote is not None:
        aliases_result = _run_config("get", "tag_aliases")
        existing = json.loads(aliases_result.stdout) if aliases_result.returncode == 0 else {}
        counts_result = _run_index("get-tag-counts", "--index", index_path)
        if counts_result.returncode != 0 or not counts_result.stdout.strip():
            click.echo("get-tag-counts failed.", err=True)
            sys.exit(1)
        counts = json.loads(counts_result.stdout)
        promoted = []
        for tag, canon in list(existing.items()):
            if canon and counts.get(tag, 0) >= promote:
                promoted.append((tag, canon, counts[tag]))
        if not promoted:
            click.echo(f"No aliased tags with {promote}+ entries.")
            return
        click.echo(f"Tags eligible for promotion ({promote}+ entries):")
        for tag, canon, count in sorted(promoted, key=lambda x: -x[2]):
            click.echo(f"  {tag} (count={count}, aliased to {canon})")
        if dry_run:
            click.echo(f"\n{len(promoted)} tags would be promoted. Re-run without --dry-run to apply.")
            return
        for tag, canon, count in promoted:
            del existing[tag]
        tmp_dir = tempfile.mkdtemp()
        config_path = os.path.join(tmp_dir, "promoted_aliases.json")
        with open(config_path, "w") as f:
            json.dump({k: v for k, v in existing.items() if v}, f)
        _run_config("set-aliases", config_path)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        click.echo(f"Promoted {len(promoted)} tags (removed from alias map).")
        return

    # --- Setup ---
    aliases_result = _run_config("get", "tag_aliases")
    existing_aliases = aliases_result.stdout if aliases_result.returncode == 0 else "{}"
    existing_aliases_dict = json.loads(existing_aliases)
    known_canonicals = set(v for v in existing_aliases_dict.values() if v)

    freq_result = _run_index("get-canonical-tags", "--min-count", "10", "--index", index_path)
    freq_tags = set(json.loads(freq_result.stdout)) if freq_result.returncode == 0 and freq_result.stdout.strip() else set()
    blocklist_result = _run_config("get", "tag_blocklist")
    blocklist = set(json.loads(blocklist_result.stdout)) if blocklist_result.returncode == 0 and blocklist_result.stdout.strip() else set()
    known_canonicals = (known_canonicals | freq_tags) - blocklist

    tmp_dir = tempfile.mkdtemp()
    aliases_path = os.path.join(tmp_dir, "existing_aliases.json")
    with open(aliases_path, "w") as f:
        f.write(existing_aliases)

    groups_result = _run_index("list-moc-groups", "--tag-aliases", aliases_path, "--index", index_path)
    if groups_result.returncode != 0:
        click.echo(f"list-moc-groups failed: {groups_result.stderr}", err=True)
        sys.exit(1)

    unresolved_result = _run_index("get-unresolved-tags", "--aliases", aliases_path,
                                    input_data=groups_result.stdout)
    if unresolved_result.returncode != 0 or not unresolved_result.stdout.strip():
        click.echo("No unresolved tags.")
        return

    unresolved = json.loads(unresolved_result.stdout)
    if not unresolved:
        click.echo("All tags resolved.")
        return

    # Skip known canonicals — already in canonical form
    truly_unresolved = {t: c for t, c in unresolved.items() if t not in known_canonicals}
    skipped_canon = len(unresolved) - len(truly_unresolved)
    click.echo(f"{len(unresolved)} unresolved tags. ({skipped_canon} already-canonical skipped)")

    if not truly_unresolved:
        click.echo("All tags resolved.")
        return

    # --- Stage 1: Full-index count gate ---
    counts_result = _run_index("get-tag-counts", "--index", index_path)
    if counts_result.returncode != 0:
        click.echo(f"get-tag-counts failed: {counts_result.stderr}", err=True)
        sys.exit(1)
    if not counts_result.stdout.strip():
        click.echo("get-tag-counts returned empty output.", err=True)
        sys.exit(1)
    full_counts = json.loads(counts_result.stdout)

    gated = {t: c for t, c in truly_unresolved.items() if full_counts.get(t, 0) >= 1}
    dropped = len(truly_unresolved) - len(gated)
    if dropped:
        click.echo(f"  ({dropped} zero-count tags dropped)")

    if not gated:
        click.echo("No tags to process after count gate.")
        return

    # --- Stage 2: MiniLM clustering ---
    click.echo(f"Clustering {len(gated)} tags with MiniLM...")
    cluster_result = _run_embed("cluster-tags", input_data=json.dumps(sorted(gated.keys())))
    if cluster_result.returncode != 0:
        click.echo(f"cluster-tags failed: {cluster_result.stderr}", err=True)
        sys.exit(1)
    if not cluster_result.stdout.strip():
        click.echo("cluster-tags returned empty output.", err=True)
        sys.exit(1)
    cluster_data = json.loads(cluster_result.stdout)
    clusters = cluster_data.get("clusters", [])
    isolated = cluster_data.get("isolated", [])

    from wiki_llm import llm_call as _llm_call, LLMAPIError, validate_schema
    llm_result = _run_config("get", "llm")
    if llm_result.returncode != 0 or not llm_result.stdout.strip():
        click.echo(f"Failed to load LLM config: {llm_result.stderr}", err=True)
        sys.exit(1)
    llm_config = json.loads(llm_result.stdout)
    protected_list = sorted(known_canonicals)

    final_aliases = {}
    consumed = set()

    # --- Stage 3a: Auto-merge (cosine >= 0.90) ---
    for cluster in [c for c in clusters if c["tier"] == "auto"]:
        tags_in = cluster["tags"]
        canonical = max(tags_in, key=lambda t: (full_counts.get(t, 0), -len(t)))
        for t in tags_in:
            if t != canonical:
                final_aliases[t] = canonical
            consumed.add(t)
        click.echo(f"  Auto: {[t for t in tags_in if t != canonical]} -> {canonical}")

    # --- Stage 3b: Per-cluster LLM confirm (0.75-0.90) ---
    for cluster in [c for c in clusters if c["tier"] == "llm"]:
        tags_in = cluster["tags"]
        cluster_counts = {t: full_counts.get(t, 0) for t in tags_in}
        user_msg = json.dumps({"tags": cluster_counts, "protected": protected_list})
        try:
            result = _llm_call("normalize-tags-cluster", user_msg, "normalize-tags-cluster",
                               llm_config, skip_validation=True)
            validate_schema(result, "normalize-tags-cluster")
        except (LLMAPIError, Exception) as e:
            click.echo(f"  Cluster LLM failed for {tags_in}: {e}", err=True)
            continue
        canonical = result.get("canonical")
        if canonical is None:
            click.echo(f"  Distinct: {tags_in}")
            continue
        for t in result.get("aliases", []):
            if t != canonical and t not in known_canonicals:
                final_aliases[t] = canonical
        for t in tags_in:
            consumed.add(t)
        click.echo(f"  LLM: {result.get('aliases', [])} -> {canonical}")

    # --- Stage 3c: Match remaining tags against existing canonicals ---
    remaining = [t for t in sorted(gated) if t not in consumed and t not in known_canonicals]
    if remaining and known_canonicals:
        click.echo(f"Matching {len(remaining)} remaining tags against {len(known_canonicals)} canonicals...")
        match_input = json.dumps({"unresolved": remaining, "canonicals": sorted(known_canonicals)})
        match_result = _run_embed("match-canonicals", input_data=match_input)
        if match_result.returncode != 0 or not match_result.stdout.strip():
            click.echo(f"match-canonicals failed: {match_result.stderr}", err=True)
            match_data = {"matches": [], "unmatched": remaining}
        else:
            match_data = json.loads(match_result.stdout)
    else:
        match_data = {"matches": [], "unmatched": remaining}

    # --- Stage 3d: LLM confirm canonical matches (reuses normalize-tags-cluster) ---
    by_canonical = {}
    for m in match_data.get("matches", []):
        by_canonical.setdefault(m["canonical"], []).append(m)

    match_rejected = []
    for canon, candidates in by_canonical.items():
        cluster_counts = {canon: full_counts.get(canon, 0)}
        for c in candidates:
            cluster_counts[c["tag"]] = full_counts.get(c["tag"], 0)
        user_msg = json.dumps({"tags": cluster_counts, "protected": [canon]})
        try:
            result = _llm_call("normalize-tags-cluster", user_msg, "normalize-tags-cluster",
                               llm_config, skip_validation=True)
            validate_schema(result, "normalize-tags-cluster")
        except (LLMAPIError, Exception) as e:
            click.echo(f"  Match LLM failed for canonical {canon}: {e}", err=True)
            match_rejected.extend(c["tag"] for c in candidates)
            continue
        result_canonical = result.get("canonical")
        if result_canonical is None:
            click.echo(f"  Match distinct: {[c['tag'] for c in candidates]} ≠ {canon}")
            match_rejected.extend(c["tag"] for c in candidates)
            continue
        for t in result.get("aliases", []):
            if t != result_canonical and t not in known_canonicals:
                final_aliases[t] = result_canonical
                click.echo(f"  Match: {t} -> {result_canonical}")
        rejected_here = [c["tag"] for c in candidates
                         if c["tag"] not in result.get("aliases", []) and c["tag"] != result_canonical]
        match_rejected.extend(rejected_here)

    # --- Stage 3e: LLM canonical assignment (true orphans) ---
    # Build orphan list with nearest canonicals attached
    unmatched_orphans = match_data.get("unmatched", [])
    # Re-attach nearest for rejected tags from match data
    match_nearest_lookup = {}
    for m in match_data.get("matches", []):
        match_nearest_lookup[m["tag"]] = [{"canonical": m["canonical"], "similarity": m["similarity"]}]
    orphan_entries = []
    for item in unmatched_orphans:
        if isinstance(item, dict):
            orphan_entries.append(item)
        else:
            orphan_entries.append({"tag": item, "nearest": match_nearest_lookup.get(item, [])})
    for tag in match_rejected:
        orphan_entries.append({"tag": tag, "nearest": match_nearest_lookup.get(tag, [])})

    orphan_entries = [o for o in orphan_entries if full_counts.get(o["tag"], 0) >= 1]
    if orphan_entries:
        ASSIGN_BATCH_SIZE = 30
        for batch_start in range(0, len(orphan_entries), ASSIGN_BATCH_SIZE):
            batch = orphan_entries[batch_start:batch_start + ASSIGN_BATCH_SIZE]
            user_msg = json.dumps({"orphans": batch})
            try:
                result = _llm_call("normalize-tags-assign", user_msg, "normalize-tags-assign",
                                   llm_config, skip_validation=True)
                validate_schema(result, "normalize-tags-assign")
                for entry in result.get("assign", []):
                    t, canon = entry.get("tag", ""), entry.get("canonical", "")
                    if t and canon and t not in known_canonicals:
                        final_aliases[t] = canon
                        click.echo(f"  Assign: {t} -> {canon}")
                for t in result.get("remove", []):
                    if t not in known_canonicals:
                        final_aliases[t] = ""
                        click.echo(f"  Assign remove: {t}")
                new_count = len(result.get("new", []))
                if new_count:
                    click.echo(f"  Assign new: {new_count} tags kept as-is")
            except (LLMAPIError, Exception) as e:
                click.echo(f"  Assign LLM failed: {e}", err=True)

    # --- Stage 4: Protected-canonical post-filter (defense-in-depth) ---
    filtered = {}
    for non_canon, canon in final_aliases.items():
        if non_canon in known_canonicals:
            click.echo(f"  Blocked: {non_canon} is a protected canonical", err=True)
            continue
        if canon == non_canon:
            continue
        filtered[non_canon] = canon

    if not filtered:
        click.echo("No aliases proposed after filtering.")
        return

    # Merge with existing aliases and write final file
    new_aliases_path = os.path.join(tmp_dir, "new_aliases.json")
    with open(new_aliases_path, "w") as f:
        json.dump(filtered, f)

    merge_result = _run_config("merge-aliases", "--existing", aliases_path, new_aliases_path)
    if merge_result.returncode == 0 and merge_result.stdout.strip():
        merged = json.loads(merge_result.stdout)
    else:
        click.echo("Warning: merge-aliases failed, using new aliases only.", err=True)
        merged = filtered

    full_path = os.path.join(tmp_dir, "full_aliases.json")
    with open(full_path, "w") as f:
        json.dump(merged, f)

    config_only = {k: v for k, v in merged.items() if v}
    config_path = os.path.join(tmp_dir, "config_aliases.json")
    with open(config_path, "w") as f:
        json.dump(config_only, f)

    if dry_run:
        click.echo("Proposed aliases (dry run):")
        for k, v in filtered.items():
            action = f"-> {v}" if v else "(remove)"
            click.echo(f"  {k} {action}")
        click.echo(f"\nTo apply: normalize-tags --apply {full_path}")
    else:
        _run_index("normalize-tags", "--aliases", full_path, "--index", index_path)
        _run_config("set-aliases", config_path)
        click.echo(f"Applied {len(filtered)} alias mappings.")
        shutil.rmtree(tmp_dir, ignore_errors=True)


@cli.group()
def idea():
    """Create, ingest, and improve wiki idea entries."""
    pass


@idea.command("create")
@click.argument("title")
@click.option("--tags", default=None, help="Comma-separated tags")
@click.option("--project", default=None, help="Project name")
@click.option("--index", default=None, help="Path to index.jsonl")
@click.option("--wiki-dir", default=None, help="Wiki content directory")
def idea_create(title, tags, project, index, wiki_dir):
    """Create a new empty idea page."""
    from wiki_idea import generate_idea_page

    idea_id = slugify(title, prefix="idea-")
    index_path = index or ".wiki/index.jsonl"
    resolved_wiki_dir = wiki_dir or "wiki"

    exists_result = _run_index("exists", idea_id, "--index", index_path)
    if exists_result.returncode == 0:
        click.echo(f"ID {idea_id} already exists.", err=True)
        sys.exit(1)

    tag_list = [t.strip() for t in tags.split(",")] if tags else []
    create_args = [
        "create-entry", "--id", idea_id, "--type", "idea",
        "--source-type", "text", "--title", title, "--status", "rendered",
    ]
    if tag_list:
        create_args.extend(["--tags", ",".join(tag_list)])
    if project:
        create_args.extend(["--project", project])

    create_result = _run_index(*create_args)
    _run_index("add", "--index", index_path, input_data=create_result.stdout)

    page = generate_idea_page(idea_id, title, tag_list, project, "")
    page_dir = os.path.join(resolved_wiki_dir, "ideas")
    os.makedirs(page_dir, exist_ok=True)
    page_path = os.path.join(page_dir, f"{idea_id}.md")
    with open(page_path, "w") as f:
        f.write(page)

    _run_index("update", idea_id, "--wiki-path", page_path, "--index", index_path)
    click.echo(f"Created {idea_id} at {page_path}. Fill in content, then run `idea improve {idea_id}`.")


@idea.command("ingest")
@click.argument("source", required=False)
@click.option("--text", "pasted_text", default=None, help="Pasted text content")
@click.option("--tags", default=None, help="Comma-separated tags")
@click.option("--project", default=None, help="Project name")
@click.option("--improve", is_flag=True, help="Auto-improve after ingest")
@click.option("--local", is_flag=True, help="Skip web evidence pass during improve")
@click.option("--index", default=None, help="Path to index.jsonl")
@click.option("--wiki-dir", default=None, help="Wiki content directory")
def idea_ingest(source, pasted_text, tags, project, improve, local, index, wiki_dir):
    """Create an idea from a file or pasted text."""
    from wiki_idea import generate_idea_page

    index_path = index or ".wiki/index.jsonl"
    resolved_wiki_dir = wiki_dir or "wiki"

    if pasted_text:
        content = pasted_text
        title = content.split("\n")[0].lstrip("# ").strip()[:60] or "Untitled Idea"
    elif source and os.path.exists(source):
        with open(source) as f:
            content = f.read()
        title = None
        for line in content.split("\n"):
            if line.startswith("# "):
                title = line.lstrip("# ").strip()
                break
        if not title:
            title = os.path.splitext(os.path.basename(source))[0].replace("-", " ").replace("_", " ").title()
    else:
        click.echo("Provide a file path or --text", err=True)
        sys.exit(1)

    idea_id = slugify(title, prefix="idea-")

    exists_result = _run_index("exists", idea_id, "--index", index_path)
    if exists_result.returncode == 0:
        click.echo(f"ID {idea_id} already exists.", err=True)
        sys.exit(1)

    tag_list = [t.strip() for t in tags.split(",")] if tags else []
    source_type = "markdown" if source and source.endswith(".md") else "text"

    create_args = [
        "create-entry", "--id", idea_id, "--type", "idea",
        "--source-type", source_type, "--title", title, "--status", "rendered",
    ]
    if tag_list:
        create_args.extend(["--tags", ",".join(tag_list)])
    if project:
        create_args.extend(["--project", project])
    if source:
        create_args.extend(["--source-path", source])

    create_result = _run_index(*create_args)
    _run_index("add", "--index", index_path, input_data=create_result.stdout)

    page = generate_idea_page(idea_id, title, tag_list, project, content)
    page_dir = os.path.join(resolved_wiki_dir, "ideas")
    os.makedirs(page_dir, exist_ok=True)
    page_path = os.path.join(page_dir, f"{idea_id}.md")
    with open(page_path, "w") as f:
        f.write(page)

    _run_index("update", idea_id, "--wiki-path", page_path, "--index", index_path)
    click.echo(f"Ingested {idea_id} from {'text' if pasted_text else source}.")

    if improve:
        click.echo("Running improve...")
        improve_args = ["idea", "improve", idea_id, "--index", index_path, "--wiki-dir", resolved_wiki_dir]
        if local:
            improve_args.append("--local")
        _run_self(*improve_args)


@idea.command("improve")
@click.argument("idea_id")
@click.option("--local", is_flag=True, help="Skip web evidence pass")
@click.option("--index", default=None, help="Path to index.jsonl")
@click.option("--wiki-dir", default=None, help="Wiki content directory")
def idea_improve(idea_id, local, index, wiki_dir):
    """Run evidence-based critique on an idea."""
    from wiki_idea import merge_tags
    from wiki_llm import llm_call as _llm_call, LLMAPIError, validate_schema
    from wiki_validate import _coerce_prose_fields
    from datetime import datetime, timezone

    index_path = index or ".wiki/index.jsonl"

    # Step 1: Get entry
    get_result = _run_index("get", idea_id, "--index", index_path)
    if get_result.returncode != 0:
        click.echo(f"Entry not found: {idea_id}", err=True)
        sys.exit(1)
    entry = json.loads(get_result.stdout)

    # Step 2: Extract user zone
    wiki_path = entry.get("wiki_path", "")
    if not wiki_path or not os.path.exists(wiki_path):
        click.echo(f"Wiki page not found: {wiki_path}", err=True)
        sys.exit(1)

    zone_result = _run_render("extract-zone", wiki_path)
    idea_text = zone_result.stdout.strip() if zone_result.returncode == 0 else ""
    if not idea_text:
        click.echo("No idea content found in user zone.", err=True)
        sys.exit(1)

    # Step 3: Extract search terms via LLM
    llm_config = json.loads(_run_config("get", "llm").stdout)

    click.echo("Extracting search terms...")
    try:
        terms_result = _llm_call("search-terms", idea_text, "search-terms", llm_config)
        search_terms = terms_result.get("terms", [])
    except (LLMAPIError, Exception):
        search_terms = entry.get("tags", [])

    # Step 4: Score fragments via MiniLM
    click.echo("Scoring evidence fragments...")
    score_result = _run_embed("score-idea", "--exclude-id", idea_id, "--top-n", "30",
                               input_data=idea_text)
    evidence = score_result.stdout if score_result.returncode == 0 else "[]"

    # Step 5: LLM critique
    click.echo("Running critique...")
    critique_input = (
        f"## Idea\nTitle: {entry.get('title', '')}\n"
        f"Tags: {', '.join(entry.get('tags', []))}\n"
        f"Project: {entry.get('project', '')}\n\n"
        f"### Idea Text\n{idea_text}\n\n"
        f"## Evidence\n{evidence}"
    )

    try:
        critique = _llm_call("idea-improve", critique_input, "idea-improve", llm_config, skip_validation=True)
        _coerce_prose_fields(critique)
        validate_schema(critique, "idea-improve")
    except (LLMAPIError, Exception) as e:
        click.echo(f"Critique failed: {e}", err=True)
        sys.exit(1)

    # Step 6: Write agent output as JSON for splice-idea (parse_agent_output detects JSON)
    # splice-idea expects TAGS_FINALIZED block; rename from schema key "tags"
    if "tags" in critique and "tags_finalized" not in critique:
        critique["tags_finalized"] = critique.pop("tags")
    agent_output_path = os.path.join(tempfile.mkdtemp(), f"{idea_id}_agent_output.json")
    with open(agent_output_path, "w") as f:
        json.dump(critique, f, ensure_ascii=False)

    # Step 7: Splice into page
    splice_result = _run_render("splice-idea", wiki_path, "--agent-output", agent_output_path)
    if splice_result.returncode != 0:
        click.echo(f"Splice failed: {splice_result.stderr}", err=True)
        sys.exit(1)

    # Step 8: Update index
    existing_tags = entry.get("tags", [])
    new_tags = critique.get("tags_finalized", critique.get("tags", []))
    merged = merge_tags(existing_tags, new_tags)

    update_args = ["update", idea_id, "--last-improved", "now",
                   "--tags", ",".join(merged), "--index", index_path]
    sources = critique.get("sources", [])
    if sources:
        update_args.extend(["--references", ",".join(sources)])
    _run_index(*update_args)

    # Delete old fragments, add new
    _run_index("delete-fragments", idea_id, "--index", index_path)
    frags = critique.get("fragments", [])
    if frags:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        frag_entries = []
        for frag in frags:
            frag_entries.append({
                "id": f"frag-{idea_id}-{frag['seq']:02d}",
                "type": frag["type"],
                "title": frag["title"],
                "tags": merged,
                "project": entry.get("project"),
                "references": [idea_id],
                "ingested": now,
            })
        _run_index("add-fragments-batch", "--index", index_path,
                    input_data=json.dumps(frag_entries))

    click.echo(f"Improved {idea_id}. Evidence review updated.")


@cli.command("synthesis-refresh")
@click.argument("entry_id")
@click.option("--index", default=None, help="Path to index.jsonl")
def synthesis_refresh(entry_id, index):
    """Refresh a stale synthesis with new evidence."""
    from datetime import datetime, timezone

    index_path = index or ".wiki/index.jsonl"

    # Step 1: Get entry
    get_result = _run_index("get", entry_id, "--index", index_path)
    if get_result.returncode != 0:
        click.echo(f"Entry not found: {entry_id}", err=True)
        sys.exit(1)
    entry = json.loads(get_result.stdout)

    if entry.get("type") != "synthesis":
        click.echo(f"{entry_id} is not a synthesis (type: {entry.get('type')})", err=True)
        sys.exit(1)
    if entry.get("locked"):
        click.echo(f"{entry_id} is locked. Use /unlock first.", err=True)
        sys.exit(1)

    # Step 2: Extract existing synthesis text
    wiki_path = entry.get("wiki_path", "")
    if not wiki_path or not os.path.exists(wiki_path):
        click.echo(f"Wiki page not found: {wiki_path}", err=True)
        sys.exit(1)

    # Read page directly and extract just the ## Synthesis content
    with open(wiki_path) as f:
        page_text = f.read()

    # Extract content between ## Synthesis and the next ## section
    import re as _re
    synth_match = _re.search(r'## Synthesis\n\n(.*?)(?=\n## )', page_text, _re.DOTALL)
    existing_text = synth_match.group(1).strip() if synth_match else ""
    if not existing_text:
        click.echo("No synthesis content found under ## Synthesis.", err=True)
        sys.exit(1)

    # Step 3: Check staleness
    stale_result = _run_index("check-stale", "--index", index_path)
    if stale_result.returncode != 0:
        click.echo(f"check-stale failed: {stale_result.stderr}", err=True)
        sys.exit(1)

    stale_list = json.loads(stale_result.stdout) if stale_result.stdout.strip() else []
    stale_entry = next((s for s in stale_list if s["id"] == entry_id), None)
    if not stale_entry:
        click.echo(f"{entry_id} is up to date — no new evidence.")
        return

    new_ids = stale_entry.get("stale_entries", [])
    click.echo(f"{len(new_ids)} new entries since last render. Reading...")

    # Step 4: Read new entries' content
    pages_result = _run_render("read-batch", "--format", "text", "--index", index_path,
                                input_data=json.dumps(new_ids))
    if pages_result.returncode != 0:
        click.echo(f"read-batch failed: {pages_result.stderr}", err=True)
        sys.exit(1)
    new_evidence = pages_result.stdout

    # Step 5: LLM call
    from wiki_llm import llm_call as _llm_call, LLMAPIError, validate_schema
    from wiki_validate import _coerce_prose_fields
    llm_config = json.loads(_run_config("get", "llm").stdout)

    user_msg = (
        f"## Existing Synthesis\n\n{existing_text}\n\n"
        f"## New Evidence ({len(new_ids)} entries)\n\n{new_evidence}"
    )

    click.echo("Refreshing synthesis...")
    try:
        result = _llm_call("synthesis-refresh", user_msg, "synthesis-refresh", llm_config, skip_validation=True)
        _coerce_prose_fields(result)
        validate_schema(result, "synthesis-refresh")
    except (LLMAPIError, Exception) as e:
        click.echo(f"LLM call failed: {e}", err=True)
        sys.exit(1)

    # Step 6: Update the wiki page
    with open(wiki_path) as f:
        page_content = f.read()

    # Replace ## Synthesis section
    new_synthesis = result.get("synthesis", "")
    new_sources = result.get("sources", [])
    source_links = "\n".join(f"- [[{s}]]" for s in new_sources)

    # Find and replace synthesis section (between ## Synthesis and the next ##)
    import re as _re
    pattern = _re.compile(r'(## Synthesis\n\n)(.*?)(\n## )', _re.DOTALL)
    match = pattern.search(page_content)
    if match:
        updated = page_content[:match.start(2)] + new_synthesis + "\n\n" + page_content[match.start(3):]
    else:
        updated = page_content

    # Replace ## Sources section
    sources_pattern = _re.compile(r'(## Sources\n\n)(.*?)(\n## )', _re.DOTALL)
    sources_match = sources_pattern.search(updated)
    if sources_match:
        updated = updated[:sources_match.start(2)] + source_links + "\n\n" + updated[sources_match.start(3):]

    # Strip any staleness banners
    updated = _re.sub(r'<!-- wiki-staleness-banner -->\n>.*?\n\n?', '', updated)

    tmp = wiki_path + ".tmp"
    with open(tmp, "w") as f:
        f.write(updated)
    os.rename(tmp, wiki_path)

    # Step 7: Update index
    new_tags = result.get("tags", [])
    existing_tags = entry.get("tags", [])
    merged_tags = list(existing_tags)
    for t in new_tags:
        if t not in merged_tags:
            merged_tags.append(t)

    update_args = ["update", entry_id, "--rendered", "now",
                   "--tags", ",".join(merged_tags),
                   "--references", ",".join(new_sources),
                   "--index", index_path]
    _run_index(*update_args)

    # Step 8: Update fragments
    _run_index("delete-fragments", entry_id, "--index", index_path)
    frags = result.get("fragments", [])
    if frags:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        frag_entries = [{
            "id": f"frag-{entry_id}-{f['seq']:02d}",
            "type": f["type"], "title": f["title"],
            "tags": merged_tags, "project": entry.get("project"),
            "references": [entry_id], "ingested": now,
        } for f in frags]
        _run_index("add-fragments-batch", "--index", index_path,
                    input_data=json.dumps(frag_entries))

    click.echo(f"Refreshed {entry_id} with {len(new_ids)} new entries. {len(frags)} fragments updated.")


if __name__ == "__main__":
    cli()
