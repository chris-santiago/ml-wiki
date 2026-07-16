import json
import os
import subprocess
import sys

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..")
ZOTERO_SCRIPT = os.path.join(SCRIPTS_DIR, "zotero_reader.py")


def _make_bbt_json(items):
    return json.dumps({"items": items})


def _run_parse(bbt_path, *extra_args):
    result = subprocess.run(
        [sys.executable, ZOTERO_SCRIPT, "parse", "--bbt", bbt_path, *extra_args],
        capture_output=True, text=True,
    )
    return result


def _write_bbt(tmp_dir, items):
    path = os.path.join(tmp_dir, "test.json")
    with open(path, "w") as f:
        f.write(_make_bbt_json(items))
    return path


SAMPLE_ITEM = {
    "citationKey": "smith2024test",
    "title": "Test Paper",
    "itemType": "journalArticle",
    "creators": [{"firstName": "John", "lastName": "Smith", "creatorType": "author"}],
    "date": "2024",
    "tags": [{"tag": "machine-learning"}],
    "publicationTitle": "Test Journal",
    "attachments": [],
}


def test_parse_without_source_name_returns_null(tmp_path):
    bbt = _write_bbt(str(tmp_path), [SAMPLE_ITEM])
    result = _run_parse(bbt, "--citekey", "smith2024test")
    assert result.returncode == 0
    entry = json.loads(result.stdout)
    assert entry["source_name"] is None


def test_parse_with_source_name_injects_value(tmp_path):
    bbt = _write_bbt(str(tmp_path), [SAMPLE_ITEM])
    result = _run_parse(bbt, "--citekey", "smith2024test", "--source-name", "my-library")
    assert result.returncode == 0
    entry = json.loads(result.stdout)
    assert entry["source_name"] == "my-library"


def test_parse_all_with_source_name(tmp_path):
    bbt = _write_bbt(str(tmp_path), [SAMPLE_ITEM])
    result = _run_parse(bbt, "--source-name", "my-library")
    assert result.returncode == 0
    entries = json.loads(result.stdout)
    assert len(entries) == 1
    assert entries[0]["source_name"] == "my-library"
