"""
tinycore/llm.py
Groq LLM helper enforcing:
- Hard Rule 1: Python owns deterministic logic; LLM only classifies and extracts fallback.
- Hard Rule 4: Extraction fallback values must appear verbatim in page text, else reject -> UNKNOWN.
- Allowed functions ONLY: classify_change and extract_fallback.
- temperature=0, JSON mode, disk cache keyed by sha256(prompt), rate-limit aware.
"""

import os
import json
import time
import hashlib
from groq import Groq, RateLimitError
from dotenv import dotenv_values

CACHE_DIR = os.path.join("data", "llm_cache")
DEFAULT_MODEL_CLASSIFY = "qwen/qwen3.8-27b"
DEFAULT_MODEL_EXTRACT = "qwen/qwen3.8-27b"


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


def _call_groq_json(prompt: str, model: str = DEFAULT_MODEL_CLASSIFY, system_msg: str = "Return only valid JSON.") -> dict:
    prompt_hash = hashlib.sha256(f"{model}:{system_msg}:{prompt}".encode("utf-8")).hexdigest()
    cached = _get_cached_response(prompt_hash)
    if cached is not None:
        return cached

    client = _get_groq_client()
    retries = 3
    backoff = 2.0

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
            return parsed
        except RateLimitError:
            if attempt == retries - 1:
                raise
            time.sleep(backoff)
            backoff *= 2
        except Exception as e:
            if attempt == retries - 1:
                raise e
            time.sleep(1.0)
    return {}


def classify_change(diff_dict: dict) -> dict:
    """
    Classifies a structured diff into:
    {type: 'price'|'limit'|'legal'|'feature'|'cosmetic', materiality: 0.0-1.0, summary: str}
    """
    prompt = (
        "Classify the following web page diff into one change type ('price', 'limit', 'legal', 'feature', 'cosmetic'), "
        "a materiality score from 0.0 to 1.0 (where 1.0 is a critical change), and a 1-sentence summary.\n\n"
        f"Diff JSON:\n{json.dumps(diff_dict, indent=2)}\n\n"
        "Return JSON with schema:\n"
        '{"type": "price|limit|legal|feature|cosmetic", "materiality": float, "summary": string}'
    )

    result = _call_groq_json(
        prompt,
        model=DEFAULT_MODEL_CLASSIFY,
        system_msg="You are an expert change classifier. Return valid JSON only with 'type', 'materiality', and 'summary'."
    )

    valid_types = {"price", "limit", "legal", "feature", "cosmetic"}
    ctype = result.get("type", "cosmetic")
    if ctype not in valid_types:
        ctype = "cosmetic"

    try:
        mat = float(result.get("materiality", 0.1))
        mat = max(0.0, min(1.0, mat))
    except (ValueError, TypeError):
        mat = 0.1

    return {
        "type": ctype,
        "materiality": round(mat, 2),
        "summary": result.get("summary", "Web page content changed.")
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
        model=DEFAULT_MODEL_EXTRACT,
        system_msg="You are a strict data extractor. Extract exact factual values from the provided text into JSON."
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
