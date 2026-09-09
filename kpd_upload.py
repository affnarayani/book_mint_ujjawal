import base64
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import requests
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

KDP_COOKIES_FILE = Path("kdp/cookies.json.encrypted")
STATUS_FILE = Path("ebook_status.json")
DETAILS_FILE = Path("ebook_details.json")

MANUSCRIPT_PDF = Path("ebook/ebook.pdf")
COVER_PNG = Path("ebook/cover.jpg")

BOOKSHELF_URL = "https://kdp.amazon.com/en_US/bookshelf?ref_=kdp_kdp_TAC_TN_bs"

PBKDF2_ITERATIONS = 200_000

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

# =========================
# ENV
# =========================
load_dotenv()

DECRYPT_KEY = os.getenv("DECRYPT_KEY")
KDP_PASSWORD = os.getenv("PASSWORD")

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
# MARKDOWN TO KDP HTML CONVERTER
# =========================
def markdown_to_kdp_html(text: str) -> str:
    """
    Converts Markdown formatted description text into KDP-supported HTML tags.
    Handles Headings, Bold text, Unordered Lists, Paragraphs, and Line Breaks.
    """
    if not text:
        return ""

    # Convert Headings
    text = re.sub(r'^###\s+(.*?)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^##\s+(.*?)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
    text = re.sub(r'^#\s+(.*?)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)

    # Convert Bold Text
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)

    # Convert Unordered Lists (- item or * item)
    lines = text.split('\n')
    in_list = False
    formatted_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith('- ') or stripped.startswith('* '):
            if not in_list:
                formatted_lines.append('<ul>')
                in_list = True
            item_text = stripped[2:].strip()
            formatted_lines.append(f'<li>{item_text}</li>')
        else:
            if in_list:
                formatted_lines.append('</ul>')
                in_list = False
            formatted_lines.append(line)

    if in_list:
        formatted_lines.append('</ul>')

    text = '\n'.join(formatted_lines)

    # Convert Paragraphs / Double line breaks to <p> tags
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    processed_paragraphs = []

    for p in paragraphs:
        if p.startswith('<h2>') or p.startswith('<h3>') or p.startswith('<ul>'):
            processed_paragraphs.append(p)
        else:
            p_formatted = p.replace('\n', '<br>')
            processed_paragraphs.append(f'<p>{p_formatted}</p>')

    return "".join(processed_paragraphs)


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
    print(f"[STEP] Loading KDP cookies from {file_path.name}...", flush=True)

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

    print("[OK] KDP Cookies loaded successfully", flush=True)
    return cookies


def upload_to_tmpfiles(screenshot_path):
    url = "https://tmpfiles.org/api/v1/upload"

    with open(screenshot_path, "rb") as file:
        response = requests.post(url, files={"file": file})

    if response.status_code == 200:
        res_data = response.json()
        # Direct view URL banane ke liye '/dl/' replace karte hain
        page_url = res_data["data"]["url"]
        direct_url = page_url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
        print(f"👉 DIRECT LINK (Expires in 2 Hours): {direct_url}")
        return direct_url
    else:
        print(f"[WARNING] Upload Failed: {response.status_code}")
        return None


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
    print(f"--- Starting KDP Publishing Process (HEADLESS={HEADLESS}) ---", flush=True)

    status_data = load_status()
    thumbnail_generated = get_status_value(status_data, "thumbnail_generated")
    kdp_uploaded = get_status_value(status_data, "kdp_uploaded")

    if not thumbnail_generated:
        print("[INFO] 'thumbnail_generated' is not True. Cannot upload to KDP yet. Exiting safely.", flush=True)
        sys.exit(0)

    if kdp_uploaded is True:
        print("[INFO] 'kdp_uploaded' is already True. Exiting safely.", flush=True)
        sys.exit(0)

    if not MANUSCRIPT_PDF.exists():
        raise FileNotFoundError(f"❌ Required manuscript file missing at '{MANUSCRIPT_PDF}'!")
    if not COVER_PNG.exists():
        raise FileNotFoundError(f"❌ Required cover image missing at '{COVER_PNG}'!")

    details_data = load_details()

    raw_title = get_status_value(status_data, "title") or ""
    if ":" in raw_title:
        title_text, subtitle_text = [part.strip() for part in raw_title.split(":", 1)]
    else:
        title_text = raw_title.strip()
        subtitle_text = ""

    raw_description = details_data.get("description_kdp", "")
    description_kdp_html = markdown_to_kdp_html(raw_description)

    subcategories = details_data.get("subcategories", [])

    raw_keywords = details_data.get("keywords", [])
    keywords = [kw.title() for kw in raw_keywords]

    cookies = load_cookies(KDP_COOKIES_FILE)

    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()

    browser = None
    page = None
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

        print("[STEP] Navigating to KDP Bookshelf URL...", flush=True)
        page.goto(BOOKSHELF_URL, wait_until="load")
        minor_wait()

        # ---------------------------------------------------------
        # PAGE 1: EBOOK DETAILS
        # ---------------------------------------------------------
        print("[STEP] Clicking '+ Create new title or series'...", flush=True)
        page.get_by_role("link", name="+ Create new title or series").click()
        minor_wait()

        print("[STEP] Clicking 'Create eBook'...", flush=True)
        page.get_by_role("button", name="Create eBook").click()
        minor_wait()

        # Password Verification Prompt Handling
        password_field = page.get_by_role("textbox", name="Password")
        if password_field.is_visible():
            print("[SECURITY] Password verification prompt detected!", flush=True)
            if not KDP_PASSWORD:
                raise RuntimeError("❌ Password prompt appeared, but 'PASSWORD' is not set in environment variables!")

            print("[STEP] Entering KDP password from environment...", flush=True)
            password_field.fill(KDP_PASSWORD)
            minor_wait()

            print("[STEP] Clicking 'Sign in'...", flush=True)
            page.get_by_role("button", name="Sign in", exact=True).click()

            major_wait()

            confirm_button = page.locator('input[aria-labelledby="pow-confirm-button-announce"]')
            if confirm_button.is_visible():
                print("[SECURITY] Confirmation prompt detected! Clicking confirm button...", flush=True)
                confirm_button.click()
                minor_wait()

        print("[STEP] Selecting language using keyboard navigation...", flush=True)
        page.get_by_role("button", name="language-dropdown").click()
        minor_wait()

        page.keyboard.press("ArrowDown")
        minor_wait()
        page.keyboard.press("Enter")
        minor_wait()

        print(f"[STEP] Filling eBook Title: '{title_text}'...", flush=True)
        page.locator("#data-title").fill(title_text)
        minor_wait()

        if subtitle_text:
            print(f"[STEP] Filling eBook Subtitle: '{subtitle_text}'...", flush=True)
            page.locator("#data-subtitle").fill(subtitle_text)
            minor_wait()

        print("[STEP] Filling Author Name (Ujjawal Kumar)...", flush=True)
        page.locator("#data-primary-author-first-name").fill("Ujjawal")
        minor_wait()
        page.locator("#data-primary-author-last-name").fill("Kumar")
        minor_wait()

        # ---------------------------------------------------------
        # PASTE VIA CLIPBOARD & REFRESH WORD COUNT
        # ---------------------------------------------------------
        print("[STEP] Pasting HTML Description into Rich Text Editor...", flush=True)

        editor_body = page.frame_locator('iframe[title="Rich Text Editor, editor1"]').locator("body")
        editor_body.click()
        minor_wait()

        editor_body.evaluate("(el) => el.innerHTML = ''")
        minor_wait()

        page.evaluate("""(htmlContent) => {
            const blob = new Blob([htmlContent], { type: 'text/html' });
            const data = [new ClipboardItem({ 'text/html': blob })];
            navigator.clipboard.write(data);
        }""", description_kdp_html)
        minor_wait()

        page.keyboard.press("Control+v")
        minor_wait()

        print("[STEP] Refreshing word count for KDP validation...", flush=True)
        page.keyboard.press("Space")
        time.sleep(0.5)
        page.keyboard.press("Backspace")
        minor_wait()

        editor_body.evaluate("""(el) => {
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'a' }));
        }""")

        page.evaluate("""() => {
            if (window.CKEDITOR && window.CKEDITOR.instances.editor1) {
                window.CKEDITOR.instances.editor1.fire('change');
                window.CKEDITOR.instances.editor1.updateElement();
            }
        }""")

        print("[OK] Description pasted and word count successfully synced.", flush=True)
        minor_wait()

        print("[STEP] Setting Publishing Rights...", flush=True)
        page.get_by_role("radio", name="I own the copyright and I").click()
        minor_wait()

        print("[STEP] Setting Sexually Explicit status...", flush=True)
        page.get_by_role("radio", name="No").click()
        minor_wait()

        print("[STEP] Selecting Primary Marketplace (Amazon.com)...", flush=True)
        page.locator("#data-digital-home-marketplace-home span").nth(1).click()
        minor_wait()
        page.get_by_role("option", name="Amazon.com", exact=True).click()
        minor_wait()

        print("[STEP] Choosing Categories...", flush=True)
        page.get_by_role("button", name="Choose categories").click()
        minor_wait()

        page.locator("span").filter(has_text="Select one").nth(2).click()
        minor_wait()

        print("[STEP] Scrolling drop-down list and selecting category by XPath...", flush=True)
        scroll_container = page.locator(".a-popover-inner.a-lgtbox-vertical-scroll")
        scroll_container.wait_for(state="visible", timeout=10000)

        category_target = page.locator("xpath=/html[1]/body[1]/div[7]/div[1]/div[1]/ul[1]/li[29]/a[1]")
        category_target.scroll_into_view_if_needed()
        minor_wait()
        category_target.click()
        minor_wait()

        for cat in subcategories:
            print(f"[STEP] Selecting Placement Category: '{cat}'...", flush=True)
            page.get_by_text(cat, exact=True).click()
            minor_wait()

        page.get_by_role("button", name="Save categories").click()
        minor_wait()

        # ---------------------------------------------------------
        # FILLING KEYWORDS IN TITLE CASE
        # ---------------------------------------------------------
        print("[STEP] Filling Title-Cased Keywords...", flush=True)
        for i, kw in enumerate(keywords[:7]):
            print(f" -> Keyword {i}: '{kw}'", flush=True)
            kw_locator = page.locator(f"#data-keywords-{i}")
            kw_locator.click()
            kw_locator.press_sequentially(kw, delay=random.randint(20, 50))
            minor_wait()

        print("[STEP] Setting Release Status...", flush=True)
        page.get_by_role("button", name="I am ready to release my book").click()
        minor_wait()

        print("[STEP] Saving and Continuing to Content Page...", flush=True)
        page.get_by_role("button", name="Save and Continue").click()
        major_wait()

        # ---------------------------------------------------------
        # PAGE 2: EBOOK CONTENT
        # ---------------------------------------------------------
        print("[STEP] Uploading Manuscript PDF...", flush=True)
        with page.expect_file_chooser() as fc_info_pdf:
            page.get_by_role("button", name="Upload manuscript").click()
        file_chooser_pdf = fc_info_pdf.value
        file_chooser_pdf.set_files(str(MANUSCRIPT_PDF.resolve()))
        print("[OK] Manuscript PDF file attached successfully.", flush=True)
        minor_wait()

        print("[STEP] Clicking 'Continue with PDF'...", flush=True)
        page.get_by_role("button", name="Continue with PDF").click()
        major_wait()

        print("[STEP] Setting DRM option...", flush=True)
        page.get_by_role("radio", name="Yes, apply Digital Rights").click()
        minor_wait()

        # COVER UPLOAD FLOW
        print("[STEP] Uploading Cover Image...", flush=True)
        page.get_by_role("button", name="Upload a cover you already").click()
        minor_wait()

        with page.expect_file_chooser() as fc_info_cover:
            page.get_by_role("button", name="Upload your cover file").click()
        file_chooser_cover = fc_info_cover.value
        file_chooser_cover.set_files(str(COVER_PNG.resolve()))
        print("[OK] Cover PNG file uploaded successfully.", flush=True)
        major_wait()

        print("[STEP] Setting AI Disclosure...", flush=True)
        page.get_by_role("link", name="Yes").click()
        minor_wait()

        # AI Texts
        page.get_by_label("Texts", exact=True).locator("span").nth(2).click()
        minor_wait()
        page.get_by_role("option", name="Some sections, with extensive").click()
        minor_wait()
        page.get_by_placeholder("e.g. ChatGPT").fill("ChatGPT")
        minor_wait()

        # AI Images
        page.get_by_label("Images", exact=True).locator("span").nth(2).click()
        minor_wait()
        page.get_by_role("option", name="One or a few AI-generated images, with minimal or no editing").click()
        minor_wait()
        page.get_by_placeholder("e.g. DALL-E").fill("DALL-E")
        minor_wait()

        # AI Translations
        page.get_by_label("Translations", exact=True).locator("span").nth(2).click()
        minor_wait()
        page.get_by_role("option", name="None").click()
        minor_wait()

        # print("[STEP] Waiting for Quality Check indicator...", flush=True)
        # page.get_by_text("Still running quality check.").wait_for(state="attached", timeout=120000)
        # print("[OK] Quality check process detected.", flush=True)
        # minor_wait()

        print("[STEP] Confirming AI answers...", flush=True)
        page.get_by_role("checkbox", name="By clicking this, I confirm").click()
        minor_wait()

        print("[STEP] Saving and Continuing to Pricing Page...", flush=True)
        page.get_by_role("button", name="Save and Continue").click()
        major_wait()

        # ---------------------------------------------------------
        # PAGE 3: EBOOK PRICING & RIGHTS
        # ---------------------------------------------------------
        print("[STEP] Setting Territories...", flush=True)
        page.get_by_role("button", name="All territories (worldwide").click()
        minor_wait()

        print("[STEP] Setting Royalty Plan (70%)...", flush=True)
        page.locator("label").filter(has_text="70%").locator("i").click()
        minor_wait()

        print("[STEP] Setting Price ($4.99)...", flush=True)
        page.locator('input[name="data[digital][channels][amazon][US][price_vat_inclusive]"]').fill("4.99")
        minor_wait()

        # ---------------------------------------------------------
        # PUBLISH EBOOK (KEEP COMMENTED OUT FOR TESTING)
        # ---------------------------------------------------------
        print("[STEP] Publishing Kindle eBook...", flush=True)
        page.get_by_role("button", name="Publish Your Kindle eBook").click()
        major_wait()
        update_status_value(status_data, "kdp_uploaded", True)
        save_status(status_data)
        print("[OK] Book successfully published to KDP!", flush=True)

    except Exception as e:
        print(f"[ERROR] Exception during KDP execution: {e}", flush=True)
        if page:
            try:
                screenshot_path = "error_screenshot.png"
                page.screenshot(path=screenshot_path, full_page=True)
                print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)

                upload_to_tmpfiles(screenshot_path)
            except Exception as screenshot_err:
                print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
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
        print("[DONE] KDP Script execution finished.", flush=True)


if __name__ == "__main__":
    run()