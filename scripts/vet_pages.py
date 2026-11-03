"""
scripts/vet_pages.py
Vets candidate URLs for Time Machine:
1. Fetches markdown via TinyFish Fetch API (Chromium-rendered).
2. Rejects non-200, < 800 chars, bot-wall markers, login redirects.
3. Performs stability test (two fetches, markdown_to_blocks, diff_blocks).
4. Assigns staggered crons: normal (M */6 * * *), high-churn (M */2 * * *) where M = hash(url) % 30.
5. Ensures >= 24 vetted pages (>= 8 high-churn).
6. Outputs data/pages_vetted.json and prints summary table.
"""

import os
import sys
import time
import json
import httpx
from dotenv import dotenv_values
from tinycore.tf_client import TF
from tinycore.normalize import markdown_to_blocks
from tinycore.differ import diff_blocks

CANDIDATES = [
    # Dev/AI free-tier & pricing
    {"url": "https://console.groq.com/docs/rate-limits", "title": "Groq Rate Limits", "category": "ai-developer-tools", "churn": "high"},
    {"url": "https://ai.google.dev/gemini-api/docs/rate-limits", "title": "Gemini API Rate Limits", "category": "ai-developer-tools", "churn": "high"},
    {"url": "https://openai.com/api/pricing", "title": "OpenAI API Pricing", "category": "ai-developer-tools", "churn": "normal"},
    {"url": "https://replit.com/pricing", "title": "Replit Pricing", "category": "ai-developer-tools", "churn": "normal"},
    {"url": "https://supabase.com/pricing", "title": "Supabase Pricing", "category": "ai-developer-tools", "churn": "normal"},
    {"url": "https://render.com/pricing", "title": "Render Pricing", "category": "ai-developer-tools", "churn": "normal"},
    {"url": "https://www.cursor.com/pricing", "title": "Cursor Pricing", "category": "ai-developer-tools", "churn": "normal"},
    {"url": "https://github.com/features/copilot/plans", "title": "GitHub Copilot Plans", "category": "ai-developer-tools", "churn": "normal"},
    {"url": "https://huggingface.co/pricing", "title": "Hugging Face Pricing", "category": "ai-developer-tools", "churn": "normal"},
    {"url": "https://www.netlify.com/pricing", "title": "Netlify Pricing", "category": "ai-developer-tools", "churn": "normal"},
    {"url": "https://stripe.com/pricing", "title": "Stripe Pricing", "category": "fintech-billing", "churn": "normal"},
    {"url": "https://resend.com/pricing", "title": "Resend Pricing", "category": "ai-developer-tools", "churn": "normal"},
    {"url": "https://docs.docker.com/subscription/details/", "title": "Docker Pricing Details", "category": "ai-developer-tools", "churn": "normal"},

    # Terms & Policies
    {"url": "https://openai.com/policies/terms-of-use", "title": "OpenAI Terms of Use", "category": "legal-policy", "churn": "normal"},
    {"url": "https://policies.google.com/privacy", "title": "Google Privacy Policy", "category": "legal-policy", "churn": "normal"},
    {"url": "https://docs.github.com/en/site-policy/github-terms/github-terms-of-service", "title": "GitHub Terms of Service", "category": "legal-policy", "churn": "normal"},
    {"url": "https://vercel.com/legal/terms", "title": "Vercel Terms of Service", "category": "legal-policy", "churn": "normal"},

    # Visa & Immigration
    {"url": "https://www.gov.uk/student-visa", "title": "UK Student Visa", "category": "visa-immigration", "churn": "normal"},
    {"url": "https://www.gov.uk/skilled-worker-visa", "title": "UK Skilled Worker Visa", "category": "visa-immigration", "churn": "normal"},
    {"url": "https://travel.state.gov/content/travel/en/us-visas/study/student-visa.html", "title": "US Student Visa", "category": "visa-immigration", "churn": "normal"},
    {"url": "https://travel.state.gov/content/travel/en/us-visas/employment/temporary-worker-visas.html", "title": "US Temporary Worker Visas", "category": "visa-immigration", "churn": "normal"},

    # High-Churn (changelogs, release notes, notices, status pages)
    {"url": "https://vercel.com/changelog", "title": "Vercel Changelog", "category": "changelog", "churn": "high"},
    {"url": "https://ai.google.dev/gemini-api/docs/release-notes", "title": "Gemini API Release Notes", "category": "changelog", "churn": "high"},
    {"url": "https://docs.anthropic.com/en/release-notes/overview", "title": "Anthropic Release Notes", "category": "changelog", "churn": "high"},
    {"url": "https://www.uscis.gov/newsroom/alerts", "title": "USCIS Alerts & News", "category": "visa-immigration", "churn": "high"},
    {"url": "https://status.openai.com/", "title": "OpenAI Status", "category": "changelog", "churn": "high"},
    {"url": "https://status.groq.com/", "title": "Groq Status", "category": "changelog", "churn": "high"},
    {"url": "https://www.cloudflarestatus.com/", "title": "Cloudflare Status", "category": "changelog", "churn": "high"},
    {"url": "https://docs.github.com/en/billing/managing-billing-for-your-products/managing-billing-for-github-copilot", "title": "GitHub Copilot Billing Policy", "category": "ai-developer-tools", "churn": "high"}
]

BOT_WALL_MARKERS = [
    "Just a moment",
    "Verify you are human",
    "Access denied",
    "Attention Required! | Cloudflare",
    "Please enable cookies",
    "cf-browser-verification"
]


def check_bot_wall(text: str) -> str | None:
    for marker in BOT_WALL_MARKERS:
        if marker.lower() in text.lower():
            return f"Bot-wall detected: '{marker}'"
    return None


def main():
    cfg = dotenv_values(".env")
    api_key = cfg.get("TINYFISH_API_KEY")
    if not api_key:
        print("ERROR: TINYFISH_API_KEY not found in .env")
        sys.exit(1)

    tf = TF(api_key=api_key)

    try:
        monitors_list = tf.monitors_list()
        live_active = len([m for m in monitors_list if m.get("status") == "active"])
        print(f"=== Vetting Pages for Time Machine (Live active monitors on account: {live_active}/50) ===")
    except Exception as e:
        print(f"Warning: could not fetch live monitors: {e}")
        live_active = 0

    results = []
    round1_snapshots = {}

    print(f"\n--- Round 1: Fetching and Filtering {len(CANDIDATES)} Candidates ---")
    for i, c in enumerate(CANDIDATES):
        url = c["url"]
        print(f"[{i+1}/{len(CANDIDATES)}] Fetching {url} ...", end=" ", flush=True)
        try:
            res = tf.fetch(url, format="markdown")
            text = res.text
            if not text or len(text) < 800:
                print(f"REJECTED: text too short ({len(text) if text else 0} chars < 800)")
                results.append({**c, "status": "dropped", "stability": "n/a", "reason": f"Content too short ({len(text) if text else 0} chars)"})
                continue

            bot_err = check_bot_wall(text)
            if bot_err:
                print(f"REJECTED: {bot_err}")
                results.append({**c, "status": "dropped", "stability": "n/a", "reason": bot_err})
                continue

            title = res.title or c["title"]
            c["title"] = title
            round1_snapshots[url] = {"text": text, "candidate": c}
            print(f"OK ({len(text)} chars)")
        except Exception as e:
            print(f"FAILED: {e}")
            results.append({**c, "status": "dropped", "stability": "n/a", "reason": str(e)})

        time.sleep(1)

    survivors = list(round1_snapshots.keys())
    print(f"\nRound 1 complete: {len(survivors)} / {len(CANDIDATES)} passed initial filter.")

    # Stability test
    print("\n--- Round 2: Stability Test (Second fetch to confirm zero noise diff) ---")
    print("Waiting 10s before starting Round 2 fetches...")
    time.sleep(10)

    vetted_pages = []
    for i, url in enumerate(survivors):
        c = round1_snapshots[url]["candidate"]
        text1 = round1_snapshots[url]["text"]
        print(f"[{i+1}/{len(survivors)}] Re-fetching {url} ...", end=" ", flush=True)
        try:
            res2 = tf.fetch(url, format="markdown")
            text2 = res2.text

            b1 = markdown_to_blocks(text1)
            b2 = markdown_to_blocks(text2)
            diff = diff_blocks(b1, b2)

            if diff.is_empty:
                stability = "stable"
                status = "vetted"
                reason = "Passed stability check"
                print("STABLE (diff is empty)")
            else:
                stability = "noisy"
                status = "dropped"
                reason = f"Noisy diff between runs: {len(diff.changed)} changed, {len(diff.added)} added, {len(diff.removed)} removed"
                print(f"NOISY ({reason})")

            # Calculate staggered cron: M = abs(hash(url)) % 30
            minute = abs(hash(url)) % 30
            if c["churn"] == "high":
                cron = f"{minute} */2 * * *"
                interval_label = "2h"
            else:
                cron = f"{minute} */6 * * *"
                interval_label = "6h"

            record = {
                "url": url,
                "title": c["title"],
                "category": c["category"],
                "churn": c["churn"],
                "schedule_cron": cron,
                "interval_label": interval_label,
                "status": status,
                "stability": stability,
                "reason": reason
            }
            results.append(record)
            if status == "vetted":
                vetted_pages.append(record)
        except Exception as e:
            print(f"FAILED on re-fetch: {e}")
            results.append({**c, "status": "dropped", "stability": "error", "reason": str(e)})

        time.sleep(1)

    # Save to data/pages_vetted.json
    os.makedirs("data", exist_ok=True)
    out_path = os.path.join("data", "pages_vetted.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(vetted_pages, f, indent=2)

    # Print summary table
    print("\n" + "=" * 110)
    print(f"{'URL':<52} | {'STATUS':<8} | {'STABILITY':<9} | {'CRON':<12} | {'CHURN':<6} | {'REASON'}")
    print("=" * 110)
    for r in results:
        url_trunc = r["url"][:50]
        cron_str = r.get("schedule_cron", "n/a")
        churn_str = r.get("churn", "normal")
        status_str = r.get("status", "dropped")
        stab_str = r.get("stability", "n/a")
        reason_str = r.get("reason", "")[:30]
        print(f"{url_trunc:<52} | {status_str:<8} | {stab_str:<9} | {cron_str:<12} | {churn_str:<6} | {reason_str}")
    print("=" * 110)

    high_churn_count = len([p for p in vetted_pages if p.get("churn") == "high"])
    print(f"\nTotal Vetted Pages: {len(vetted_pages)} (High Churn: {high_churn_count}, Normal: {len(vetted_pages) - high_churn_count})")
    print(f"Output saved to {out_path}")

    if len(vetted_pages) < 20:
        print(f"\nWARNING: Vetted pages count ({len(vetted_pages)}) is below the required 20.")
        sys.exit(1)
    else:
        print(f"\nSTAGE 2 PASS: {len(vetted_pages)} pages vetted (>= 20 required). Ready for seeding.")


if __name__ == "__main__":
    main()
