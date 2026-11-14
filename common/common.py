"""
common.py
Shared configuration, locator loading, waits, crypto, status helpers and
OnlyFiles screenshot upload used across every script in this project.

All locators are loaded from /locators/<platform>_locators.txt so they can be
edited manually without touching Python code.
"""

import ast
import base64
import json
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "json" / "config.json"

with CONFIG_PATH.open("r", encoding="utf-8") as _f:
    CONFIG: Dict[str, Any] = json.load(_f)

BROWSER_CFG = CONFIG["browser"]
PATHS_CFG = CONFIG["paths"]
WAIT_CFG = CONFIG["wait"]
RETRY_CFG = CONFIG["retry"]
GEMINI_CFG = CONFIG["gemini"]
PUBLISH_CFG = CONFIG["publishing"]
CRYPTO_CFG = CONFIG["crypto"]
APP_CFG = CONFIG["app"]
LOCATORS_CFG = CONFIG.get("locators", {})

PBKDF2_ITERATIONS = int(CRYPTO_CFG.get("pbkdf2_iterations", 200_000))
AUTO_CORRECT_LOCATOR = bool(LOCATORS_CFG.get("auto_correct_locator", True))
CHANGED_LOCATORS_FILE = ROOT_DIR / "locators" / "changed_locators.json"


def path(key: str) -> Path:
    """Return an absolute Path for a configured relative path key."""
    return ROOT_DIR / PATHS_CFG[key]


# =========================
# LOCATOR LOADING
# =========================
class Locators:
    """Container for platform locators loaded from a text file."""

    def __init__(self, platform: str, values: Dict[str, Any]):
        self.platform = platform
        self._values = values

    def get(self, name: str, default: Any = None) -> Any:
        return self._values.get(name, default)

    def __getitem__(self, name: str) -> Any:
        return self._values[name]

    def __contains__(self, name: str) -> bool:
        return name in self._values

    def __getattr__(self, name: str) -> Any:
        try:
            return self._values[name]
        except KeyError:
            raise AttributeError(
                f"Locator '{name}' not found in {self.platform}_locators.txt"
            )

    def keys(self):
        return self._values.keys()


class LocatorChain:
    """
    A locator with primary + fallback candidates.

    Behaves like the primary Playwright locator for ordinary calls
    (click / fill / wait_for / or_ / ...), so existing code keeps working.
    Use .resolve() to walk the chain and return the first locator that
    actually exists on the page.
    """

    def __init__(self, platform: str, name: str, primary_expr: str, primary, fallbacks: Optional[List[Any]] = None):
        self.platform = platform
        self.name = name
        self.primary_expr = primary_expr
        self._primary = primary
        self._fallbacks = list(fallbacks or [])

    def resolve(self):
        """Return the first locator in the chain that exists on the page, else None."""
        try:
            if self._primary is not None and self._primary.count() > 0:
                return self._primary
        except Exception:
            pass
        for fb in self._fallbacks:
            try:
                if fb.count() > 0:
                    return fb
            except Exception:
                continue
        return None

    def candidates(self) -> List[Any]:
        """All locator candidates in priority order (primary first)."""
        out = []
        if self._primary is not None:
            out.append(self._primary)
        out.extend(self._fallbacks)
        return out

    def __getattr__(self, name: str) -> Any:
        # Delegate transparently to the primary locator so that
        # loc.<name>.click() / .fill() / .wait_for() / .or_() keep working.
        return getattr(self._primary, name)

    def __str__(self) -> str:
        return self.primary_expr


def _extract_balanced(text: str, start: int) -> str:
    """Return the substring from a '[' up to its matching ']'."""
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


# =========================
# CHANGED LOCATORS JSON (SELF-HEALING)
# =========================
def load_changed_locators() -> List[Dict[str, Any]]:
    """Load the changed_locators.json file (a top-level list of entries)."""
    if not CHANGED_LOCATORS_FILE.exists():
        return []
    try:
        with CHANGED_LOCATORS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("entries"), list):
            return data["entries"]
        return []
    except Exception as exc:
        print(f"[WARN] Could not read {CHANGED_LOCATORS_FILE.name}: {exc}", flush=True)
        return []


def _atomic_write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


def save_changed_locators(entries: List[Dict[str, Any]]) -> None:
    """Persist the changed_locators.json file atomically (top-level list)."""
    CHANGED_LOCATORS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(CHANGED_LOCATORS_FILE, entries)


def _is_id_selector(expr: str) -> bool:
    """
    True if expr targets an element by a raw CSS id selector ('#...').

    Such selectors (e.g. page.locator('#radix-_r_8o_')) are auto-generated and
    unstable, so they must never be recorded as suggested locators.
    """
    if not expr:
        return False
    s = expr.strip()
    if s.startswith("page.locator(") and s.endswith(")"):
        inner = s[len("page.locator("):-1].strip()
        if (inner.startswith('"') and inner.endswith('"')) or (
            inner.startswith("'") and inner.endswith("'")
        ):
            inner = inner[1:-1].strip()
        return inner.startswith("#")
    return s.startswith("#")


def record_broken_locator(platform: str, name: str, broke_locator: str, suggested_locator: str) -> None:
    """
    Immediately append/update a broken-locator entry in changed_locators.json.

    Entry shape:
        {
          "platform": "kdp",
          "name": "kdp_title_input",
          "broke_locator": "page.locator('#data-title')",
          "suggested_locator": "page.get_by_role('textbox', name='Title')",
          "detected_at": "2026-11-14T08:00:00Z"
        }
    """
    if not suggested_locator or _is_id_selector(suggested_locator):
        print(
            f"[LOCATOR] Skipping suggestion for '{name}' on '{platform}': "
            f"id-based selectors are unstable ({suggested_locator!r}).",
            flush=True,
        )
        return

    entries = load_changed_locators()
    now = datetime.now(timezone.utc).isoformat()
    found = False
    for entry in entries:
        if entry.get("platform") == platform and entry.get("name") == name:
            entry["broke_locator"] = broke_locator
            entry["suggested_locator"] = suggested_locator
            entry["detected_at"] = now
            found = True
            break
    if not found:
        entries.append({
            "platform": platform,
            "name": name,
            "broke_locator": broke_locator,
            "suggested_locator": suggested_locator,
            "detected_at": now,
        })
    save_changed_locators(entries)
    print(f"[LOCATOR] Recorded broken locator '{name}' on '{platform}' -> {CHANGED_LOCATORS_FILE.name}", flush=True)


def _parse_locator_expr(expr: str, eval_ns: Dict[str, Any]):
    """Evaluate a single locator expression (or return a plain string/list)."""
    if expr.startswith("[") and expr.endswith("]"):
        try:
            return ast.literal_eval(expr)
        except (ValueError, SyntaxError):
            return eval(expr, eval_ns)  # noqa: S307 - trusted local file
    if (expr.startswith('"') and expr.endswith('"')) or (expr.startswith("'") and expr.endswith("'")):
        try:
            return ast.literal_eval(expr)
        except (ValueError, SyntaxError):
            return expr
    return eval(expr, eval_ns)  # noqa: S307 - trusted local file


def _normalize_locator_expr(expr: str) -> str:
    """Normalize a locator expression for tolerant comparison."""
    s = expr.strip()
    # Strip a surrounding page.locator("...") / page.locator('...') wrapper.
    if s.startswith("page.locator(") and s.endswith(")"):
        inner = s[len("page.locator("):-1].strip()
        if (inner.startswith('"') and inner.endswith('"')) or (
            inner.startswith("'") and inner.endswith("'")
        ):
            s = inner[1:-1]
    # Collapse internal whitespace.
    return " ".join(s.split())


def _file_contains_locator_expr(locators_file: Path, expr: str) -> bool:
    """
    True if the locator expression already appears in the txt file
    (as a primary assignment value, a duplicate assignment value, or a
    '# fallback:' line). Comparison is tolerant of an optional
    ``page.locator("...")`` wrapper and surrounding whitespace, so a bare
    CSS selector recorded in changed_locators.json matches a
    ``page.locator("...")`` line written to the txt file.
    """
    try:
        lines = locators_file.read_text(encoding="utf-8").splitlines()
    except Exception:
        return False

    target = _normalize_locator_expr(expr)
    if not target:
        return False
    for line in lines:
        stripped = line.strip()
        candidate = None
        if stripped.startswith("# fallback:"):
            candidate = stripped[len("# fallback:"):]
        elif "=" in stripped and not stripped.startswith("#"):
            candidate = stripped.split("=", 1)[1]
        if candidate is None:
            continue
        if _normalize_locator_expr(candidate) == target:
            return True
    return False


def _looks_like_playwright_expr(expr: str) -> bool:
    """
    True if expr is already a Playwright expression (page.locator(...) /
    page.get_by_* / page.frame_locator ...), as opposed to a bare CSS
    selector like 'div > div > ...' which must be wrapped in
    page.locator(...) before it can be written to a locator txt file.
    """
    s = expr.strip()
    if not s:
        return False
    # Common Playwright entry points.
    for prefix in (
        "page.locator(",
        "page.get_by_",
        "page.frame_locator(",
        "page.get_by_test_id(",
        "page.get_by_role(",
        "page.get_by_text(",
        "page.get_by_label(",
        "page.get_by_placeholder(",
    ):
        if s.startswith(prefix):
            return True
    return False


def _is_valid_expr(expr: str) -> bool:
    """True if expr is a syntactically valid Python expression."""
    try:
        ast.parse(expr, mode="eval")
        return True
    except SyntaxError:
        return False


def _append_fallback_to_file(locators_file: Path, name: str, fallback_expr: str) -> bool:
    """
    Append a fallback as a duplicate assignment line:

        name = <fallback_expr>

    The loader treats the first ``name = ...`` as the primary and every
    subsequent ``name = ...`` as a fallback, so no special marker is needed.
    The new line is inserted right after the last existing assignment for
    ``name`` (or appended at the end if none exists).

    Returns True if the fallback was written, False if it was skipped
    (e.g. invalid syntax).
    """
    if not _is_valid_expr(fallback_expr):
        print(
            f"[WARN] Skipping invalid fallback for '{name}': {fallback_expr!r} is not a valid expression.",
            flush=True,
        )
        return False

    lines = locators_file.read_text(encoding="utf-8").splitlines()
    out: List[str] = []
    last_assignment_idx = -1
    for idx, line in enumerate(lines):
        out.append(line)
        stripped = line.strip()
        if stripped.startswith(f"{name} =") and not stripped.startswith("#"):
            last_assignment_idx = idx

    if last_assignment_idx == -1:
        out.append(f"{name} = {fallback_expr}")
    else:
        out.insert(last_assignment_idx + 1, f"{name} = {fallback_expr}")

    locators_file.write_text("\n".join(out) + "\n", encoding="utf-8")
    return True


def apply_auto_correct() -> None:
    """
    Process changed_locators.json at startup.

    If auto_correct_locator is ON:
      - For each entry, if the suggested locator is already present in the
        platform's txt file (primary or fallback), just drop the entry.
      - Otherwise append it as a '# fallback:' line under the primary, then
        drop the entry.
    If OFF:
      - Drop the entry only if the suggested locator already exists in the txt.
      - Otherwise keep it (developer must fix manually).
    """
    entries = load_changed_locators()
    if not entries:
        return

    remaining: List[Dict[str, Any]] = []
    for entry in entries:
        platform = entry.get("platform")
        name = entry.get("name")
        suggested = entry.get("suggested_locator")
        if not platform or not name or not suggested:
            continue

        # Never apply an unstable id-based suggestion (e.g. '#radix-...').
        if _is_id_selector(suggested):
            print(
                f"[AUTO-CORRECT] Dropping id-based suggestion for '{name}' on '{platform}': {suggested}",
                flush=True,
            )
            continue

        locators_file = ROOT_DIR / "locators" / f"{platform}_locators.txt"
        if not locators_file.exists():
            print(f"[WARN] Locator file missing for platform '{platform}': {locators_file}", flush=True)
            remaining.append(entry)
            continue

        already_present = _file_contains_locator_expr(locators_file, suggested)

        if AUTO_CORRECT_LOCATOR:
            if already_present:
                # Entry is dropped once processed (suggestion already in txt).
                continue
            # Wrap a bare CSS selector in page.locator(...) so it is a valid
            # Playwright expression before writing it to the txt file.
            candidate = suggested
            if not _looks_like_playwright_expr(candidate):
                candidate = f"page.locator({suggested!r})"
            if _append_fallback_to_file(locators_file, name, candidate):
                print(f"[AUTO-CORRECT] Appended fallback for '{name}' on '{platform}': {candidate}", flush=True)
                # Drop the entry only when the fallback was successfully written.
                continue
            # If the append failed (invalid syntax), keep the entry for manual review.
            remaining.append(entry)
        else:
            if not already_present:
                # Keep it so the developer sees it still needs a manual fix.
                remaining.append(entry)

    save_changed_locators(remaining)


def load_locators(platform: str, page) -> Locators:
    """
    Load locators for a platform from /locators/<platform>_locators.txt.

    Supported line forms:
        name = <playwright expression using page / re>
        name = ["selector1", "selector2", ...]
        name = "plain string value"
        name = <expr>
        # fallback: <playwright expression using page / re>   (legacy form)

    If a ``name`` is assigned more than once, the FIRST assignment becomes the
    primary locator and every subsequent assignment for that name becomes a
    fallback. This lets auto-correct append new fallbacks as plain
    ``name = ...`` lines. Legacy ``# fallback:`` lines are still honored.
    Auto-correct runs once here at startup.
    """
    apply_auto_correct()

    locators_file = ROOT_DIR / "locators" / f"{platform}_locators.txt"
    if not locators_file.exists():
        raise FileNotFoundError(f"Locator file not found: {locators_file}")

    eval_ns: Dict[str, Any] = {"page": page, "re": re}
    values: Dict[str, Any] = {}
    # Collect every assignment expression per name, in file order.
    assignments: Dict[str, List[str]] = {}
    # Legacy '# fallback:' lines are attributed to the most recent primary name.
    fallback_exprs: Dict[str, List[str]] = {}
    current_name: Optional[str] = None

    with locators_file.open("r", encoding="utf-8") as f:
        raw_lines = f.readlines()

    i = 0
    while i < len(raw_lines):
        line = raw_lines[i].strip()
        i += 1

        if not line:
            continue

        if line.startswith("# fallback:"):
            expr = line[len("# fallback:"):].strip()
            if current_name and expr:
                fallback_exprs.setdefault(current_name, []).append(expr)
            continue

        if line.startswith("#"):
            continue
        if "=" not in line:
            continue

        name, expr = line.split("=", 1)
        name = name.strip()
        expr = expr.strip()

        if not name.isidentifier():
            raise ValueError(f"Invalid locator name '{name}' in {locators_file.name}")

        # Multi-line list support: keep appending lines until brackets AND quotes balance
        if expr.startswith("[") and (expr.count("[") > expr.count("]") or expr.count('"') % 2 != 0):
            buffer = expr
            while i < len(raw_lines) and (
                buffer.count("[") > buffer.count("]")
                or buffer.count('"') % 2 != 0
            ):
                buffer += " " + raw_lines[i].strip()
                i += 1
            expr = buffer

        assignments.setdefault(name, []).append(expr)
        current_name = name

    # Build the final values: first assignment = primary, rest = fallbacks.
    for name, exprs in assignments.items():
        primary_value = _parse_locator_expr(exprs[0], eval_ns)

        if not _is_playwright_locator(primary_value):
            # Plain string / list: keep as-is (no chain).
            values[name] = primary_value
            continue

        fallbacks = []
        for fexpr in exprs[1:]:
            try:
                fallbacks.append(_parse_locator_expr(fexpr, eval_ns))
            except Exception as exc:
                print(f"[WARN] Could not evaluate fallback '{fexpr}' for '{name}': {exc}", flush=True)

        # Legacy '# fallback:' lines appended after the primary.
        for fexpr in fallback_exprs.get(name, []):
            try:
                fallbacks.append(_parse_locator_expr(fexpr, eval_ns))
            except Exception as exc:
                print(f"[WARN] Could not evaluate fallback '{fexpr}' for '{name}': {exc}", flush=True)

        values[name] = LocatorChain(platform, name, exprs[0], primary_value, fallbacks)

    return Locators(platform, values)


def _is_playwright_locator(value: Any) -> bool:
    """Heuristic: does this object look like a Playwright locator?"""
    if value is None:
        return False
    # Locators expose .count and .click / .fill
    return hasattr(value, "count") and (hasattr(value, "click") or hasattr(value, "fill"))


# =========================
# WAITS
# =========================
def _sleep(seconds: float, label: str) -> None:
    print(f"[WAIT-{label}] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)


def minor_wait() -> None:
    """Wait strategy for essential UI rendering steps."""
    _sleep(random.uniform(WAIT_CFG["minor_min"], WAIT_CFG["minor_max"]), "MINOR")


def major_wait() -> None:
    """Wait strategy for major page loads and transitions."""
    _sleep(random.uniform(WAIT_CFG["major_min"], WAIT_CFG["major_max"]), "MAJOR")


def closing_wait() -> None:
    """Wait strategy applied right before the browser closes."""
    _sleep(random.uniform(WAIT_CFG["closing_min"], WAIT_CFG["closing_max"]), "CLOSING")


def custom_random_wait(min_sec: float = None, max_sec: float = None) -> None:
    """Generic bounded random wait."""
    lo = WAIT_CFG["minor_min"] if min_sec is None else min_sec
    hi = WAIT_CFG["minor_max"] if max_sec is None else max_sec
    _sleep(random.uniform(lo, hi), "WAIT")


# =========================
# CRYPTO (COOKIE DECRYPTION)
# =========================
def _derive_key(password: bytes, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(password)


def _decrypt_payload(payload: Dict[str, Any], password: str) -> bytes:
    salt = base64.b64decode(payload["s"])
    nonce = base64.b64decode(payload["n"])
    ciphertext = base64.b64decode(payload["ct"])

    key = _derive_key(password.encode("utf-8"), salt)
    aesgcm = AESGCM(key)

    try:
        return aesgcm.decrypt(nonce, ciphertext, None)
    except InvalidTag:
        raise RuntimeError("Decryption failed (InvalidTag)")


def _normalize_cookies(cookies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for c in cookies:
        if "partitionKey" in c and isinstance(c["partitionKey"], dict):
            if "topLevelSite" in c["partitionKey"]:
                c["partitionKey"] = str(c["partitionKey"]["topLevelSite"])
            else:
                del c["partitionKey"]

        if "sameSite" in c:
            val = str(c["sameSite"]).lower()
            if val in ["no_restriction", "none", "unspecified", "null"]:
                c["sameSite"] = "None"
            elif val == "lax":
                c["sameSite"] = "Lax"
            elif val == "strict":
                c["sameSite"] = "Strict"
            else:
                c["sameSite"] = "Lax"
    return cookies


def require_decrypt_key() -> str:
    key = os.getenv("DECRYPT_KEY")
    if not key:
        raise RuntimeError("DECRYPT_KEY missing in environment variables")
    return key


def load_cookies(file_path: Path) -> List[Dict[str, Any]]:
    """Load and decrypt a cookies.json.encrypted file."""
    decrypt_key = require_decrypt_key()
    print(f"[STEP] Loading and decrypting cookies from {file_path}...", flush=True)

    if not file_path.exists():
        raise FileNotFoundError(f"Encrypted cookie file not found: {file_path}")

    with file_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    plaintext = _decrypt_payload(payload, decrypt_key)
    cookies = json.loads(plaintext.decode("utf-8"))

    if isinstance(cookies, dict):
        if "cookies" in cookies and isinstance(cookies["cookies"], list):
            cookies = cookies["cookies"]
        else:
            cookies = [cookies]

    cookies = _normalize_cookies(cookies)
    print("[OK] Cookies loaded successfully", flush=True)
    return cookies


# =========================
# STATUS HELPERS
# =========================
def load_status(status_file: Path):
    if not status_file.exists():
        raise FileNotFoundError(f"'{status_file}' not found!")
    with status_file.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_status(status_file: Path, data) -> None:
    with status_file.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_status_value(status_list, key_name):
    for item in status_list:
        if key_name in item:
            return item[key_name]
    return None


def update_status_value(status_list, key_name, new_value) -> None:
    for item in status_list:
        if key_name in item:
            item[key_name] = new_value
            return
    status_list.append({key_name: new_value})


# =========================
# JSON DETAILS LOADER
# =========================
def load_details(details_file: Path):
    if not details_file.exists():
        raise FileNotFoundError(f"'{details_file}' not found!")
    with details_file.open("r", encoding="utf-8") as f:
        return json.load(f)


# =========================
# SCREENSHOT + ONLYFILES
# =========================
def capture_error_screenshot(page) -> Path:
    """Capture an error screenshot to /error/error_screenshot.png (replacing any existing file)."""
    error_path = ROOT_DIR / PATHS_CFG["error_screenshot"]
    error_path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(error_path), full_page=True)
    print(f"[OK] Error screenshot captured: {error_path}", flush=True)
    return error_path


def upload_to_onlyfiles(screenshot_path) -> str:
    """Upload a screenshot to OnlyFiles and return a direct link (expires in 48 hours)."""
    url = "https://api.onlyfiles.com/v1/upload"
    expire_seconds = 172800  # 48 hours

    try:
        with open(screenshot_path, "rb") as file:
            response = requests.post(url, files={"file": file}, data={"expire": expire_seconds})

        if response.status_code == 200:
            res_data = response.json()
            if res_data.get("status"):
                direct_url = res_data["data"]["file"]["url"]["full"]
                print(f"DIRECT LINK (Expires in 48 Hours): {direct_url}", flush=True)
                return direct_url
            error_msg = res_data.get("error", {}).get("message", "Unknown error")
            print(f"[WARNING] Upload Failed: {error_msg}", flush=True)
            return None
        print(f"[WARNING] Upload Failed: {response.status_code}", flush=True)
        return None
    except Exception as upload_err:
        print(f"[WARNING] Could not upload screenshot to OnlyFiles: {upload_err}", flush=True)
        return None


def report_error_and_upload(page) -> None:
    """Capture the error screenshot, then upload it to OnlyFiles."""
    if page is None:
        return
    try:
        screenshot_path = capture_error_screenshot(page)
        upload_to_onlyfiles(screenshot_path)
    except Exception as screenshot_err:
        print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)