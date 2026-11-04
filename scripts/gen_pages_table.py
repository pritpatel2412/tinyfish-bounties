"""
scripts/gen_pages_table.py
Reads tracked pages from database and generates a Markdown table for docs/TRACKED_PAGES.md
"""
import os
import sys
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from tinycore.store import get_store

def main():
    store = get_store("sqlite")
    all_pages = store.list_pages()
    pages = [p for p in all_pages if not p.get("is_test")]
    
    lines = [
        "# Tracked Pages Fleet (Time Machine)",
        "",
        f"Time Machine currently monitors **{len(pages)}** high-value developer, infrastructure, and framework surfaces.",
        "",
        "| Category | Page Title | Monitored URL | Cadence | Baseline Date | Status |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |"
    ]
    
    for p in sorted(pages, key=lambda x: (x.get("category", ""), x.get("title", ""))):
        cat = p.get("category", "general")
        title = p.get("title", "Untitled")
        url = p.get("url", "")
        interval = p.get("interval_label") or p.get("schedule_cron", "hourly")
        baseline = (p.get("history_starts_at") or p.get("added_at") or "")[:10]
        status = "Active" if p.get("active") else "Paused"
        lines.append(f"| `{cat}` | {title} | [{url}]({url}) | {interval} | {baseline} | `{status}` |")

    os.makedirs("docs", exist_ok=True)
    content = "\n".join(lines) + "\n"
    with open(os.path.join("docs", "TRACKED_PAGES.md"), "w", encoding="utf-8") as f:
        f.write(content)
        
    print(f"Generated docs/TRACKED_PAGES.md with {len(pages)} pages.")

if __name__ == "__main__":
    main()
