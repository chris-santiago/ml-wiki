import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from wiki_build import build_listing, build_banner_list, prefilter_rank_candidates


def test_build_listing_basic():
    entries = [
        {"id": "smith2024", "title": "Smith Paper", "type": "paper", "status": "rendered"},
        {"id": "exp-test", "title": "Test Experiment", "type": "experiment", "status": "stub"},
    ]
    result = build_listing(entries, stale_ids=set())
    assert "[[smith2024]]" in result
    assert "[[exp-test]]" in result
    assert "paper" in result
    assert "stub" in result


def test_build_listing_sorted_by_title():
    entries = [
        {"id": "z-paper", "title": "Zebra Paper", "type": "paper", "status": "rendered"},
        {"id": "a-paper", "title": "Alpha Paper", "type": "paper", "status": "rendered"},
    ]
    result = build_listing(entries, stale_ids=set())
    lines = result.strip().split("\n")
    assert "Alpha" in lines[0]
    assert "Zebra" in lines[1]


def test_build_listing_stale_marked():
    entries = [
        {"id": "synthesis-old", "title": "Old Synthesis", "type": "synthesis", "status": "rendered"},
    ]
    result = build_listing(entries, stale_ids={"synthesis-old"})
    assert "⚠️" in result


def test_build_listing_with_year():
    entries = [
        {"id": "smith2024", "title": "Smith Paper", "type": "paper", "status": "rendered", "year": 2024},
    ]
    result = build_listing(entries, stale_ids=set())
    assert "(2024)" in result


def test_build_banner_list_synthesis():
    stale = [{"id": "synthesis-foo", "stale_entries": ["a", "b", "c"]}]
    banners = build_banner_list(stale, entry_lookup={"synthesis-foo": {"wiki_path": "wiki/syntheses/synthesis-foo.md"}})
    assert len(banners) == 1
    assert banners[0]["wiki_path"] == "wiki/syntheses/synthesis-foo.md"
    assert "3 new entries" in banners[0]["banner_text"]
    assert "Stale synthesis" in banners[0]["banner_text"]


def test_build_banner_list_idea_never_improved():
    stale = [{"id": "idea-test", "stale_entries": ["never_improved"]}]
    banners = build_banner_list(stale, entry_lookup={"idea-test": {"wiki_path": "wiki/ideas/idea-test.md"}})
    assert "Unreviewed idea" in banners[0]["banner_text"]


def test_build_banner_list_idea_stale():
    stale = [{"id": "idea-test", "stale_entries": ["new-paper-1", "new-paper-2"]}]
    banners = build_banner_list(stale, entry_lookup={"idea-test": {"wiki_path": "wiki/ideas/idea-test.md"}})
    assert "Stale idea" in banners[0]["banner_text"]
    assert "2 new entries" in banners[0]["banner_text"]


def test_build_banner_list_skips_no_wiki_path():
    stale = [{"id": "synthesis-no-path", "stale_entries": ["a"]}]
    banners = build_banner_list(stale, entry_lookup={"synthesis-no-path": {"wiki_path": ""}})
    assert len(banners) == 0


def test_prefilter_rank_candidates_keeps_specific():
    target = {"tags": ["fraud-detection", "embeddings", "account-takeover"]}
    candidates = [
        {"id": "a", "tags": ["fraud-detection", "account-takeover"]},
        {"id": "b", "tags": ["deep-learning"]},
    ]
    result = prefilter_rank_candidates(target, candidates)
    assert len(result) == 1
    assert result[0]["id"] == "a"


def test_prefilter_rank_candidates_keeps_non_broad():
    target = {"tags": ["deep-learning", "fraud-detection"]}
    candidates = [
        {"id": "a", "tags": ["fraud-detection"]},
    ]
    result = prefilter_rank_candidates(target, candidates)
    assert len(result) == 1


def test_prefilter_rank_candidates_removes_broad_only():
    target = {"tags": ["deep-learning", "machine-learning"]}
    candidates = [
        {"id": "a", "tags": ["deep-learning"]},
    ]
    result = prefilter_rank_candidates(target, candidates)
    assert len(result) == 0
