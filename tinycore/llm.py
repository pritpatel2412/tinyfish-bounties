"""
tinycore/llm.py
Groq LLM helper enforcing:
- Hard Rule 1: Python owns deterministic logic; LLM only classifies and extracts fallback.
- Hard Rule 4: Extraction fallback values must appear verbatim in page text, else reject -> UNKNOWN.
- Allowed functions ONLY: classify_change and extract_fallback.
- temperature=0, JSON mode, disk cache keyed by sha256(prompt), rate-limit aware.
- GROQ_MODELS fallback chain; never default to "price" on failure; Python computes materiality deterministically.
"""

import hashlib
import json
import logging
import os
import time

from dotenv import dotenv_values
from groq import Groq, NotFoundError, RateLimitError

logger = logging.getLogger("tinycore.llm")

CACHE_DIR = os.path.join("data", "llm_cache")
DEFAULT_GROQ_MODELS = "qwen/qwen3.8-27b,openai/gpt-oss-120b,openai/gpt-oss-20b"


def _get_groq_client() -> Groq:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        api_key = dotenv_values(".env").get("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY is not configured")
    return Groq(api_key=api_key)


def _get_cached_response(prompt_hash: str) -> dict | None:
    cache_file = os.path.join(CACHE_DIR, f"{prompt_hash}.json")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def _save_cached_response(prompt_hash: str, data: dict):
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(CACHE_DIR, f"{prompt_hash}.json")
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def _call_groq_json(prompt: str, model: str | None = None, system_msg: str = "Return only valid JSON.") -> dict:
    """Calls Groq with fallback model chain from GROQ_MODELS."""
    client = _get_groq_client()
    models_env = os.getenv("GROQ_MODELS", DEFAULT_GROQ_MODELS)
    model_candidates = [model] if model else [m.strip() for m in models_env.split(",") if m.strip()]

    last_error = None
    for model in model_candidates:
        prompt_hash = hashlib.sha256(f"{model}:{system_msg}:{prompt}".encode("utf-8")).hexdigest()
        cached = _get_cached_response(prompt_hash)
        if cached is not None:
            return cached

        retries = 2
        backoff = 1.0
        for attempt in range(retries):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.0,
                    response_format={"type": "json_object"}
                )
                content = resp.choices[0].message.content or "{}"
                parsed = json.loads(content)
                _save_cached_response(prompt_hash, parsed)
                logger.info(f"Groq classification successful using model: {model}")
                return parsed
            except NotFoundError as e:
                logger.warning(f"Groq model '{model}' not found (404), trying next model in chain: {e}")
                last_error = e
                break  # Try next model immediately
            except RateLimitError as e:
                last_error = e
                if attempt == retries - 1:
                    logger.warning(f"Rate limited on Groq model '{model}': {e}")
                    break
                time.sleep(backoff)
                backoff *= 2
            except Exception as e:
                last_error = e
                if attempt == retries - 1:
                    logger.warning(f"Groq error with model '{model}': {e}")
                    break
                time.sleep(1.0)

    if last_error:
        raise last_error
    return {}


def compute_materiality(diff_dict: dict, baseline_blocks_count: int = 0) -> tuple[float, bool]:
    """
    Computes materiality score and suspect_capture_variance deterministically in Python.
    - numeric_delta present -> >=0.8 (0.85)
    - >=10% of blocks changed -> 0.6
    - added-only with added/baseline_blocks > 0.10 and no removals -> suspect_capture_variance=True, cap at 0.3
    - else -> 0.2 (within 0.1-0.3)
    """
    stats = diff_dict.get("stats", {})
    added = diff_dict.get("added", [])
    removed = diff_dict.get("removed", [])
    changed = diff_dict.get("changed", [])

    n_added = len(added) if isinstance(added, list) else stats.get("added", 0)
    n_removed = len(removed) if isinstance(removed, list) else stats.get("removed", 0)
    n_changed = len(changed) if isinstance(changed, list) else stats.get("changed", 0)

    has_numeric = False
    if isinstance(changed, list):
        for c in changed:
            if isinstance(c, dict) and c.get("numeric_delta") is not None:
                has_numeric = True
                break
    if stats.get("numeric_deltas", 0) > 0:
        has_numeric = True

    suspect_capture_variance = False
    if baseline_blocks_count > 0 and n_removed == 0 and (n_added / baseline_blocks_count) > 0.10:
        suspect_capture_variance = True

    if suspect_capture_variance:
        materiality = 0.3
    elif has_numeric:
        materiality = 0.85
    elif baseline_blocks_count > 0 and ((n_added + n_removed + n_changed) / baseline_blocks_count) >= 0.10:
        materiality = 0.6
    else:
        materiality = 0.2

    return materiality, suspect_capture_variance


def classify_change(diff_dict: dict, baseline_blocks_count: int = 0) -> dict:
    """
    Classifies a structured diff.
    Python deterministically computes materiality and suspect_capture_variance.
    Groq only labels type + summary.
    If Groq fails or returns invalid JSON: type="unclassified", summary from counts. Never defaults to "price".
    """
    stats = diff_dict.get("stats", {})
    added = diff_dict.get("added", [])
    removed = diff_dict.get("removed", [])
    changed = diff_dict.get("changed", [])
    n_added = len(added) if isinstance(added, list) else stats.get("added", 0)
    n_removed = len(removed) if isinstance(removed, list) else stats.get("removed", 0)
    n_changed = len(changed) if isinstance(changed, list) else stats.get("changed", 0)

    materiality, suspect_variance = compute_materiality(diff_dict, baseline_blocks_count)
    default_summary = f"{n_added} added, {n_removed} removed, {n_changed} changed"

    prompt = (
        "Classify the following web page diff into one change type ('price', 'limit', 'legal', 'feature', 'cosmetic'), "
        "and a 1-sentence summary.\n\n"
        f"Diff JSON:\n{json.dumps(diff_dict, indent=2)}\n\n"
        "Return JSON with schema:\n"
        '{"type": "price|limit|legal|feature|cosmetic", "summary": string}'
    )

    try:
        result = _call_groq_json(
            prompt,
            None,
            "You are an expert change classifier. Return valid JSON only with 'type' and 'summary'."
        )
        valid_types = {"price", "limit", "legal", "feature", "cosmetic"}
        ctype = result.get("type", "").lower()
        if ctype not in valid_types:
            ctype = "unclassified"
        summary = result.get("summary") or default_summary
    except Exception as e:
        logger.warning(f"Groq classification failed ({e}); falling back to unclassified.")
        ctype = "unclassified"
        summary = default_summary

    return {
        "type": ctype,
        "materiality": round(materiality, 2),
        "summary": summary,
        "suspect_capture_variance": suspect_variance
    }


def extract_fallback(page_text: str, schema: dict) -> dict:
    """
    Extracts fallback fields according to schema.
    Enforces Hard Rule 4: Every extracted scalar value MUST appear verbatim in page_text,
    else replaced with 'UNKNOWN'.
    """
    prompt = (
        f"Extract the requested fields from the following page text according to the JSON schema.\n"
        f"Schema:\n{json.dumps(schema, indent=2)}\n\n"
        f"Page Text:\n{page_text[:12000]}\n\n"
        "Return valid JSON matching the schema."
    )

    raw_extracted = _call_groq_json(
        prompt,
        None,
        "You are a strict data extractor. Extract exact factual values from the provided text into JSON."
    )

    # Hard Rule 4: Validate verbatim presence in page_text
    validated = {}
    lower_page = page_text.lower()

    for k, val in raw_extracted.items():
        if val is None:
            validated[k] = None
        elif isinstance(val, (int, float, bool)):
            validated[k] = val
        elif isinstance(val, str):
            clean_val = val.strip()
            if clean_val and clean_val.lower() in lower_page:
                validated[k] = clean_val
            else:
                validated[k] = "UNKNOWN"
        elif isinstance(val, list):
            valid_list = []
            for item in val:
                if isinstance(item, str):
                    if item.strip().lower() in lower_page:
                        valid_list.append(item.strip())
                else:
                    valid_list.append(item)
            validated[k] = valid_list
        else:
            validated[k] = val

    return validated
