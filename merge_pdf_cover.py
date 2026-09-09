import json
import os
import sys
from pathlib import Path
from PIL import Image
from pypdf import PdfReader, PdfWriter

# File paths
STATUS_FILE = Path("ebook_status.json")
pdf_path = "ebook/ebook.pdf"
cover_path = "ebook/cover.jpg"
temp_cover_pdf = "ebook/temp_cover.pdf"
temp_output_pdf = "ebook/temp_ebook.pdf"


# =========================
# STATUS HELPERS
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
# MAIN MERGE LOGIC
# =========================
def run():
    status_data = load_status()
    thumbnail_generated = get_status_value(status_data, "thumbnail_generated")
    pdf_merged = get_status_value(status_data, "pdf_merged")

    # Guard 1: thumbnail_generated must be True
    if thumbnail_generated is not True:
        print("[INFO] 'thumbnail_generated' is not True. Cannot merge cover yet. Exiting safely.", flush=True)
        sys.exit(0)

    # Guard 2: pdf_merged must not be True
    if pdf_merged is True:
        print("[INFO] 'pdf_merged' is already True. Exiting safely.", flush=True)
        sys.exit(0)

    try:
        # Check if source files exist
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"❌ Required PDF file missing at '{pdf_path}'")
        if not os.path.exists(cover_path):
            raise FileNotFoundError(f"❌ Required Cover file missing at '{cover_path}'")

        # 1. Direct PDF ke first page ki dimensions padhein (bina convert kiye)
        reader = PdfReader(pdf_path)
        first_page = reader.pages[0]

        # PDF dimensions points mein hoti hain (1 pt = 1/72 inch)
        width_pt = float(first_page.mediabox.width)
        height_pt = float(first_page.mediabox.height)

        print(f"PDF First Page Dimensions: {width_pt:.2f} pt x {height_pt:.2f} pt", flush=True)

        # 2. Cover JPG image ko usi size (dimensions) mein fit/resize karke temporary PDF banayein
        dpi = 300  # High resolution / quality
        width_px = int(round(width_pt * dpi / 72))
        height_px = int(round(height_pt * dpi / 72))

        with Image.open(cover_path) as img:
            # JPG image ko PDF dimensions ke mutabiq resize karein
            resized_img = img.resize((width_px, height_px), Image.Resampling.LANCZOS)
            # Temporary PDF format mein save karein
            resized_img.save(temp_cover_pdf, "PDF", resolution=float(dpi))

        # 3. Merging process: Resized Cover page ko ebook.pdf ke beginning (first page) mein merge karein
        pdf_writer = PdfWriter()

        # Pehle cover page add karein
        cover_reader = PdfReader(temp_cover_pdf)
        pdf_writer.add_page(cover_reader.pages[0])

        # Phir original ebook ke saare pages append karein
        for page in reader.pages:
            pdf_writer.add_page(page)

        # New merged PDF save karein
        with open(temp_output_pdf, "wb") as output_file:
            pdf_writer.write(output_file)

        # 4. Temporary PDF file cleanup karein
        if os.path.exists(temp_cover_pdf):
            os.remove(temp_cover_pdf)

        # 5. Original ebook.pdf ko new merged PDF se replace karein
        os.replace(temp_output_pdf, pdf_path)

        print("Cover successfully first page par merge ho gaya hai aur ebook/ebook.pdf replace ho gayi hai!", flush=True)

        # Update status
        update_status_value(status_data, "pdf_merged", True)
        save_status(status_data)
        print("✅ 'pdf_merged' successfully set to True in ebook_status.json!", flush=True)

    except Exception as e:
        print(f"[ERROR] Exception during PDF cover merging: {e}", flush=True)
        # Clean up temporary files if created
        if os.path.exists(temp_cover_pdf):
            os.remove(temp_cover_pdf)
        if os.path.exists(temp_output_pdf):
            os.remove(temp_output_pdf)
        sys.exit(1)


if __name__ == "__main__":
    run()