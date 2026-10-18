"""
Upload checks for case documents: accepted types (JPEG, PNG, PDF, TIFF), size limit, and
PDF page rendering.

A file is accepted only when its content matches its extension. The extension alone is
not trusted, so a renamed file is refused. PDFs are rendered to one PNG per page, so the
vision pipeline only ever sees images.
"""

from __future__ import annotations

import pathlib
import uuid

from app import config

# Limits come from .env (MAX_UPLOAD_MB, MAX_PDF_PAGES, PDF_RENDER_DPI, UPLOAD_EXTENSIONS).
MAX_FILE_BYTES = config.MAX_UPLOAD_BYTES
MAX_PDF_PAGES = config.MAX_PDF_PAGES
RENDER_DPI = config.PDF_RENDER_DPI
ALLOWED_EXTS = config.UPLOAD_EXTENSIONS


class UploadError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def sniff(head: bytes) -> str | None:
    """Kind from the file's first bytes: jpeg, png, pdf or tiff, or None if unknown."""
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    return None


def check_upload(filename: str | None, data: bytes) -> str:
    """Return the detected kind. Raises UploadError when the name or the content is not allowed."""
    ext = pathlib.Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise UploadError(415, f"unsupported file type '{ext or 'none'}' in '{filename}'; "
                               "allowed: JPEG, PNG, PDF, TIFF")
    if len(data) > MAX_FILE_BYTES:
        raise UploadError(413, f"'{filename}' is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    kind = sniff(data[:16])
    if kind is None:
        raise UploadError(415, f"'{filename}' is not a valid JPEG, PNG, PDF or TIFF file")
    expected = {"jpeg": {".jpg", ".jpeg"}, "png": {".png"}, "pdf": {".pdf"}, "tiff": {".tif", ".tiff"}}
    if ext not in expected[kind]:
        raise UploadError(415, f"'{filename}' contents are {kind.upper()} but the extension is {ext}")
    return kind


def render_pdf_pages(data: bytes, folder: pathlib.Path) -> list[pathlib.Path]:
    """One PNG per page, written into folder. Raises UploadError for unreadable or oversized PDFs."""
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as exc:                     # encrypted, truncated or corrupt
        raise UploadError(422, f"could not read the PDF: {type(exc).__name__}") from exc
    written: list[pathlib.Path] = []
    try:
        pages = len(pdf)
        if pages == 0:
            raise UploadError(422, "the PDF has no pages")
        if pages > MAX_PDF_PAGES:
            raise UploadError(422, f"the PDF has {pages} pages; the limit is {MAX_PDF_PAGES}")
        folder.mkdir(parents=True, exist_ok=True)
        for i in range(pages):
            image = pdf[i].render(scale=RENDER_DPI / 72).to_pil()
            target = folder / f"{uuid.uuid4()}.png"
            image.save(target, format="PNG")
            written.append(target)
        return written
    except Exception:
        for path in written:                     # do not leave half a PDF behind
            path.unlink(missing_ok=True)
        raise
    finally:
        pdf.close()
