import os
import sys
import json
import time
import random
import base64
from pathlib import Path
from typing import List, Dict, Any, Tuple

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
HEADLESS = True

COOKIES_FILE = "claude/cookies.json.encrypted"
EBOOK_IDEAS_FILE = "ebook_ideas.json"
EBOOK_STATUS_FILE = "ebook_status.json"

PBKDF2_ITERATIONS = 200_000

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

# Prompt template with placeholder for {title}
PROMPT_TEMPLATE = """## TASK

You are acting as a professional ebook author, editor, and designer. Your job is to deeply research the topic **[TOPIC]**, then write, design, and produce a complete, publish-ready ebook on this topic as a single PDF file.

Do not ask me any questions at any point. Make every decision yourself based on the instructions below, and output only the final PDF.

**Strict topic name rule**: The topic name given below in `[TOPIC]` must be used exactly as provided, word-for-word, with no rewording, rephrasing, shortening, expanding, or "improving" it. This applies everywhere it appears in the ebook — the table of contents, the copyright/about pages, headers, and any other reference throughout the PDF. The topic name itself must remain 100% unchanged.

---

## STEP 1: RESEARCH & STRATEGY (internal — do not show this in the output)

Before writing, do the following internally:
- Deeply research **[TOPIC]** to understand its most important angles, pain points, misconceptions, and opportunities for the reader.
- Identify the target reader's core intent: what problem are they trying to solve, or what desire are they trying to fulfill, by picking up an ebook on this topic?
- Decide the single most compelling angle/perspective to write this ebook from — one that makes it feel practical, fresh, and worth the reader's time, rather than generic or recycled content.
- Decide a logical chapter structure that builds from foundational understanding to advanced/actionable insight, ending in transformation or empowerment for the reader.
- Decide a color palette and visual theme for the entire ebook based on the psychology of the topic (e.g., trust-building blues/greens for finance, warm energetic tones for motivation/growth topics, calm muted tones for wellness, bold high-contrast tones for business/hustle topics, etc.). The palette must be chosen deliberately to evoke the right emotional response for this specific topic — never use a generic default palette.
- Decide font pairing: one distinctive, professional serif or display font for headings/titles (evoking authority and polish), and one highly readable sans-serif font for body text. Ensure strong readability at standard reading size, proper line spacing, and consistent hierarchy (H1/H2/H3 sizes clearly differentiated).

---

## STEP 2: CONTENT REQUIREMENTS

- Total length: **40–60 pages**.
- Language: **English only**, regardless of the topic.
- Tone: informative, engaging, and confident — written like a premium paid ebook, not a generic blog compilation.
- Opening hook: The very first page of content (after the TOC) must open with a strong hook — a compelling question, surprising fact, relatable scenario, or bold statement — that creates curiosity and makes the reader want to keep reading. Maintain this sense of curiosity/momentum throughout the ebook (each chapter should end in a way that pulls the reader into the next).
- Structure the ebook into clearly defined chapters/sections with a logical flow (introduction → core content chapters → practical application → conclusion).
- **Chapter-end "Key Takeaways" box**: At the end of every chapter, include a visually distinct summary box with 3–5 concise bullet points recapping the chapter's most important points.
- **Real-world examples / mini case studies**: Wherever the topic allows, include short, relatable real-world examples or mini case studies (fictionalized/composite is fine if needed) to make abstract concepts concrete rather than purely theoretical.
- **Actionable worksheet/checklist section**: Near the end of the ebook (before the closing chapter), include a practical, ready-to-use checklist, action plan, or worksheet-style page the reader can immediately apply — something that makes them feel they got tangible, usable value.
- Ending: The ebook must always end on a strong, positive, motivating note — leaving the reader feeling genuinely satisfied, empowered, and confident that their time (and money) was well spent reading it. Avoid abrupt or flat endings.
- Include minimal supporting visuals only where they truly aid understanding: simple illustrations, icons, one or two diagrams/flowcharts, or simple infographics. Do not overload the ebook with images — content and clarity come first.

---

## STEP 3: MANDATORY STRUCTURE (in this exact order)

**Do NOT include any cover/title page.** The PDF must start directly with the Copyright Page as page 1.

1. **Copyright Page**
   - Standard copyright notice using the **current year**.
   - Author/Publisher name: **"Mind To Better" (Ujjawal Kumar)**.
   - Standard "All rights reserved" language.

2. **Disclaimer Page**
   - A short, professional disclaimer appropriate to the topic (e.g., general-information disclaimer; if the topic touches finance, health, legal, or similarly sensitive areas, include an appropriately worded advisory that the content is for informational purposes and not a substitute for professional advice).

3. **Table of Contents**
   - Clean, professional layout listing all chapters/sections with accurate page numbers.
   - Internally hyperlinked/clickable so readers can jump directly to any chapter from the TOC (using standard PDF bookmarks/links).

4. **Introduction (with the opening hook)**

5. **Main Body Chapters**
   - Well-structured chapters covering the topic comprehensively, each ending with a "Key Takeaways" box.

6. **Actionable Checklist / Worksheet Section**

7. **Conclusion** — ending on a positive, empowering note as described above.

8. **About the Author Page**
   - A short, professional bio section introducing **"Mind To Better"** and its founder, **Ujjawal Kumar**, positioned with authority and credibility relevant to the ebook's subject matter.

---

## STEP 4: FORMATTING & DESIGN RULES

- **Professional layout throughout**: consistent margins, spacing, and alignment on every page.
- **Standard margins**: Use standard, professional page margins on every page, applied consistently throughout the entire document.
- **Orphan heading rule (strict)**: A subheading must never appear at the very bottom of a page with its content starting only on the next page. If a subheading would otherwise land at the bottom of a page, push the entire subheading (and its content) to the start of the next page instead, so the page ends cleanly and looks polished.
- **Standard page numbering**: Every page must have a page number in a consistent position (e.g., bottom center or bottom corner), using a consistent numbering style throughout.
- **Consistent branding footer**: Every page should carry a small, unobtrusive footer element with "Mind To Better" branding — subtle, not distracting from the content.
- **Typography consistency**: Use the heading and body fonts decided in Step 1 consistently throughout — consistent font sizes for H1/H2/H3, consistent paragraph styling, consistent bullet/list styling, and consistent spacing rules across the entire document.
- **Color consistency**: Apply the chosen color palette consistently across headings, key-takeaway boxes, checklist section, dividers, and any diagrams/icons — the whole ebook should feel like one cohesive, designed product, not a plain text document.
- **Visual hierarchy**: Clear differentiation between chapter titles, subheadings, body text, quotes/callouts, and key-takeaway boxes.

---

## FINAL OUTPUT REQUIREMENT

- Output **only a single PDF file**, named exactly: **`ebook.pdf`**
- Do not output the content as plain text, markdown, or any other format — the final deliverable must be the fully designed, formatted PDF itself.
- Do not ask any follow-up or clarifying questions at any stage — research, decide, design, and generate the complete ebook directly based on this prompt alone.

---

**[TOPIC]:** {title}"""


# =========================
# DYNAMIC WAITS
# =========================
def custom_random_wait(min_sec=6, max_sec=12):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)


def custom_closing_wait(min_sec=15, max_sec=30):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Final wait before closing browser ({seconds:.2f} seconds)...", flush=True)
    time.sleep(seconds)


# =========================
# CRYPTO (COOKIES DECRYPTION)
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


def load_cookies(file_path: Path, decrypt_key: str) -> List[Dict[str, Any]]:
    print("[STEP] Loading and decrypting cookies...", flush=True)

    if not file_path.exists():
        raise FileNotFoundError(f"❌ Encrypted cookie file not found: {file_path}")

    with file_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    plaintext = _decrypt_payload(payload, decrypt_key)
    cookies = json.loads(plaintext.decode("utf-8"))

    if isinstance(cookies, dict):
        if "cookies" in cookies and isinstance(cookies["cookies"], list):
            cookies = cookies["cookies"]
        else:
            cookies = [cookies]

    # Sanitize Cookie properties for Playwright compatibility
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

    print("[OK] Cookies loaded successfully", flush=True)
    custom_random_wait(6, 12)
    return cookies


# =========================
# JSON HELPERS
# =========================
def get_next_ebook_topic(file_path: str = EBOOK_IDEAS_FILE) -> str:
    """Scans ebook_ideas.json from top to bottom for the next topic without ebook_published."""
    print("[STEP] Scanning ebook_ideas.json for next available topic...", flush=True)
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"❌ {file_path} not found.")

    with open(file_path, "r", encoding="utf-8") as f:
        ideas = json.load(f)

    for entry in ideas:
        if "ebook_published" not in entry:
            title = entry.get("title")
            if title:
                print(f"[OK] Selected topic: {title}", flush=True)
                custom_random_wait(6, 12)
                return title

    raise RuntimeError("❌ No available topic found in ebook_ideas.json.")


def read_ebook_status(file_path: str = EBOOK_STATUS_FILE) -> Tuple[str, str, bool]:
    """Reads claude_url, title, and ebook_downloaded status from ebook_status.json."""
    claude_url = ""
    title = ""
    ebook_downloaded = False
    
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data:
            if "claude_url" in item:
                claude_url = item.get("claude_url", "")
            if "title" in item:
                title = item.get("title", "")
            if "ebook_downloaded" in item:
                ebook_downloaded = bool(item.get("ebook_downloaded", False))
                
    return claude_url, title, ebook_downloaded


def update_ebook_status(claude_url: str = None, title: str = None, ebook_downloaded: bool = None, file_path: str = EBOOK_STATUS_FILE):
    """Updates key-value fields inside ebook_status.json list structure."""
    print(f"[STEP] Updating {file_path}...", flush=True)
    if not os.path.exists(file_path):
        print(f"[WARN] {file_path} not found. Skipping status update.", flush=True)
        return

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    for item in data:
        if claude_url is not None and "claude_url" in item:
            item["claude_url"] = claude_url
        if title is not None and "title" in item:
            item["title"] = title
        if ebook_downloaded is not None and "ebook_downloaded" in item:
            item["ebook_downloaded"] = ebook_downloaded

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print(f"[OK] {file_path} updated successfully.", flush=True)
    custom_random_wait(6, 12)


# =========================
# HELPER FUNCTIONS FOR PAGE INTERACTION
# =========================
def check_token_exhausted(page) -> bool:
    """Checks if any token limit error is visible on screen."""
    out_of_free_messages = page.get_by_text("You are out of free messages(")
    upgrade_text = page.get_by_role("heading", name="Upgrade to keep chatting")
    upgrade_notice_link = page.get_by_test_id("notice-region-slot-shift").get_by_role("link", name="Upgrade")
    
    custom_random_wait(6, 12)
    
    if (
        out_of_free_messages.is_visible() 
        or upgrade_text.is_visible() 
        or upgrade_notice_link.is_visible()
    ):
        print("[ALERT] Token limit signal detected!", flush=True)
        return True
        
    return False


def wait_for_stop_button_to_disappear(page, is_fresh_run: bool = False, topic_title: str = ""):
    """Infinitely waits while the Stop response button is visible."""
    stop_button = page.get_by_role("button", name="Stop response")
    print("[STEP] Waiting for output generation to complete (infinitely monitoring stop button)...", flush=True)
    while True:
        if check_token_exhausted(page):
            if is_fresh_run:
                current_url = page.url
                if current_url and current_url != "https://claude.ai/new":
                    print(f"[INFO] Updating status file before exiting due to token exhaustion: URL={current_url}, Title={topic_title}", flush=True)
                    update_ebook_status(claude_url=current_url, title=topic_title)
            print("[EXIT] Token exhausted during generation process. Exiting with sys.exit(0)...", flush=True)
            sys.exit(0)

        if stop_button.is_visible():
            time.sleep(5)
        else:
            print("[OK] Stop button disappeared. Generation finished.", flush=True)
            custom_random_wait(6, 12)
            break


def send_prompt_text(page, prompt_text: str):
    """Inputs text into chat prompt field and clicks send button."""
    input_text_box = page.get_by_test_id("chat-input")
    input_text_box.wait_for(state="visible", timeout=30000)

    custom_random_wait(6, 12)

    print("[STEP] Typing prompt into input field...", flush=True)
    input_text_box.fill(prompt_text)

    custom_random_wait(6, 12)

    print("[STEP] Clicking send button...", flush=True)
    send_button = page.get_by_test_id("chat-input-send")
    send_button.click()

    custom_random_wait(6, 12)


# =========================
# MAIN
# =========================
def run(decrypt_key: str):
    print("[START] Script started", flush=True)

    # Check state in ebook_status.json before doing anything
    saved_claude_url, saved_title, is_downloaded = read_ebook_status(EBOOK_STATUS_FILE)

    if is_downloaded:
        print("[STOP] 'ebook_downloaded' is set to True in ebook_status.json. Program will not run. Exiting cleanly...", flush=True)
        sys.exit(0)

    # Load decrypted cookies
    cookies = load_cookies(Path(COOKIES_FILE), decrypt_key)

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
        context.add_cookies(cookies)

        page = context.new_page()

        # Decide starting URL & initial prompt action
        if saved_claude_url.strip() != "":
            target_url = saved_claude_url
            is_fresh_run = False
            topic_title = saved_title
            print(f"[STEP] Existing claude_url found. Navigating to: {target_url}", flush=True)
        else:
            target_url = "https://claude.ai/new"
            is_fresh_run = True
            topic_title = get_next_ebook_topic(EBOOK_IDEAS_FILE)
            print(f"[STEP] Blank claude_url. Starting fresh run at: {target_url}", flush=True)

        page.goto(target_url, wait_until="load")
        custom_random_wait(6, 12)

        # Verify login status using test-id 'user-menu-button'
        print("[STEP] Verifying login status via 'user-menu-button'...", flush=True)
        user_menu_button = page.get_by_test_id("user-menu-button")
        user_menu_button.wait_for(state="visible", timeout=120000)
        print("[SUCCESS] Login success! User menu button detected.", flush=True)
        custom_random_wait(6, 12)

        # Token exhaust check right after navigation
        if check_token_exhausted(page):
            if is_fresh_run:
                current_url = page.url
                if current_url and current_url != "https://claude.ai/new":
                    print(f"[INFO] Updating status file before initial exit: URL={current_url}, Title={topic_title}", flush=True)
                    update_ebook_status(claude_url=current_url, title=topic_title)
            print("[EXIT] Token limit reached immediately upon load. Exiting with sys.exit(0)...", flush=True)
            sys.exit(0)

        # Fresh execution flow: send primary prompt
        if is_fresh_run:
            final_prompt = PROMPT_TEMPLATE.format(title=topic_title)
            send_prompt_text(page, final_prompt)
            
            # Wait for generation to end
            wait_for_stop_button_to_disappear(page, is_fresh_run=True, topic_title=topic_title)

            # Update status JSON with generated chat URL
            current_url = page.url
            print(f"[INFO] New Chat URL: {current_url}", flush=True)
            update_ebook_status(claude_url=current_url, title=topic_title)

        # Download & Continuation Loop
        download_button_locator = page.get_by_test_id("transcript-sizer").get_by_role("button", name="Download").or_(
            page.get_by_label("Download", exact=True)
        )
        continue_button_locator = page.get_by_role("button", name="Continue")

        while True:
            if check_token_exhausted(page):
                if is_fresh_run:
                    current_url = page.url
                    if current_url and current_url != "https://claude.ai/new":
                        print(f"[INFO] Updating status file before loop exit: URL={current_url}, Title={topic_title}", flush=True)
                        update_ebook_status(claude_url=current_url, title=topic_title)
                print("[EXIT] Token limit reached. Exiting with sys.exit(0)...", flush=True)
                sys.exit(0)

            # Check download button availability
            if download_button_locator.first.is_visible():
                print("[SUCCESS] Download button located!", flush=True)
                download_btn = download_button_locator.first
                
                # Ensure target directory exists
                ebook_dir = Path("ebook")
                ebook_dir.mkdir(parents=True, exist_ok=True)
                target_path = ebook_dir / "ebook.pdf"

                print("[STEP] Downloading ebook PDF...", flush=True)
                with page.expect_download(timeout=120000) as download_info:
                    download_btn.click()

                download = download_info.value
                download.save_as(target_path)
                print(f"[SUCCESS] Ebook saved successfully to: {target_path}", flush=True)
                custom_random_wait(6, 12)

                # Update status
                if is_fresh_run:
                    current_url = page.url
                    update_ebook_status(claude_url=current_url, title=topic_title, ebook_downloaded=True)
                else:
                    update_ebook_status(ebook_downloaded=True)
                break

            # Check Continue button if download button is not visible
            elif continue_button_locator.is_visible():
                print("[STEP] Download button not ready, but 'Continue' button detected. Clicking...", flush=True)
                continue_button_locator.click()
                custom_random_wait(6, 12)
                wait_for_stop_button_to_disappear(page, is_fresh_run=is_fresh_run, topic_title=topic_title)

            # Neither Download nor Continue button found: send completion prompt
            else:
                print("[STEP] Neither Download nor Continue button detected. Sending continuation prompt...", flush=True)
                send_prompt_text(page, "Continue and complete the last query.")
                wait_for_stop_button_to_disappear(page, is_fresh_run=is_fresh_run, topic_title=topic_title)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR] Script execution broke down due to trace:", e, flush=True)
        sys.exit(1)

    finally:
        # Wait 15 to 30 seconds before closing browser/teardown
        custom_closing_wait(15, 30)

        if browser:
            try:
                browser.close()
            except:
                pass

        try:
            pw_cm.__exit__(None, None, None)
        except:
            pass

        print("[DONE] Script execution environment torn down cleanly.", flush=True)


if __name__ == "__main__":
    load_dotenv()
    DECRYPT_KEY = os.getenv("DECRYPT_KEY")
    if not DECRYPT_KEY:
        raise RuntimeError("DECRYPT_KEY missing in environment variables")
    run(DECRYPT_KEY)