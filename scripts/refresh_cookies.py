#!/usr/bin/env python3
"""Refresh cookies.json with a visible Chrome login (run when requests start returning 403).

Opens the same Chrome for Testing build and user agent the scraper uses, logs in
through the site's header Login popup with AUTH_EMAIL/AUTH_PASSWORD from .env,
and saves the session cookies to ./cookies.json. If Cloudflare shows its
"Verify you are human" check, click it in the window; the script never tries
to answer that check itself. Waits up to 6 hours.

Usage (from the repository root):
    .venv/bin/python scripts/refresh_cookies.py
"""
import json, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "selenium_ingestion_final"))
from browser import create_driver
from config_loader import load_config
from selenium.webdriver.common.by import By

cfg = load_config(str(ROOT / "selenium_ingestion_final/config.yaml"))
EMAIL, PASSWORD = cfg["authentication"]["email"], cfg["authentication"]["password"]

def log(*a): print(*a, flush=True)
def site_loaded(d):
    # The real site (logged in or not) has the DNN header; the Cloudflare
    # interstitial does not. Real pages also embed challenge-platform scripts,
    # so that string cannot be used to detect the interstitial.
    d.switch_to.default_content(); s = d.page_source
    return 'dnn_MyAccountLink1' in s or "dnnModal.show" in s

def challenged(d):
    return "just a moment" in d.title.lower() or not site_loaded(d)
def logged_in(d):
    d.switch_to.default_content(); s = d.page_source
    return 'id="dnn_MyAccountLink1_MyAccount"' in s or 'class="myAccount opened"' in s

def wait_clear(d, what):
    while challenged(d):
        log(f"Cloudflare check on {what} - please complete it in the window..."); time.sleep(2)

def try_login(d):
    d.switch_to.default_content()
    if not d.execute_script("var a=document.querySelector(\"a[onclick*='dnnModal.show']\"); if(a){a.click();return true} return false"):
        log("Login link not found"); return
    # Wait up to 20s for the login popup's fields, inside its iframe if it has one.
    USER = "input[id$='txtUsername'], input[type='email'], input[id*='User']"
    for _ in range(20):
        time.sleep(1)
        d.switch_to.default_content()
        if d.find_elements(By.CSS_SELECTOR, USER):
            break
        frame = next((f for f in d.find_elements(By.TAG_NAME, "iframe")
                      if "popup" in (f.get_attribute("id") or "").lower()
                      or "login" in (f.get_attribute("src") or "").lower()), None)
        if frame:
            d.switch_to.frame(frame)
            if d.find_elements(By.CSS_SELECTOR, USER):
                break
    d.execute_script("""for (const b of document.querySelectorAll('button,a')) {
        if (/accept all/i.test(b.textContent)) { b.click(); break; } }""")
    time.sleep(1)
    u = d.find_element(By.CSS_SELECTOR, "input[id$='txtUsername'], input[type='email'], input[id*='User']")
    p = d.find_element(By.CSS_SELECTOR, "input[type='password']")
    u.clear(); u.send_keys(EMAIL); p.clear(); p.send_keys(PASSWORD)
    btn = d.find_element(By.XPATH, "//a[contains(@id,'cmdLogin')]")
    d.execute_script("arguments[0].click();", btn)
    log("Submitted login form"); time.sleep(8)
    d.switch_to.default_content()

d = create_driver({"selenium": {"browser": "chrome", "headless": False, "page_load_timeout": 100}})
try:
    log("UA:", d.execute_script("return navigator.userAgent"))
    d.get("https://acukwik.com"); time.sleep(4)
    wait_clear(d, "home page")
    deadline = time.time() + 6 * 3600
    attempts = 0
    while time.time() < deadline:
        if not logged_in(d) and (attempts < 2 or attempts % 10 == 0):
            attempts += 1
            try:
                wait_clear(d, "page")
                try_login(d)
            except Exception as e:
                log("Auto-login step failed:", type(e).__name__, str(e)[:200], "- retrying; you can also log in manually in the window")
                try:
                    d.switch_to.default_content(); d.get("https://acukwik.com"); time.sleep(4)
                except Exception:
                    pass
        elif not logged_in(d):
            attempts += 1
        if logged_in(d):
            d.get("https://acukwik.com/Airport-Info/HCMI"); time.sleep(4)
            wait_clear(d, "airport page")
            if logged_in(d):
                cookies = d.get_cookies()
                out = ROOT / "cookies.json"
                out.write_text(json.dumps(cookies, indent=2)); out.chmod(0o600)
                log(f"SAVED {len(cookies)} cookies:", sorted({c['name'] for c in cookies}))
                sys.exit(0)
        time.sleep(3)
    log("TIMEOUT: not logged in within 6 hours"); sys.exit(1)
finally:
    d.quit()
