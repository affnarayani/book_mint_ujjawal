"""
gemini_vision.py
Gemini Vision fallback for Playwright locators.

When a primary locator fails (not found / not actionable), this module takes a
screenshot of the current page, asks Gemini (gemini-3.5-flash-lite) for the pixel
coordinates of the element described by the failing locator, clicks at those
coordinates and then probes the DOM element at that point to build a reusable
Playwright locator.

The returned locator can then be used exactly like the original one
(click / fill / read text / etc.).
"""

import base64
import json
import os
import re
from functools import lru_cache
from typing import Optional

import requests

from common.common import (
    GEMINI_CFG,
    LocatorChain,
    record_broken_locator,
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
GEMINI_MODEL = GEMINI_CFG.get("model", "gemini-3.5-flash-lite")
GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

# JS that finds the most specific, stable selector for the element at (x, y).
_PROBE_JS = """
([x, y]) => {
    const el = document.elementFromPoint(x, y);
    if (!el) return null;

    const escape = (v) => (window.CSS && CSS.escape) ? CSS.escape(v) : String(v).replace(/[^a-zA-Z0-9_-]/g, '\\\\$&');
    const cssPath = (node) => {
        const parts = [];
        let cur = node;
        while (cur && cur.nodeType === 1 && parts.length < 6) {
            let sel = cur.nodeName.toLowerCase();
            if (cur.id) {
                sel += '#' + escape(cur.id);
                parts.unshift(sel);
                break;
            }
            const parent = cur.parentNode;
            if (parent) {
                const sameTag = Array.from(parent.children).filter(c => c.nodeName === cur.nodeName);
                if (sameTag.length > 1) {
                    const idx = sameTag.indexOf(cur) + 1;
                    sel += `:nth-of-type(${idx})`;
                }
            }
            parts.unshift(sel);
            cur = cur.parentElement;
        }
        return parts.join(' > ');
    };

    const result = {
        tag: el.tagName.toLowerCase(),
        id: el.id || null,
        testId: el.getAttribute('data-testid') || null,
        name: el.getAttribute('name') || null,
        type: el.getAttribute('type') || null,
        role: el.getAttribute('role') || null,
        ariaLabel: el.getAttribute('aria-label') || null,
        placeholder: el.getAttribute('placeholder') || null,
        value: (el.value || '').slice(0, 80),
        text: (el.innerText || el.textContent || '').trim().slice(0, 80),
        cssPath: cssPath(el)
    };

    if (result.testId) result.preferred = `[data-testid="${result.testId}"]`;
    else if (result.id) result.preferred = '#' + escape(result.id);
    else result.preferred = result.cssPath;

    return result;
}
"""


@lru_cache(maxsize=1)
def _client_configured() -> bool:
    return bool(GEMINI_API_KEY)


def _extract_coordinates(text: str) -> Optional[tuple]:
    """Parse the first (x, y) pair out of the model response."""
    if not text:
        return None

    match = re.search(r'"?x"?\s*[:=]\s*(-?\d+).{0,40}?"?y"?\s*[:=]\s*(-?\d+)', text, re.DOTALL)
    if match:
        return int(match.group(1)), int(match.group(2))

    match = re.search(r'[\(\[]\s*(-?\d+)\s*,\s*(-?\d+)\s*[\)\]]', text)
    if match:
        return int(match.group(1)), int(match.group(2))

    match = re.search(r'(-?\d+)\s*[,; ]\s*(-?\d+)', text)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def _ask_gemini_for_coordinates(screenshot_b64: str, description: str) -> Optional[tuple]:
    """Ask Gemini Vision for pixel coordinates of the described element."""
    if not _client_configured():
        print("[GEMINI] No API key configured; vision fallback skipped.", flush=True)
        return None

    prompt = (
        "You are a UI automation assistant. The attached image is a screenshot of a web page. "
        f"Find the element described as: '{description}'.\n"
        "Return ONLY the center point of that element in this exact format: x=<int>, y=<int>. "
        "Coordinates must be integers relative to the top-left of the image. "
        "If the element is not visible, respond exactly: NOT_FOUND"
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/png", "data": screenshot_b64}},
                ]
            }
        ],
        "generationConfig": {
            "temperature": GEMINI_CFG.get("temperature", 0.0),
            "maxOutputTokens": GEMINI_CFG.get("max_output_tokens", 512),
        },
    }

    try:
        response = requests.post(
            f"{GEMINI_ENDPOINT}?key={GEMINI_API_KEY}",
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()

        text = ""
        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                if "text" in part:
                    text += part["text"]

        if "NOT_FOUND" in text.upper():
            print(f"[GEMINI] Element not found on page: {description}", flush=True)
            return None

        coords = _extract_coordinates(text)
        if coords:
            print(f"[GEMINI] Resolved '{description}' to coordinates {coords}", flush=True)
        else:
            print(f"[GEMINI] Could not parse coordinates from response: {text!r}", flush=True)
        return coords
    except Exception as exc:
        print(f"[GEMINI] Vision request failed: {exc}", flush=True)
        return None


def _probe_element(page, x: int, y: int):
    """
    Return (locator, selector_expression) for the element at (x, y).

    selector_expression is a clean Playwright selector string (CSS or
    role/name), suitable for rebuilding a page.* expression.
    """
    try:
        info = page.evaluate(_PROBE_JS, [x, y])
    except Exception as exc:
        print(f"[GEMINI] DOM probe failed: {exc}", flush=True)
        return None, None

    if not info:
        return None, None

    preferred = info.get("preferred")
    if preferred:
        locator = page.locator(preferred).first
        try:
            if locator.count() > 0:
                return locator, f'page.locator({preferred!r})'
        except Exception:
            pass

    # Fallback to a role-based locator when available
    role = info.get("role")
    name = info.get("ariaLabel") or info.get("text")
    if role and name:
        locator = page.get_by_role(role, name=name).first
        try:
            if locator.count() > 0:
                return locator, f'page.get_by_role({role!r}, name={name!r})'
        except Exception:
            pass

    return None, None


def vision_resolve(page, description: str):
    """
    Resolve a page element via Gemini Vision fallback.

    Returns a tuple (locator, selector_expression) or (None, None).
    selector_expression is a clean Playwright expression string such as
    ``page.locator("...")`` or ``page.get_by_role("...", name="...")`` that
    can be written back into a locator txt file as a fallback.
    """
    if not GEMINI_CFG.get("enabled", True):
        return None, None

    try:
        # Viewport screenshot (not full_page) so pixel coordinates match the
        # viewport coordinate space used by page.mouse.* and elementFromPoint.
        # A full_page capture returns coordinates that do not line up with the
        # viewport, which is why vision clicks landed on the wrong spot.
        screenshot_bytes = page.screenshot()
    except Exception as exc:
        print(f"[GEMINI] Could not capture screenshot for vision fallback: {exc}", flush=True)
        return None, None

    screenshot_b64 = base64.b64encode(screenshot_bytes).decode("ascii")
    coords = _ask_gemini_for_coordinates(screenshot_b64, description)
    if not coords:
        return None, None

    x, y = coords
    # Hover so any hover-dependent UI settles; do NOT click here. The caller
    # (smart_click / the download flow) performs a single click on the returned
    # locator, otherwise menus and toggles would be double-fired.
    try:
        page.mouse.move(x, y)
        page.wait_for_timeout(200)
    except Exception:
        pass

    return _probe_element(page, x, y)


def describe(locator) -> str:
    """Build a human readable description for a locator, used in vision prompts."""
    try:
        return str(locator)
    except Exception:
        return "target element"


def _as_chain(locator):
    """Return the locator if it is a LocatorChain, else None."""
    return locator if isinstance(locator, LocatorChain) else None


def resolve_locator(page, primary_locator, description: str = None, timeout: int = 5000):
    """
    Return a working locator for the described element.

    Strategy:
      1. If a LocatorChain is given, walk primary -> fallbacks and return the
         first candidate that exists on the page.
      2. Otherwise, if the primary locator already resolves, return it.
      3. If nothing resolves, record the break in changed_locators.json and use
         Gemini Vision fallback to locate and rebuild the element.
    """
    chain = _as_chain(primary_locator)

    if chain is not None:
        resolved = chain.resolve()
        if resolved is not None:
            return resolved
        # All chain candidates failed: record + vision.
        broke = chain.primary_expr
        desc = description or broke
        print(f"[LOCATOR] Chain '{chain.name}' exhausted; using Gemini Vision fallback: {desc}", flush=True)
        vision_locator, selector_expr = vision_resolve(page, desc)
        if vision_locator is not None:
            record_broken_locator(chain.platform, chain.name, broke, selector_expr)
        return vision_locator

    if primary_locator is not None:
        try:
            if primary_locator.count() > 0:
                return primary_locator
        except Exception:
            pass

    desc = description or describe(primary_locator)
    print(f"[LOCATOR] Primary locator failed, using Gemini Vision fallback: {desc}", flush=True)
    locator, _ = vision_resolve(page, desc)
    return locator


def smart_click(page, primary_locator, description: str = None) -> bool:
    """Click a locator, falling back to Gemini Vision when needed."""
    desc = description or describe(primary_locator)
    locator = resolve_locator(page, primary_locator, description)
    if locator is None:
        print(f"[LOCATOR] Could not resolve '{desc}'; click skipped.", flush=True)
        return False
    try:
        locator.first.click(timeout=5000)
        return True
    except Exception as exc:
        print(f"[LOCATOR] Click failed for '{desc}': {exc}", flush=True)
        return False


def smart_fill(page, primary_locator, value: str, description: str = None) -> bool:
    """Fill a locator, falling back to Gemini Vision when needed."""
    locator = resolve_locator(page, primary_locator, description=description)
    if locator is None:
        return False
    try:
        locator.first.fill(value, timeout=5000)
        return True
    except Exception as exc:
        print(f"[LOCATOR] Vision fill failed: {exc}", flush=True)
        return False


def smart_is_visible(page, primary_locator, description: str = None) -> bool:
    """Check visibility, falling back to Gemini Vision when the primary is missing."""
    chain = _as_chain(primary_locator)
    if chain is not None:
        try:
            if chain.resolve() is not None:
                return True
        except Exception:
            pass
    elif primary_locator is not None:
        try:
            if primary_locator.count() > 0 and primary_locator.first.is_visible():
                return True
        except Exception:
            pass
    locator = resolve_locator(page, primary_locator, description)
    if locator is None:
        return False
    try:
        return locator.first.is_visible()
    except Exception:
        return False