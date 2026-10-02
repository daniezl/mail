"""Local raster previews; attachment contents never execute in the app."""
import base64
import threading
from functools import lru_cache

PDF_LOCK = threading.Lock()
IMAGE_TYPES = {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}


def preview_kind(attachment):
    if attachment['mime'] in IMAGE_TYPES: return 'image'
    if attachment['mime'] == 'application/pdf' or attachment['name'].lower().endswith('.pdf'): return 'pdf'
    return None


def attachment_metadata(attachment, html):
    result = {k:v for k,v in attachment.items() if k != 'data'}
    result['preview'] = preview_kind(attachment)
    result['size'] = len(base64.b64decode(attachment['data']))
    result['inline'] = result['preview'] == 'image' and ('base64,' + attachment['data']) in html
    return result


@lru_cache(maxsize=12)
def pdf_page(data, page):
    # PyMuPDF operations are serialized because the server handles requests in threads.
    import fitz
    if len(data) > 30 * 1024 * 1024: raise ValueError('PDF too large')
    with PDF_LOCK, fitz.open(stream=data, filetype='pdf') as doc:
        if doc.needs_pass or page < 0 or page >= len(doc): raise ValueError('Page unavailable')
        sheet=doc[page]
        scale=min(2, 1400 / max(sheet.rect.width, sheet.rect.height, 1))
        pix=sheet.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        return pix.tobytes('png'), len(doc)
