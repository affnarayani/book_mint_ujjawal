import base64
import random
import sys
from pathlib import Path

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

INPUT_COVER_PNG = path("ebook_cover_png")
OUTPUT_BANNER_PNG = path("ebook_banner_png")

MAX_RETRIES = RETRY_CFG["banner_max_retries"]
CHATGPT_URL = "https://chatgpt.com/"


# =========================
# MAIN EXECUTION
# =========================
def run():
    print(f"--- Starting Banner Generation Process (HEADLESS={HEADLESS}) ---", flush=True)

    status_data = load_status(STATUS_FILE)
    cover_generated = get_status_value(status_data, "cover_generated")
    banner_generated = get_status_value(status_data, "banner_generated")

    # Guard condition: cover_generated must be True AND banner_generated must be False/None
    if not cover_generated:
        print("[INFO] 'cover_generated' is not True. Cannot generate banner yet. Exiting safely.", flush=True)
        sys.exit(0)

    if banner_generated is True:
        print("[INFO] 'banner_generated' is already True. Exiting safely.", flush=True)
        sys.exit(0)

    if not INPUT_COVER_PNG.exists():
        raise FileNotFoundError(f"Required input cover file not found at '{INPUT_COVER_PNG}'!")

    prompt_text = "Please convert this attached image into a 1672x941 aspect ratio banner and output the image."
    print(f"[OK] Prompt:\n{prompt_text}\n", flush=True)

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

        # Upload image attachment
        print("[STEP] Attaching ebook/cover.png...", flush=True)
        file_input = resolve_locator(page, loc.chatgpt_file_input, "file input element")
        if file_input is not None and file_input.count() > 0:
            file_input.first.set_input_files(str(INPUT_COVER_PNG.resolve()))
            print("[OK] Attached ebook/cover.png successfully.", flush=True)
            minor_wait()
        else:
            raise RuntimeError("Could not find file input element to attach PNG.")

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
            print(f"[STEP] Waiting for banner generation... Attempt {attempt}/{MAX_RETRIES}", flush=True)
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
                download.save_as(OUTPUT_BANNER_PNG)
                print(f"[OK] Banner image downloaded: {OUTPUT_BANNER_PNG}", flush=True)
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
                        with open(OUTPUT_BANNER_PNG, "wb") as fh:
                            fh.write(base64.b64decode(base64_data))
                        print(f"[OK] Banner image extracted from Blob and saved: {OUTPUT_BANNER_PNG}", flush=True)
                        image_downloaded_successfully = True
            except Exception as fallback_err:
                print(f"[WARNING] Image extraction failed: {fallback_err}", flush=True)

        if image_downloaded_successfully:
            update_status_value(status_data, "banner_generated", True)
            save_status(STATUS_FILE, status_data)
            print("[OK] 'banner_generated' successfully set to True in ebook_status.json!", flush=True)
        else:
            raise RuntimeError("Banner image download terminated without saving.")

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