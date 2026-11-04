"""
scripts/vet_proposed_pages.py
Vets 5 candidate high-churn pages for Time Machine capacity extension.
"""

import os
import sys
import json
import time
from dotenv import dotenv_values, load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tinycore.tf_client import TF
from tinycore.normalize import markdown_to_blocks
from tinycore.differ import diff_blocks

load_dotenv()

CANDIDATES = [
    {
        "url": "https://docs.python.org/3/whatsnew/",
        "title": "What's New in Python - Python Docs",
        "category": "changelog",
        "churn": "high"
    },
    {
        "url": "https://developers.cloudflare.com/workers/platform/changelog/",
        "title": "Cloudflare Workers Changelog",
        "category": "changelog",
        "churn": "high"
    },
    {
        "url": "https://nodejs.org/en/about/previous-releases",
        "title": "Node.js Releases",
        "category": "changelog",
        "churn": "high"
    },
    {
        "url": "https://supabase.com/changelog",
        "title": "Supabase Changelog",
        "category": "changelog",
        "churn": "high"
    },
    {
        "url": "https://help.openai.com/en/articles/6825453-chatgpt-release-notes",
        "title": "ChatGPT Release Notes",
        "category": "changelog",
        "churn": "high"
    }
]

BOT_WALL_MARKERS = [
    "captcha", "cloudflare ray id", "access denied", "please verify you are a human",
    "security check to continue", "ddos protection", "checking your browser"
]


def vet_candidate(tf: TF, cand: dict) -> dict:
    url = cand["url"]
    print(f"Vetting: {cand['title']} ({url})")

    # Fetch 1
    t0 = time.time()
    try:
        f1 = tf.fetch(url, format="markdown")
    except Exception as e:
        print(f"  [FAIL] Fetch 1 failed: {e}")
        return {"url": url, "passed": False, "reason": f"Fetch error: {e}"}

    lat1 = round((time.time() - t0) * 1000, 2)
    text1 = f1.text or ""
    if len(text1) < 800:
        print(f"  [FAIL] Text too short: {len(text1)} chars")
        return {"url": url, "passed": False, "reason": f"Too short ({len(text1)} chars)"}

    # Bot wall check
    lower = text1[:2000].lower()
    for marker in BOT_WALL_MARKERS:
        if marker in lower:
            print(f"  [FAIL] Bot wall marker detected: {marker}")
            return {"url": url, "passed": False, "reason": f"Bot wall marker: {marker}"}

    # Fetch 2 for stability
    time.sleep(2.0)
    try:
        f2 = tf.fetch(url, format="markdown")
    except Exception as e:
        print(f"  [FAIL] Fetch 2 failed: {e}")
        return {"url": url, "passed": False, "reason": f"Fetch 2 error: {e}"}

    text2 = f2.text or ""
    blocks1 = markdown_to_blocks(text1)
    blocks2 = markdown_to_blocks(text2)
    diff = diff_blocks(blocks1, blocks2)

    if not diff.is_empty:
        print(f"  [WARN] Page had back-to-back diffs (added: {len(diff.added)}, removed: {len(diff.removed)}, changed: {len(diff.changed)})")
        # Check if difference is trivial timestamp/token or major instability
        if len(diff.added) + len(diff.removed) + len(diff.changed) > 3:
            return {"url": url, "passed": False, "reason": "Unstable consecutive renders"}

    print(f"  [PASS] Stability verified. Blocks: {len(blocks1)}, Latency: {lat1}ms, Chars: {len(text1)}")
    return {
        "url": url,
        "title": cand["title"],
        "category": cand["category"],
        "churn": cand["churn"],
        "passed": True,
        "blocks_count": len(blocks1),
        "chars": len(text1),
        "latency_ms": lat1
    }


def main():
    api_key = os.getenv("TINYFISH_API_KEY")
    tf = TF(api_key=api_key)
    results = []

    print("=== Vetting 5 Proposed High-Churn Candidates ===")
    for cand in CANDIDATES:
        res = vet_candidate(tf, cand)
        results.append(res)
        time.sleep(1.0)

    print("\n================ VETTING RESULTS ================")
    passed = [r for r in results if r["passed"]]
    print(f"Total evaluated: {len(results)} | Passed: {len(passed)} | Failed: {len(results) - len(passed)}\n")
    for r in results:
        status_sym = "[PASS]" if r["passed"] else "[FAIL]"
        print(f"{status_sym:<7} {r['title'][:35]:<37} | Chars: {r.get('chars', 0):<6} | {r.get('reason', 'OK')}")

    with open("data/proposed_pages_vetted.json", "w", encoding="utf-8") as f:
        json.dump(passed, f, indent=2)


if __name__ == "__main__":
    main()
