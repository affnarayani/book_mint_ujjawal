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

COOKIES_DIR = Path("cookies")
encrypted_files = list(COOKIES_DIR.glob("*.encrypted"))

if not encrypted_files:
    raise RuntimeError("❌ No .encrypted cookie files found in 'cookies/' folder")

CHATGPT_COOKIES_FILE = random.choice(encrypted_files)
print(f"[OK] Randomly selected cookie file: {CHATGPT_COOKIES_FILE.name}", flush=True)

STATUS_FILE = Path("ebook_status.json")

OUTPUT_DIR = Path("ebook")
INPUT_COVER_PNG = OUTPUT_DIR / "cover.png"
OUTPUT_THUMBNAIL_PNG = OUTPUT_DIR / "thumbnail.png"

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
    """Wait strategy for major page loads & image generation loops (30 to 60 seconds)"""
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


# =========================
# MAIN EXECUTION
# =========================
def run():
    print(f"--- Starting Thumbnail Generation Process (HEADLESS={HEADLESS}) ---", flush=True)

    status_data = load_status()
    banner_generated = get_status_value(status_data, "banner_generated")
    thumbnail_generated = get_status_value(status_data, "thumbnail_generated")

    # Guard condition: banner_generated must be True AND thumbnail_generated must be False/None
    if not banner_generated:
        print("[INFO] 'banner_generated' is not True. Cannot generate thumbnail yet. Exiting safely.", flush=True)
        sys.exit(0)

    if thumbnail_generated is True:
        print("[INFO] 'thumbnail_generated' is already True. Exiting safely.", flush=True)
        sys.exit(0)

    if not INPUT_COVER_PNG.exists():
        raise FileNotFoundError(f"❌ Required input cover file not found at '{INPUT_COVER_PNG}'!")

    prompt_text = "Please convert this attached image into a 1254x1254 1:1 ratio square thumbnail."
    print(f"[OK] Prompt:\n{prompt_text}\n", flush=True)

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

        # Upload image attachment
        print("[STEP] Attaching ebook/cover.png...", flush=True)
        file_input = page.locator('input[type="file"]')
        if file_input.count() > 0:
            file_input.first.set_input_files(str(INPUT_COVER_PNG.resolve()))
            print("[OK] Attached ebook/cover.png successfully.", flush=True)
            minor_wait()  # Wait briefly for upload processing
        else:
            raise RuntimeError("❌ Could not find file input element to attach PNG.")

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
            print(f"[STEP] Waiting for thumbnail generation... Attempt {attempt}/{MAX_RETRIES}", flush=True)
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
                    minor_wait()  # Essential wait for Download option to render
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

        # Download Strategy: Direct Download Button
        print("[STEP] Checking for Direct 'Download' button...", flush=True)
        direct_download_btn = page.get_by_role("button", name="Download").first

        if direct_download_btn.is_visible():
            try:
                with page.expect_download(timeout=60000) as download_info:
                    direct_download_btn.click()

                download = download_info.value
                download.save_as(OUTPUT_THUMBNAIL_PNG)
                print(f"✅ Thumbnail image downloaded: {OUTPUT_THUMBNAIL_PNG}", flush=True)
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
                        with open(OUTPUT_THUMBNAIL_PNG, "wb") as fh:
                            fh.write(base64.b64decode(base64_data))
                        print(f"✅ Thumbnail image extracted from Blob and saved: {OUTPUT_THUMBNAIL_PNG}", flush=True)
                        image_downloaded_successfully = True
            except Exception as fallback_err:
                print(f"[WARNING] Image extraction failed: {fallback_err}", flush=True)

        if image_downloaded_successfully:
            update_status_value(status_data, "thumbnail_generated", True)
            save_status(status_data)
            print("✅ 'thumbnail_generated' successfully set to True in ebook_status.json!", flush=True)
        else:
            raise RuntimeError("❌ Thumbnail image download terminated without saving.")

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