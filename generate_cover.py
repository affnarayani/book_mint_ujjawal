import base64
import json
import random
import re
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from common.common import (
    BROWSER_CFG,
    RETRY_CFG,
    load_cookies,
    load_locators,
    load_status,
    major_wait,
    minor_wait,
    path,
    report_error_and_upload,
    save_status,
    update_status_value,
    get_status_value,
)
from common.gemini_vision import resolve_locator

# =========================
# CONFIG
# =========================
HEADLESS = BROWSER_CFG["headless"]
USER_AGENT = BROWSER_CFG["user_agent"]
LAUNCH_ARGS = BROWSER_CFG["launch_args"]
GRANT_PERMISSIONS = BROWSER_CFG["grant_permissions"]

CHATGPT_COOKIES_DIR = path("chatgpt_cookies_dir")
STATUS_FILE = path("ebook_status")
DETAILS_FILE = path("ebook_details")

PNG_PATH = path("ebook_cover_png")
JPG_PATH = path("ebook_cover_jpg")
MAX_RETRIES = RETRY_CFG["cover_max_retries"]
CHATGPT_URL = "https://chatgpt.com/"


# =========================
# HELPERS
# =========================
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

    status_data = load_status(STATUS_FILE)
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
        raise ValueError("'title' key not found in status file!")

    if not DETAILS_FILE.exists():
        raise FileNotFoundError(f"'{DETAILS_FILE}' not found!")

    with DETAILS_FILE.open("r", encoding="utf-8") as f:
        details_data = json.load(f)

    description_kdp = details_data.get("description_kdp", "")
    clean_desc = clean_markdown(description_kdp)[:300]

    prompt_text = (
        f"You are an elite eBook cover designer and consumer psychologist. Your covers make people stop "
        f"scrolling and click 'buy' within 2 seconds, purely through psychological trigger design â€” not templates.\n\n"

        f"BOOK: '{title}'\n"
        f"CONTEXT: {clean_desc}\n\n"

        f"STEP 1 â€” PSYCHOLOGICAL ANALYSIS (do this silently before designing):\n"
        f"Identify the single core emotional trigger this specific topic evokes in its ideal reader â€” "
        f"e.g. quiet ambition, status anxiety, relief, rebellion, FOMO, calm confidence, urgency, curiosity, "
        f"aspiration, contrarian pride. Do NOT default to generic 'bold hustle' energy unless the topic actually "
        f"demands it. A book about restraint, quietness, or minimalism should FEEL restrained and minimal on the "
        f"cover â€” the design philosophy must mirror the book's own message, not contradict it.\n\n"

        f"STEP 2 â€” COLOR PSYCHOLOGY:\n"
        f"Choose a palette (dark/deep base + one dominant accent + one secondary highlight) derived from the "
        f"emotional trigger you identified â€” not a default palette. Muted, restrained topics can use desaturated "
        f"or low-contrast-but-premium palettes instead of loud neon accents.\n\n"

        f"STEP 3 â€” LAYOUT ARCHITECTURE (choose or blend freely, do NOT default to the same structure every time):\n"
        f"Pick whichever composition genuinely fits this topic's psychology â€” options include (but aren't limited to):\n"
        f"  â€¢ Minimalist center-focus: one powerful title moment, huge negative space, single small symbolic icon\n"
        f"  â€¢ Editorial/magazine-style: asymmetric grid, small kicker text, pull-quote style secondary line\n"
        f"  â€¢ Split-composition: title dominates one zone, a small vector illustration anchors another\n"
        f"  â€¢ Stacked-hierarchy: eyebrow + dramatic title + understated proof element, no heavy dividers/boxes\n"
        f"  â€¢ Framed/bordered: a restrained single border or corner marks, title as the only real content\n"
        f"Only include banners, stat bars, bullet lists, or CTA bars IF they genuinely strengthen this specific "
        f"topic's psychological pitch â€” do not force all of them onto every cover. Fewer, more deliberate "
        f"elements usually read as more premium than a fully packed layout.\n\n"

        f"STEP 4 â€” TITLE TREATMENT:\n"
        f"Render the title exactly as written, no spelling/wording changes: '{title}'. "
        f"Use confident modern typography with clear hierarchy â€” but let word emphasis (size/weight/color shift) "
        f"be chosen based on which word/phrase carries the emotional punch for THIS topic specifically.\n\n"

        f"STEP 5 â€” VISUAL ELEMENT (constrained):\n"
        f"You may include ONE small vector illustration, line-art icon, abstract shape, or object-style graphic "
        f"that symbolically represents the topic â€” never a full photographic scene, never a realistic person. "
        f"This visual element must occupy no more than ~30% of the total cover area, and must feel intentional, "
        f"not decorative filler.\n\n"

        f"STEP 6 â€” POLISH:\n"
        f"Exactly 1024x1536 px, portrait orientation. Flat modern design, clean grid alignment, high-contrast "
        f"legibility at thumbnail size, no clutter, no stock-photo imagery, no clichÃ©d icons (no generic lightbulbs, "
        f"handshakes, or rocket ships unless truly topic-specific). "
        f"Final result should feel like a premium, non-fiction Kindle bestseller that this specific reader would "
        f"pick up because it visually *feels* like their internal state or aspiration â€” not a generic business book."
    )

    print(f"[OK] Generated Prompt:\n{prompt_text}\n", flush=True)

    encrypted_files = list(CHATGPT_COOKIES_DIR.glob("*.encrypted"))
    if not encrypted_files:
        raise RuntimeError(f"No .encrypted cookie files found in '{CHATGPT_COOKIES_DIR.name}/' folder")

    chatgpt_cookies_file = random.choice(encrypted_files)
    print(f"[OK] Randomly selected cookie file: {chatgpt_cookies_file.name}", flush=True)

    cookies = load_cookies(Path(chatgpt_cookies_file))

    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()

    browser = None
    page = None
    try:
        browser = pw.chromium.launch(headless=HEADLESS, args=LAUNCH_ARGS)

        context = browser.new_context(no_viewport=True, user_agent=USER_AGENT)
        context.grant_permissions(GRANT_PERMISSIONS)

        print("[STEP] Adding cookies to browser context...", flush=True)
        context.add_cookies(cookies)

        page = context.new_page()
        print("[OK] Cookies added successfully", flush=True)

        loc = load_locators("chatgpt", page)

        print("[STEP] Opening ChatGPT Main URL...", flush=True)
        page.goto(CHATGPT_URL, wait_until="load")
        major_wait()

        # Locate chat box
        print("[STEP] Locating chat textbox...", flush=True)
        chat_box = resolve_locator(page, loc.chatgpt_chat_textbox, "Chat with ChatGPT textbox")
        if chat_box is None:
            chat_box = resolve_locator(page, loc.chatgpt_contenteditable_textbox, "contenteditable chat textbox")
        if chat_box is None:
            chat_box = resolve_locator(page, loc.chatgpt_prompt_textarea, "prompt textarea")

        if chat_box is None:
            raise RuntimeError("Could not locate the ChatGPT chat textbox.")

        chat_box.click()
        print("[OK] Textbox located and clicked successfully.", flush=True)

        print("[STEP] Filling prompt into ChatGPT...", flush=True)
        # Use fill() instead of type(): type() sends a real Enter key for every
        # newline, which submits the prompt before it is fully entered.
        chat_box.fill(prompt_text)

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
                skip_button = resolve_locator(page, loc.chatgpt_skip_button, "Skip button")
                if skip_button is not None and skip_button.is_visible():
                    print("[INFO] 'Skip' button detected! Clicking 'Skip'...", flush=True)
                    skip_button.click()
                    minor_wait()

                    option_1 = loc.chatgpt_image_option_template(1)
                    option_2 = loc.chatgpt_image_option_template(2)

                    if option_1.first.is_visible() or option_2.first.is_visible():
                        chosen_option = random.choice([option_1, option_2])
                        if chosen_option.first.is_visible():
                            chosen_option.first.click()
                            minor_wait()
            except Exception:
                pass

            # Detect 'Share this image' button
            try:
                share_button = resolve_locator(page, loc.chatgpt_share_image_button, "Share this image button")
                if share_button is not None and share_button.is_visible():
                    print("[OK] 'Share this image' button located successfully!", flush=True)
                    share_button.click()
                    print("[STEP] Clicked 'Share this image' button.", flush=True)
                    minor_wait()
                    found_share = True
                    break
            except Exception as e:
                print(f"[DEBUG] Could not click share button yet: {e}", flush=True)

            if not found_share and attempt == RETRY_CFG["continue_prompt_attempt"]:
                try:
                    chat_box = resolve_locator(page, loc.chatgpt_chat_textbox, "Chat with ChatGPT textbox")
                    if chat_box is None:
                        chat_box = resolve_locator(page, loc.chatgpt_prompt_textarea, "prompt textarea")

                    if chat_box is not None:
                        chat_box.click()
                        chat_box.fill("Please continue generating the image")
                        page.keyboard.press("Enter")
                        major_wait()
                except Exception:
                    pass

        if not found_share:
            raise RuntimeError("Image generation timed out or Share button failed to appear.")

        # 'This image' menu item is optional: it only appears in some UI states.
        # Do not use vision fallback for it; click only when the raw locator is
        # actually visible, otherwise just move on.
        this_image_menuitem = loc.chatgpt_image_this_image_menuitem
        resolved_item = this_image_menuitem.resolve() if this_image_menuitem is not None else None
        if resolved_item is not None and resolved_item.is_visible():
            resolved_item.first.click()
            minor_wait()

        # Download Strategy: Direct Download Button
        print("[STEP] Checking for Direct 'Download' button...", flush=True)
        direct_download_btn = resolve_locator(page, loc.chatgpt_image_download_button, "Download button")

        if direct_download_btn is not None and direct_download_btn.is_visible():
            try:
                with page.expect_download(timeout=60000) as download_info:
                    direct_download_btn.first.click()

                download = download_info.value
                download.save_as(PNG_PATH)
                print(f"[OK] Original PNG image downloaded: {PNG_PATH}", flush=True)
                image_downloaded_successfully = True
                major_wait()
            except Exception as direct_dl_err:
                print(f"[WARNING] Direct download failed, attempting container extract: {direct_dl_err}", flush=True)

        # Fallback Strategy: Blob / Image extraction
        if not image_downloaded_successfully:
            try:
                generated_image_btn = resolve_locator(
                    page, loc.chatgpt_generated_image_button, "Generated image button"
                )
                if generated_image_btn is not None and generated_image_btn.is_visible():
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
                        print(f"[OK] Image extracted from Blob and saved: {PNG_PATH}", flush=True)
                        image_downloaded_successfully = True
            except Exception as fallback_err:
                print(f"[WARNING] Image extraction failed: {fallback_err}", flush=True)

        if image_downloaded_successfully:
            convert_png_to_jpg(PNG_PATH, JPG_PATH)

            update_status_value(status_data, "cover_generated", True)
            save_status(STATUS_FILE, status_data)
            print("[OK] 'cover_generated' successfully set to True in ebook_status.json!", flush=True)
        else:
            raise RuntimeError("Cover image download terminated without saving.")

    except Exception as e:
        print(f"[ERROR] {e}", flush=True)
        report_error_and_upload(page)
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