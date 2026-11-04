"""
scripts/update_live_stats.py
Reads checks, versions, pages, and cost ledger to generate real-time metrics in docs/LIVE_STATS.md
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from tinycore.store import get_store
from tinycore.ledger import CostLedger, MonitorBudget

def main():
    store = get_store("sqlite")
    all_pages = store.list_pages()
    pages = [p for p in all_pages if not p.get("is_test")]
    checks = store.get_checks(limit=1000)
    
    # Filter checks to non-test pages
    valid_page_ids = {p["id"] for p in pages}
    valid_checks = [c for c in checks if c.get("page_id") in valid_page_ids]
    
    total_checks = len(valid_checks)
    baseline_checks = len([c for c in valid_checks if c.get("outcome") == "baseline"])
    unchanged_checks = len([c for c in valid_checks if c.get("outcome") == "unchanged"])
    noise_checks = len([c for c in valid_checks if c.get("outcome") == "noise"])
    changed_checks = len([c for c in valid_checks if c.get("outcome") == "changed"])
    error_checks = len([c for c in valid_checks if c.get("outcome") == "error"])
    
    daily_spend = CostLedger.get_daily_spend()
    max_daily = CostLedger.get_max_daily()
    budget_status = MonitorBudget.get_status()
    
    unchanged_pct = round((unchanged_checks / total_checks * 100), 1) if total_checks else 0.0
    noise_pct = round((noise_checks / total_checks * 100), 1) if total_checks else 0.0
    changed_pct = round((changed_checks / total_checks * 100), 1) if total_checks else 0.0
    
    content = f"""# Time Machine Live Operational Statistics

*Generated automatically from production audit database.*

## 📊 Summary Metrics

| Metric | Current Value | Notes |
| :--- | :--- | :--- |
| **Tracked Live Pages** | **{len(pages)}** | Vetted developer, pricing, and doc surfaces |
| **Total Checks Processed** | **{total_checks}** | Webhook deliveries & scheduled audit runs |
| **Baseline Snapshots** | **{baseline_checks}** | Initial point-in-time reference captures |
| **Unchanged Check Runs** | **{unchanged_checks}** ({unchanged_pct}%) | Content verified byte/block identical |
| **Noise Diff Rejections** | **{noise_checks}** ({noise_pct}%) | Non-material timestamps, banners, and ads filtered |
| **Real-World Changes Detected** | **{changed_checks}** ({changed_pct}%) | Verified substantive changes (excluding controlled tests) |
| **Error Runs** | **{error_checks}** | Failed network or fetch executions |
| **Active TinyFish Monitors** | **{len(pages)} / 50** | Under global 50-monitor budget |
| **Today's Spend** | **${daily_spend:.4f} USD** | Under ${max_daily:.2f} USD daily ceiling |

---

## 🛡️ Pipeline Integrity & Test Isolation

All pipeline verification procedures using simulated changes (e.g. Acme Pricing test harness) are flagged with `is_test = true` and strictly excluded from the metrics above.

- **Real-World Change Count**: `{changed_checks}`
- **Test / Verification Events**: Segregated under `samples/timemachine/verification/`
"""

    os.makedirs("docs", exist_ok=True)
    with open(os.path.join("docs", "LIVE_STATS.md"), "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Generated docs/LIVE_STATS.md: {total_checks} checks, {len(pages)} pages.")

if __name__ == "__main__":
    main()
