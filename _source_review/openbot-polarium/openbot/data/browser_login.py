"""
Browser login capture for Polarium using Playwright.

Flow:
  1. Open Polarium login page in headful Chromium
  2. User logs in manually (2FA, captcha, etc handled by user)
  3. Capture SSO token (ssid), cookies, broker_id from network/storage
  4. Persist to .env and state.json for future use
  5. Show success animation in terminal

This is the most reliable way to authenticate — no password storage,
handles 2FA, captcha, region locks, and session refresh automatically.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional, Tuple, Dict
from urllib.parse import urlparse

log = logging.getLogger(__name__)

POLARIUM_LOGIN_URL = "https://trade.polariumbroker.com/pt/login?redirect_url=traderoom%2F"
POLARIUM_TRADER_URL = "https://trade.polariumbroker.com/traderoom"
STATE_FILE = "polarium_session.json"

# JS to extract SSO + broker info from various storages
EXTRACT_JS = """
() => {
    const result = {sso: null, broker_id: null, account_type: null, extra: {}};
    // localStorage
    try {
        for (let i = 0; i < localStorage.length; i++) {
            const k = localStorage.key(i);
            const v = localStorage.getItem(k);
            if (!result.sso && (k.toLowerCase().includes('ssid') || k.toLowerCase().includes('token') || k.toLowerCase().includes('auth'))) {
                result.sso = v;
            }
            if (k.toLowerCase().includes('broker')) {
                try { result.broker_id = JSON.parse(v).id || parseInt(v); } catch(e) { result.broker_id = parseInt(v); }
            }
            if (k.toLowerCase().includes('account') || k.toLowerCase().includes('balance')) {
                result.extra[k] = v;
            }
        }
    } catch(e) { result.extra.localStorageError = String(e); }
    // sessionStorage
    try {
        for (let i = 0; i < sessionStorage.length; i++) {
            const k = sessionStorage.key(i);
            const v = sessionStorage.getItem(k);
            if (!result.sso && k.toLowerCase().includes('ssid')) result.sso = v;
            if (k.toLowerCase().includes('broker')) {
                try { result.broker_id = JSON.parse(v).id || parseInt(v); } catch(e) { result.broker_id = parseInt(v); }
            }
        }
    } catch(e) { result.extra.sessionStorageError = String(e); }
    // Cookies accessible via document.cookie (only non-httpOnly)
    try {
        const cookies = document.cookie.split(';');
        for (const c of cookies) {
            const [k, v] = c.trim().split('=');
            if (!result.sso && (k === 'ssid' || k === 'sso_token' || k === 'auth_token')) {
                result.sso = v;
            }
        }
    } catch(e) { result.extra.cookieError = String(e); }
    return result;
}
"""


class BrowserLoginCapture:
    """Captures Polarium session via headful browser."""

    def __init__(
        self,
        login_url: str = POLARIUM_LOGIN_URL,
        success_url_pattern: str = "trade.polariumbroker.com",
        headless: bool = False,
        timeout_sec: int = 300,
    ) -> None:
        self.login_url = login_url
        self.success_url_pattern = success_url_pattern
        self.headless = headless
        self.timeout_sec = timeout_sec

    async def capture(self) -> Dict:
        """
        Opens browser, waits for user to complete login, captures SSO.

        Returns dict with: sso_token, cookies, broker_id, account_type, extra.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise RuntimeError(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            ) from e

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            )
            page = await context.new_page()

            # Storage for captured network info
            captured = {"sso": None, "broker_id": None, "account_type": None, "extra": {}}

            # Intercept network requests to find auth tokens
            async def on_request(request):
                url = request.url
                if "login" in url or "auth" in url or "ssid" in url:
                    headers = request.headers
                    for k, v in headers.items():
                        if "auth" in k.lower() or "ssid" in k.lower() or "token" in k.lower():
                            if not captured["sso"]:
                                captured["sso"] = v
                                log.info("Captured SSO from request header: %s", k)
                # POST body may contain the ssid after login response
                post_data = request.post_data
                if post_data and "ssid" in post_data:
                    try:
                        m = re.search(r'"ssid"\s*:\s*"([^"]+)"', post_data)
                        if m and not captured["sso"]:
                            captured["sso"] = m.group(1)
                            log.info("Captured SSO from POST body")
                    except Exception:
                        pass

            async def on_response(response):
                url = response.url
                if "/login" in url or "/auth" in url or "session" in url:
                    try:
                        body = await response.text()
                        # Look for ssid/token in response body
                        for pat in [r'"ssid"\s*:\s*"([^"]+)"', r'"token"\s*:\s*"([^"]+)"', r'"sso_token"\s*:\s*"([^"]+)"']:
                            m = re.search(pat, body)
                            if m and not captured["sso"]:
                                captured["sso"] = m.group(1)
                                log.info("Captured SSO from response: %s", url)
                        # Look for broker_id
                        m = re.search(r'"broker_id"\s*:\s*(\d+)', body)
                        if m and not captured["broker_id"]:
                            captured["broker_id"] = int(m.group(1))
                            log.info("Captured broker_id: %d", captured["broker_id"])
                    except Exception:
                        pass

            page.on("request", lambda r: asyncio.create_task(on_request(r)))
            page.on("response", lambda r: asyncio.create_task(on_response(r)))

            log.info("Opening Polarium login page: %s", self.login_url)
            await page.goto(self.login_url, wait_until="networkidle")

            print("\n" + "="*60)
            print("  NAVEGADOR ABERTO — faça login na Polarium")
            print("="*60)
            print("  1. Digite seu email e senha")
            print("  2. Complete 2FA/captcha se aparecer")
            print("  3. Aguarde ser redirecionado para o trader")
            print("  O openbot vai capturar a sessão automaticamente")
            print("="*60 + "\n")

            # Wait for user to complete login (URL change to trader/dashboard)
            try:
                await page.wait_for_url(
                    lambda url: self.success_url_pattern in url and "login" not in url.lower(),
                    timeout=self.timeout_sec * 1000
                )
            except Exception as e:
                log.warning("URL wait timeout/error: %s — trying DOM extraction anyway", e)

            # Give a moment for post-login requests
            await page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(2)

            # Extract from JS storages
            try:
                js_result = await page.evaluate(EXTRACT_JS)
                if js_result.get("sso") and not captured["sso"]:
                    captured["sso"] = js_result["sso"]
                    log.info("Captured SSO from browser storage")
                if js_result.get("broker_id") and not captured["broker_id"]:
                    captured["broker_id"] = js_result["broker_id"]
                if js_result.get("account_type"):
                    captured["account_type"] = js_result["account_type"]
                captured["extra"].update(js_result.get("extra", {}))
            except Exception as e:
                log.warning("JS extraction failed: %s", e)

            # Capture cookies
            cookies = await context.cookies()
            cookie_dict = {c["name"]: c["value"] for c in cookies if "polarium" in c.get("domain", "") or "quadcode" in c.get("domain", "")}

            await browser.close()

            if not captured["sso"]:
                raise RuntimeError(
                    "Failed to capture SSO token. Make sure you completed login "
                    "(saw the trader dashboard) before the timeout."
                )

            return {
                "sso_token": captured["sso"],
                "cookies": cookie_dict,
                "broker_id": captured["broker_id"] or 11,
                "account_type": captured["account_type"] or "practice",
                "extra": captured["extra"],
            }

    @staticmethod
    def save_session(session: Dict, path: str = STATE_FILE) -> None:
        """Persist captured session to JSON file."""
        with open(path, "w") as f:
            json.dump(session, f, indent=2)
        # Restrict permissions
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        log.info("Session saved to %s", path)

    @staticmethod
    def load_session(path: str = STATE_FILE) -> Optional[Dict]:
        """Load previously captured session."""
        if not Path(path).exists():
            return None
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return None

    @staticmethod
    def update_env_file(session: Dict, env_path: str = ".env") -> None:
        """Update .env with captured SSO token (creates if missing)."""
        lines = []
        if Path(env_path).exists():
            with open(env_path) as f:
                lines = f.read().splitlines()

        sso_updated = False
        broker_updated = False
        new_lines = []
        for line in lines:
            if line.startswith("POLARIUM_SSO_TOKEN="):
                new_lines.append(f"POLARIUM_SSO_TOKEN={session['sso_token']}")
                sso_updated = True
            elif line.startswith("POLARIUM_BROKER_ID="):
                new_lines.append(f"POLARIUM_BROKER_ID={session.get('broker_id', 11)}")
                broker_updated = True
            else:
                new_lines.append(line)

        if not sso_updated:
            new_lines.append(f"POLARIUM_SSO_TOKEN={session['sso_token']}")
        if not broker_updated:
            new_lines.append(f"POLARIUM_BROKER_ID={session.get('broker_id', 11)}")

        with open(env_path, "w") as f:
            f.write("\n".join(new_lines) + "\n")
        log.info(".env updated with captured session")
