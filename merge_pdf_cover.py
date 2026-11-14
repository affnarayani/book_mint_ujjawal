import os
import sys

from PIL import Image
from pypdf import PdfReader, PdfWriter

from common.common import (
    load_status,
    path,
    save_status,
    update_status_value,
    get_status_value,
)

# File paths
STATUS_FILE = path("ebook_status")
pdf_path = str(path("ebook_pdf"))
cover_path = str(path("ebook_cover_jpg"))
temp_cover_pdf = str(path("ebook_dir") / "temp_cover.pdf")
temp_output_pdf = str(path("ebook_dir") / "temp_ebook.pdf")


# =========================
# MAIN MERGE LOGIC
# =========================
def run():
    status_data = load_status(STATUS_FILE)
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
            raise FileNotFoundError(f"Required PDF file missing at '{pdf_path}'")
        if not os.path.exists(cover_path):
            raise FileNotFoundError(f"Required cover file missing at '{cover_path}'")

        # 1. Read the first page dimensions of the source PDF directly (without converting)
        reader = PdfReader(pdf_path)
        first_page = reader.pages[0]

        # PDF dimensions are in points (1 pt = 1/72 inch)
        width_pt = float(first_page.mediabox.width)
        height_pt = float(first_page.mediabox.height)

        print(f"PDF First Page Dimensions: {width_pt:.2f} pt x {height_pt:.2f} pt", flush=True)

        # 2. Fit/resize the cover JPG to the same dimensions and build a temporary PDF
        dpi = 300  # High resolution / quality
        width_px = int(round(width_pt * dpi / 72))
        height_px = int(round(height_pt * dpi / 72))

        with Image.open(cover_path) as img:
            # Resize the JPG to match the PDF dimensions
            resized_img = img.resize((width_px, height_px), Image.Resampling.LANCZOS)
            # Save as a temporary PDF
            resized_img.save(temp_cover_pdf, "PDF", resolution=float(dpi))

        # 3. Merging process: merge the resized cover page into the beginning (first page) of ebook.pdf
        pdf_writer = PdfWriter()

        # Add the cover page first
        cover_reader = PdfReader(temp_cover_pdf)
        pdf_writer.add_page(cover_reader.pages[0])

        # Then append all pages of the original ebook
        for page in reader.pages:
            pdf_writer.add_page(page)

        # Save the new merged PDF
        with open(temp_output_pdf, "wb") as output_file:
            pdf_writer.write(output_file)

        # 4. Clean up temporary files
        if os.path.exists(temp_cover_pdf):
            os.remove(temp_cover_pdf)

        # 5. Replace the original ebook.pdf with the new merged PDF
        os.replace(temp_output_pdf, pdf_path)

        print("Cover successfully merged into the first page and ebook/ebook.pdf replaced!", flush=True)

        # Update status
        update_status_value(status_data, "pdf_merged", True)
        save_status(STATUS_FILE, status_data)
        print("[OK] 'pdf_merged' successfully set to True in ebook_status.json!", flush=True)

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