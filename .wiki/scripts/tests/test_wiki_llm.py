import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from wiki_llm import extract_json, LLMOutputParseError


def test_extract_plain_json():
    raw = '{"summary": "hello", "tags": ["a"]}'
    assert extract_json(raw) == {"summary": "hello", "tags": ["a"]}


def test_extract_json_from_code_fence():
    raw = '```json\n{"summary": "hello"}\n```'
    assert extract_json(raw) == {"summary": "hello"}


def test_extract_json_from_code_fence_no_lang():
    raw = '```\n{"summary": "hello"}\n```'
    assert extract_json(raw) == {"summary": "hello"}


def test_extract_json_with_surrounding_prose():
    raw = 'Here is the result:\n{"summary": "hello", "tags": []}\nDone.'
    assert extract_json(raw) == {"summary": "hello", "tags": []}


def test_extract_json_nested_braces():
    raw = '{"fragments": [{"seq": 1, "type": "claim", "title": "x"}]}'
    result = extract_json(raw)
    assert result["fragments"][0]["type"] == "claim"


def test_extract_json_raises_on_garbage():
    with pytest.raises(LLMOutputParseError):
        extract_json("This is not JSON at all")


def test_extract_json_raises_on_empty():
    with pytest.raises(LLMOutputParseError):
        extract_json("")


from wiki_llm import resolve_model, AGENT_TIERS


def test_resolve_model_known_agent():
    config = {
        "models": {"nano": "openai/gpt-5.4-nano", "mini": "openai/gpt-5.4-mini", "full": "openai/gpt-5.4"},
        "model_overrides": {},
    }
    assert resolve_model("render-shallow", config) == "openai/gpt-5.4-nano"
    assert resolve_model("query-p2", config) == "openai/gpt-5.4"
    assert resolve_model("build-moc", config) == "openai/gpt-5.4"
    assert resolve_model("lint", config) == "openai/gpt-5.4-mini"


def test_resolve_model_override():
    config = {
        "models": {"nano": "openai/gpt-5.4-nano", "mini": "openai/gpt-5.4-mini", "full": "openai/gpt-5.4"},
        "model_overrides": {"render-shallow": "google/gemini-2.5-flash"},
    }
    assert resolve_model("render-shallow", config) == "google/gemini-2.5-flash"


def test_resolve_model_unknown_agent_defaults_to_nano():
    config = {
        "models": {"nano": "openai/gpt-5.4-nano", "mini": "openai/gpt-5.4-mini", "full": "openai/gpt-5.4"},
        "model_overrides": {},
    }
    assert resolve_model("unknown-agent", config) == "openai/gpt-5.4-nano"


def test_agent_tiers_complete():
    expected_agents = {
        "render-shallow", "render-deep", "build-moc", "build-rank",
        "build-map", "query-p1", "query-p2", "lint", "normalize-tags",
        "normalize-tags-cluster", "normalize-tags-junk", "normalize-tags-assign",
        "idea-improve", "search-terms", "synthesis-refresh",
    }
    assert set(AGENT_TIERS.keys()) == expected_agents


from wiki_llm import validate_schema


def test_validate_schema_passes_valid():
    data = {
        "summary": "A paper about X.",
        "key_claims": "- Claim 1\n- Claim 2",
        "methods": "They used Y.",
        "results": "- Result 1",
        "limitations": "- Limitation 1",
        "tags": ["machine-learning", "transformers"],
        "fragments": [{"seq": 1, "type": "claim", "title": "X outperforms Y"}],
    }
    validate_schema(data, "render-paper")  # should not raise


def test_validate_schema_rejects_missing_field():
    data = {
        "summary": "A paper about X.",
        "key_claims": "- Claim 1",
        "methods": "They used Y.",
        "results": "- Result 1",
        # missing "limitations"
        "tags": ["ml"],
        "fragments": [{"seq": 1, "type": "claim", "title": "X"}],
    }
    with pytest.raises(Exception):
        validate_schema(data, "render-paper")


def test_validate_schema_rejects_bad_fragment_type():
    data = {
        "summary": "A paper.",
        "key_claims": "- Claim",
        "methods": "Method",
        "results": "Result",
        "limitations": "Limitation",
        "tags": ["ml"],
        "fragments": [{"seq": 1, "type": "INVALID", "title": "X"}],
    }
    with pytest.raises(Exception):
        validate_schema(data, "render-paper")
