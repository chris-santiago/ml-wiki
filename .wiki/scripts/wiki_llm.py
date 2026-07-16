# /// script
# requires-python = ">=3.11"
# dependencies = ["jsonschema", "openai"]
# ///

"""OpenRouter LLM client for the wiki CLI.

Provides sync and async LLM dispatch, JSON extraction from freeform
responses, schema validation, model tier resolution, and retry logic.
"""

import asyncio
import json
import os
import re
import time
from pathlib import Path

import jsonschema
from openai import OpenAI, APITimeoutError, APIConnectionError, RateLimitError, APIStatusError

SCRIPTS_DIR = Path(__file__).resolve().parent
SCHEMAS_DIR = SCRIPTS_DIR.parent / "schemas"
AGENTS_DIR = SCRIPTS_DIR.parent / "agents"

AGENT_TIERS = {
    "render-shallow": "nano",
    "render-deep": "mini",
    "build-moc": "full",
    "build-rank": "nano",
    "build-map": "mini",
    "query-p1": "nano",
    "query-p2": "full",
    "lint": "mini",
    "normalize-tags": "nano",
    "normalize-tags-cluster": "mini",
    "normalize-tags-junk": "nano",
    "normalize-tags-assign": "mini",
    "idea-improve": "mini",
    "search-terms": "nano",
    "synthesis-refresh": "full",
}


class LLMOutputParseError(Exception):
    def __init__(self, message, raw_response=None):
        super().__init__(message)
        self.raw_response = raw_response


class LLMAPIError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def extract_json(raw: str) -> dict:
    raw = raw.strip()
    if not raw:
        raise LLMOutputParseError("Empty response", raw)

    # Try 1: direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Try 2: extract from code fence
    fence_match = re.search(r'```(?:json)?\s*\n(.*?)\n```', raw, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try 3: find outermost { ... }
    first_brace = raw.find('{')
    last_brace = raw.rfind('}')
    if first_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(raw[first_brace:last_brace + 1])
        except json.JSONDecodeError:
            pass

    raise LLMOutputParseError("Could not extract JSON from response", raw)


def _load_schema(schema_name: str) -> dict:
    path = SCHEMAS_DIR / f"{schema_name}.json"
    with open(path) as f:
        return json.load(f)


def validate_schema(data: dict, schema_name: str) -> None:
    schema = _load_schema(schema_name)
    jsonschema.validate(instance=data, schema=schema)


def resolve_model(agent: str, config: dict) -> str:
    overrides = config.get("model_overrides") or {}
    if agent in overrides:
        return overrides[agent]
    tier = AGENT_TIERS.get(agent, "nano")
    return config["models"][tier]


def load_template(agent: str) -> str:
    path = AGENTS_DIR / f"{agent}.md"
    return path.read_text()


def _get_api_key(config: dict) -> str:
    env_var = config.get("api_key_env", "OPENROUTER_API_KEY")
    key = os.environ.get(env_var)
    if not key:
        raise LLMAPIError(f"Environment variable {env_var} not set")
    return key



def llm_call(
    agent: str,
    input_data: str,
    schema: str,
    config: dict,
    json_mode: bool = True,
    max_retries: int = 3,
    output_path: str | None = None,
    skip_validation: bool = False,
) -> dict:
    api_key = _get_api_key(config)
    model = resolve_model(agent, config)
    system = load_template(agent)
    base_url = config.get("base_url", "https://openrouter.ai/api/v1")
    timeout = config.get("timeout", 120)
    params = config.get("default_params") or {}

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": input_data},
        ],
        **params,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            resp = client.chat.completions.create(**kwargs)
            raw_content = resp.choices[0].message.content or ""
            parsed = extract_json(raw_content)
            if not skip_validation:
                validate_schema(parsed, schema)

            if output_path:
                tmp = output_path + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(parsed, f, ensure_ascii=False)
                os.rename(tmp, output_path)

            return parsed

        except RateLimitError as e:
            last_error = e
            if attempt < max_retries:
                retry_after = int(e.response.headers.get("Retry-After", 60 * (attempt + 1))) if e.response else 60 * (attempt + 1)
                time.sleep(retry_after)
                continue
            raise LLMAPIError(f"Rate limited after {max_retries} retries", 429)

        except APIStatusError as e:
            last_error = e
            if e.status_code in (502, 503) and attempt < max_retries:
                time.sleep(2 ** attempt)
                continue
            raise LLMAPIError(f"Server error {e.status_code} after {max_retries} retries", e.status_code)

        except (APITimeoutError, APIConnectionError) as e:
            last_error = e
            if attempt < max_retries:
                time.sleep(2 ** attempt)
                continue
            raise LLMAPIError(f"Connection failed after {max_retries} retries: {e}")

    raise LLMAPIError(f"Exhausted retries: {last_error}")


async def _async_call_one(client, sem, model, system_prompt, user_msg, config, schema_name, output_path=None, max_retries=3, skip_validation=False):
    """Single async API call via openai SDK with retry and semaphore."""
    for attempt in range(max_retries + 1):
        try:
            async with sem:
                resp = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=model,
                        temperature=config.get("default_params", {}).get("temperature", 0),
                        max_tokens=config.get("default_params", {}).get("max_tokens", 16384),
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_msg},
                        ],
                    ),
                    timeout=config.get("timeout", 120),
                )
            raw = resp.choices[0].message.content or ""
            parsed = extract_json(raw)
            if not skip_validation:
                validate_schema(parsed, schema_name)

            if output_path:
                tmp = output_path + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(parsed, f, ensure_ascii=False)
                os.rename(tmp, output_path)

            return parsed, None

        except (LLMOutputParseError, jsonschema.ValidationError) as e:
            return None, f"Output validation failed: {e}"
        except Exception as exc:
            if attempt < max_retries:
                await asyncio.sleep(2.0 ** attempt)
                continue
            return None, f"{type(exc).__name__}: {exc}"

    return None, "Exhausted retries"


async def llm_batch(
    agent: str,
    items: list[tuple[str, str]],
    schema: str,
    config: dict,
    concurrency: int = 25,
    output_dir: str | None = None,
    skip_validation: bool = False,
) -> list[tuple[str, dict | None, str | None]]:
    """Dispatch multiple LLM calls concurrently via openai SDK.

    Uses AsyncOpenAI pointed at OpenRouter's base_url.

    Args:
        agent: template name (e.g. "render-shallow")
        items: list of (item_id, user_message) tuples
        schema: schema name for validation (e.g. "render-paper")
        config: LLM config dict from wiki_config.py get llm
        concurrency: max parallel requests
        output_dir: if set, write results to {output_dir}/{item_id}.json

    Returns:
        list of (item_id, result_dict_or_none, error_or_none)
    """
    from openai import AsyncOpenAI

    api_key = _get_api_key(config)
    model = resolve_model(agent, config)
    system = load_template(agent)
    base_url = config.get("base_url", "https://openrouter.ai/api/v1")

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    sem = asyncio.Semaphore(concurrency)

    async def dispatch(item_id, user_msg):
        out_path = os.path.join(output_dir, f"{item_id}.json") if output_dir else None
        result, error = await _async_call_one(
            client, sem, model, system, user_msg, config, schema, out_path,
            skip_validation=skip_validation,
        )
        return item_id, result, error

    tasks = [dispatch(item_id, msg) for item_id, msg in items]
    results = await asyncio.gather(*tasks)
    return list(results)
