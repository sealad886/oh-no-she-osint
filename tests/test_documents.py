import base64
import hashlib
import io
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import zlib

from local_osint import documents
from local_osint.engine import Engine, InvalidInput
from local_osint.workspace import Workspace


def office(format="docx", xml=None, extras=None):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml" if format == "docx" else "content.xml", xml or '<w:document xmlns:w="urn:word"><w:body><w:p><w:r><w:t>Public bulletin</w:t><w:br w:type="page"/><w:t>Second affiliation example.org</w:t></w:r></w:p></w:body></w:document>')
        for key, value in (extras or {}).items():
            archive.writestr(key, value)
    return out.getvalue()


def pdf(*texts, image=None):
    values = [b'<< /Type /Catalog /Pages 2 0 R >>', b'', b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    kids = []
    for text in texts:
        page_id, content_id = len(values) + 1, len(values) + 2
        kids.append(str(page_id) + ' 0 R')
        resources = b'<< /Font << /F1 3 0 R >> >>'
        if image is not None:
            width, height, pixels = image
            image_id = len(values) + 3
            resources = ('<< /XObject << /Im0 ' + str(image_id) + ' 0 R >> >>').encode()
            content = b'q 612 0 0 792 0 0 cm /Im0 Do Q'
        else:
            content = ('BT /F1 18 Tf 50 700 Td (' + text.replace('(', '\\(').replace(')', '\\)') + ') Tj ET').encode()
        values.append(b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources ' + resources + b' /Contents ' + str(content_id).encode() + b' 0 R >>')
        values.append(b'<< /Length ' + str(len(content)).encode() + b' >>\nstream\n' + content + b'\nendstream')
        if image is not None:
            compressed = zlib.compress(pixels)
            values.append(('/Type /XObject /Subtype /Image /Width ' + str(width) + ' /Height ' + str(height) + ' /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode /Length ' + str(len(compressed))).encode().join([b'<< ', b' >>\nstream\n']) + compressed + b'\nendstream')
    values[1] = ('<< /Type /Pages /Kids [' + ' '.join(kids) + '] /Count ' + str(len(kids)) + ' >>').encode()
    out = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for index, value in enumerate(values, 1):
        offsets.append(len(out))
        out.extend(str(index).encode() + b' 0 obj\n' + value + b'\nendobj\n')
    start = len(out)
    out.extend(('xref\n0 ' + str(len(values) + 1) + '\n0000000000 65535 f \n').encode())
    for offset in offsets[1:]:
        out.extend(f'{offset:010d} 00000 n \n'.encode())
    out.extend(('trailer\n<< /Size ' + str(len(values) + 1) + ' /Root 1 0 R >>\nstartxref\n' + str(start) + '\n%%EOF\n').encode())
    return bytes(out)


def image_fixture():
    glyphs = {'L': ['10000'] * 6 + ['11111'], 'O': ['01110', '10001', '10001', '10001', '10001', '10001', '01110'], 'C': ['01111', '10000', '10000', '10000', '10000', '10000', '01111'], 'A': ['01110', '10001', '10001', '11111', '10001', '10001', '10001'], 'E': ['11111', '10000', '10000', '11110', '10000', '10000', '11111'], 'V': ['10001', '10001', '10001', '10001', '10001', '01010', '00100'], 'I': ['11111', '00100', '00100', '00100', '00100', '00100', '11111'], 'D': ['11110', '10001', '10001', '10001', '10001', '10001', '11110'], 'N': ['10001', '11001', '11001', '10101', '10011', '10011', '10001']}
    text, scale = 'LOCAL EVIDENCE', 10
    width, height = len(text) * 60 + 80, 150
    pixels = bytearray([255]) * (width * height)
    for index, char in enumerate(text):
        for y, row in enumerate(glyphs.get(char, [])):
            for x, bit in enumerate(row):
                if bit == '1':
                    for dy in range(scale):
                        start = (40 + y * scale + dy) * width + 40 + index * 60 + x * scale
                        pixels[start:start + scale] = b'\0' * scale
    def chunk(kind, value):
        return struct.pack('>I', len(value)) + kind + value + struct.pack('>I', zlib.crc32(kind + value))
    filtered = b''.join(b'\0' + bytes(pixels[y * width:(y + 1) * width]) for y in range(height))
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(filtered)) + chunk(b'IEND', b'')
    return png, (width, height, bytes(pixels))


def raster_fixture(directory):
    """Render a fictional Helvetica bulletin, then remove its searchable text."""
    source, prefix = Path(directory) / 'fixture.pdf', Path(directory) / 'fixture-raster'
    source.write_bytes(pdf('PUBLIC BULLETIN'))
    subprocess.run([documents.tool_binary('pdftoppm'), '-f', '1', '-l', '1', '-singlefile', '-scale-to', '1500', '-gray', str(source), str(prefix)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    magic, width, height, maximum, pixels = prefix.with_suffix('.pgm').read_bytes().split(None, 4)
    assert magic == b'P5' and maximum == b'255'
    width, height = int(width), int(height)
    def chunk(kind, value):
        return struct.pack('>I', len(value)) + kind + value + struct.pack('>I', zlib.crc32(kind + value))
    filtered = b''.join(b'\0' + pixels[y * width:(y + 1) * width] for y in range(height))
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(filtered)) + chunk(b'IEND', b'')
    return png, (width, height, pixels)


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.engine = Engine(Path(self.tmp.name) / 'private')
        self.case = self.engine.create_case('Document fixture')['id']

    def tearDown(self):
        self.tmp.cleanup()

    def test_docx_originals_and_exact_page_spans_are_retained(self):
        raw = office()
        saved = self.engine.ingest(self.case, raw, 'Bulletin', format='docx')
        detail = self.engine.evidence(self.case, saved['id'])
        self.assertEqual(detail['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(len(detail['document']['pages']), 2)
        page = detail['document']['pages'][1]
        self.assertIn('Second affiliation', detail['text'][page['start']:page['end']])
        exported = self.engine.export(self.case)
        self.assertEqual(base64.b64decode(exported['evidence'][0]['original_base64']), raw)
        self.assertNotIn('text', saved['document'])

    def test_odt_text_and_embedded_page_break(self):
        xml = '<office:document-content xmlns:office="urn:office" xmlns:text="urn:text"><office:body><office:text><text:p>First public page<text:soft-page-break/>Second public page</text:p></office:text></office:body></office:document-content>'
        result = documents.extract(office('odt', xml), 'odt')
        self.assertEqual(len(result['pages']), 2)
        self.assertIn('First public page', result['text'][:result['pages'][0]['end']])
        self.assertIn('Second public page', result['text'][result['pages'][1]['start']:])

    def test_unsafe_or_bomb_office_containers_are_rejected(self):
        cases = [office(extras={'../escape': 'bad'}), office(xml='<!DOCTYPE x [<!ENTITY e "unsafe">]><x>&e;</x>'), office(extras={'large': 'x' * (documents.MAX_ZIP_BYTES + 1)})]
        for raw in cases:
            with self.subTest(size=len(raw)), self.assertRaises(InvalidInput):
                self.engine.ingest(self.case, raw, 'Invalid office', format='docx')
        self.assertEqual(self.engine.view(self.case)['counts']['evidence'], 0)

    def test_office_page_limit_retains_original_and_marks_partial_extraction(self):
        xml = '<w:document xmlns:w="urn:word"><w:body><w:p><w:r>' + ''.join('<w:t>Public page</w:t><w:br w:type="page"/>' for _ in range(101)) + '</w:r></w:p></w:body></w:document>'
        raw = office(xml=xml)
        saved = self.engine.ingest(self.case, raw, 'Long bulletin', format='docx')
        self.assertEqual(saved['document']['status'], 'limited')
        self.assertEqual(len(saved['document']['pages']), 100)
        with self.engine.connection() as db:
            self.assertEqual(db.execute('SELECT raw FROM evidence WHERE id=?', (saved['id'],)).fetchone()[0], raw)

    def test_missing_pdf_tool_preserves_binary_original_with_actionable_status(self):
        raw = pdf('Retained public source')
        with patch('local_osint.documents.shutil.which', return_value=None):
            saved = self.engine.ingest(self.case, raw, 'PDF original', format='pdf')
        self.assertEqual(saved['document']['status'], 'unavailable')
        self.assertTrue(saved['document']['warnings'])
        with self.engine.connection() as db:
            row = dict(db.execute('SELECT * FROM evidence WHERE id=?', (saved['id'],)).fetchone())
        self.assertEqual(row['raw'], raw)
        self.assertEqual(row['text'], '')

    def test_document_option_injection_and_oversized_image_rejected_before_native_tool(self):
        png, _ = image_fixture()
        bomb = png[:16] + struct.pack('>II', 10000, 10000) + png[24:]
        with patch('local_osint.documents._run') as native:
            for options in ({'language': 'eng;id'}, {'ocr': 'yes'}, {'command': 'id'}):
                with self.assertRaises(InvalidInput):
                    self.engine.ingest(self.case, png, 'Image', format='png', document_options=options)
            with self.assertRaises(InvalidInput):
                documents.extract(bomb, 'png')
            native.assert_not_called()

    @unittest.skipUnless(shutil.which('pdftotext'), 'Poppler unavailable')
    def test_native_pdf_text_extraction_has_page_provenance(self):
        result = documents.extract(pdf('First public bulletin', 'Second public affiliation'), 'pdf')
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(len(result['pages']), 2)
        self.assertIn('Second public affiliation', result['text'][result['pages'][1]['start']:])
        self.assertTrue(all(p['method'] == 'pdftotext' for p in result['pages']))

    @unittest.skipUnless(shutil.which('tesseract') and shutil.which('pdftoppm'), 'Tesseract/Poppler unavailable')
    def test_native_image_ocr_stays_local_and_records_method(self):
        png, _ = raster_fixture(self.tmp.name)
        result = documents.extract(png, 'png')
        self.assertEqual(result['status'], 'ready', result['warnings'])
        self.assertIn('PUBLIC BULLETIN', result['text'].upper())
        self.assertIn('tesseract', result['pages'][0]['method'])

    @unittest.skipUnless(all(shutil.which(tool) for tool in ('pdftotext', 'pdftoppm', 'tesseract')), 'Poppler/Tesseract unavailable')
    def test_native_scanned_pdf_uses_automatic_ocr(self):
        _, pixels = raster_fixture(self.tmp.name)
        result = documents.extract(pdf('', image=pixels), 'pdf')
        self.assertEqual(result['status'], 'ready', result['warnings'])
        self.assertIn('PUBLIC BULLETIN', result['text'].upper())
        self.assertEqual(result['pages'][0]['page'], 1)
        self.assertIn('tesseract', result['pages'][0]['method'])

    @unittest.skipUnless(shutil.which('pdftotext'), 'Poppler unavailable')
    def test_reextract_adds_version_without_changing_original_row_or_mentions(self):
        raw = pdf('Public second extraction')
        with patch('local_osint.documents.shutil.which', return_value=None):
            saved = self.engine.ingest(self.case, raw, 'PDF original', format='pdf')
        with self.engine.connection() as db:
            before = dict(db.execute('SELECT * FROM evidence WHERE id=?', (saved['id'],)).fetchone())
        retried = self.engine.reextract(self.case, saved['id'])
        self.assertEqual(retried['document']['status'], 'ready')
        with self.engine.connection() as db:
            after = dict(db.execute('SELECT * FROM evidence WHERE id=?', (saved['id'],)).fetchone())
            self.assertEqual(db.execute('SELECT count(*) FROM workspace_extractions WHERE evidence_id=?', (saved['id'],)).fetchone()[0], 2)
        self.assertEqual(before, after)
        retrieved = Workspace(self.engine).retrieve(self.case, 'Public second')
        self.assertTrue(any('Public second extraction' in item['text'] for item in retrieved))


if __name__ == '__main__':
    unittest.main()
