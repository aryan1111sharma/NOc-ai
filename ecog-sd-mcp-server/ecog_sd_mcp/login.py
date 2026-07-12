"""Playwright password login for Ecog Atlassian Cloud → fresh Cookie header string.

MFA must be OFF (spec). No CAPTCHA solving — if challenged, raise and let the operator
run headed once or inject ECOG_COOKIE manually. Selectors are current-as-of 2026-07 and
should be verified against the live login page.
"""

from __future__ import annotations

import re

from .auth import _UA, cookies_to_header

_CAPTCHA_MARKERS = ("captcha", "recaptcha", "verify you are human", "unusual traffic")
# URL fragments that mean Atlassian interrupted login with a verification/MFA challenge.
_CHALLENGE_URL_MARKERS = ("/login/mfa", "/challenge", "/verify", "/mfa")


def _is_captcha(html: str) -> bool:
    low = html.lower()
    return any(m in low for m in _CAPTCHA_MARKERS)


async def fetch_cookies_via_login(settings) -> str:
    """Drive Atlassian login with email+password; return the ecog.atlassian.net Cookie header."""
    from playwright.async_api import async_playwright  # lazy: only needed in login mode

    if not (settings.ecog_email and settings.ecog_password):
        raise RuntimeError("Ecog auto-login needs ECOG_EMAIL and ECOG_PASSWORD.")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=settings.ecog_headless)
        try:
            ctx = await browser.new_context(user_agent=_UA)
            page = await ctx.new_page()
            await page.goto(settings.ecog_base_url + "/", wait_until="domcontentloaded")
            # id.atlassian.com flow: email -> Continue -> password -> Log in.
            # The email field's id is dynamic (username-uid*), so target it by name; the
            # submit is a text button ("Continue" then "Log in"), not a stable id.
            await page.fill("input[name=username]", settings.ecog_email)
            await page.get_by_role("button", name=re.compile("continue", re.I)).first.click()
            await page.wait_for_selector("input[name=password]", state="visible", timeout=30000)
            await page.fill("input[name=password]", settings.ecog_password)
            await page.get_by_role("button", name=re.compile("log in|continue", re.I)).first.click()
            try:
                await page.wait_for_url("**ecog.atlassian.net/**", timeout=45000)
            except Exception:
                await _raise_if_challenged(page)
                raise
            await _raise_if_challenged(page)   # a challenge can also resolve without a nav error
            header = cookies_to_header(await ctx.cookies(settings.ecog_base_url))
            if not header:
                raise RuntimeError(
                    "Ecog login produced no cookies (likely an unhandled verification step) — "
                    "inject a fresh ECOG_COOKIE from a logged-in browser instead."
                )
            return header
        finally:
            await browser.close()


async def _raise_if_challenged(page) -> None:
    """Raise a clear, actionable error if Atlassian interrupted login with a
    verification / MFA / CAPTCHA challenge (common when logging in from an
    unfamiliar/datacenter IP, even with account MFA off)."""
    url = page.url
    if any(m in url for m in _CHALLENGE_URL_MARKERS) or _is_captcha(await page.content()):
        raise RuntimeError(
            "Ecog auto-login was stopped by Atlassian identity verification "
            f"(challenge at {url.split('?')[0]}). Risk-based verification fires for logins "
            "from unfamiliar IPs (e.g. this container), so headless auto-login can't complete. "
            "Inject a fresh ECOG_COOKIE (copy the 'cookie:' request header from a logged-in "
            "browser) — the tools run fine on that."
        )
