import fitz
import numpy as np
from rapidocr_onnxruntime import RapidOCR

# Create a simple PDF page with text
doc = fitz.open()
page = doc.new_page(width=200, height=100)
page.insert_text((20, 50), "Hello OCR World", fontsize=16)

# Render page to pixmap
pix = page.get_pixmap()

# Convert pixmap to RGB if it is RGBA
if pix.n == 4:
    pix = fitz.Pixmap(fitz.csRGB, pix)
img_np = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3)

engine = RapidOCR()
res, elapse = engine(img_np)
print("OCR Result on rendered text:", res)
if res:
    for line in res:
        print("Text:", line[1], "Score:", line[2])
