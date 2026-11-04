"""
scripts/generate_review_pack.py
C6: Generates docs/REVIEW_2026-11-04.md for the 4 existing manual changes:
1. Vercel Changelog
2. Stripe Pricing
3. Render Pricing
4. OpenAI Business Pricing
"""

import os
import sys
import json
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tinycore.store import get_store
from tinycore.llm import compute_materiality

def generate_review_pack():
    store = get_store("sqlite")
    
    targets = [
        {
            "id": "e79f308f-2b75-47e0-9c81-fe4d91ac3724",
            "name": "Vercel Changelog",
            "url": "https://vercel.com/changelog",
            "check_id": "a0b47a80-341f-45d0-b8dd-7902eb8aaa9d",
            "notes": "Classified initially as 'price' 0.1 with fallback text due to 'LongCat 2.5' -> 'Tencent Hy4' numeric detection. Actually real feature changelog entries.",
            "recommended_verdict": "real",
            "recommended_reason": "Real new product release notes and updates published to the public Vercel changelog feed."
        },
        {
            "id": "3e802da7-d288-451a-a6cf-7cea2e44743f",
            "name": "Stripe Pricing",
            "url": "https://stripe.com/pricing",
            "check_id": "c6e08948-cdf3-4724-8541-cbd716ed48ba",
            "notes": "51 added blocks, 0 removed on 498-block baseline (10.2% added-only). Triggered suspect_capture_variance.",
            "recommended_verdict": "artifact",
            "recommended_reason": "Capture variance caused by dynamic client-side hydration expanding accordion/sub-table sections on second run."
        },
        {
            "id": "62fd75cf-6673-4411-b906-b6a2f1eac7c4",
            "name": "Render Pricing",
            "url": "https://render.com/pricing",
            "check_id": "add1c97e-6fc4-4171-a69e-f5f818a91423",
            "notes": "3 added blocks, 0 removed. Promotional banners for startup credits and migration assistance.",
            "recommended_verdict": "real",
            "recommended_reason": "Real promotional banner addition on the public Render pricing surface, though low materiality (0.2-0.3)."
        },
        {
            "id": "60a73440-5ad2-4135-babb-4d91e724ae06",
            "name": "OpenAI Business Pricing",
            "url": "https://openai.com/api/pricing",
            "check_id": "99f73bf5-081f-4e3b-9d66-9ca2d7797f13",
            "notes": "2 added, 2 removed blocks. Consumer pricing link replaced with 'chat with a buying agent' enterprise CTA.",
            "recommended_verdict": "real",
            "recommended_reason": "Real navigation and CTA copy change on OpenAI's enterprise pricing surface."
        }
    ]

    lines = []
    lines.append("# Human Review Pack — Changes Observed 2026-11-04")
    lines.append("")
    lines.append(f"**Generated At:** {datetime.now(timezone.utc).isoformat()}  ")
    lines.append("**Context:** 4 changes were captured during the manual verification round (`2026-11-04T11:03–11:05 UTC`). None have been counted as 'real' scheduled changes. This review pack presents the diffs, screenshots, capture variance analysis, and recommended verdicts for human sign-off.")
    lines.append("")
    lines.append("---")
    lines.append("")

    for idx, t in enumerate(targets, 1):
        pid = t["id"]
        p = store.get_page(pid)
        v1 = store.get_version(pid, 1)
        v2 = store.get_version(pid, 2)
        diff_rec = store.get_diff(pid, 1, 2)
        diff = diff_rec.get("diff", {}) if diff_rec else {}

        v1_blocks = v1.get("blocks", []) if v1 else []
        v2_blocks = v2.get("blocks", []) if v2 else []
        baseline_count = len(v1_blocks)

        mat, suspect_var = compute_materiality(diff, baseline_blocks_count=baseline_count)

        added = diff.get("added", [])
        removed = diff.get("removed", [])
        changed = diff.get("changed", [])

        lines.append(f"## {idx}. {t['name']} (`{t['url']}`)")
        lines.append("")
        lines.append(f"- **Page ID:** `{pid}`")
        lines.append(f"- **Check ID:** `{t['check_id']}`")
        lines.append(f"- **Observed At:** `{v2.get('observed_at') if v2 else 'N/A'}`")
        lines.append(f"- **Baseline (v1) Blocks:** {baseline_count}")
        lines.append(f"- **New (v2) Blocks:** {len(v2_blocks)}")
        lines.append(f"- **Diff Stats:** {len(added)} added, {len(removed)} removed, {len(changed)} changed")
        lines.append(f"- **Suspect Capture Variance:** `{'YES (flagged)' if suspect_var else 'NO'}`")
        lines.append(f"- **Computed Materiality:** `{mat}`")
        lines.append(f"- **Screenshot v1:** `{v1.get('screenshot_url') if v1 else 'None'}`")
        lines.append(f"- **Screenshot v2:** `{v2.get('screenshot_url') if v2 else 'None'}`")
        lines.append(f"- **Initial Diagnostic Notes:** {t['notes']}")
        lines.append(f"- **Recommended Verdict:** **`{t['recommended_verdict'].upper()}`**")
        lines.append(f"- **Reasoning:** {t['recommended_reason']}")
        lines.append("")

        # Added Blocks (first 15 lines)
        lines.append("### Added Blocks (Sample):")
        if added:
            lines.append("```text")
            for b in added[:15]:
                txt = b.get("text", "") if isinstance(b, dict) else str(b)
                lines.append(f"+ {txt[:120]}")
            lines.append("```")
        else:
            lines.append("*None*")
        lines.append("")

        # Removed Blocks (first 15 lines)
        lines.append("### Removed Blocks (Sample):")
        if removed:
            lines.append("```text")
            for b in removed[:15]:
                txt = b.get("text", "") if isinstance(b, dict) else str(b)
                lines.append(f"- {txt[:120]}")
            lines.append("```")
        else:
            lines.append("*None*")
        lines.append("")

        # Changed Blocks
        lines.append("### Changed Blocks (Sample):")
        if changed:
            lines.append("```text")
            for c in changed[:5]:
                b_txt = c.get("before", "") if isinstance(c.get("before"), str) else c.get("before", {}).get("text", "")
                a_txt = c.get("after", "") if isinstance(c.get("after"), str) else c.get("after", {}).get("text", "")
                lines.append(f"BEFORE: {b_txt[:120]}")
                lines.append(f"AFTER:  {a_txt[:120]}")
                if c.get("numeric_delta"):
                    lines.append(f"DELTA:  {c.get('numeric_delta')}")
                lines.append("---")
            lines.append("```")
        else:
            lines.append("*None*")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append("## Sign-Off Action")
    lines.append("Judges and administrators can apply verdicts via:")
    lines.append("```bash")
    lines.append('curl -X POST "https://timemachine-api.onrender.com/admin/changes/{check_id}/review" \\')
    lines.append('  -H "X-Admin-Token: $ADMIN_TOKEN" \\')
    lines.append('  -H "Content-Type: application/json" \\')
    lines.append('  -d \'{"review_status": "real|artifact", "review_note": "reasoning"}\'')
    lines.append("```")
    lines.append("")

    out_path = os.path.join("docs", "REVIEW_2026-11-04.md")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Generated review pack at {out_path}")

if __name__ == "__main__":
    generate_review_pack()
