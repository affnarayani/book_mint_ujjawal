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
from PIL import Image

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

# =========================
# CONFIG
# =========================
HEADLESS = True

COOKIES_DIR = Path("cookies")
encrypted_files = list(COOKIES_DIR.glob("*.encrypted"))

if not encrypted_files:
    raise RuntimeError("❌ No .encrypted cookie files found in 'cookies/' folder")

CHATGPT_COOKIES_FILE = random.choice(encrypted_files)
print(f"[OK] Randomly selected cookie file: {CHATGPT_COOKIES_FILE.name}", flush=True)

STATUS_FILE = Path("ebook_status.json")
DETAILS_FILE = Path("ebook_details.json")

OUTPUT_DIR = Path("ebook")
OUTPUT_DIR.mkdir(exist_ok=True)

PNG_PATH = OUTPUT_DIR / "cover.png"
JPG_PATH = OUTPUT_DIR / "cover.jpg"

PBKDF2_ITERATIONS = 200_000
MAX_RETRIES = 10

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

# =========================
# ENV
# =========================
load_dotenv()

DECRYPT_KEY = os.getenv("DECRYPT_KEY")

if not DECRYPT_KEY:
    raise RuntimeError("DECRYPT_KEY missing")


# =========================
# CUSTOM RANDOM WAITS
# =========================
def minor_wait():
    """Wait strategy for essential UI rendering steps (6 to 12 seconds)"""
    seconds = random.uniform(6, 12)
    print(f"[WAIT-MINOR] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)

def major_wait():
    """Wait strategy for major page loads & image generation loops (15 to 30 seconds)"""
    seconds = random.uniform(30, 60)
    print(f"[WAIT-MAJOR] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)


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
    print("[STEP] Loading cookies...", flush=True)

    with file_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    plaintext = _decrypt_payload(payload, DECRYPT_KEY)
    cookies = json.loads(plaintext.decode("utf-8"))

    # normalize SameSite and PartitionKey
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

    print("[OK] Cookies loaded", flush=True)
    return cookies


# =========================
# EBOOK STATUS HELPERS
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


def clean_markdown(text):
    text = re.sub(r"[#*`\-]", "", text)
    return " ".join(text.split())


def convert_png_to_jpg(png_path, jpg_path):
    print("[STEP] Converting PNG to high-quality JPG using Pillow...", flush=True)
    with Image.open(png_path) as img:
        rgb_img = img.convert("RGB")
        rgb_img.save(jpg_path, "JPEG", quality=95)
    print(f"[OK] JPG cover saved at: {jpg_path}", flush=True)


# =========================
# MAIN EXECUTION
# =========================
def run():
    print(f"--- Starting Cover Generation Process (HEADLESS={HEADLESS}) ---", flush=True)

    status_data = load_status()
    title = get_status_value(status_data, "title")
    details_generated = get_status_value(status_data, "details_generated")
    cover_generated = get_status_value(status_data, "cover_generated")

    # Guard 1: details_generated must be True
    if not details_generated:
        print("[INFO] 'details_generated' is not True. Cannot generate cover yet. Exiting safely.", flush=True)
        sys.exit(0)

    # Guard 2: cover_generated must not be True
    if cover_generated is True:
        print("[INFO] 'cover_generated' is already True. Exiting safely.", flush=True)
        sys.exit(0)

    if not title:
        raise ValueError("❌ 'title' key not found in status file!")

    if not DETAILS_FILE.exists():
        raise FileNotFoundError(f"❌ '{DETAILS_FILE}' not found!")

    with DETAILS_FILE.open("r", encoding="utf-8") as f:
        details_data = json.load(f)

    description_kdp = details_data.get("description_kdp", "")
    clean_desc = clean_markdown(description_kdp)[:300]

    # prompt_text = (
    #     f"Design a bold, modern eBook cover in a structured, landing-page-style layout, "
    #     f"exactly 1024x1536 px, portrait orientation. "
    #     f"Book topic and theme: '{title}' — {clean_desc} "
    #     f"Independently choose a color palette based on color psychology matching this book's specific theme — "
    #     f"a dark or deep background color with one strong accent color derived from the topic's emotional core "
    #     f"(e.g. urgency, ambition, trust, growth — whatever fits), plus a secondary highlight color for callouts. "
    #     f"Layout, top to bottom: "
    #     f"(1) A thin colored banner strip across the top with a small category/series label on the left and "
    #     f"a small badge or tag on the right (e.g. edition, year, or level indicator); "
    #     f"(2) A short uppercase eyebrow label introducing the book's angle; "
    #     f"(3) A large, bold, multi-line title in mixed typography — serif or display font for most words, "
    #     f"with the single most important number or outcome word rendered dramatically larger and in the accent color; "
    #     f"(4) A thin horizontal divider line; "
    #     f"(5) Three short bullet points, each with a small colored dot or icon marker, highlighting distinct key benefits; "
    #     f"(6) A bordered rectangular stat bar divided into three sections, each showing a short number and a small label beneath it; "
    #     f"(7) A solid accent-colored call-to-action bar near the bottom with a short punchy action phrase and an arrow icon; "
    #     f"(8) A slim footer row with a category label on the left and small outlined tag chips on the right. "
    #     f"Title text to render exactly as written, no spelling or wording changes: '{title}'. "
    #     f"Typography: strong visual hierarchy, confident modern fonts, high contrast for legibility at thumbnail size. "
    #     f"Style: premium, high-contrast, flat modern design, clean grid alignment, no stock-photo imagery, no clutter — "
    #     f"pure typography, shapes, and color blocking. "
    #     f"Overall impression: scroll-stopping, high-converting, premium non-fiction Kindle bestseller cover."
    # )

    prompt_text = (
        f"You are an elite eBook cover designer and consumer psychologist. Your covers make people stop "
        f"scrolling and click 'buy' within 2 seconds, purely through psychological trigger design — not templates.\n\n"

        f"BOOK: '{title}'\n"
        f"CONTEXT: {clean_desc}\n\n"

        f"STEP 1 — PSYCHOLOGICAL ANALYSIS (do this silently before designing):\n"
        f"Identify the single core emotional trigger this specific topic evokes in its ideal reader — "
        f"e.g. quiet ambition, status anxiety, relief, rebellion, FOMO, calm confidence, urgency, curiosity, "
        f"aspiration, contrarian pride. Do NOT default to generic 'bold hustle' energy unless the topic actually "
        f"demands it. A book about restraint, quietness, or minimalism should FEEL restrained and minimal on the "
        f"cover — the design philosophy must mirror the book's own message, not contradict it.\n\n"

        f"STEP 2 — COLOR PSYCHOLOGY:\n"
        f"Choose a palette (dark/deep base + one dominant accent + one secondary highlight) derived from the "
        f"emotional trigger you identified — not a default palette. Muted, restrained topics can use desaturated "
        f"or low-contrast-but-premium palettes instead of loud neon accents.\n\n"

        f"STEP 3 — LAYOUT ARCHITECTURE (choose or blend freely, do NOT default to the same structure every time):\n"
        f"Pick whichever composition genuinely fits this topic's psychology — options include (but aren't limited to):\n"
        f"  • Minimalist center-focus: one powerful title moment, huge negative space, single small symbolic icon\n"
        f"  • Editorial/magazine-style: asymmetric grid, small kicker text, pull-quote style secondary line\n"
        f"  • Split-composition: title dominates one zone, a small vector illustration anchors another\n"
        f"  • Stacked-hierarchy: eyebrow + dramatic title + understated proof element, no heavy dividers/boxes\n"
        f"  • Framed/bordered: a restrained single border or corner marks, title as the only real content\n"
        f"Only include banners, stat bars, bullet lists, or CTA bars IF they genuinely strengthen this specific "
        f"topic's psychological pitch — do not force all of them onto every cover. Fewer, more deliberate "
        f"elements usually read as more premium than a fully packed layout.\n\n"

        f"STEP 4 — TITLE TREATMENT:\n"
        f"Render the title exactly as written, no spelling/wording changes: '{title}'. "
        f"Use confident modern typography with clear hierarchy — but let word emphasis (size/weight/color shift) "
        f"be chosen based on which word/phrase carries the emotional punch for THIS topic specifically.\n\n"

        f"STEP 5 — VISUAL ELEMENT (constrained):\n"
        f"You may include ONE small vector illustration, line-art icon, abstract shape, or object-style graphic "
        f"that symbolically represents the topic — never a full photographic scene, never a realistic person. "
        f"This visual element must occupy no more than ~30% of the total cover area, and must feel intentional, "
        f"not decorative filler.\n\n"

        f"STEP 6 — POLISH:\n"
        f"Exactly 1024x1536 px, portrait orientation. Flat modern design, clean grid alignment, high-contrast "
        f"legibility at thumbnail size, no clutter, no stock-photo imagery, no clichéd icons (no generic lightbulbs, "
        f"handshakes, or rocket ships unless truly topic-specific). "
        f"Final result should feel like a premium, non-fiction Kindle bestseller that this specific reader would "
        f"pick up because it visually *feels* like their internal state or aspiration — not a generic business book."
    )

    print(f"[OK] Generated Prompt:\n{prompt_text}\n", flush=True)

    cookies = load_cookies(Path(CHATGPT_COOKIES_FILE))

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

        print("[STEP] Opening ChatGPT Main URL...", flush=True)
        page.goto("https://chatgpt.com/", wait_until="load")
        major_wait()

        # Locate chat box
        print("[STEP] Locating chat textbox...", flush=True)
        chat_box = page.get_by_role("textbox", name="Chat with ChatGPT")
        if chat_box.count() == 0:
            chat_box = page.locator('div[contenteditable="true"]').filter(
                has=page.locator("p", has_text="Describe or edit an image")
            ).first
        if chat_box.count() == 0:
            chat_box = page.locator("#prompt-textarea")

        if chat_box.count() > 0:
            chat_box.first.click()
            print("[OK] Textbox located and clicked successfully.", flush=True)
        else:
            raise RuntimeError("❌ Textbox locator load nahi ho paya.")

        print("[STEP] Typing prompt into ChatGPT...", flush=True)
        chat_box.first.type(prompt_text, timeout=0)
        
        page.keyboard.press("Enter")
        print("[OK] Prompt submitted successfully.", flush=True)
        major_wait()

        found_share = False
        image_downloaded_successfully = False

        for attempt in range(1, MAX_RETRIES + 1):
            print(f"[STEP] Waiting for image generation... Attempt {attempt}/{MAX_RETRIES}", flush=True)
            major_wait()

            # Skip or Preference logic
            try:
                skip_button = page.get_by_role("button", name="Skip")
                if skip_button.first.is_visible():
                    print("[INFO] 'Skip' button detected! Clicking 'Skip'...", flush=True)
                    skip_button.first.click()
                    minor_wait()

                    option_1 = page.locator("div").filter(has_text=re.compile(r"^1Image 1Image 1 is better$")).get_by_label("")
                    option_2 = page.locator("div").filter(has_text=re.compile(r"^2Image 2Image 2 is better$")).get_by_label("")

                    if option_1.first.is_visible() or option_2.first.is_visible():
                        chosen_option = random.choice([option_1, option_2])
                        if chosen_option.first.is_visible():
                            chosen_option.first.click()
                            minor_wait()
            except Exception:
                pass

            # Detect 'Share this image' button
            try:
                locator = page.get_by_role("button", name="Share this image").first
                if locator.is_visible():
                    print("✅ 'Share this image' button located successfully!", flush=True)
                    locator.click()  # Explicitly click the Share button
                    print("[STEP] Clicked 'Share this image' button.", flush=True)
                    minor_wait()  # Essential 6-12 sec wait for Download option to render
                    found_share = True
                    break
            except Exception as e:
                print(f"[DEBUG] Could not click share button yet: {e}", flush=True)

            if not found_share and attempt == 5:
                try:
                    chat_box = page.get_by_role("textbox", name="Chat with ChatGPT")
                    if chat_box.count() == 0:
                        chat_box = page.locator("#prompt-textarea")

                    if chat_box.count() > 0:
                        chat_box.first.click()
                        chat_box.first.type("Please continue generating the image")
                        page.keyboard.press("Enter")
                        major_wait()
                except Exception:
                    pass

        if not found_share:
            raise RuntimeError("❌ Image generation timed out or Share button failed to appear.")

        page.get_by_role("menuitem", name="This image").click()
        minor_wait()
        
        # Download Strategy: Direct Download Button
        print("[STEP] Checking for Direct 'Download' button...", flush=True)
        direct_download_btn = page.get_by_role("button", name="Download").first

        if direct_download_btn.is_visible():
            try:
                with page.expect_download(timeout=60000) as download_info:
                    direct_download_btn.click()

                download = download_info.value
                download.save_as(PNG_PATH)
                print(f"✅ Original PNG image downloaded: {PNG_PATH}", flush=True)
                image_downloaded_successfully = True
                major_wait()
            except Exception as direct_dl_err:
                print(f"[WARNING] Direct download failed, attempting container extract: {direct_dl_err}", flush=True)

        # Fallback Strategy: Blob / Image extraction
        if not image_downloaded_successfully:
            try:
                generated_image_btn = page.get_by_role("button", name=re.compile(r"Generated image:.*", re.IGNORECASE)).first
                if generated_image_btn.is_visible():
                    img_element = generated_image_btn.locator("img").first
                    img_src = img_element.get_attribute("src")

                    if img_src and img_src.startswith("blob:"):
                        base64_data = page.evaluate("""async (url) => {
                            const response = await fetch(url);
                            const blob = await response.blob();
                            return new Promise((resolve) => {
                                const reader = new FileReader();
                                reader.onloadend = () => resolve(reader.result.split(',')[1]);
                                reader.readAsDataURL(blob);
                            });
                        }""", img_src)
                        with open(PNG_PATH, "wb") as fh:
                            fh.write(base64.b64decode(base64_data))
                        print(f"✅ Image extracted from Blob and saved: {PNG_PATH}", flush=True)
                        image_downloaded_successfully = True
            except Exception as fallback_err:
                print(f"[WARNING] Image extraction failed: {fallback_err}", flush=True)

        if image_downloaded_successfully:
            convert_png_to_jpg(PNG_PATH, JPG_PATH)

            update_status_value(status_data, "cover_generated", True)
            save_status(status_data)
            print("✅ 'cover_generated' successfully set to True in ebook_status.json!", flush=True)
        else:
            raise RuntimeError("❌ Cover image download terminated without saving.")

    except Exception as e:
        print(f"[ERROR] {e}", flush=True)
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
        print("[DONE] Script execution finished.", flush=True)


if __name__ == "__main__":
    run()