import os
from PIL import Image
from pypdf import PdfReader, PdfWriter

# File paths
pdf_path = "ebook/ebook.pdf"
cover_path = "ebook/cover.jpg"
temp_cover_pdf = "ebook/temp_cover.pdf"
temp_output_pdf = "ebook/temp_ebook.pdf"

# 1. Direct PDF ke first page ki dimensions padhein (bina convert kiye)
reader = PdfReader(pdf_path)
first_page = reader.pages[0]

# PDF dimensions points mein hoti hain (1 pt = 1/72 inch)
width_pt = float(first_page.mediabox.width)
height_pt = float(first_page.mediabox.height)

print(f"PDF First Page Dimensions: {width_pt:.2f} pt x {height_pt:.2f} pt")

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

print("Cover successfully first page par merge ho gaya hai aur ebook/ebook.pdf replace ho gayi hai!")