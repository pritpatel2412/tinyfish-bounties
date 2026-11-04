"""
scripts/audit_pages.py
C8: Page quality audit for all 28 tracked pages:
- Inspects latest version for blocks_count, text length, title, url
- Flags:
  * openai.com/api/pricing (page title "Business Pricing" - wrong landing page)
  * ai.google.dev/gemini-api/docs/rate-limits (1 block - client-side JS shell)
  * Any page with text length < 800 chars or < 5 blocks
- Recommends concrete vetted replacements.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tinycore.store import get_store

def audit_pages():
    store = get_store("sqlite")
    pages = [p for p in store.list_pages() if not p.get("is_test")]

    print("=" * 120)
    print("C8: TRACKED PAGE QUALITY AUDIT")
    print("=" * 120)
    print(f"{'Page URL':<46} | {'Title':<25} | {'Blocks':<6} | {'Chars':<7} | {'Status / Flags'}")
    print("-" * 120)

    flagged = []
    clean = []

    for p in pages:
        pid = p["id"]
        v = store.get_latest_version(pid) or store.get_version(pid, 1) or {}
        blocks = v.get("blocks", [])
        text_md = v.get("text_md") or ""
        title = p.get("title") or v.get("title") or "Unknown"

        blocks_count = len(blocks)
        text_len = len(text_md)

        url = p["url"]
        flags = []

        if "openai.com/api/pricing" in url and "Business" in title:
            flags.append("FLAG: landing redirects to business pricing rather than API model tokens")
        if "gemini-api/docs/rate-limits" in url or blocks_count <= 2:
            flags.append("FLAG: JS client-side shell (minimal blocks, empty content)")
        if blocks_count < 5 and not flags:
            flags.append(f"FLAG: low blocks ({blocks_count} < 5)")
        if text_len < 800 and not flags:
            flags.append(f"FLAG: short content ({text_len} < 800 chars)")

        disp_url = url if len(url) <= 46 else url[:43] + "..."
        disp_title = title if len(title) <= 25 else title[:22] + "..."
        flag_str = "; ".join(flags) if flags else "[CLEAN]"

        print(f"{disp_url:<46} | {disp_title:<25} | {blocks_count:<6} | {text_len:<7} | {flag_str}")

        if flags:
            flagged.append({
                "page": p,
                "title": title,
                "blocks_count": blocks_count,
                "text_len": text_len,
                "flags": flags
            })
        else:
            clean.append(p)

    print("=" * 120)
    print(f"\nAudit Summary: {len(pages)} total pages | {len(clean)} clean | {len(flagged)} flagged")

    if flagged:
        print("\nFlagged Pages & Proposed Replacements:")
        for f in flagged:
            p = f["page"]
            print(f"\n[!] {p['url']} ({f['title']})")
            for fl in f["flags"]:
                print(f"    Issue: {fl}")
            if "openai.com/api/pricing" in p["url"]:
                print("    -> Proposed Replacement: https://openai.com/pricing (or direct developers model pricing page)")
            elif "gemini-api/docs/rate-limits" in p["url"]:
                print("    -> Proposed Replacement: https://ai.google.dev/pricing (renders static markdown with real pricing tiers)")

    return flagged

if __name__ == "__main__":
    audit_pages()
