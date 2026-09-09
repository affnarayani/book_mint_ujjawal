import os
import sys
import json
import time
import base64
import random
import re
import requests
from pathlib import Path
from typing import List, Dict, Any

from dotenv import load_dotenv

from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth


# =========================
# CONFIG
# =========================
HEADLESS = False

COOKIES_DIR = Path("cookies")
encrypted_files = list(COOKIES_DIR.glob("*.encrypted"))

if not encrypted_files:
    raise RuntimeError("❌ No .encrypted cookie files found in 'cookies/' folder")

CHATGPT_COOKIES_FILE = random.choice(encrypted_files)

EBOOK_PATH = Path("ebook/ebook.pdf")
OUTPUT_FILE = Path("ebook_details.json")
STATUS_FILE = Path("ebook_status.json")

PBKDF2_ITERATIONS = 200_000

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

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
# ENV
# =========================
load_dotenv()

DECRYPT_KEY = os.getenv("DECRYPT_KEY")

if not DECRYPT_KEY:
    raise RuntimeError("DECRYPT_KEY missing")


# =========================
# RANDOM WAIT
# =========================
def custom_random_wait(min_sec, max_sec):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
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


def upload_to_tmpfiles(screenshot_path):
    url = "https://tmpfiles.org/api/v1/upload"
    
    with open(screenshot_path, "rb") as file:
        response = requests.post(url, files={"file": file})
        
    if response.status_code == 200:
        res_data = response.json()
        page_url = res_data["data"]["url"]
        direct_url = page_url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
        print(f"👉 DIRECT LINK (Expires in 2 Hours): {direct_url}")
        return direct_url
    else:
        print(f"[WARNING] Upload Failed: {response.status_code}")
        return None


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
            print(f"[STATUS SKIP] Conditions not met. Exiting safely.", flush=True)
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
            print(f"✅ [STATUS UPDATE] Successfully set 'details_generated': True in '{STATUS_FILE.name}'.", flush=True)
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


def get_latest_chat_response_text(page) -> str:
    """
    Robust multi-layered selector search for getting raw text content from ChatGPT chat response
    """
    selectors = [
        'div[data-message-author-role="assistant"] pre code',
        'div[data-message-author-role="assistant"] pre',
        'div[data-message-author-role="assistant"] .markdown',
        'div[data-message-author-role="assistant"]',
        'pre code',
        'pre'
    ]
    
    for selector in selectors:
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
    Extracts raw clean URL if brackets/markdown got injected somehow
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

    print(f"[OK] Randomly selected cookie file: {CHATGPT_COOKIES_FILE.name}", flush=True)

    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        f.write("")
    print(f"[OK] '{OUTPUT_FILE.name}' initialized", flush=True)

    cookies = load_cookies(Path(CHATGPT_COOKIES_FILE))
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()

    browser = None
    try:
        browser = pw.chromium.launch(
            headless=HEADLESS,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled"
            ]
        )

        context = browser.new_context(
            no_viewport=True,
            user_agent=USER_AGENT
        )

        context.grant_permissions(["clipboard-read", "clipboard-write"])
        print("[STEP] Adding cookies to browser context...", flush=True)
        context.add_cookies(cookies)

        page = context.new_page()
        print("[OK] Cookies added successfully", flush=True)

        print("[STEP] Opening ChatGPT Main URL...", flush=True)
        target_url = sanitize_url("[https://chatgpt.com/](https://chatgpt.com/)")
        
        # Hard safety guarantee for pure URL string
        page.goto(str(target_url), wait_until="load")
        print("[OK] URL opened successfully (Logged In)", flush=True)

        custom_random_wait(30, 60)

        # File Upload
        print("[STEP] Opening composer attachments menu...", flush=True)
        composer_plus_btn = page.get_by_test_id("composer-plus-btn")
        
        if composer_plus_btn.count() == 0:
            raise RuntimeError("❌ 'composer-plus-btn' not found on page.")

        composer_plus_btn.click()
        custom_random_wait(2, 4)

        print("[STEP] Intercepting file chooser dialog to attach eBook...", flush=True)
        upload_option = page.locator('div').filter(has_text=re.compile(r'^Add photos & filesUpload from computer$', re.IGNORECASE)).nth(2)

        if upload_option.count() == 0:
            upload_option = page.get_by_text("Upload from computer")

        with page.expect_file_chooser() as fc_info:
            upload_option.click()
        
        file_chooser = fc_info.value
        file_chooser.set_files(str(EBOOK_PATH.resolve()))
        print(f"[OK] File '{EBOOK_PATH.name}' selected and injected successfully.", flush=True)

        print("[STEP] Waiting 30 to 60 seconds for file upload to process...", flush=True)
        custom_random_wait(30, 60)

        # Textbox Interaction
        print("[STEP] Locating chat textbox...", flush=True)
        textbox = page.get_by_role('textbox', name='Chat with ChatGPT')
        
        if textbox.count() == 0:
            textbox = page.locator('div[contenteditable="true"]').filter(has=page.locator('p', has_text='Ask anything')).first
            
        if textbox.count() == 0:
            textbox = page.locator('#prompt-textarea')

        if textbox.count() > 0:
            textbox.first.click()
            print("[OK] Textbox located and clicked successfully.", flush=True)
        else:
            raise RuntimeError("❌ Textbox locator load nahi ho paya.")

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
        textbox.first.fill(prompt)
        custom_random_wait(5, 10)

        print("[STEP] Locating and clicking send button...", flush=True)
        send_button = page.get_by_test_id('send-button')
        send_button.click()
        
        custom_random_wait(15, 25)

        # ============================================
        # STREAM MONITORING & POLLING
        # ============================================
        print("[STEP] Polling ChatGPT response stream...", flush=True)
        json_content = None
        last_length = 0
        stable_counter = 0
        max_check_cycles = 30  # ~7.5 minutes max

        for cycle in range(max_check_cycles):
            time.sleep(15)
            
            current_text = get_latest_chat_response_text(page)
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
                    if 'page' in locals() and page:
                        try:
                            screenshot_path = "error_screenshot.png"
                            page.screenshot(path=screenshot_path, full_page=True)
                            upload_to_tmpfiles(screenshot_path)
                        except:
                            pass
                    if browser:
                        browser.close()
                    sys.exit(1)

            if parsed_json:
                print(f"[STEP] Writing JSON data to '{OUTPUT_FILE.name}'...", flush=True)
                with OUTPUT_FILE.open("w", encoding="utf-8") as f:
                    json.dump(parsed_json, f, indent=4, ensure_ascii=False)
                print(f"✅ Success: eBook Markdown details written to '{OUTPUT_FILE.name}'.", flush=True)

                update_status_details_generated()

        else:
            print("❌ Max cycles completed without obtaining valid text. Exiting...", flush=True)
            if 'page' in locals() and page:
                try:
                    screenshot_path = "error_screenshot.png"
                    page.screenshot(path=screenshot_path, full_page=True)
                    upload_to_tmpfiles(screenshot_path)
                except:
                    pass
            if browser:
                browser.close()
            sys.exit(1)

        print("[STEP] Performing random wait before browser exit...", flush=True)
        custom_random_wait(30, 60)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR]", e, flush=True)
        if 'page' in locals() and page:
            try:
                screenshot_path = "error_screenshot.png"
                page.screenshot(path=screenshot_path, full_page=True)
                upload_to_tmpfiles(screenshot_path)
            except:
                pass
        if browser:
            try:
                browser.close()
            except:
                pass
        sys.exit(1)

    finally:
        if browser:
            try:
                browser.close()
            except:
                pass

        try:
            pw_cm.__exit__(None, None, None)
        except:
            pass

        print("[DONE] Script finished", flush=True)


if __name__ == "__main__":
    run()