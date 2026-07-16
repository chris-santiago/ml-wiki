"""Tests for wiki CLI ingest command."""

import json
import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wiki_cli import cli

# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------

_CLI_PATH = str(Path(__file__).resolve().parent.parent / "wiki_cli.py")


def _run_cli_subprocess(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, _CLI_PATH, *args],
        capture_output=True,
        text=True,
    )


def _make_completed_process(stdout: str = "", stderr: str = "", returncode: int = 0) -> CompletedProcess:
    return CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


_FAKE_ENTRY_JSON = json.dumps({
    "id": "smith2024",
    "type": "paper",
    "source_type": "pdf",
    "source_path": "/tmp/smith2024.pdf",
    "wiki_path": None,
    "zotero_uri": None,
    "pdf_uri": None,
    "arxiv_id": None,
    "title": "smith2024",
    "citation": None,
    "year": None,
    "tags": [],
    "project": None,
    "source_name": None,
    "references": [],
    "status": "stub",
    "locked": False,
    "ingested": "2026-01-01T00:00:00Z",
    "rendered": None,
    "last_improved": None,
})

_FAKE_IMAGE_ENTRY_JSON = json.dumps({
    "id": "img-confusion-matrix",
    "type": "image",
    "source_type": "image",
    "source_path": "/tmp/wiki/assets/confusion_matrix.png",
    "wiki_path": None,
    "zotero_uri": None,
    "pdf_uri": None,
    "arxiv_id": None,
    "title": "Confusion Matrix",
    "citation": None,
    "year": None,
    "tags": [],
    "project": None,
    "source_name": None,
    "references": [],
    "status": "stub",
    "locked": False,
    "ingested": "2026-01-01T00:00:00Z",
    "rendered": None,
    "last_improved": None,
})

_FAKE_ASSEMBLE_IMAGE_STDOUT = json.dumps({
    "wiki_path": "wiki/images/img-confusion-matrix.md",
    "tags_finalized": [],
    "fragments": [],
})


def _make_default_ingest_side_effect(entry_stdout: str = _FAKE_ENTRY_JSON):
    """Return a side_effect for _run_index that works for a normal (non-collision) ingest."""

    def side_effect(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "exists":
            # Not found — return code 1 (not a collision)
            return _make_completed_process(returncode=1)
        if subcommand == "create-entry":
            return _make_completed_process(stdout=entry_stdout, returncode=0)
        if subcommand in ("add", "update", "add-fragments-batch"):
            return _make_completed_process(returncode=0)
        if subcommand == "get":
            return _make_completed_process(stdout=entry_stdout, returncode=0)
        return _make_completed_process(returncode=0)

    return side_effect


def _make_image_ingest_side_effect():
    """Return a side_effect for _run_index that covers the image ingest call sequence."""

    def side_effect(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "exists":
            return _make_completed_process(returncode=1)
        if subcommand == "create-entry":
            return _make_completed_process(stdout=_FAKE_IMAGE_ENTRY_JSON, returncode=0)
        if subcommand in ("add", "update", "add-fragments-batch"):
            return _make_completed_process(returncode=0)
        if subcommand == "get":
            return _make_completed_process(stdout=_FAKE_IMAGE_ENTRY_JSON, returncode=0)
        return _make_completed_process(returncode=0)

    return side_effect


# ---------------------------------------------------------------------------
# Unit tests (mock _run_* helpers)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ingest_pdf_args(mock_run_helpers, wiki_env, tmp_path):
    """PDF ingest calls _run_index create-entry with type=paper, source_type=pdf, status=stub."""
    mock_run_helpers["_run_index"].side_effect = _make_default_ingest_side_effect()

    pdf_path = tmp_path / "smith2024.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    runner = CliRunner()
    result = runner.invoke(cli, [
        "ingest", str(pdf_path),
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, f"ingest failed:\n{result.output}"

    index_mock = mock_run_helpers["_run_index"]
    create_calls = [c for c in index_mock.call_args_list if c[0][0] == "create-entry"]
    assert len(create_calls) == 1, f"Expected one create-entry call, got {len(create_calls)}"

    call_args = list(create_calls[0][0])
    assert "--type" in call_args
    assert call_args[call_args.index("--type") + 1] == "paper"
    assert "--source-type" in call_args
    assert call_args[call_args.index("--source-type") + 1] == "pdf"
    assert "--status" in call_args
    assert call_args[call_args.index("--status") + 1] == "stub"


@pytest.mark.unit
def test_ingest_text_flag(mock_run_helpers, wiki_env):
    """--text ingest writes text file to sources_dir and calls create-entry with source_type=text."""
    mock_run_helpers["_run_index"].side_effect = _make_default_ingest_side_effect(
        entry_stdout=json.dumps({
            "id": "my-note",
            "type": "paper",
            "source_type": "text",
            "source_path": str(wiki_env.sources_dir / "my-note.txt"),
            "wiki_path": None,
            "zotero_uri": None,
            "pdf_uri": None,
            "arxiv_id": None,
            "title": "my-note",
            "citation": None,
            "year": None,
            "tags": [],
            "project": None,
            "source_name": None,
            "references": [],
            "status": "stub",
            "locked": False,
            "ingested": "2026-01-01T00:00:00Z",
            "rendered": None,
            "last_improved": None,
        })
    )

    runner = CliRunner()
    result = runner.invoke(cli, [
        "ingest",
        "--text", "some content here",
        "--id", "my-note",
        "--index", str(wiki_env.index_path),
        "--sources-dir", str(wiki_env.sources_dir),
    ])
    assert result.exit_code == 0, f"ingest --text failed:\n{result.output}"

    # Text file must be written to sources_dir in-process
    txt_path = wiki_env.sources_dir / "my-note.txt"
    assert txt_path.exists(), f"Expected text file at {txt_path}"
    assert txt_path.read_text() == "some content here"

    # create-entry must receive source_type=text
    index_mock = mock_run_helpers["_run_index"]
    create_calls = [c for c in index_mock.call_args_list if c[0][0] == "create-entry"]
    assert len(create_calls) == 1
    call_args = list(create_calls[0][0])
    assert "--source-type" in call_args
    assert call_args[call_args.index("--source-type") + 1] == "text"


@pytest.mark.unit
def test_ingest_image_calls_assemble(mock_run_helpers, wiki_env, tmp_path):
    """Image ingest copies file to assets/ and calls _run_render with assemble-image."""
    mock_run_helpers["_run_index"].side_effect = _make_image_ingest_side_effect()
    mock_run_helpers["_run_render"].return_value = _make_completed_process(
        stdout=_FAKE_ASSEMBLE_IMAGE_STDOUT, returncode=0
    )

    img_path = tmp_path / "confusion_matrix.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    runner = CliRunner()
    result = runner.invoke(cli, [
        "ingest", str(img_path),
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
        "--caption", "Confusion Matrix",
        "--description", "A confusion matrix.",
    ])
    assert result.exit_code == 0, f"image ingest failed:\n{result.output}"

    render_mock = mock_run_helpers["_run_render"]
    render_calls = render_mock.call_args_list
    assemble_calls = [c for c in render_calls if c[0][0] == "assemble-image"]
    assert len(assemble_calls) >= 1, (
        f"Expected _run_render('assemble-image') to be called; calls: {[c[0][0] for c in render_calls]}"
    )


@pytest.mark.unit
def test_ingest_collision_fails(mock_run_helpers, wiki_env, tmp_path):
    """When _run_index exists returns 0 (found), ingest must exit non-zero."""

    def collision_side_effect(*args, **kw):
        subcommand = args[0] if args else ""
        if subcommand == "exists":
            return _make_completed_process(returncode=0)  # collision
        return _make_completed_process(returncode=0)

    mock_run_helpers["_run_index"].side_effect = collision_side_effect

    pdf_path = tmp_path / "existing.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    runner = CliRunner()
    result = runner.invoke(cli, [
        "ingest", str(pdf_path),
        "--index", str(wiki_env.index_path),
    ])
    assert result.exit_code != 0, "Expected non-zero exit for collision; got 0"


@pytest.mark.unit
def test_ingest_directory_recursive(mock_run_helpers, wiki_env, tmp_path):
    """Directory ingest calls _run_self once per file in the directory."""
    mock_run_helpers["_run_index"].side_effect = _make_default_ingest_side_effect()
    mock_run_helpers["_run_self"].return_value = _make_completed_process(returncode=0)

    ingest_dir = tmp_path / "papers"
    ingest_dir.mkdir()
    (ingest_dir / "paper-a.pdf").write_bytes(b"%PDF-1.4 fake")
    (ingest_dir / "paper-b.pdf").write_bytes(b"%PDF-1.4 fake")

    runner = CliRunner()
    result = runner.invoke(cli, [
        "ingest", str(ingest_dir),
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, f"directory ingest failed:\n{result.output}"

    self_mock = mock_run_helpers["_run_self"]
    assert self_mock.call_count == 2, (
        f"Expected _run_self called twice (once per PDF), got {self_mock.call_count}"
    )


# ---------------------------------------------------------------------------
# Integration tests (real scripts on disk)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_ingest_pdf_creates_stub(wiki_env, tmp_path):
    """Real PDF ingest creates a stub entry in the index."""
    pdf_path = tmp_path / "smith2024.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    runner = CliRunner()
    result = runner.invoke(cli, [
        "ingest", str(pdf_path),
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, f"ingest failed:\n{result.output}"

    entries = [
        json.loads(line)
        for line in wiki_env.index_path.read_text().splitlines()
        if line.strip()
    ]
    matching = [e for e in entries if e["id"] == "smith2024"]
    assert len(matching) == 1, f"Expected 1 entry with id 'smith2024', got {len(matching)}"
    entry = matching[0]
    assert entry["type"] == "paper"
    assert entry["source_type"] == "pdf"
    assert entry["status"] == "stub"


@pytest.mark.integration
def test_ingest_markdown_copies_file(wiki_env, tmp_path):
    """Markdown reference ingest copies the file to wiki/_pages/ with status=rendered."""
    md_path = tmp_path / "notes-on-rnns.md"
    md_path.write_text("# Notes on RNNs\nContent here.")

    runner = CliRunner()
    result = runner.invoke(cli, [
        "ingest", str(md_path),
        "--source-kind", "reference",
        "--title", "Notes on RNNs",
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, f"markdown ingest failed:\n{result.output}"

    # File must be copied to _pages/
    dest = wiki_env.wiki_dir / "_pages" / "notes-on-rnns.md"
    assert dest.exists(), f"Expected copied file at {dest}"

    # Index entry must have status=rendered
    entries = [
        json.loads(line)
        for line in wiki_env.index_path.read_text().splitlines()
        if line.strip()
    ]
    matching = [e for e in entries if e["id"] == "notes-on-rnns"]
    assert len(matching) == 1
    assert matching[0]["status"] == "rendered"


@pytest.mark.integration
def test_ingest_image_to_assets(wiki_env, tmp_path):
    """Image ingest copies file to wiki/assets/ and creates an index entry."""
    img_path = tmp_path / "confusion_matrix.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    runner = CliRunner()
    result = runner.invoke(cli, [
        "ingest", str(img_path),
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
        "--caption", "Confusion Matrix",
        "--description", "A confusion matrix showing model performance.",
    ])
    assert result.exit_code == 0, f"image ingest failed:\n{result.output}"

    # File must be in assets/
    dest = wiki_env.wiki_dir / "assets" / "confusion_matrix.png"
    assert dest.exists(), f"Expected image at {dest}"

    # Entry must exist in index
    entries = [
        json.loads(line)
        for line in wiki_env.index_path.read_text().splitlines()
        if line.strip()
    ]
    matching = [e for e in entries if e["id"] == "img-confusion-matrix"]
    assert len(matching) == 1, f"Expected index entry for img-confusion-matrix; got {[e['id'] for e in entries]}"


@pytest.mark.integration
def test_ingest_text_writes_source(wiki_env):
    """--text ingest writes .wiki/sources/<id>.txt with the given content."""
    runner = CliRunner()
    result = runner.invoke(cli, [
        "ingest",
        "--text", "test content",
        "--id", "pasted-note",
        "--index", str(wiki_env.index_path),
        "--sources-dir", str(wiki_env.sources_dir),
    ])
    assert result.exit_code == 0, f"text ingest failed:\n{result.output}"

    txt_path = wiki_env.sources_dir / "pasted-note.txt"
    assert txt_path.exists(), f"Expected text file at {txt_path}"
    assert txt_path.read_text() == "test content"


@pytest.mark.integration
def test_ingest_directory_batch(wiki_env, tmp_path):
    """Directory ingest of 2 PDFs adds 2 entries to the index."""
    ingest_dir = tmp_path / "papers"
    ingest_dir.mkdir()
    (ingest_dir / "paper-a.pdf").write_bytes(b"%PDF-1.4 fake")
    (ingest_dir / "paper-b.pdf").write_bytes(b"%PDF-1.4 fake")

    runner = CliRunner()
    result = runner.invoke(cli, [
        "ingest", str(ingest_dir),
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert result.exit_code == 0, f"directory ingest failed:\n{result.output}"

    entries = [
        json.loads(line)
        for line in wiki_env.index_path.read_text().splitlines()
        if line.strip()
    ]
    assert len(entries) == 2, f"Expected 2 index entries, got {len(entries)}: {[e['id'] for e in entries]}"


@pytest.mark.integration
def test_ingest_collision_fails_real(wiki_env, tmp_path):
    """Ingesting the same PDF twice — second call must fail."""
    pdf_path = tmp_path / "smith2024.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    runner = CliRunner()
    first = runner.invoke(cli, [
        "ingest", str(pdf_path),
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert first.exit_code == 0, f"First ingest failed:\n{first.output}"

    second = runner.invoke(cli, [
        "ingest", str(pdf_path),
        "--index", str(wiki_env.index_path),
        "--wiki-dir", str(wiki_env.wiki_dir),
    ])
    assert second.exit_code != 0, "Second ingest (collision) should have failed but exited 0"


# ---------------------------------------------------------------------------
# Smoke tests (subprocess)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_ingest_subprocess_help():
    """ingest --help exits 0 and mentions --sync and --tags."""
    result = _run_cli_subprocess("ingest", "--help")
    assert result.returncode == 0
    assert "--sync" in result.stdout
    assert "--tags" in result.stdout


@pytest.mark.smoke
def test_ingest_sync_help():
    """ingest --help exits 0 and mentions --source."""
    result = _run_cli_subprocess("ingest", "--help")
    assert result.returncode == 0
    assert "--source" in result.stdout
