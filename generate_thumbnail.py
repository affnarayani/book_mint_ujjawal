import os
import sys
import json
import base64
import random
import asyncio
import shutil
import warnings
from pathlib import Path
from dotenv import load_dotenv

# Ignore all warnings
warnings.filterwarnings("ignore")

load_dotenv()
DECRYPT_KEY = os.getenv("DECRYPT_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

if GOOGLE_API_KEY:
    os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY

HEADLESS = False  # Local testing ke liye False
ROOT_DIR = os.path.dirname(__file__)
COOKIES_DIR = os.path.join(ROOT_DIR, "cookies")
IMAGE_OUTPUT_DIR = os.path.join(ROOT_DIR, "ebook")
ERROR_SCREENSHOT = os.path.join(ROOT_DIR, "error_screenshot.png")
STATUS_FILE = Path(os.path.join(ROOT_DIR, "ebook_status.json"))
CDP_PORT = 9222

# =========================
# SECURE STREAM (Masking Secrets)
# =========================
class SecureStream:
    def __init__(self, original_stream):
        self.original_stream = original_stream
        self.sensitive_words = [v for v in [DECRYPT_KEY, GOOGLE_API_KEY] if v]

    def write(self, message):
        if message:
            for word in self.sensitive_words:
                message = message.replace(word, "[REDACTED_SECRET]")
            if "proactor_events.py" in message or "I/O operation on closed pipe" in message:
                return
        try:
            self.original_stream.write(message)
        except Exception:
            pass

    def flush(self):
        try:
            self.original_stream.flush()
        except Exception:
            pass

sys.stdout = SecureStream(sys.stdout)
sys.stderr = SecureStream(sys.stderr)

from browser_use import Agent, ChatGoogle, Browser
from playwright.async_api import async_playwright, Page
from playwright_stealth import Stealth

# --- CRYPTOGRAPHY IMPORTS ---
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


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
# CRYPTO & COOKIES HELPERS
# =========================
def _derive_key_from_password(password: bytes, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=200_000,
    )
    return kdf.derive(password)

def _decrypt_payload(payload: dict, password: str) -> bytes:
    try:
        salt = base64.b64decode(payload["s"])
        nonce = base64.b64decode(payload["n"])
        ct = base64.b64decode(payload["ct"])
        
        key = _derive_key_from_password(password.encode("utf-8"), salt)
        aesgcm = AESGCM(key)
        
        plaintext = aesgcm.decrypt(nonce, ct, None)
        return plaintext
    except Exception as e:
        raise ValueError(f"Decryption failed: {e}")

def get_random_decrypted_cookies(password: str) -> list:
    if not os.path.exists(COOKIES_DIR):
        raise FileNotFoundError(f"Directory '{COOKIES_DIR}' not found.")
    
    encrypted_files = [f for f in os.listdir(COOKIES_DIR) if f.endswith(".encrypted")]
    if not encrypted_files:
        raise FileNotFoundError(f"No encrypted cookie files found in '{COOKIES_DIR}'.")
    
    chosen_file = random.choice(encrypted_files)
    file_path = os.path.join(COOKIES_DIR, chosen_file)
    print(f"[STEP] Selected encrypted cookie file: {chosen_file}", flush=True)
    
    with open(file_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
        
    decrypted_bytes = _decrypt_payload(payload, password)
    cookies_data = json.loads(decrypted_bytes.decode("utf-8"))
    
    return cookies_data

async def check_login(page: Page) -> bool:
    try:
        profile_menu = page.get_by_role('button', name='Open profile menu')
        upgrade_btn = page.get_by_role('button', name='Upgrade', exact=True)
        
        await profile_menu.or_(upgrade_btn).first.wait_for(state="visible", timeout=20000)
        return True
    except Exception:
        return False


# =========================
# MAIN RUNNER
# =========================
async def main():
    print("[START] Image generator script initialized", flush=True)
    
    if not DECRYPT_KEY:
        print("[ERROR] DECRYPT_KEY is missing in environment/.env", flush=True)
        sys.exit(1)

    # Status check load karein
    try:
        status_data = load_status()
    except Exception as e:
        print(f"[ERROR] {e}", flush=True)
        sys.exit(1)

    banner_generated = get_status_value(status_data, "banner_generated")
    thumbnail_generated = get_status_value(status_data, "thumbnail_generated")

    if not banner_generated:
        print("[INFO] 'banner_generated' is not True. Cannot generate thumbnail yet. Exiting safely.", flush=True)
        sys.exit(0)

    if thumbnail_generated is True:
        print("[INFO] 'thumbnail_generated' is already True. Exiting safely.", flush=True)
        sys.exit(0)

    prompt_text = "Convert this attached image into a 1254x1254 1:1 ratio square thumbnail."
    print(f"[STEP] Prompt being used: {prompt_text}", flush=True)

    INPUT_COVER_PNG = os.path.join(ROOT_DIR, "ebook", "cover.png")
    if not os.path.exists(INPUT_COVER_PNG):
        print(f"[ERROR] Required input cover file not found at '{INPUT_COVER_PNG}'!", flush=True)
        sys.exit(1)

    try:
        cookies = get_random_decrypted_cookies(DECRYPT_KEY)
    except Exception as e:
        print(f"[ERROR] Error loading/decrypting cookies: {e}", flush=True)
        sys.exit(1)

    os.makedirs(IMAGE_OUTPUT_DIR, exist_ok=True)
    final_image_path = os.path.join(IMAGE_OUTPUT_DIR, "thumbnail.png")

    # 1. Playwright setup with CDP port
    stealth = Stealth()
    pw_cm = stealth.use_async(async_playwright())
    pw_instance = await pw_cm.__aenter__()

    browser_instance = await pw_instance.chromium.launch(
        headless=HEADLESS,
        args=[
            "--start-maximized",
            f"--remote-debugging-port={CDP_PORT}",
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox"
        ]
    )
    
    context = await browser_instance.new_context(
        no_viewport=True,
        accept_downloads=True,  # Downloads explicitly enabled
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    
    await context.add_cookies(cookies)
    page = await context.new_page()

    print("[STEP] Navigating to ChatGPT with cookies...", flush=True)
    await page.goto("https://chatgpt.com/", timeout=60000)
    
    print("[STEP] Verifying login status...", flush=True)
    is_logged_in = await check_login(page)

    if not is_logged_in:
        print("[ERROR] Cookie login failed: Required login/profile buttons not found.", flush=True)
        await browser_instance.close()
        await pw_cm.__aexit__(None, None, None)
        sys.exit(1)
    else:
        print("[STEP] Opening attachment menu and attaching file...", flush=True)
        try:
            async with page.expect_file_chooser() as fc_info:
                # 1. Pehle 'Add files and more' button par click karein
                await page.get_by_role('button', name='Add files and more').click()
                
                # 2. Fir 'Add photos & files Upload' button par click karein
                await page.get_by_role('button', name='Add photos & files Upload').click()
            
            # OS window open kiye bina direct file set karein
            file_chooser = await fc_info.value
            await file_chooser.set_files(str(Path(INPUT_COVER_PNG).resolve()))
            print("[OK] Attached ebook/cover.png successfully.", flush=True)
            
        except Exception as e:
            print(f"[ERROR] Failed to attach file: {e}", flush=True)
            raise e
    
    print("[SUCCESS] Cookie login verified successfully! Attaching browser-use via CDP...", flush=True)

    # 2. Browser-use Agent setup
    llm = ChatGoogle(model="gemini-3.5-flash-lite")

    task_prompt = f"""
    1. You are already on the active ChatGPT authenticated page with the image attached.
    2. Find the message input textarea/box.
    3. Type this exact prompt into it and submit: '{prompt_text}'
    4. Wait patiently for ChatGPT to generate and render the image completely.
    5. Click on the generated image to open its preview/modal view.
    6. Locate the share button and then the download button to download the image OR directly locate the download button, if available.
    7. Make sure the generated image has been downloaded successfully.
    """

    downloaded_file_path = None

    async def handle_download(download):
        nonlocal downloaded_file_path
        temp_path = await download.path()
        if temp_path:
            downloaded_file_path = temp_path
            print(f"[DOWNLOAD DETECTED] Temp path: {temp_path}", flush=True)

    context.on("download", handle_download)

    try:
        custom_browser = Browser(cdp_url=f"http://localhost:{CDP_PORT}")

        agent = Agent(
            task=task_prompt,
            llm=llm,
            browser=custom_browser
        )

        history = await agent.run()
        
        if not downloaded_file_path and hasattr(history, 'downloaded_files') and history.downloaded_files():
            downloaded_file_path = history.downloaded_files()[-1]

        if downloaded_file_path and os.path.exists(downloaded_file_path):
            shutil.copy(downloaded_file_path, final_image_path)
            print(f"\n[SUCCESS] Image successfully captured and saved to: {final_image_path}")
            
            # Status update karein aur save karein
            update_status_value(status_data, "thumbnail_generated", True)
            save_status(status_data)
            print("✅ 'thumbnail_generated' successfully set to True in ebook_status.json!", flush=True)
        else:
            print(f"\n[ERROR] Downloaded file could not be tracked or found.", flush=True)
            sys.exit(1)

    except Exception as e:
        print(f"[ERROR] Script crashed: {e}", flush=True)
        try:
            os.makedirs(os.path.dirname(ERROR_SCREENSHOT), exist_ok=True)
            await page.screenshot(path=ERROR_SCREENSHOT, full_page=True)
            print(f"[OK] Error screenshot captured: {ERROR_SCREENSHOT}", flush=True)
        except Exception:
            pass
        sys.exit(1)
        
    finally:
        try:
            await browser_instance.close()
        except:
            pass
        try:
            await pw_cm.__aexit__(None, None, None)
        except:
            pass
        try:
            sys.stderr = open(os.devnull, 'w')
        except Exception:
            pass
        print("Process Finished Cleanly.", flush=True)

    return 0

if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nUser manually stopped the task.")
        sys.exit(1)