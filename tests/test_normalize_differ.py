"""
tests/test_normalize_differ.py
Tests content normalization, noise stripping, and structured diffing.
"""

from tinycore.normalize import clean_html, strip_noise, to_blocks, Block
from tinycore.differ import diff_blocks, extract_numbers_with_units, compute_numeric_deltas


def test_strip_noise_dates_and_timestamps():
    text = (
        "Acme Pricing\n"
        "Last updated: November 3, 2026 at 12:45:00 UTC\n"
        "Starter plan: $10/mo\n"
        "© 2024-2026 Acme Corp. All rights reserved.\n"
        "Viewed 4,520 times. 2 hours ago."
    )
    cleaned = strip_noise(text)
    assert "November 3, 2026" not in cleaned
    assert "Last updated" not in cleaned
    assert "2024-2026" not in cleaned
    assert "4,520 times" not in cleaned
    assert "2 hours ago" not in cleaned
    assert "Starter plan: $10/mo" in cleaned


def test_diff_blocks_pure_noise_is_empty():
    # V1 and V2 differ ONLY by dates and tracking parameters
    v1_text = (
        "# Pricing\n\n"
        "Starter Plan: $10/mo\n"
        "Updated on 2026-10-01T08:00:00Z\n"
        "Visit https://example.com/pricing?utm_source=twitter&session_id=abc12345"
    )
    v2_text = (
        "# Pricing\n\n"
        "Starter Plan: $10/mo\n"
        "Updated on 2026-11-03T18:00:00Z\n"
        "Visit https://example.com/pricing?utm_source=newsletter&session_id=xyz98765"
    )

    b1 = to_blocks(v1_text)
    b2 = to_blocks(v2_text)

    diff = diff_blocks(b1, b2)
    assert diff.is_empty is True
    assert len(diff.added) == 0
    assert len(diff.removed) == 0
    assert len(diff.changed) == 0


def test_diff_blocks_price_change():
    v1_text = (
        "# Plans\n\n"
        "## Pro\n\n"
        "Pro Plan costs $20/mo with 50 req/sec limit."
    )
    v2_text = (
        "# Plans\n\n"
        "## Pro\n\n"
        "Pro Plan costs $29/mo with 100 req/sec limit."
    )

    b1 = to_blocks(v1_text)
    b2 = to_blocks(v2_text)

    diff = diff_blocks(b1, b2)
    assert diff.is_empty is False
    assert len(diff.changed) == 1
    ch = diff.changed[0]
    assert ch.heading_path == "Plans > Pro"
    assert len(ch.numeric_delta) >= 1

    # Check numeric deltas
    deltas = {d["unit"]: (d["old"], d["new"]) for d in ch.numeric_delta}
    assert "$/mo" in deltas
    assert deltas["$/mo"] == (20.0, 29.0)


def test_clean_html_removes_scripts_and_banners():
    html = """
    <html>
      <head>
        <script>console.log("bad");</script>
        <style>.bad { color: red; }</style>
      </head>
      <body>
        <nav><a href="/">Home</a></nav>
        <div id="cookie-consent-banner">Accept all cookies</div>
        <h1>Our Pricing</h1>
        <p>Basic: $5/mo</p>
        <footer>Copyright 2026</footer>
      </body>
    </html>
    """
    cleaned = clean_html(html)
    assert "console.log" not in cleaned
    assert "Accept all cookies" not in cleaned
    assert "Our Pricing" in cleaned
    assert "Basic: $5/mo" in cleaned


def test_markdown_to_blocks_table_price_change():
    from tinycore.normalize import markdown_to_blocks

    v1_md = """# Pricing Plans
| Plan | Price | Features |
| :--- | :---: | :--- |
| Basic | $10/mo | 1 user, basic api |
| Pro | $25/mo | 5 users, priority api |
| Enterprise | $100/mo | Unlimited users |
"""

    v2_md = """# Pricing Plans
| Plan | Price | Features |
| :--- | :---: | :--- |
| Basic | $10/mo | 1 user, basic api |
| Pro | $39/mo | 5 users, priority api |
| Enterprise | $100/mo | Unlimited users |
"""

    b1 = markdown_to_blocks(v1_md)
    b2 = markdown_to_blocks(v2_md)

    # 4 rows (1 header + 3 data rows) under heading 'Pricing Plans'
    assert len(b1) == 4
    assert len(b2) == 4
    assert all(b.heading_path == "Pricing Plans" for b in b1)

    diff = diff_blocks(b1, b2)
    assert diff.is_empty is False
    assert len(diff.changed) == 1
    assert len(diff.added) == 0
    assert len(diff.removed) == 0

    ch = diff.changed[0]
    assert ch.heading_path == "Pricing Plans"
    assert "$25/mo" in ch.before
    assert "$39/mo" in ch.after
    assert len(ch.numeric_delta) >= 1
    deltas = {d["unit"]: (d["old"], d["new"]) for d in ch.numeric_delta}
    assert "$/mo" in deltas
    assert deltas["$/mo"] == (25.0, 39.0)


def test_markdown_to_blocks_reordered_noise_is_empty():
    from tinycore.normalize import markdown_to_blocks

    v1_md = """# Changelog & Status
Last updated: October 24, 2026 at 15:30:00 UTC
Our primary API cluster is fully operational with 99.99% uptime.
© 2024-2026 Platform Inc.
Reviewed by security team on 2026-10-20
"""

    v2_md = """# Changelog & Status
Reviewed by security team on 2026-11-03
Our primary API cluster is fully operational with 99.99% uptime.
Last updated: November 03, 2026 at 20:15:00 UTC
© 2024-2026 Platform Inc.
"""

    b1 = markdown_to_blocks(v1_md)
    b2 = markdown_to_blocks(v2_md)

    diff = diff_blocks(b1, b2)
    assert diff.is_empty is True
    assert len(diff.changed) == 0
    assert len(diff.added) == 0
    assert len(diff.removed) == 0
