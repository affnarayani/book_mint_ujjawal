import json
import random
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from common.common import (
    BROWSER_CFG,
    custom_random_wait,
    load_cookies,
    load_locators,
    path,
    report_error_and_upload,
)
from common.gemini_vision import resolve_locator, smart_click

# =========================
# CONFIG
# =========================
HEADLESS = BROWSER_CFG["headless"]
USER_AGENT = BROWSER_CFG["user_agent"]
LAUNCH_ARGS = BROWSER_CFG["launch_args"]
GRANT_PERMISSIONS = BROWSER_CFG["grant_permissions"]

CHATGPT_COOKIES_DIR = path("chatgpt_cookies_dir")
EBOOK_PATH = path("ebook_pdf")
OUTPUT_FILE = path("ebook_details")
STATUS_FILE = path("ebook_status")

# Strict Subcategories list
ALLOWED_SUBCATEGORIES = [
    "Abuse", "Affirmations", "Aging", "Anger Management", "Anxieties & Phobias",
    "Communication & Social Skills", "Creativity", "Eating Disorders & Body Image",
    "Emotions", "Fashion & Style", "General", "Green Lifestyle", "Happiness",
    "Indigenous Mental Health & Healing", "Inner Child", "Journal Writing",
    "Journaling", "Memory Improvement", "Motivational",
    "Neuro-Linguistic Programming (NLP)", "Personal Transformation",
    "Self-Esteem", "Self-Hypnosis", "Self-Management", "Sexual Instruction",
    "Spiritual", "Stress Management", "Success", "Time Management"
]


# =========================
# STATUS CHECK & UPDATE
# =========================
def check_status_prerequisites() -> bool:
    if not STATUS_FILE.exists():
        print(f"[STATUS] '{STATUS_FILE.name}' not found. Exiting...", flush=True)
        return False

    try:
        with STATUS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)

        ebook_downloaded = None
        details_generated = None

        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    if "ebook_downloaded" in item:
                        ebook_downloaded = item["ebook_downloaded"]
                    if "details_generated" in item:
                        details_generated = item["details_generated"]
        elif isinstance(data, dict):
            ebook_downloaded = data.get("ebook_downloaded")
            details_generated = data.get("details_generated")

        print(f"[STATUS INFO] Parsed values -> ebook_downloaded: {ebook_downloaded}, details_generated: {details_generated}", flush=True)

        if ebook_downloaded is True and details_generated is False:
            print("[STATUS OK] Conditions met: ebook_downloaded=True and details_generated=False. Proceeding...", flush=True)
            return True
        else:
            print("[STATUS SKIP] Conditions not met. Exiting safely.", flush=True)
            return False

    except Exception as e:
        print(f"[STATUS ERROR] Could not read/parse '{STATUS_FILE.name}': {e}. Exiting...", flush=True)
        return False


def update_status_details_generated():
    if not STATUS_FILE.exists():
        print(f"[WARNING] '{STATUS_FILE.name}' file missing while trying to update status.", flush=True)
        return

    try:
        with STATUS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)

        updated = False
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and "details_generated" in item:
                    item["details_generated"] = True
                    updated = True
        elif isinstance(data, dict):
            data["details_generated"] = True
            updated = True

        if updated:
            with STATUS_FILE.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"[STATUS UPDATE] Successfully set 'details_generated': True in '{STATUS_FILE.name}'.", flush=True)
        else:
            print(f"[WARNING] Key 'details_generated' not found in '{STATUS_FILE.name}' structure to update.", flush=True)

    except Exception as e:
        print(f"[WARNING] Could not update '{STATUS_FILE.name}': {e}", flush=True)


# =========================
# HELPER: EXTRACT & CLEAN RAW JSON
# =========================
def extract_json_from_text(text: str) -> str:
    text = text.strip()

    # Strip OpenAI citation tags if present, e.g. :contentReference[oaicite:0]{index=0}
    text = re.sub(r':contentReference\[oaicite:\d+\]\{index=\d+\}', '', text)

    # Clean leading/trailing markdown code blocks
    text = re.sub(r'^```json\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^```\s*', '', text)
    text = re.sub(r'\s*```$', '', text)

    first_bracket = text.find('{')
    last_bracket = text.rfind('}')

    if first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket:
        return text[first_bracket:last_bracket + 1].strip()

    return text.strip()


def get_latest_chat_response_text(page, response_selectors) -> str:
    """
    Robust multi-layered selector search for getting raw text content from the ChatGPT chat response.
    """
    for selector in response_selectors:
        try:
            elements = page.locator(selector).all()
            if elements:
                last_elem = elements[-1]
                text = last_elem.evaluate("el => el.innerText").strip()
                if text and len(text) > 20:
                    return text
        except Exception:
            continue

    return ""


def sanitize_url(raw_url: str) -> str:
    """
    Extracts a raw clean URL if brackets/markdown got injected somehow.
    """
    match = re.search(r'https?://[^\s\]\)\"]+', raw_url)
    if match:
        return match.group(0)
    return raw_url.strip()


# =========================
# MAIN
# =========================
def run():
    print("[START] Script started", flush=True)

    if not check_status_prerequisites():
        sys.exit(0)

    if not EBOOK_PATH.exists():
        print(f"[ERROR] Ebook file not found at path '{EBOOK_PATH}'. Exiting...", flush=True)
        sys.exit(1)

    encrypted_files = list(CHATGPT_COOKIES_DIR.glob("*.encrypted"))
    if not encrypted_files:
        raise RuntimeError(f"No .encrypted cookie files found in '{CHATGPT_COOKIES_DIR.name}/' folder")

    chatgpt_cookies_file = random.choice(encrypted_files)
    print(f"[OK] Randomly selected cookie file: {chatgpt_cookies_file.name}", flush=True)

    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        f.write("")
    print(f"[OK] '{OUTPUT_FILE.name}' initialized", flush=True)

    cookies = load_cookies(Path(chatgpt_cookies_file))
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

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
        target_url = sanitize_url("[https://chatgpt.com/](https://chatgpt.com/)")

        # Hard safety guarantee for a pure URL string
        page.goto(str(target_url), wait_until="load")
        print("[OK] URL opened successfully (Logged In)", flush=True)

        custom_random_wait(30, 60)

        # File Upload
        print("[STEP] Opening composer attachments menu...", flush=True)
        composer_plus_btn = resolve_locator(page, loc.chatgpt_composer_plus_button, "composer plus button")
        if composer_plus_btn is None:
            raise RuntimeError("'composer-plus-btn' not found on page.")

        composer_plus_btn.click()
        custom_random_wait(2, 4)

        print("[STEP] Intercepting file chooser dialog to attach eBook...", flush=True)
        upload_option = loc.chatgpt_composer_upload_option

        if upload_option.count() == 0:
            upload_option = resolve_locator(page, loc.chatgpt_upload_from_computer_text, "Upload from computer text")
            if upload_option is None:
                raise RuntimeError("Could not locate the 'Upload from computer' option.")

        with page.expect_file_chooser() as fc_info:
            upload_option.click()

        file_chooser = fc_info.value
        file_chooser.set_files(str(EBOOK_PATH.resolve()))
        print(f"[OK] File '{EBOOK_PATH.name}' selected and injected successfully.", flush=True)

        print("[STEP] Waiting for file upload to process...", flush=True)
        custom_random_wait(30, 60)

        # Textbox Interaction
        print("[STEP] Locating chat textbox...", flush=True)
        textbox = resolve_locator(page, loc.chatgpt_chat_textbox, "Chat with ChatGPT textbox")
        if textbox is None:
            textbox = resolve_locator(page, loc.chatgpt_contenteditable_textbox, "contenteditable chat textbox")
        if textbox is None:
            textbox = resolve_locator(page, loc.chatgpt_prompt_textarea, "prompt textarea")

        if textbox is None:
            raise RuntimeError("Could not locate the ChatGPT chat textbox.")

        textbox.click()
        print("[OK] Textbox located and clicked successfully.", flush=True)

        subcat_str = "\n".join([f"- {cat}" for cat in ALLOWED_SUBCATEGORIES])

        prompt = (
            f"IMPORTANT:\n"
            f"The attached PDF is a complete ebook. Read it carefully and understand its content, tone, target reader, and core promise.\n"
            f"Do NOT search the web or append any web citations or content references (:contentReference...).\n"
            f"Return ONLY ONE valid, strictly-formatted JSON object wrapped inside a single ```json code block```.\n"
            f"Do NOT output explanations, plain text, comments, notes or conversational text outside the JSON block.\n\n"

            f"SUBCATEGORIES LIST:\n"
            f"{subcat_str}\n\n"

            f"GUIDELINES FOR GENERATING DESCRIPTIONS:\n"
            f"Write high-converting, psychologically compelling product descriptions formatted in RICH MARKDOWN (using headings like ##, bold text **, bullet points -):\n"
            f"- Hook (first 2-3 lines): Strong pattern-interrupt, curiosity-driven, or pain-point-driven opening.\n"
            f"- Problem agitation: Name specific pain, frustration, or desire.\n"
            f"- Transformation/promise: Clearly state concrete outcomes.\n"
            f"- Authority/credibility signal: Include markers like 'based on proven frameworks'.\n"
            f"- Chapter/content preview: 4-6 bullet points teasing key takeaways.\n"
            f"- Objection handling: Address common hesitations in 1-2 lines.\n"
            f"- Urgency/scarcity trigger: Soft sense of urgency.\n"
            f"- Strong CTA: Action-driving closing line.\n"
            f"- SEO optimization: Naturally weave 5-8 relevant keywords/phrases into the text.\n\n"

            f"REQUIRED OUTPUT JSON FORMAT:\n"
            f"```json\n"
            f"{{\n"
            f'  "description_kdp": "Markdown formatted long description for Amazon KDP (Strictly around 500-600 words total, maximum 3000 characters)",\n'
            f'  "description_gumroad": "Markdown formatted punchy description for Gumroad (Strictly around 250-300 words total, maximum 1500 characters)",\n'
            f'  "subcategories": [\n'
            f'    "Subcategory 1",\n'
            f'    "Subcategory 2",\n'
            f'    "Subcategory 3"\n'
            f'  ],\n'
            f'  "keywords": [\n'
            f'    "Keyword 1",\n'
            f'    "Keyword 2",\n'
            f'    "Keyword 3",\n'
            f'    "Keyword 4",\n'
            f'    "Keyword 5",\n'
            f'    "Keyword 6",\n'
            f'    "Keyword 7"\n'
            f'  ]\n'
            f"}}\n"
            f"```\n\n"

            f"STRICT MARKDOWN & JSON ESCAPING RULES:\n"
            f"1. Format 'description_kdp' and 'description_gumroad' using clean Markdown syntax.\n"
            f"2. Ensure all Markdown line breaks inside the string are properly escaped as '\\n' so the JSON stays 100% valid and parsable.\n"
            f"3. Do NOT use raw unescaped double-quotes inside markdown string; use single-quotes if needed.\n"
            f"4. 'description_kdp': Follow guidelines. 250-350 words, max 2500 characters.\n"
            f"5. 'description_gumroad': Follow guidelines. 80-100 words, max 1000 characters.\n"
            f"6. 'subcategories': Choose EXACTLY 3 subcategories from the list above.\n"
            f"7. 'keywords': Choose EXACTLY 7 keywords/phrases as an array of strings.\n"
        )

        print("[STEP] Entering prompt into textbox...", flush=True)
        textbox.fill(prompt)
        custom_random_wait(5, 10)

        print("[STEP] Locating and clicking send button...", flush=True)
        send_clicked = False
        for attempt in range(1, 4):
            if smart_click(page, loc.chatgpt_send_button, "send button"):
                send_clicked = True
                break
            print(f"[WARN] Send button click attempt {attempt}/3 failed; retrying...", flush=True)
            custom_random_wait(3, 6)
        if not send_clicked:
            raise RuntimeError("Could not click the ChatGPT send button after retries.")

        custom_random_wait(15, 25)

        # ============================================
        # STREAM MONITORING & POLLING
        # ============================================
        print("[STEP] Polling ChatGPT response stream...", flush=True)
        response_selectors = loc.chatgpt_response_selectors
        json_content = None
        last_length = 0
        stable_counter = 0
        max_check_cycles = 30  # ~7.5 minutes max

        for cycle in range(max_check_cycles):
            time.sleep(15)

            current_text = get_latest_chat_response_text(page, response_selectors)
            current_length = len(current_text)

            print(f"[STREAM INFO] Cycle {cycle+1}: Previous Length = {last_length}, Current Length = {current_length}", flush=True)

            # Anti-premature guard: Require minimum 250 characters
            if current_length > 250:
                if current_length == last_length:
                    stable_counter += 1
                    if '}' in current_text and stable_counter >= 2:
                        json_content = current_text
                        print("[OK] Stream stabilized with complete JSON structure.", flush=True)
                        break
                    elif stable_counter >= 3:
                        json_content = current_text
                        print("[OK] Stream length fully stabilized.", flush=True)
                        break
                    else:
                        print(f"[WAIT] Response unchanged (Stable count: {stable_counter}/3). Waiting...", flush=True)
                else:
                    stable_counter = 0
            else:
                print(f"[WAIT] Still generating... Current length ({current_length}) is too short.", flush=True)

            last_length = current_length

        # JSON parsing and saving
        if json_content:
            raw_extracted = extract_json_from_text(json_content)
            parsed_json = None

            print(f"[STEP] Extracting JSON (Raw Length: {len(raw_extracted)} chars)...", flush=True)

            # Stage 1: Try direct parse
            try:
                parsed_json = json.loads(raw_extracted)
            except json.JSONDecodeError:
                # Stage 2: Escape unescaped newlines inside strings
                print("[WARNING] Direct JSON parse failed, applying newline escaping...", flush=True)
                try:
                    sanitized = re.sub(r'(?<!\\)\r?\n', r'\\n', raw_extracted)
                    parsed_json = json.loads(sanitized)
                except Exception as final_err:
                    print(f"[CRITICAL ERROR] Final JSON parsing failed: {final_err}", flush=True)
                    report_error_and_upload(page)
                    if browser:
                        browser.close()
                    sys.exit(1)

            if parsed_json:
                print(f"[STEP] Writing JSON data to '{OUTPUT_FILE.name}'...", flush=True)
                with OUTPUT_FILE.open("w", encoding="utf-8") as f:
                    json.dump(parsed_json, f, indent=4, ensure_ascii=False)
                print(f"[Success] eBook Markdown details written to '{OUTPUT_FILE.name}'.", flush=True)

                update_status_details_generated()

        else:
            print("[ERROR] Max cycles completed without obtaining valid text. Exiting...", flush=True)
            report_error_and_upload(page)
            if browser:
                browser.close()
            sys.exit(1)

        print("[STEP] Performing random wait before browser exit...", flush=True)
        custom_random_wait(30, 60)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR]", e, flush=True)
        report_error_and_upload(page)
        if browser:
            try:
                browser.close()
            except Exception:
                pass
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

        print("[DONE] Script finished", flush=True)


if __name__ == "__main__":
    run()