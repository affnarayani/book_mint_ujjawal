import random
import re
import sys
import time

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from common.common import (
    APP_CFG,
    BROWSER_CFG,
    PUBLISH_CFG,
    load_cookies,
    load_details,
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
from common.gemini_vision import (
    resolve_locator,
    smart_click,
    smart_fill,
)

# =========================
# CONFIG
# =========================
HEADLESS = BROWSER_CFG["headless"]
USER_AGENT = BROWSER_CFG["user_agent"]
LAUNCH_ARGS = BROWSER_CFG["launch_args"]
GRANT_PERMISSIONS = BROWSER_CFG["grant_permissions"]

KDP_COOKIES_FILE = path("kdp_cookies")
STATUS_FILE = path("ebook_status")
DETAILS_FILE = path("ebook_details")

MANUSCRIPT_PDF = path("ebook_pdf")
COVER_PNG = path("ebook_cover_jpg")

BOOKSHELF_URL = "https://kdp.amazon.com/en_US/bookshelf?ref_=kdp_kdp_TAC_TN_bs"


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
# MAIN EXECUTION
# =========================
def run():
    print(f"--- Starting KDP Publishing Process (HEADLESS={HEADLESS}) ---", flush=True)

    status_data = load_status(STATUS_FILE)
    pdf_merged = get_status_value(status_data, "pdf_merged")
    kdp_uploaded = get_status_value(status_data, "kdp_uploaded")

    if not pdf_merged:
        print("[INFO] 'pdf_merged' is not True. Cannot upload to KDP yet. Exiting safely.", flush=True)
        sys.exit(0)

    if kdp_uploaded is True:
        print("[INFO] 'kdp_uploaded' is already True. Exiting safely.", flush=True)
        sys.exit(0)

    if not MANUSCRIPT_PDF.exists():
        raise FileNotFoundError(f"Required manuscript file missing at '{MANUSCRIPT_PDF}'!")
    if not COVER_PNG.exists():
        raise FileNotFoundError(f"Required cover image missing at '{COVER_PNG}'!")

    details_data = load_details(DETAILS_FILE)

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
        browser = pw.chromium.launch(headless=HEADLESS, args=LAUNCH_ARGS)

        context = browser.new_context(no_viewport=True, user_agent=USER_AGENT)
        context.grant_permissions(GRANT_PERMISSIONS)

        print("[STEP] Adding cookies to browser context...", flush=True)
        context.add_cookies(cookies)

        page = context.new_page()
        print("[OK] Cookies added successfully", flush=True)

        loc = load_locators("kdp", page)

        print("[STEP] Navigating to KDP Bookshelf URL...", flush=True)
        page.goto(BOOKSHELF_URL, wait_until="load")
        minor_wait()

        # ---------------------------------------------------------
        # PAGE 1: EBOOK DETAILS
        # ---------------------------------------------------------
        print("[STEP] Clicking '+ Create new title or series'...", flush=True)
        smart_click(page, loc.kdp_create_new_title_link, "'+ Create new title or series' link")
        minor_wait()

        print("[STEP] Clicking 'Create eBook'...", flush=True)
        smart_click(page, loc.kdp_create_ebook_button, "'Create eBook' button")
        minor_wait()

        # Password Verification Prompt Handling
        password_field = resolve_locator(page, loc.kdp_password_textbox, "password textbox")
        if password_field is not None and password_field.is_visible():
            print("[SECURITY] Password verification prompt detected!", flush=True)
            import os
            kdp_password = os.getenv("PASSWORD")
            if not kdp_password:
                raise RuntimeError("Password prompt appeared, but 'PASSWORD' is not set in environment variables!")

            print("[STEP] Entering KDP password from environment...", flush=True)
            password_field.fill(kdp_password)
            minor_wait()

            print("[STEP] Clicking 'Sign in'...", flush=True)
            smart_click(page, loc.kdp_sign_in_button, "'Sign in' button")

            major_wait()

            confirm_button = resolve_locator(page, loc.kdp_password_confirm_button, "password confirmation button")
            if confirm_button is not None and confirm_button.is_visible():
                print("[SECURITY] Confirmation prompt detected! Clicking confirm button...", flush=True)
                smart_click(page, loc.kdp_password_confirm_button, "password confirmation button")
                minor_wait()

        print("[STEP] Selecting language using keyboard navigation...", flush=True)
        smart_click(page, loc.kdp_language_dropdown_button, "language dropdown button")
        minor_wait()

        page.keyboard.press("ArrowDown")
        minor_wait()
        page.keyboard.press("Enter")
        minor_wait()

        print(f"[STEP] Filling eBook Title: '{title_text}'...", flush=True)
        smart_fill(page, loc.kdp_title_input, title_text, "eBook title input")
        minor_wait()

        if subtitle_text:
            print(f"[STEP] Filling eBook Subtitle: '{subtitle_text}'...", flush=True)
            smart_fill(page, loc.kdp_subtitle_input, subtitle_text, "eBook subtitle input")
            minor_wait()

        print("[STEP] Filling Author Name...", flush=True)
        smart_fill(page, loc.kdp_author_first_name_input, APP_CFG["default_author_first_name"], "author first name input")
        minor_wait()
        smart_fill(page, loc.kdp_author_last_name_input, APP_CFG["default_author_last_name"], "author last name input")
        minor_wait()

        # ---------------------------------------------------------
        # PASTE VIA CLIPBOARD & REFRESH WORD COUNT
        # ---------------------------------------------------------
        print("[STEP] Pasting HTML Description into Rich Text Editor...", flush=True)

        editor_body = resolve_locator(page, loc.kdp_description_editor_frame, "rich text editor body")
        if editor_body is None:
            raise RuntimeError("Could not locate the KDP rich text editor.")
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
        smart_click(page, loc.kdp_own_copyright_radio, "I own the copyright radio")
        minor_wait()

        print("[STEP] Setting Sexually Explicit status...", flush=True)
        smart_click(page, loc.kdp_not_explicit_radio, "not explicit radio")
        minor_wait()

        print("[STEP] Selecting Primary Marketplace (Amazon.com)...", flush=True)
        smart_click(page, loc.kdp_marketplace_dropdown, "marketplace dropdown")
        minor_wait()
        smart_click(page, loc.kdp_marketplace_amazon_com_option, "Amazon.com option")
        minor_wait()

        print("[STEP] Choosing Categories...", flush=True)
        smart_click(page, loc.kdp_choose_categories_button, "Choose categories button")
        minor_wait()

        smart_click(page, loc.kdp_category_select_one_span, "'Select one' category span")
        minor_wait()

        print("[STEP] Scrolling drop-down list and selecting category by XPath...", flush=True)
        scroll_container = resolve_locator(page, loc.kdp_category_scroll_container, "category scroll container")
        if scroll_container is not None:
            scroll_container.wait_for(state="visible", timeout=10000)

        category_target = resolve_locator(page, loc.kdp_category_target_link, "category target link")
        if category_target is None:
            raise RuntimeError("Could not locate category target link.")
        category_target.scroll_into_view_if_needed()
        minor_wait()
        category_target.click()
        minor_wait()

        for cat in subcategories:
            print(f"[STEP] Selecting Placement Category: '{cat}'...", flush=True)
            cat_locator = loc.kdp_category_text_template(cat)
            smart_click(page, cat_locator, f"placement category '{cat}'")
            minor_wait()

        smart_click(page, loc.kdp_save_categories_button, "Save categories button")
        minor_wait()

        # ---------------------------------------------------------
        # FILLING KEYWORDS IN TITLE CASE
        # ---------------------------------------------------------
        print("[STEP] Filling Title-Cased Keywords...", flush=True)
        for i, kw in enumerate(keywords[:7]):
            print(f" -> Keyword {i}: '{kw}'", flush=True)
            kw_locator = page.locator(loc.kdp_keyword_input_template.format(index=i))
            kw_locator.click()
            kw_locator.press_sequentially(kw, delay=random.randint(20, 50))
            minor_wait()

        print("[STEP] Setting Release Status...", flush=True)
        smart_click(page, loc.kdp_ready_to_release_button, "I am ready to release my book button")
        minor_wait()

        print("[STEP] Saving and Continuing to Content Page...", flush=True)
        smart_click(page, loc.kdp_save_and_continue_button, "Save and Continue button")
        major_wait()

        # ---------------------------------------------------------
        # PAGE 2: EBOOK CONTENT
        # ---------------------------------------------------------
        print("[STEP] Uploading Manuscript PDF...", flush=True)
        with page.expect_file_chooser() as fc_info_pdf:
            smart_click(page, loc.kdp_upload_manuscript_button, "Upload manuscript button")
        file_chooser_pdf = fc_info_pdf.value
        file_chooser_pdf.set_files(str(MANUSCRIPT_PDF.resolve()))
        print("[OK] Manuscript PDF file attached successfully.", flush=True)
        minor_wait()

        print("[STEP] Clicking 'Continue with PDF'...", flush=True)
        smart_click(page, loc.kdp_continue_with_pdf_button, "Continue with PDF button")
        major_wait()

        print("[STEP] Setting DRM option...", flush=True)
        smart_click(page, loc.kdp_apply_drm_radio, "apply DRM radio")
        minor_wait()

        # COVER UPLOAD FLOW
        print("[STEP] Uploading Cover Image...", flush=True)
        smart_click(page, loc.kdp_upload_cover_choose_button, "upload a cover you already have button")
        minor_wait()

        with page.expect_file_chooser() as fc_info_cover:
            smart_click(page, loc.kdp_upload_cover_file_button, "upload your cover file button")
        file_chooser_cover = fc_info_cover.value
        file_chooser_cover.set_files(str(COVER_PNG.resolve()))
        print("[OK] Cover JPG file uploaded successfully.", flush=True)
        major_wait()

        print("[STEP] Setting AI Disclosure...", flush=True)
        smart_click(page, loc.kdp_ai_disclosure_yes_link, "AI disclosure Yes link")
        minor_wait()

        # AI Texts
        smart_click(page, loc.kdp_ai_texts_label, "AI texts label")
        minor_wait()
        smart_click(page, loc.kdp_ai_texts_some_sections_option, "AI texts some sections option")
        minor_wait()
        smart_fill(page, loc.kdp_ai_texts_tool_placeholder, PUBLISH_CFG["ai_text_tool"], "AI texts tool placeholder")
        minor_wait()

        # AI Images
        smart_click(page, loc.kdp_ai_images_label, "AI images label")
        minor_wait()
        smart_click(page, loc.kdp_ai_images_one_or_few_option, "AI images one or a few option")
        minor_wait()
        smart_fill(page, loc.kdp_ai_images_tool_placeholder, PUBLISH_CFG["ai_image_tool"], "AI images tool placeholder")
        minor_wait()

        # AI Translations
        smart_click(page, loc.kdp_ai_translations_label, "AI translations label")
        minor_wait()
        smart_click(page, loc.kdp_ai_translations_none_option, "AI translations none option")
        minor_wait()

        print("[STEP] Confirming AI answers...", flush=True)
        smart_click(page, loc.kdp_ai_confirm_checkbox, "AI confirm checkbox")
        minor_wait()

        print("[STEP] Saving and Continuing to Pricing Page...", flush=True)
        smart_click(page, loc.kdp_save_and_continue_button, "Save and Continue button")
        major_wait()

        # ---------------------------------------------------------
        # PAGE 3: EBOOK PRICING & RIGHTS
        # ---------------------------------------------------------
        print("[STEP] Setting Territories...", flush=True)
        smart_click(page, loc.kdp_all_territories_button, "all territories button")
        minor_wait()

        print("[STEP] Setting Royalty Plan (70%)...", flush=True)
        smart_click(page, loc.kdp_royalty_70_label_icon, "70% royalty label icon")
        minor_wait()

        print(f"[STEP] Setting Price (${PUBLISH_CFG['kdp_price']})...", flush=True)
        smart_fill(page, loc.kdp_price_input, PUBLISH_CFG["kdp_price"], "price input")
        minor_wait()

        # ---------------------------------------------------------
        # PUBLISH EBOOK
        # ---------------------------------------------------------
        print("[STEP] Publishing Kindle eBook...", flush=True)
        smart_click(page, loc.kdp_publish_button, "Publish Your Kindle eBook button")
        major_wait()
        update_status_value(status_data, "kdp_uploaded", True)
        save_status(STATUS_FILE, status_data)
        print("[OK] Book successfully published to KDP!", flush=True)

    except Exception as e:
        print(f"[ERROR] Exception during KDP execution: {e}", flush=True)
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
        print("[DONE] KDP Script execution finished.", flush=True)


if __name__ == "__main__":
    run()