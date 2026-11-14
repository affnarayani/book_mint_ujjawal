import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from common.common import (
    BROWSER_CFG,
    PATHS_CFG,
    ROOT_DIR,
    closing_wait,
    load_cookies,
    load_locators,
    major_wait,
    minor_wait,
    path,
    report_error_and_upload,
)
from common.gemini_vision import smart_click, smart_is_visible, vision_resolve

# =========================
# CONFIG
# =========================
HEADLESS = BROWSER_CFG["headless"]
USER_AGENT = BROWSER_CFG["user_agent"]
LAUNCH_ARGS = BROWSER_CFG["launch_args"]
GRANT_PERMISSIONS = BROWSER_CFG["grant_permissions"]

COOKIES_FILE = path("claude_cookies")
EBOOK_IDEAS_FILE = path("ebook_ideas")
EBOOK_STATUS_FILE = path("ebook_status")

# Prompt template with placeholder for {title}
PROMPT_TEMPLATE = """## TASK

You are acting as a professional ebook author, editor, and designer. Your job is to deeply research the topic **[TOPIC]**, then write, design, and produce a complete, publish-ready ebook on this topic as a single PDF file.

Do not ask me any questions at any point. Make every decision yourself based on the instructions below, and output only the final PDF.

**Strict topic name rule**: The topic name given below in `[TOPIC]` must be used exactly as provided, word-for-word, with no rewording, rephrasing, shortening, expanding, or "improving" it. This applies everywhere it appears in the ebook â€” the table of contents, the copyright/about pages, headers, and any other reference throughout the PDF. The topic name itself must remain 100% unchanged.

---

## STEP 1: RESEARCH & STRATEGY (internal â€” do not show this in the output)

Before writing, do the following internally:
- Deeply research **[TOPIC]** to understand its most important angles, pain points, misconceptions, and opportunities for the reader.
- Identify the target reader's core intent: what problem are they trying to solve, or what desire are they trying to fulfill, by picking up an ebook on this topic?
- Decide the single most compelling angle/perspective to write this ebook from â€” one that makes it feel practical, fresh, and worth the reader's time, rather than generic or recycled content.
- Decide a logical chapter structure that builds from foundational understanding to advanced/actionable insight, ending in transformation or empowerment for the reader.
- Decide a color palette and visual theme for the entire ebook based on the psychology of the topic (e.g., trust-building blues/greens for finance, warm energetic tones for motivation/growth topics, calm muted tones for wellness, bold high-contrast tones for business/hustle topics, etc.). The palette must be chosen deliberately to evoke the right emotional response for this specific topic â€” never use a generic default palette.
- Decide font pairing: one distinctive, professional serif or display font for headings/titles (evoking authority and polish), and one highly readable sans-serif font for body text. Ensure strong readability at standard reading size, proper line spacing, and consistent hierarchy (H1/H2/H3 sizes clearly differentiated).

---

## STEP 2: CONTENT REQUIREMENTS

- Total length: **40â€“60 pages**.
- Language: **English only**, regardless of the topic.
- Tone: informative, engaging, and confident â€” written like a premium paid ebook, not a generic blog compilation.
- Opening hook: The very first page of content (after the TOC) must open with a strong hook â€” a compelling question, surprising fact, relatable scenario, or bold statement â€” that creates curiosity and makes the reader want to keep reading. Maintain this sense of curiosity/momentum throughout the ebook (each chapter should end in a way that pulls the reader into the next).
- Structure the ebook into clearly defined chapters/sections with a logical flow (introduction â†’ core content chapters â†’ practical application â†’ conclusion).
- **Chapter-end "Key Takeaways" box**: At the end of every chapter, include a visually distinct summary box with 3â€“5 concise bullet points recapping the chapter's most important points.
- **Real-world examples / mini case studies**: Wherever the topic allows, include short, relatable real-world examples or mini case studies (fictionalized/composite is fine if needed) to make abstract concepts concrete rather than purely theoretical.
- **Actionable worksheet/checklist section**: Near the end of the ebook (before the closing chapter), include a practical, ready-to-use checklist, action plan, or worksheet-style page the reader can immediately apply â€” something that makes them feel they got tangible, usable value.
- Ending: The ebook must always end on a strong, positive, motivating note â€” leaving the reader feeling genuinely satisfied, empowered, and confident that their time (and money) was well spent reading it. Avoid abrupt or flat endings.
- Include minimal supporting visuals only where they truly aid understanding: simple illustrations, icons, one or two diagrams/flowcharts, or simple infographics. Do not overload the ebook with images â€” content and clarity come first.

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

7. **Conclusion** â€” ending on a positive, empowering note as described above.

8. **About the Author Page**
   - A short, professional bio section introducing **"Mind To Better"** and its founder, **Ujjawal Kumar**, positioned with authority and credibility relevant to the ebook's subject matter.

---

## STEP 4: FORMATTING & DESIGN RULES

- **Professional layout throughout**: consistent margins, spacing, and alignment on every page.
- **Standard margins**: Use standard, professional page margins on every page, applied consistently throughout the entire document.
- **Orphan heading rule (strict)**: A subheading must never appear at the very bottom of a page with its content starting only on the next page. If a subheading would otherwise land at the bottom of a page, push the entire subheading (and its content) to the start of the next page instead, so the page ends cleanly and looks polished.
- **Standard page numbering**: Every page must have a page number in a consistent position (e.g., bottom center or bottom corner), using a consistent numbering style throughout.
- **Consistent branding footer**: Every page should carry a small, unobtrusive footer element with "Mind To Better" branding â€” subtle, not distracting from the content.
- **Typography consistency**: Use the heading and body fonts decided in Step 1 consistently throughout â€” consistent font sizes for H1/H2/H3, consistent paragraph styling, consistent bullet/list styling, and consistent spacing rules across the entire document.
- **Color consistency**: Apply the chosen color palette consistently across headings, key-takeaway boxes, checklist section, dividers, and any diagrams/icons â€” the whole ebook should feel like one cohesive, designed product, not a plain text document.
- **Visual hierarchy**: Clear differentiation between chapter titles, subheadings, body text, quotes/callouts, and key-takeaway boxes.

---

## FINAL OUTPUT REQUIREMENT

- Output **only a single PDF file**, named exactly: **`ebook.pdf`**
- Do not output the content as plain text, markdown, or any other format â€” the final deliverable must be the fully designed, formatted PDF itself.
- Do not ask any follow-up or clarifying questions at any stage â€” research, decide, design, and generate the complete ebook directly based on this prompt alone.

---

**[TOPIC]:** {title}"""


# =========================
# DYNAMIC WAITS
# =========================
def custom_closing_wait():
    closing_wait()


# =========================
# JSON HELPERS
# =========================
def get_next_ebook_topic(file_path: Path = EBOOK_IDEAS_FILE) -> str:
    """Scans ebook_ideas.json from top to bottom for the next topic without ebook_published."""
    print("[STEP] Scanning ebook_ideas.json for next available topic...", flush=True)
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"{file_path} not found.")

    with open(file_path, "r", encoding="utf-8") as f:
        ideas = json.load(f)

    for entry in ideas:
        if "ebook_published" not in entry:
            title = entry.get("title")
            if title:
                print(f"[OK] Selected topic: {title}", flush=True)
                minor_wait()
                return title

    raise RuntimeError("No available topic found in ebook_ideas.json.")


def _is_real_session_url(url: str) -> bool:
    """True if url points to an actual Claude chat session (not 'new'/blank)."""
    if not url or not url.strip():
        return False
    cleaned = url.strip()
    # 'new' is a placeholder for a fresh chat, not a resumable session.
    if cleaned.rstrip("/").endswith("/new"):
        return False
    return "/chat/" in cleaned


def read_ebook_status(file_path: Path = EBOOK_STATUS_FILE):
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


def update_ebook_status(claude_url: str = None, title: str = None, ebook_downloaded: bool = None, file_path: Path = EBOOK_STATUS_FILE):
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
    minor_wait()


# =========================
# HELPER FUNCTIONS FOR PAGE INTERACTION
# =========================
def check_token_exhausted(page, loc) -> bool:
    """Checks if any token limit error is visible on screen."""
    major_wait()

    out_of_free = smart_is_visible(page, loc.claude_out_of_free_messages_text, "out of free messages text")

    if out_of_free:
        print("[ALERT] Token limit signal detected!", flush=True)
        return True

    return False


def wait_for_stop_button_to_disappear(page, loc, is_fresh_run: bool = False, topic_title: str = ""):
    """Infinitely waits while the Stop response button is visible."""
    print("[STEP] Waiting for output generation to complete (infinitely monitoring stop button)...", flush=True)
    while True:
        if check_token_exhausted(page, loc):
            if is_fresh_run:
                current_url = page.url
                if _is_real_session_url(current_url):
                    print(f"[INFO] Updating status file before exiting due to token exhaustion: URL={current_url}, Title={topic_title}", flush=True)
                    update_ebook_status(claude_url=current_url, title=topic_title)
            print("[EXIT] Token exhausted during generation process. Exiting with sys.exit(0)...", flush=True)
            sys.exit(0)

        stop_visible = smart_is_visible(page, loc.claude_stop_response_button, "Stop response button")
        if stop_visible:
            time.sleep(5)
        else:
            print("[OK] Stop button disappeared. Generation finished.", flush=True)
            minor_wait()
            break


def send_prompt_text(page, loc, prompt_text: str):
    """Inputs text into chat prompt field and clicks send button."""
    input_text_box = loc.claude_chat_input_box
    input_text_box.wait_for(state="visible", timeout=30000)

    minor_wait()

    print("[STEP] Typing prompt into input field...", flush=True)
    input_text_box.fill(prompt_text)

    minor_wait()

    print("[STEP] Clicking send button...", flush=True)
    smart_click(page, loc.claude_chat_send_button, "chat send button")

    minor_wait()


# =========================
# MAIN
# =========================
def run():
    print("[START] Script started", flush=True)

    saved_claude_url, saved_title, is_downloaded = read_ebook_status(EBOOK_STATUS_FILE)

    if is_downloaded:
        print("[STOP] 'ebook_downloaded' is set to True in ebook_status.json. Program will not run. Exiting cleanly...", flush=True)
        sys.exit(0)

    cookies = load_cookies(COOKIES_FILE)

    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()

    browser = None
    page = None
    try:
        browser = pw.chromium.launch(headless=HEADLESS, args=LAUNCH_ARGS)

        context = browser.new_context(no_viewport=True, user_agent=USER_AGENT)

        context.grant_permissions(GRANT_PERMISSIONS)
        context.add_cookies(cookies)

        page = context.new_page()

        loc = load_locators("claude", page)

        # Decide starting URL & initial prompt action
        if _is_real_session_url(saved_claude_url):
            target_url = saved_claude_url
            is_fresh_run = False
            topic_title = saved_title
            print(f"[STEP] Existing claude session found. Navigating to: {target_url}", flush=True)
        else:
            target_url = "https://claude.ai/new"
            is_fresh_run = True
            topic_title = get_next_ebook_topic(EBOOK_IDEAS_FILE)
            print(f"[STEP] No resumable session. Starting fresh run at: {target_url}", flush=True)

        try:
            page.goto(target_url, wait_until="load", timeout=120000)
        except Exception as goto_err:
            print(f"[WARN] Initial navigation failed ({goto_err}). Refreshing page once and retrying...", flush=True)
            try:
                page.reload(wait_until="load", timeout=120000)
                print("[OK] Page refreshed successfully. Retrying...", flush=True)
            except Exception as reload_err:
                print(f"[ERROR] Page refresh also failed: {reload_err}", flush=True)
                raise
        minor_wait()

        # Verify login status using test-id 'user-menu-button'
        print("[STEP] Verifying login status via 'user-menu-button'...", flush=True)
        user_menu_button = loc.claude_user_menu_button
        user_menu_button.wait_for(state="visible", timeout=120000)
        print("[SUCCESS] Login success! User menu button detected.", flush=True)
        minor_wait()

        # Accept cookies banner if present
        accept_cookies = page.locator("span", has_text="Accept all cookies")
        try:
            if accept_cookies.count() > 0 and accept_cookies.first.is_visible():
                print("[STEP] 'Accept all cookies' banner detected. Clicking it...", flush=True)
                accept_cookies.first.click(timeout=5000)
                minor_wait()
                print("[OK] Cookies accepted.", flush=True)
        except Exception as cookie_err:
            print(f"[WARN] Could not click 'Accept all cookies': {cookie_err}", flush=True)

        # Token exhaust check right after navigation
        if check_token_exhausted(page, loc):
            if is_fresh_run:
                current_url = page.url
                if _is_real_session_url(current_url):
                    print(f"[INFO] Updating status file before initial exit: URL={current_url}, Title={topic_title}", flush=True)
                    update_ebook_status(claude_url=current_url, title=topic_title)
            print("[EXIT] Token limit reached immediately upon load. Exiting with sys.exit(0)...", flush=True)
            sys.exit(0)

        # Fresh execution flow: send primary prompt
        if is_fresh_run:
            final_prompt = PROMPT_TEMPLATE.format(title=topic_title)
            send_prompt_text(page, loc, final_prompt)

            # Wait for generation to end
            wait_for_stop_button_to_disappear(page, loc, is_fresh_run=True, topic_title=topic_title)

            # Update status JSON with generated chat URL (only real session URLs)
            current_url = page.url
            print(f"[INFO] New Chat URL: {current_url}", flush=True)
            if _is_real_session_url(current_url):
                update_ebook_status(claude_url=current_url, title=topic_title)
            else:
                print("[WARN] Generated URL is not a real session (still 'new'); skipping status URL update.", flush=True)
                update_ebook_status(title=topic_title)

        # Download & Continuation Loop
        download_button_locator = loc.claude_transcript_download_button.or_(
            loc.claude_download_label_button
        )
        continue_button_locator = loc.claude_continue_button

        while True:
            if check_token_exhausted(page, loc):
                if is_fresh_run:
                    current_url = page.url
                    if _is_real_session_url(current_url):
                        print(f"[INFO] Updating status file before loop exit: URL={current_url}, Title={topic_title}", flush=True)
                        update_ebook_status(claude_url=current_url, title=topic_title)
                print("[EXIT] Token limit reached. Exiting with sys.exit(0)...", flush=True)
                sys.exit(0)

            download_visible = smart_is_visible(page, download_button_locator, "transcript download button")
            if download_visible:
                print("[SUCCESS] Download button located!", flush=True)

                # Ensure target directory exists
                ebook_dir = ROOT_DIR / PATHS_CFG["ebook_dir"]
                ebook_dir.mkdir(parents=True, exist_ok=True)
                target_path = ebook_dir / "ebook.pdf"

                print("[STEP] Downloading ebook PDF...", flush=True)
                # Open the file card before clicking download
                file_card_open = page.get_by_test_id("file-card-open")
                try:
                    if file_card_open.count() > 0 and file_card_open.first.is_visible():
                        print("[STEP] Opening file card before download...", flush=True)
                        file_card_open.first.click(timeout=5000)
                        minor_wait()
                        print("[OK] File card opened.", flush=True)
                except Exception as card_err:
                    print(f"[WARN] Could not open file card: {card_err}", flush=True)

                vision_locator, _ = vision_resolve(page, "transcript download button")
                download_locator = vision_locator or download_button_locator
                with page.expect_download(timeout=120000) as download_info:
                    download_locator.first.click()

                download = download_info.value
                download.save_as(target_path)
                print(f"[SUCCESS] Ebook saved successfully to: {target_path}", flush=True)
                minor_wait()

                # Update status
                if is_fresh_run:
                    current_url = page.url
                    if _is_real_session_url(current_url):
                        update_ebook_status(claude_url=current_url, title=topic_title, ebook_downloaded=True)
                    else:
                        print("[WARN] Downloaded URL is not a real session (still 'new'); skipping status URL update.", flush=True)
                        update_ebook_status(title=topic_title, ebook_downloaded=True)
                else:
                    update_ebook_status(ebook_downloaded=True)
                break

            # Check Continue button or send fallback continuation prompt
            elif smart_is_visible(page, continue_button_locator, "Continue button"):
                print("[STEP] 'Continue' button detected. Sending continuation prompt...", flush=True)
                send_prompt_text(page, loc, "Continue and complete the last query.")
                wait_for_stop_button_to_disappear(page, loc, is_fresh_run=is_fresh_run, topic_title=topic_title)

            # Neither Download nor Continue button found: send completion prompt
            else:
                print("[STEP] Neither Download nor Continue button detected. Sending continuation prompt...", flush=True)
                send_prompt_text(page, loc, "Continue and complete the last query.")
                wait_for_stop_button_to_disappear(page, loc, is_fresh_run=is_fresh_run, topic_title=topic_title)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR] Script execution broke down due to trace:", e, flush=True)
        report_error_and_upload(page)
        sys.exit(1)

    finally:
        # Wait before closing browser/teardown
        custom_closing_wait()

        if browser:
            try:
                browser.close()
            except Exception:
                pass

        try:
            pw_cm.__exit__(None, None, None)
        except Exception:
            pass

        print("[DONE] Script execution environment torn down cleanly.", flush=True)


if __name__ == "__main__":
    run()