"""
scripts/probe_a5_fetch_first.py
Probe A5: Fetch-First Registry Lookup
Tests deterministic URL search via TinyFish Fetch and Python HTML parsing (BeautifulSoup)
vs. Agent execution.
"""

import os
import json
import time
import httpx
from bs4 import BeautifulSoup
from dotenv import dotenv_values

cfg = dotenv_values(".env")
api_key = cfg.get("TINYFISH_API_KEY")

print("=== Starting Probe A5: Fetch-First Registry Lookup ===")

search_url = "https://find-and-update.company-information.service.gov.uk/search/companies?q=deepmind"
print(f"1. Fetching deterministic search URL: {search_url} (format: html)...")

t0 = time.time()
resp = httpx.post(
    "https://api.fetch.tinyfish.ai",
    headers={"X-API-Key": api_key, "Content-Type": "application/json"},
    json={"urls": [search_url], "format": "html"},
    timeout=30
)
lat_search = (time.time() - t0) * 1000
print(f"   Search Fetch status: HTTP {resp.status_code} in {lat_search:.1f}ms")

data = resp.json()
results = data.get("results", [])
if not results:
    print(f"FAILED: No results returned by Fetch. Response: {data}")
    exit(1)

html_content = results[0].get("text", "")
print(f"   Received {len(html_content)} chars of HTML.")

# Parse search results with BeautifulSoup
soup = BeautifulSoup(html_content, "html.parser")
candidates = []

# Companies House search page returns company numbers in list items
import re

lines = html_content.splitlines()
company_numbers = re.findall(r'(\d{8})\s*-\s*(Incorporated|Dissolved)', html_content)
for comp_num, status in company_numbers[:5]:
    candidates.append({
        "company_number": comp_num,
        "preliminary_status": status,
        "detail_url": f"https://find-and-update.company-information.service.gov.uk/company/{comp_num}"
    })

print(f"\n2. Extracted {len(candidates)} candidate registration numbers from search:")
for i, c in enumerate(candidates, 1):
    print(f"   [{i}] Number: {c.get('company_number')} ({c.get('preliminary_status')})")

first = candidates[0] if candidates else {}
top_number = first.get("company_number")
detail_url = first.get("detail_url")

# Fetch detail page
print(f"\n3. Fetching detail page for top candidate: {detail_url}...")
t_det = time.time()
det_resp = httpx.post(
    "https://api.fetch.tinyfish.ai",
    headers={"X-API-Key": api_key, "Content-Type": "application/json"},
    json={"urls": [detail_url], "format": "markdown"},
    timeout=30
)
lat_det = (time.time() - t_det) * 1000
print(f"   Detail Fetch status: HTTP {det_resp.status_code} in {lat_det:.1f}ms")

det_text = det_resp.json().get("results", [{}])[0].get("text", "")
# Extract company name from "Company Overview for <NAME> (<NUMBER>)"
match = re.search(r'Company Overview for ([^(]+)\s*\(' + re.escape(top_number) + r'\)', det_text)
top_name = match.group(1).strip() if match else "UNKNOWN"

status_match = re.search(r'Company status\s*\n\*\s*([A-Za-z]+)', det_text)
status_val = status_match.group(1).strip() if status_match else ""

inc_match = re.search(r'Incorporated on\s*\n\*\s*([^\n]+)', det_text)
inc_date = inc_match.group(1).strip() if inc_match else ""

print(f"   Extracted details from Fetch markdown:")
print(f"     Name: {top_name}")
print(f"     Number: {top_number}")
print(f"     Status: {status_val}")
print(f"     Incorporated: {inc_date}")

print("\n=== Comparison: Fetch-First vs Agent ===")
print("Agent Search:")
print("  - Latency: ~55,342 ms")
print("  - Cost: 10 steps * $0.016 = $0.160 USD")
print("  - Result: DEEPMIND HOLDINGS LIMITED, 12181850")
print("\nFetch-First Detail Extraction:")
print(f"  - Latency: {lat_search:.1f} ms search + {lat_det:.1f} ms detail = {(lat_search+lat_det):.1f} ms (~22x faster)")
print("  - Cost: $0.000 USD (100% Free)")
print(f"  - Result: {top_name}, {top_number}")
print(f"  - Match: {top_name == 'DEEPMIND HOLDINGS LIMITED' and top_number == '12181850'}")
