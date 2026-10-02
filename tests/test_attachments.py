import base64
import unittest
import fitz
from webmail.attachments import attachment_metadata, pdf_page, preview_kind

class AttachmentTests(unittest.TestCase):
    def test_only_rendered_inline_images_are_hidden(self):
        a={'id':'0','name':'picture.png','mime':'image/png','data':base64.b64encode(b'png').decode()}
        self.assertTrue(attachment_metadata(a,'<img src="data:image/png;base64,'+a['data']+'">')['inline'])
        self.assertFalse(attachment_metadata(a,'<p>No image here</p>')['inline'])
        self.assertNotIn('data',attachment_metadata(a,''))
        self.assertIsNone(preview_kind({'mime':'image/svg+xml','name':'image.svg'}))
    def test_pdf_raster_and_pagination(self):
        with fitz.open() as doc:
            doc.new_page().insert_text((72,72),'First page')
            doc.new_page().insert_text((72,72),'Second page')
            data=doc.tobytes()
        first,count=pdf_page(data,0);second,_=pdf_page(data,1)
        self.assertEqual(count,2);self.assertTrue(first.startswith(b'\x89PNG'));self.assertNotEqual(first,second)
        with self.assertRaises(ValueError):pdf_page(data,2)
        with self.assertRaises(ValueError):pdf_page(data,-1)
