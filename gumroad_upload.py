import base64
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

# =========================
# CONFIG
# =========================
HEADLESS = False  # Set to False to view browser UI directly

GUMROAD_COOKIES_FILE = Path("gumroad/cookies.json.encrypted")
STATUS_FILE = Path("ebook_status.json")
DETAILS_FILE = Path("ebook_details.json")

EBOOK_PDF = Path("ebook/ebook.pdf")
BANNER_PNG = Path("ebook/banner.png")
THUMBNAIL_PNG = Path("ebook/thumbnail.png")

GUMROAD_DASHBOARD_URL = "https://app.gumroad.com/products"

PBKDF2_ITERATIONS = 200_000

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

# =========================
# ENV
# =========================
load_dotenv()

DECRYPT_KEY = os.getenv("DECRYPT_KEY")

if not DECRYPT_KEY:
    raise RuntimeError("❌ DECRYPT_KEY missing in environment variables")


# =========================
# CUSTOM RANDOM WAITS
# =========================
def minor_wait():
    """Wait strategy for essential UI rendering steps (6 to 12 seconds)"""
    seconds = random.uniform(6, 12)
    print(f"[WAIT-MINOR] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)


def major_wait():
    """Wait strategy for major page loads & transitions (30 to 60 seconds)"""
    seconds = random.uniform(30, 60)
    print(f"[WAIT-MAJOR] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)


# =========================
# URL SLUG GENERATOR
# =========================
def generate_custom_url_slug(title: str) -> str:
    """
    Generates a clean URL slug from the full title.
    Removes special characters/punctuation, replaces spaces with hyphens, and converts to lowercase.
    e.g., 'Medium Vault: How Course Funnels Builds $1,000/Month' -> 'medium-vault-how-course-funnels-builds-1000-month'
    """
    cleaned = re.sub(r'[^a-zA-Z0-9\s-]', '', title)
    slug = re.sub(r'[\s-]+', '-', cleaned).strip('-').lower()
    return slug


# =========================
# CRYPTO
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
        raise RuntimeError("❌ Decryption failed (InvalidTag)")


def load_cookies(file_path: Path) -> List[Dict[str, Any]]:
    print(f"[STEP] Loading Gumroad cookies from {file_path.name}...", flush=True)

    with file_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    plaintext = _decrypt_payload(payload, DECRYPT_KEY)
    cookies = json.loads(plaintext.decode("utf-8"))

    # Normalize SameSite and PartitionKey
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

    print("[OK] Gumroad Cookies loaded successfully", flush=True)
    return cookies


# =========================
# STATUS HELPERS
# =========================
def load_status():
    if not STATUS_FILE.exists():
        raise FileNotFoundError(f"❌ '{STATUS_FILE}' not found!")
    with STATUS_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_status(data):
    with STATUS_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_status_value(status_list, key_name):
    for item in status_list:
        if key_name in item:
            return item[key_name]
    return None


def update_status_value(status_list, key_name, new_value):
    updated = False
    for item in status_list:
        if key_name in item:
            item[key_name] = new_value
            updated = True
            break
    if not updated:
        status_list.append({key_name: new_value})


# =========================
# JSON DETAILS LOADER
# =========================
def load_details():
    if not DETAILS_FILE.exists():
        raise FileNotFoundError(f"❌ '{DETAILS_FILE}' not found!")
    with DETAILS_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


# =========================
# MAIN EXECUTION
# =========================
def run():
    print(f"--- Starting Gumroad Publishing Process (HEADLESS={HEADLESS}) ---", flush=True)

    status_data = load_status()
    kdp_uploaded = get_status_value(status_data, "kdp_uploaded")
    gumroad_uploaded = get_status_value(status_data, "gumroad_uploaded")

    # MUST run only when kdp_uploaded is True and gumroad_uploaded is False
    if not kdp_uploaded:
        print("[INFO] 'kdp_uploaded' is not True. Cannot upload to Gumroad yet. Exiting safely.", flush=True)
        sys.exit(0)

    if gumroad_uploaded is True:
        print("[INFO] 'gumroad_uploaded' is already True. Exiting safely.", flush=True)
        sys.exit(0)

    if not EBOOK_PDF.exists():
        raise FileNotFoundError(f"❌ Required manuscript file missing at '{EBOOK_PDF}'!")
    if not BANNER_PNG.exists():
        raise FileNotFoundError(f"❌ Required banner image missing at '{BANNER_PNG}'!")
    if not THUMBNAIL_PNG.exists():
        raise FileNotFoundError(f"❌ Required thumbnail image missing at '{THUMBNAIL_PNG}'!")

    details_data = load_details()

    full_title = get_status_value(status_data, "title") or ""
    description_gumroad = details_data.get("description_gumroad", "")
    custom_url_slug = generate_custom_url_slug(full_title)

    print(f"[INFO] Full Title: '{full_title}'", flush=True)
    print(f"[INFO] Generated Custom URL Slug: '{custom_url_slug}'", flush=True)

    cookies = load_cookies(GUMROAD_COOKIES_FILE)

    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()

    browser = None
    try:
        browser = pw.chromium.launch(
            headless=HEADLESS,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
        )

        context = browser.new_context(no_viewport=True, user_agent=USER_AGENT)
        context.grant_permissions(["clipboard-read", "clipboard-write"])

        print("[STEP] Adding cookies to browser context...", flush=True)
        context.add_cookies(cookies)

        page = context.new_page()
        print("[OK] Cookies added successfully", flush=True)

        print("[STEP] Navigating to Gumroad Dashboard URL...", flush=True)
        page.goto(GUMROAD_DASHBOARD_URL, wait_until="load")
        minor_wait()

        # ---------------------------------------------------------
        # STEP 1: NEW PRODUCT CREATION
        # ---------------------------------------------------------
        print("[STEP] Clicking 'New product' button...", flush=True)
        page.get_by_role("link", name="New product").click()
        minor_wait()

        print(f"[STEP] Filling Name field with complete title: '{full_title}'...", flush=True)
        page.get_by_role("textbox", name="Name").fill(full_title)
        minor_wait()

        print("[STEP] Selecting 'E-book' type radio button...", flush=True)
        page.get_by_role("radio", name="E-book E-book Offer a book or").click()
        minor_wait()

        print("[STEP] Filling Price field ($27)...", flush=True)
        page.get_by_role("textbox", name="Price").fill("27")
        minor_wait()

        print("[STEP] Clicking 'Next: Customize' button...", flush=True)
        page.get_by_role("button", name="Next: Customize").click()
        major_wait()

        # ---------------------------------------------------------
        # STEP 2: CUSTOMIZE PRODUCT DETAILS & MEDIA
        # ---------------------------------------------------------
        print("[STEP] Filling Description field from description_gumroad...", flush=True)
        description_box = page.get_by_label("Description")
        description_box.click()
        description_box.fill(description_gumroad)
        minor_wait()

        print(f"[STEP] Filling Custom URL field with slug: '{custom_url_slug}'...", flush=True)
        page.get_by_role("textbox", name="URL").fill(custom_url_slug)
        minor_wait()

        print("[STEP] Uploading Banner / Cover Image (ebook/banner.png)...", flush=True)
        page.get_by_role("button", name="Upload images or videos").click()
        minor_wait()

        with page.expect_file_chooser() as fc_info_banner:
            page.get_by_role("tab", name="Computer files").click()
        file_chooser_banner = fc_info_banner.value
        file_chooser_banner.set_files(str(BANNER_PNG.resolve()))
        print("[OK] Banner PNG uploaded successfully.", flush=True)
        major_wait()

        print("[STEP] Uploading Thumbnail Image (ebook/thumbnail.png)...", flush=True)
        with page.expect_file_chooser() as fc_info_thumb:
            page.get_by_text("Upload").click()
        file_chooser_thumb = fc_info_thumb.value
        file_chooser_thumb.set_files(str(THUMBNAIL_PNG.resolve()))
        print("[OK] Thumbnail PNG uploaded successfully.", flush=True)
        major_wait()

        print("[STEP] Clicking 'Save and continue' button...", flush=True)
        page.get_by_role("button", name="Save and continue").click()
        major_wait()

        # ---------------------------------------------------------
        # STEP 3: CONTENT / FILE UPLOAD
        # ---------------------------------------------------------
        print("[STEP] Uploading eBook PDF File (ebook/ebook.pdf)...", flush=True)
        page.get_by_role("button", name="Upload your files").click()
        minor_wait()

        with page.expect_file_chooser() as fc_info_pdf:
            page.get_by_role("menuitem", name="Computer files").click()
        file_chooser_pdf = fc_info_pdf.value
        file_chooser_pdf.set_files(str(EBOOK_PDF.resolve()))
        print("[OK] eBook PDF uploaded successfully.", flush=True)
        major_wait()

        print("[STEP] Saving Content changes...", flush=True)
        page.get_by_role("button", name="Save changes").click()
        major_wait()

        # ---------------------------------------------------------
        # STEP 4: RECEIPT CONFIGURATION
        # ---------------------------------------------------------
        print("[STEP] Switching to 'Receipt' tab...", flush=True)
        page.get_by_role("tab", name="Receipt").click()
        minor_wait()

        print("[STEP] Filling Button text field ('Download Your Ebook')...", flush=True)
        page.get_by_role("textbox", name="Button text").fill("Download Your Ebook")
        minor_wait()

        custom_message = f"Thank you for purchasing {full_title}."
        print(f"[STEP] Filling Custom message textbox: '{custom_message}'...", flush=True)
        page.get_by_role("textbox", name="Custom message").fill(custom_message)
        minor_wait()

        print("[STEP] Saving Receipt changes...", flush=True)
        page.get_by_role("button", name="Save changes").click()
        major_wait()

        # ---------------------------------------------------------
        # STEP 5: PUBLISH PRODUCT
        # ---------------------------------------------------------
        print("[STEP] Clicking 'Publish and continue' button...", flush=True)
        page.get_by_role("button", name="Publish and continue").click()
        major_wait()

        # ---------------------------------------------------------
        # SUCCESS & STATUS UPDATE
        # ---------------------------------------------------------
        update_status_value(status_data, "gumroad_uploaded", True)
        save_status(status_data)
        print("✅ [OK] Product successfully published to Gumroad & status updated to gumroad_uploaded=True!", flush=True)

    except Exception as e:
        print(f"[ERROR] Exception during Gumroad execution: {e}", flush=True)
        sys.exit(1)

    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        try:
            pw_cm.__exit__(None, None, None)
        except Exception:
            pass
        print("[DONE] Gumroad Script execution finished.", flush=True)


if __name__ == "__main__":
    run()