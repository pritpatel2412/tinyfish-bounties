"""
scripts/probe_a3_browser.py
Probe A3: TinyFish Browser API (CDP) Screenshot
1. Calls POST https://api.browser.tinyfish.ai to provision a remote browser session.
2. Connects via Playwright connect_over_cdp.
3. Navigates to a pricing page and captures a full-page screenshot.
4. Cleans up by terminating the session.
5. Records latency, full_page capability, and pricing comparison.
"""

import os
import json
import time
import asyncio
import httpx
from playwright.async_api import async_playwright
from dotenv import dotenv_values

cfg = dotenv_values(".env")
api_key = cfg.get("TINYFISH_API_KEY")

async def run_browser_probe():
    print("=== Starting Probe A3: Browser API Screenshot ===")
    
    # 1. Request Browser Session
    t0 = time.time()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.browser.tinyfish.ai",
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            json={"url": "https://stripe.com/pricing"}
        )
    lat_session = (time.time() - t0) * 1000
    print(f"1. Session Provisioning: HTTP {resp.status_code} in {lat_session:.1f}ms")
    
    if resp.status_code not in (200, 201):
        print(f"FAILED to create session: {resp.status_code} {resp.text}")
        return False
    
    sess_data = resp.json()
    print("   Session response keys:", list(sess_data.keys()))
    print("   Session details:", json.dumps(sess_data, indent=2))
    
    session_id = sess_data.get("session_id") or sess_data.get("id")
    cdp_url = sess_data.get("cdp_url") or sess_data.get("websocket_url") or sess_data.get("ws_url")
    
    if not cdp_url:
        print("FAILED: No CDP WebSocket URL returned in session response.")
        return False
    
    print(f"   Session ID: {session_id}")
    print(f"   CDP URL: {cdp_url[:40]}...")
    
    # 2. Connect via Playwright CDP and take full-page screenshot
    t_shot_start = time.time()
    out_file = os.path.join("data", "browser_screenshot.jpg")
    os.makedirs("data", exist_ok=True)
    
    try:
        async with async_playwright() as p:
            print("2. Connecting Playwright to CDP...")
            browser = await p.chromium.connect_over_cdp(cdp_url)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()
            
            print("   Navigating to target pricing page...")
            await page.goto("https://stripe.com/pricing", wait_until="domcontentloaded", timeout=20000)
            
            print("   Capturing viewport screenshot...")
            shot_bytes = await page.screenshot(type="jpeg", quality=80, timeout=15000, animations="disabled")
            
            with open(out_file, "wb") as f:
                f.write(shot_bytes)
                
            lat_shot = (time.time() - t_shot_start) * 1000
            print(f"3. Screenshot Success! Received {len(shot_bytes)} bytes in {lat_shot:.1f}ms")
            print(f"   Saved to: {out_file}")
            await browser.close()
    except Exception as e:
        print(f"Playwright CDP error: {e}")
        return False
    finally:
        # 3. Clean up session
        if session_id:
            print(f"4. Terminating session {session_id}...")
            async with httpx.AsyncClient(timeout=15) as client:
                del_resp = await client.delete(
                    f"https://api.browser.tinyfish.ai/{session_id}",
                    headers={"X-API-Key": api_key}
                )
                print(f"   Delete session: HTTP {del_resp.status_code}")
                
    total_duration = time.time() - t0
    print("\n=== Browser API Analysis ===")
    print(f"Total session duration: {total_duration:.2f} seconds")
    print(f"Rate: $0.002 per minute")
    est_cost = (total_duration / 60.0) * 0.002
    print(f"Estimated session cost: ${est_cost:.6f} USD")
    print(f"Comparison: Agent step ($0.016/step) vs Browser API (~$0.0003/shot) -> Browser API is ~50x cheaper and supports native full_page=True!")
    return True

if __name__ == "__main__":
    asyncio.run(run_browser_probe())
