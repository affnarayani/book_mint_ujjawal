import re
import sys

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from common.common import (
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

GUMROAD_COOKIES_FILE = path("gumroad_cookies")
STATUS_FILE = path("ebook_status")
DETAILS_FILE = path("ebook_details")

EBOOK_PDF = path("ebook_pdf")
BANNER_PNG = path("ebook_banner_png")
THUMBNAIL_PNG = path("ebook_thumbnail_png")

GUMROAD_DASHBOARD_URL = "https://app.gumroad.com/products"


# =========================
# URL SLUG GENERATOR
# =========================
def generate_custom_url_slug(title: str) -> str:
    """
    Generates a clean URL slug from the full title.
    Removes special characters/punctuation, replaces spaces with hyphens, and converts to lowercase.
    e.g., 'Medium Vault: How Course Funnels Builds $1,000/Month' -> 'medium-vault-how-course-funnels-builds-1000-month'
    """
    cleaned = re.sub(r'[^a-zA-Z0-9\s-]', '', title)
    slug = re.sub(r'[\s-]+', '-', cleaned).strip('-').lower()
    return slug


# =========================
# MAIN EXECUTION
# =========================
def run():
    print(f"--- Starting Gumroad Publishing Process (HEADLESS={HEADLESS}) ---", flush=True)

    status_data = load_status(STATUS_FILE)
    kdp_uploaded = get_status_value(status_data, "kdp_uploaded")
    gumroad_uploaded = get_status_value(status_data, "gumroad_uploaded")

    # MUST run only when kdp_uploaded is True and gumroad_uploaded is False
    if not kdp_uploaded:
        print("[INFO] 'kdp_uploaded' is not True. Cannot upload to Gumroad yet. Exiting safely.", flush=True)
        sys.exit(0)

    if gumroad_uploaded is True:
        print("[INFO] 'gumroad_uploaded' is already True. Exiting safely.", flush=True)
        sys.exit(0)

    if not EBOOK_PDF.exists():
        raise FileNotFoundError(f"Required manuscript file missing at '{EBOOK_PDF}'!")
    if not BANNER_PNG.exists():
        raise FileNotFoundError(f"Required banner image missing at '{BANNER_PNG}'!")
    if not THUMBNAIL_PNG.exists():
        raise FileNotFoundError(f"Required thumbnail image missing at '{THUMBNAIL_PNG}'!")

    details_data = load_details(DETAILS_FILE)

    full_title = get_status_value(status_data, "title") or ""
    description_gumroad = details_data.get("description_gumroad", "")
    custom_url_slug = generate_custom_url_slug(full_title)

    print(f"[INFO] Full Title: '{full_title}'", flush=True)
    print(f"[INFO] Generated Custom URL Slug: '{custom_url_slug}'", flush=True)

    cookies = load_cookies(GUMROAD_COOKIES_FILE)

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

        loc = load_locators("gumroad", page)

        print("[STEP] Navigating to Gumroad Dashboard URL...", flush=True)
        page.goto(GUMROAD_DASHBOARD_URL, wait_until="load")
        minor_wait()

        # ---------------------------------------------------------
        # STEP 1: NEW PRODUCT CREATION
        # ---------------------------------------------------------
        print("[STEP] Clicking 'New product' button...", flush=True)
        smart_click(page, loc.gumroad_new_product_link, "'New product' link")
        minor_wait()

        print(f"[STEP] Filling Name field with complete title: '{full_title}'...", flush=True)
        smart_fill(page, loc.gumroad_name_textbox, full_title, "product Name textbox")
        minor_wait()

        print("[STEP] Selecting 'E-book' type radio button...", flush=True)
        smart_click(page, loc.gumroad_ebook_radio, "E-book type radio")
        minor_wait()

        print(f"[STEP] Filling Price field (${PUBLISH_CFG['gumroad_price']})...", flush=True)
        smart_fill(page, loc.gumroad_price_textbox, PUBLISH_CFG["gumroad_price"], "Price textbox")
        minor_wait()

        print("[STEP] Clicking 'Next: Customize' button...", flush=True)
        smart_click(page, loc.gumroad_next_customize_button, "Next: Customize button")
        major_wait()

        # ---------------------------------------------------------
        # STEP 2: CUSTOMIZE PRODUCT DETAILS & MEDIA
        # ---------------------------------------------------------
        print("[STEP] Filling Description field from description_gumroad...", flush=True)
        description_box = resolve_locator(page, loc.gumroad_description_label, "Description field")
        if description_box is None:
            raise RuntimeError("Could not locate the Gumroad description field.")
        description_box.click()
        description_box.fill(description_gumroad)
        minor_wait()

        print(f"[STEP] Filling Custom URL field with slug: '{custom_url_slug}'...", flush=True)
        smart_fill(page, loc.gumroad_url_textbox, custom_url_slug, "custom URL textbox")
        minor_wait()

        print("[STEP] Uploading Banner / Cover Image...", flush=True)
        smart_click(page, loc.gumroad_upload_images_button, "Upload images or videos button")
        minor_wait()

        with page.expect_file_chooser() as fc_info_banner:
            smart_click(page, loc.gumroad_computer_files_tab, "Computer files tab")
        file_chooser_banner = fc_info_banner.value
        file_chooser_banner.set_files(str(BANNER_PNG.resolve()))
        print("[OK] Banner PNG uploaded successfully.", flush=True)
        major_wait()

        print("[STEP] Uploading Thumbnail Image...", flush=True)
        with page.expect_file_chooser() as fc_info_thumb:
            smart_click(page, loc.gumroad_upload_text, "Upload text")
        file_chooser_thumb = fc_info_thumb.value
        file_chooser_thumb.set_files(str(THUMBNAIL_PNG.resolve()))
        print("[OK] Thumbnail PNG uploaded successfully.", flush=True)
        major_wait()

        print("[STEP] Clicking 'Save and continue' button...", flush=True)
        smart_click(page, loc.gumroad_save_and_continue_button, "Save and continue button")
        major_wait()

        # ---------------------------------------------------------
        # STEP 3: CONTENT / FILE UPLOAD
        # ---------------------------------------------------------
        print("[STEP] Uploading eBook PDF File...", flush=True)
        smart_click(page, loc.gumroad_upload_files_button, "Upload your files button")
        minor_wait()

        with page.expect_file_chooser() as fc_info_pdf:
            smart_click(page, loc.gumroad_computer_files_menuitem, "Computer files menu item")
        file_chooser_pdf = fc_info_pdf.value
        file_chooser_pdf.set_files(str(EBOOK_PDF.resolve()))
        print("[OK] eBook PDF uploaded successfully.", flush=True)
        major_wait()

        print("[STEP] Saving Content changes...", flush=True)
        smart_click(page, loc.gumroad_save_changes_button, "Save changes button")
        major_wait()

        # ---------------------------------------------------------
        # STEP 4: RECEIPT CONFIGURATION
        # ---------------------------------------------------------
        print("[STEP] Switching to 'Receipt' tab...", flush=True)
        smart_click(page, loc.gumroad_receipt_tab, "Receipt tab")
        minor_wait()

        print(f"[STEP] Filling Button text field ('{PUBLISH_CFG['gumroad_button_text']}')...", flush=True)
        smart_fill(page, loc.gumroad_button_text_textbox, PUBLISH_CFG["gumroad_button_text"], "Button text textbox")
        minor_wait()

        custom_message = f"Thank you for purchasing {full_title}."
        print(f"[STEP] Filling Custom message textbox: '{custom_message}'...", flush=True)
        smart_fill(page, loc.gumroad_custom_message_textbox, custom_message, "Custom message textbox")
        minor_wait()

        print("[STEP] Saving Receipt changes...", flush=True)
        smart_click(page, loc.gumroad_save_changes_button, "Save changes button")
        major_wait()

        # ---------------------------------------------------------
        # STEP 5: PUBLISH PRODUCT
        # ---------------------------------------------------------
        print("[STEP] Clicking 'Publish and continue' button...", flush=True)
        smart_click(page, loc.gumroad_publish_and_continue_button, "Publish and continue button")
        major_wait()

        # ---------------------------------------------------------
        # SUCCESS & STATUS UPDATE
        # ---------------------------------------------------------
        update_status_value(status_data, "gumroad_uploaded", True)
        save_status(STATUS_FILE, status_data)
        print("[OK] Product successfully published to Gumroad & status updated to gumroad_uploaded=True!", flush=True)

    except Exception as e:
        print(f"[ERROR] Exception during Gumroad execution: {e}", flush=True)
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
        print("[DONE] Gumroad Script execution finished.", flush=True)


if __name__ == "__main__":
    run()