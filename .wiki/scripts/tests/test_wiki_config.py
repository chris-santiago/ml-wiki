"""Tests for wiki_config.py — PyYAML boundary for .wiki/config.yaml.

Design note on monkeypatching:
    wiki_config.load_config() uses `path=CONFIG_PATH` as a default argument.
    Default argument values are bound at function-definition time, so patching
    the module-level CONFIG_PATH after import does NOT affect calls to
    load_config() with no arguments.

    For tests that need an isolated config (get/set-aliases), we therefore
    monkeypatch _wiki_config.load_config with a wrapper that reads the
    wiki_env config instead.  For set-aliases we also patch CONFIG_PATH so
    that the atomic write path (tmp = CONFIG_PATH + ".tmp") lands in the
    wiki_env directory rather than the real .wiki/.
"""

import importlib.util
import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).resolve().parent.parent  # .wiki/scripts/
SCRIPT_PATH = str(SCRIPTS_DIR / "wiki_config.py")

# ---------------------------------------------------------------------------
# Import the module under test for direct-call tests.
# ---------------------------------------------------------------------------

_spec = importlib.util.spec_from_file_location("wiki_config", SCRIPT_PATH)
_wiki_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_wiki_config)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(*args) -> subprocess.CompletedProcess:
    """Run wiki_config.py as a subprocess."""
    return subprocess.run(
        [sys.executable, SCRIPT_PATH, *args],
        capture_output=True,
        text=True,
    )


class _CapturingStream:
    """Minimal stdout/stderr capture for monkeypatching sys.stdout / sys.stderr."""

    def __init__(self, buffer: list):
        self._buf = buffer

    def write(self, text: str) -> int:
        self._buf.append(text)
        return len(text)

    def flush(self):
        pass


# ---------------------------------------------------------------------------
# Fixture: patch load_config to read from wiki_env (isolated config)
# ---------------------------------------------------------------------------


def _patch_load_config(monkeypatch, config_path: str):
    """Monkeypatch load_config so cmd_* functions read from config_path.

    This is necessary because load_config() has `path=CONFIG_PATH` as a
    default argument — that default is bound at definition time and is NOT
    affected by later changes to the module-level CONFIG_PATH attribute.
    """
    def _load(path=None):
        with open(config_path) as f:
            return yaml.safe_load(f)
    monkeypatch.setattr(_wiki_config, "load_config", _load)


# ---------------------------------------------------------------------------
# get tests — direct calls against the wiki_env isolated config
# ---------------------------------------------------------------------------


def test_get_wiki_dir(wiki_env, monkeypatch):
    """get wiki_dir returns the value stored in the config (literal string)."""
    _patch_load_config(monkeypatch, str(wiki_env.config_path))
    args = Namespace(key="wiki_dir", name=None, type=None)
    out = []
    monkeypatch.setattr("sys.stdout", _CapturingStream(out))
    _wiki_config.cmd_get(args)
    result = json.loads("".join(out))
    # wiki_env config stores the absolute path of wiki_dir
    assert result == str(wiki_env.wiki_dir)


def test_get_llm(wiki_env, monkeypatch):
    """get llm returns a dict with base_url, api_key_env, and models keys."""
    _patch_load_config(monkeypatch, str(wiki_env.config_path))
    args = Namespace(key="llm", name=None, type=None)
    out = []
    monkeypatch.setattr("sys.stdout", _CapturingStream(out))
    _wiki_config.cmd_get(args)
    result = json.loads("".join(out))
    assert isinstance(result, dict)
    assert "base_url" in result
    assert "api_key_env" in result
    assert "models" in result


def test_get_tag_aliases(wiki_env, monkeypatch):
    """get tag_aliases returns an empty dict for the wiki_env config."""
    _patch_load_config(monkeypatch, str(wiki_env.config_path))
    args = Namespace(key="tag_aliases", name=None, type=None)
    out = []
    monkeypatch.setattr("sys.stdout", _CapturingStream(out))
    _wiki_config.cmd_get(args)
    result = json.loads("".join(out))
    assert result == {}


def test_get_sources(wiki_env, monkeypatch):
    """get sources returns an empty list for the wiki_env config."""
    _patch_load_config(monkeypatch, str(wiki_env.config_path))
    args = Namespace(key="sources", name=None, type=None)
    out = []
    monkeypatch.setattr("sys.stdout", _CapturingStream(out))
    _wiki_config.cmd_get(args)
    result = json.loads("".join(out))
    assert result == []


def test_get_default_render_depth(wiki_env, monkeypatch):
    """get default_render_depth returns 'shallow' from the wiki_env config."""
    _patch_load_config(monkeypatch, str(wiki_env.config_path))
    args = Namespace(key="default_render_depth", name=None, type=None)
    out = []
    monkeypatch.setattr("sys.stdout", _CapturingStream(out))
    _wiki_config.cmd_get(args)
    result = json.loads("".join(out))
    assert result == "shallow"


def test_get_source_by_name_not_found(wiki_env, monkeypatch):
    """get sources --name nonexistent prints error JSON to stderr and exits 1."""
    _patch_load_config(monkeypatch, str(wiki_env.config_path))
    args = Namespace(key="sources", name="nonexistent", type=None)
    err = []
    monkeypatch.setattr("sys.stderr", _CapturingStream(err))
    with pytest.raises(SystemExit) as exc:
        _wiki_config.cmd_get(args)
    assert exc.value.code == 1
    error_data = json.loads("".join(err))
    assert "error" in error_data


# ---------------------------------------------------------------------------
# set-aliases — direct call with monkeypatched load_config + CONFIG_PATH.
# Never run set-aliases via subprocess — it would mutate the real config.
#
# cmd_set_aliases uses CONFIG_PATH in two places:
#   1. load_config() (patched via load_config monkeypatch)
#   2. The atomic write: tmp = CONFIG_PATH + ".tmp" / os.rename(tmp, CONFIG_PATH)
# Both must point at wiki_env.config_path.
# ---------------------------------------------------------------------------


def test_set_aliases_overwrites(wiki_env, monkeypatch, tmp_path):
    """set-aliases overwrites tag_aliases in config while preserving other fields."""
    config_str = str(wiki_env.config_path)
    _patch_load_config(monkeypatch, config_str)
    monkeypatch.setattr(_wiki_config, "CONFIG_PATH", config_str)

    aliases = {"transformers": "attention-mechanisms", "bert": "transformers"}
    aliases_file = tmp_path / "aliases.json"
    aliases_file.write_text(json.dumps(aliases))

    # Run set-aliases
    out = []
    monkeypatch.setattr("sys.stdout", _CapturingStream(out))
    _wiki_config.cmd_set_aliases(Namespace(aliases_file=str(aliases_file)))
    saved_report = json.loads("".join(out))
    assert saved_report["saved"] == len(aliases)

    # Re-read tag_aliases via cmd_get to confirm they were written
    out2 = []
    monkeypatch.setattr("sys.stdout", _CapturingStream(out2))
    _wiki_config.cmd_get(Namespace(key="tag_aliases", name=None, type=None))
    assert json.loads("".join(out2)) == aliases

    # Confirm sources were preserved (field preservation check)
    out3 = []
    monkeypatch.setattr("sys.stdout", _CapturingStream(out3))
    _wiki_config.cmd_get(Namespace(key="sources", name=None, type=None))
    assert json.loads("".join(out3)) == []


def test_set_aliases_rejects_wrapped_map(wiki_env, monkeypatch, tmp_path):
    """set-aliases refuses a wrong-shaped map and does not touch the config.

    Regression: a {"aliases": {...}} wrapper (non-string value) used to be
    persisted verbatim, polluting tag_aliases.
    """
    config_str = str(wiki_env.config_path)
    _patch_load_config(monkeypatch, config_str)
    monkeypatch.setattr(_wiki_config, "CONFIG_PATH", config_str)

    before = wiki_env.config_path.read_text()

    aliases_file = tmp_path / "aliases.json"
    aliases_file.write_text(json.dumps({"aliases": {"dl": "deep-learning"}}))

    with pytest.raises(SystemExit) as exc:
        _wiki_config.cmd_set_aliases(Namespace(aliases_file=str(aliases_file)))
    assert exc.value.code == 1
    # Config file must be unchanged.
    assert wiki_env.config_path.read_text() == before


# ---------------------------------------------------------------------------
# merge-aliases — subprocess (reads from arg files, writes to stdout only)
# ---------------------------------------------------------------------------


def test_merge_aliases_combines(tmp_path):
    """merge-aliases merges two alias JSON files and prints merged JSON to stdout."""
    existing_file = tmp_path / "existing.json"
    existing_file.write_text(
        json.dumps({"bert": "transformers", "gru": "recurrent-neural-networks"})
    )

    new_file = tmp_path / "new.json"
    new_file.write_text(
        json.dumps({"gru": "gated-recurrent-units", "lstm": "recurrent-neural-networks"})
    )

    result = _run("merge-aliases", "--existing", str(existing_file), str(new_file))
    assert result.returncode == 0

    merged = json.loads(result.stdout)
    assert merged["gru"] == "gated-recurrent-units"   # new wins on collision
    assert merged["bert"] == "transformers"            # existing-only preserved
    assert merged["lstm"] == "recurrent-neural-networks"  # new-only included


# ---------------------------------------------------------------------------
# validate — subprocess (accepts positional config path argument)
# ---------------------------------------------------------------------------


def test_validate_passes_valid(wiki_env):
    """validate with a well-formed config exits 0 and reports valid: true."""
    result = _run("validate", str(wiki_env.config_path))
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["valid"] is True


def test_validate_rejects_missing_keys(tmp_path):
    """validate rejects a config that is missing the research_wiki root key."""
    bad_config = tmp_path / "bad_config.yaml"
    bad_config.write_text("something_else:\n  foo: bar\n")

    result = _run("validate", str(bad_config))
    assert result.returncode != 0
    data = json.loads(result.stdout)
    assert data["valid"] is False
    assert len(data["errors"]) > 0
