try:
    import pytesseract
    import cv2
    import numpy as np
    _OCR_AVAILABLE = True
except Exception:
    _OCR_AVAILABLE = False


def extract_text_from_image(image_bytes):
    if not _OCR_AVAILABLE:
        return "OCR not available: pytesseract/cv2 not installed"

    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    text = pytesseract.image_to_string(img)
    return text