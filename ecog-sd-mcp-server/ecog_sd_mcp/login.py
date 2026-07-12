"""Playwright password login for Ecog Atlassian Cloud → fresh Cookie header string.

MFA must be OFF (spec). No CAPTCHA solving — if challenged, raise and let the operator
run headed once or inject ECOG_COOKIE manually. Selectors are current-as-of 2026-07 and
should be verified against the live login page.
"""

from __future__ import annotations

from .auth import _UA, cookies_to_header

_CAPTCHA_MARKERS = ("captcha", "recaptcha", "verify you are human", "unusual traffic")


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
            await page.fill("input#username", settings.ecog_email)
            await page.click("#login-submit")
            await page.wait_for_selector("input#password", timeout=30000)
            if _is_captcha(await page.content()):
                raise RuntimeError(
                    "Ecog login hit a CAPTCHA/verification screen — run once with "
                    "ECOG_HEADLESS=false or inject a fresh ECOG_COOKIE manually."
                )
            await page.fill("input#password", settings.ecog_password)
            await page.click("#login-submit")
            await page.wait_for_url("**ecog.atlassian.net/**", timeout=45000)
            header = cookies_to_header(await ctx.cookies(settings.ecog_base_url))
            if not header:
                raise RuntimeError("Ecog login produced no cookies.")
            return header
        finally:
            await browser.close()
