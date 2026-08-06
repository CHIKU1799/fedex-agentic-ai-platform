try:
    import cv2
    import numpy as np
    from pyzbar.pyzbar import decode as pyzbar_decode
    _BARCODE_AVAILABLE = True
except Exception:
    _BARCODE_AVAILABLE = False


def decode_barcode(image_bytes):
    if not _BARCODE_AVAILABLE:
        return None

    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    barcodes = pyzbar_decode(img)

    for barcode in barcodes:
        return barcode.data.decode("utf-8")

    return None