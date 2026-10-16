"""Bounded local document extraction; originals never leave this machine.

Office containers are read in memory, never unpacked. Optional native tools run
with fixed arguments, private temporary files, time/output bounds and the same
parent-bound supervisor used by the owned application services.
"""

import io
import hashlib
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
import xml.etree.ElementTree as ET

FORMATS = frozenset({"pdf", "docx", "odt", "png", "jpeg", "tiff"})
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_PAGES = 100
MAX_ZIP_BYTES = 8 * 1024 * 1024
MAX_NATIVE_BYTES = 16 * 1024 * 1024
EXTRACTOR_VERSION = "documents-v1"


def tool_binary(name):
    selected = shutil.which(name)
    if selected:
        for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
            candidate = Path(prefix) / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return selected


def capabilities():
    tools = {name: bool(tool_binary(name)) for name in ("pdftotext", "pdftoppm", "tesseract")}
    return {"formats": sorted(FORMATS), "tools": tools, "pdf_text": tools["pdftotext"],
            "pdf_ocr": tools["pdftoppm"] and tools["tesseract"], "image_ocr": tools["tesseract"],
            "office_text": True, "max_bytes": MAX_DOCUMENT_BYTES, "max_pages": MAX_PAGES,
            "processing": "local", "automatic_ocr": True}


def _invalid(message):
    from .engine import InvalidInput
    return InvalidInput(message)


def _assemble(pages, version=EXTRACTOR_VERSION, status="ready", warnings=None):
    text, spans, position = [], [], 0
    for number, value, method in pages:
        value = value.replace("\x00", "").replace("\r\n", "\n").strip()
        if text:
            text.append("\n\n")
            position += 2
        start = position
        text.append(value)
        position += len(value)
        spans.append({"page": number, "start": start, "end": position, "method": method})
    result = "".join(text)
    if len(result.encode("utf-8")) > MAX_TEXT_BYTES:
        raise _invalid("Extracted document text exceeds the 2 MiB limit.")
    metadata_id = hashlib.sha256(repr((spans, status, warnings or [])).encode()).hexdigest()[:12]
    return {"text": result, "pages": spans, "extractor_version": version + ":" + metadata_id, "status": status, "warnings": warnings or []}


def _office(raw, format):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if len(members) > 300 or len({entry.filename for entry in members}) != len(members):
                raise _invalid("Document archive has too many or duplicate members.")
            size = 0
            for entry in members:
                path = entry.filename.replace("\\", "/")
                if path.startswith("/") or ".." in path.split("/") or ((entry.external_attr >> 16) & 0o170000) == 0o120000 or entry.flag_bits & 1:
                    raise _invalid("Document archive contains unsafe or encrypted members.")
                size += entry.file_size
                if size > MAX_ZIP_BYTES or entry.file_size > MAX_ZIP_BYTES:
                    raise _invalid("Document archive exceeds the 8 MiB expanded limit.")
            member = "word/document.xml" if format == "docx" else "content.xml"
            with archive.open(member) as stream:
                xml = stream.read(MAX_ZIP_BYTES + 1)
            if len(xml) > MAX_ZIP_BYTES or b"\0" in xml or b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                raise _invalid("Document XML is oversized or declares unsupported entities.")
            root = ET.fromstring(xml)
    except (zipfile.BadZipFile, KeyError, ET.ParseError, RuntimeError, OSError) as exc:
        raise _invalid("Document is not a readable DOCX or ODT container.") from exc
    pages, parts, number = [], [], 1
    class PageLimit(Exception):
        pass
    def page():
        nonlocal parts, number
        if len(pages) >= MAX_PAGES:
            raise PageLimit()
        pages.append((number, "".join(parts), format + "-xml"))
        parts, number = [], number + 1
    def visit(node):
        tag = node.tag.rsplit("}", 1)[-1]
        if format == "docx":
            if tag == "t":
                parts.append(node.text or "")
            elif tag == "tab":
                parts.append("\t")
            elif tag == "br":
                if any(key.rsplit("}", 1)[-1] == "type" and value == "page" for key, value in node.attrib.items()):
                    page()
                else:
                    parts.append("\n")
            elif tag == "lastRenderedPageBreak":
                page()
            for child in node:
                visit(child)
            if tag in {"p", "tr"}:
                parts.append("\n")
        else:
            if tag == "soft-page-break":
                page()
                return
            if node.text:
                parts.append(node.text)
            for child in node:
                visit(child)
                if child.tail:
                    parts.append(child.tail)
            if tag in {"p", "h"}:
                parts.append("\n")
    body = next((node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "body"), root)
    limited = False
    try:
        visit(body)
        if parts or not pages:
            page()
    except PageLimit:
        limited = True
    except RecursionError as exc:
        raise _invalid("Document XML nesting exceeds the parser limit.") from exc
    warnings = ["Office page boundaries reflect embedded page breaks; rendered pagination may differ."]
    if limited:
        warnings.append("Only the first 100 document pages were processed.")
    return _assemble(pages, EXTRACTOR_VERSION + ":" + format + "-xml", status="limited" if limited else "ready", warnings=warnings)


def _private_file(path, raw):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)


def _image_bounds(raw, format):
    """Reject oversized raster dimensions before invoking a native decoder."""
    try:
        if format == "png":
            width, height = struct.unpack(">II", raw[16:24])
        elif format == "jpeg":
            offset, width, height = 2, 0, 0
            while offset + 4 <= len(raw):
                if raw[offset] != 0xff:
                    raise ValueError()
                marker = raw[offset + 1]
                offset += 2
                if marker == 0xff:
                    offset -= 1
                    continue
                if marker in {0x01, *range(0xd0, 0xd9)}:
                    continue
                length = int.from_bytes(raw[offset:offset + 2], "big")
                if length < 2 or offset + length > len(raw):
                    raise ValueError()
                if marker in {0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf}:
                    height, width = struct.unpack(">HH", raw[offset + 3:offset + 7])
                    break
                offset += length
        else:
            order = "little" if raw[:2] == b"II" else "big"
            offset = int.from_bytes(raw[4:8], order)
            count = int.from_bytes(raw[offset:offset + 2], order)
            if count > 1000 or offset + 2 + count * 12 + 4 > len(raw):
                raise ValueError()
            dimensions = {}
            for index in range(count):
                entry = raw[offset + 2 + index * 12:offset + 14 + index * 12]
                tag, type = int.from_bytes(entry[:2], order), int.from_bytes(entry[2:4], order)
                if tag in {256, 257} and int.from_bytes(entry[4:8], order) == 1 and type in {3, 4}:
                    dimensions[tag] = int.from_bytes(entry[8:10 if type == 3 else 12], order)
            if int.from_bytes(raw[offset + 2 + count * 12:offset + 6 + count * 12], order):
                raise _invalid("Multi-page TIFF is unsupported. Convert it to PDF for page-level OCR.")
            width, height = dimensions[256], dimensions[257]
        if min(width, height) < 1 or max(width, height) > 10000 or width * height > 16000000:
            raise _invalid("Image exceeds the 16-megapixel local OCR limit.")
    except (ValueError, KeyError, struct.error, IndexError) as exc:
        raise _invalid("Image dimensions are invalid or exceed the local OCR limits.") from exc


def _run(arguments, directory, deadline):
    from . import owned_process
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _invalid("Local document extraction timed out; original remains available for retry.")
    argv = [sys.executable, str(Path(owned_process.__file__).resolve()), str(os.getpid()), "--",
            sys.executable, "-m", "local_osint.documents", "--native", *map(str, arguments)]
    try:
        process = subprocess.Popen(argv, cwd=str(Path(__file__).resolve().parent.parent), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            process.wait(timeout=min(30, remaining))
        except subprocess.TimeoutExpired as exc:
            process.terminate()
            try:
                process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            raise _invalid("Local document extraction timed out; original remains available for retry.") from exc
        if process.returncode:
            raise _invalid("Local document tool could not read this file. Check its format, permissions or installed language data.")
    except OSError as exc:
        raise _invalid("Local document tool could not start. Check its installation.") from exc


def _read_text(path):
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_TEXT_BYTES + 1)
        if len(raw) > MAX_TEXT_BYTES:
            raise _invalid("Extracted document text exceeds the 2 MiB limit.")
        return raw.decode("utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise _invalid("Local document tool returned unreadable text.") from exc


def extract(raw, format, *, ocr=True, language="eng"):
    """Return text plus character/page spans; unavailable tools preserve no claims."""
    if format not in FORMATS or not isinstance(raw, bytes) or not raw or len(raw) > MAX_DOCUMENT_BYTES:
        raise _invalid("Choose a supported document containing 1 byte to 2 MiB.")
    if type(ocr) is not bool or not isinstance(language, str) or not re.fullmatch(r"[a-z]{3}(?:\+[a-z]{3}){0,2}", language):
        raise _invalid("Choose automatic OCR on/off and up to three installed three-letter language codes.")
    signatures = {"pdf": raw.startswith(b"%PDF-"), "png": raw.startswith(b"\x89PNG\r\n\x1a\n"),
                  "jpeg": raw.startswith(b"\xff\xd8\xff"), "tiff": raw.startswith((b"II*\0", b"MM\0*"))}
    if format in signatures and not signatures[format]:
        raise _invalid("Document bytes do not match the selected file format.")
    if format in {"docx", "odt"}:
        return _office(raw, format)
    if format in {"png", "jpeg", "tiff"}:
        _image_bounds(raw, format)
    tools = {key: tool_binary(key) for key in ("pdftotext", "pdftoppm", "tesseract")}
    if format != "pdf" and not (ocr and tools["tesseract"]):
        return _assemble([], EXTRACTOR_VERSION + ":none", "needs_ocr" if not ocr else "unavailable", ["Enable OCR and install Tesseract with the selected language data to extract this image locally."])
    if format == "pdf" and not tools["pdftotext"]:
        return _assemble([], EXTRACTOR_VERSION + ":none", "unavailable", ["Install Poppler (pdftotext and pdftoppm) for local PDF extraction; original retained."])
    warnings, pages, status = [], [], "ready"
    with tempfile.TemporaryDirectory(prefix="local-osint-document-", dir=Path(tempfile.gettempdir()).resolve()) as temporary:
        directory = Path(temporary)
        source = directory / ("original." + format)
        _private_file(source, raw)
        deadline = time.monotonic() + 90
        if format == "pdf":
            output = directory / "text.txt"
            try:
                _run([tools["pdftotext"], "-f", "1", "-l", str(MAX_PAGES + 1), "-enc", "UTF-8", "-eol", "unix", str(source), str(output)], directory, deadline)
                values = _read_text(output).split("\f")
                if values and not values[-1].strip():
                    values.pop()
                if len(values) > MAX_PAGES:
                    status = "limited"
                    warnings.append("Only the first 100 document pages were processed.")
                values = values[:MAX_PAGES] or [""]
            except ValueError as exc:
                return _assemble([], EXTRACTOR_VERSION + ":pdf", "unavailable", [str(exc)])
        else:
            values = [""]
        for number, text in enumerate(values, 1):
            method = "pdftotext" if format == "pdf" else "tesseract"
            if len(re.sub(r"\W", "", text)) < 10 and ocr:
                if not tools["tesseract"] or (format == "pdf" and not tools["pdftoppm"]):
                    status = "needs_ocr"
                    warnings.append("Page needs OCR. Install Tesseract and Poppler with the selected language data.")
                else:
                    try:
                        image = source
                        if format == "pdf":
                            prefix = directory / ("page-" + str(number))
                            _run([tools["pdftoppm"], "-f", str(number), "-l", str(number), "-singlefile", "-scale-to", "2000", "-png", str(source), str(prefix)], directory, deadline)
                            image = prefix.with_suffix(".png")
                        output = directory / ("ocr-" + str(number))
                        _run([tools["tesseract"], str(image), str(output), "-l", language, "--psm", "3"], directory, deadline)
                        text, method = _read_text(output.with_suffix(".txt")), "tesseract:" + language
                    except ValueError as exc:
                        status = "needs_ocr"
                        warnings.append(str(exc))
                    finally:
                        if format == "pdf":
                            (directory / ("page-" + str(number) + ".png")).unlink(missing_ok=True)
                        (directory / ("ocr-" + str(number) + ".txt")).unlink(missing_ok=True)
            elif not text.strip() and not ocr:
                status = "needs_ocr"
                warnings.append("Image-only page retained. Enable OCR to process it locally.")
            pages.append((number, text, method))
    # Tool identity and OCR settings are part of provenance. A different native
    # build cannot overwrite an extraction even when it produces identical text.
    identities = []
    for name, binary in sorted(tools.items()):
        if binary:
            info = Path(binary).stat()
            identities.append((name, str(Path(binary).resolve()), info.st_size, info.st_mtime_ns))
    signature = hashlib.sha256(repr((identities, ocr, language, [p[2] for p in pages], status, sorted(set(warnings)))).encode()).hexdigest()[:16]
    return _assemble(pages, EXTRACTOR_VERSION + ":" + format + ":" + signature, status, list(dict.fromkeys(warnings)))


def _native_main():
    if len(sys.argv) < 3 or sys.argv[1] != "--native":
        raise SystemExit(2)
    os.umask(0o077)
    if os.name == "posix":
        import resource
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_NATIVE_BYTES, MAX_NATIVE_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (30, 31))
    os.execv(sys.argv[2], sys.argv[2:])


if __name__ == "__main__":
    _native_main()
