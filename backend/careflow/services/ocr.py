"""Document text extraction: PyMuPDF for digital PDFs, Tesseract (via pytesseract) for scanned pages and photos.
Produces per-page text with confidence + honest flags. OCR output is NEVER treated as a verified clinical
finding: low-confidence text carries `low_source_quality` into every fact extracted from it."""
from __future__ import annotations

import io
import os
import shutil
from dataclasses import dataclass, field

from ..config import get_settings


class DocumentError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class PageResult:
    page: int
    text: str
    method: str  # pymupdf | tesseract
    confidence: float | None  # 0..100 for tesseract, None for embedded text
    flags: list[str] = field(default_factory=list)


@dataclass
class DocResult:
    pages: list[PageResult]
    flags: list[str]
    method: str

    @property
    def text(self) -> str:
        return "\n\n".join(f"[page {p.page}]\n{p.text}" for p in self.pages if p.text.strip())

    @property
    def mean_confidence(self) -> float | None:
        c = [p.confidence for p in self.pages if p.confidence is not None and p.text.strip()]
        return round(sum(c) / len(c), 1) if c else None


def _configure_tesseract():
    import pytesseract
    s = get_settings()
    cmd = s.tesseract_cmd or shutil.which("tesseract")
    if not cmd:
        raise DocumentError("ocr_unavailable", "Tesseract is not installed (brew install tesseract).")
    pytesseract.pytesseract.tesseract_cmd = cmd
    if s.tessdata_dir:
        os.environ["TESSDATA_PREFIX"] = s.tessdata_dir
    elif "TESSDATA_PREFIX" not in os.environ:
        local = s.data_dir / "tessdata"
        if local.exists():
            os.environ["TESSDATA_PREFIX"] = str(local)
    return pytesseract


def available_langs() -> list[str]:
    try:
        return _configure_tesseract().get_languages(config="")
    except Exception:  # noqa: BLE001
        return []


def tesseract_status() -> dict:
    try:
        pt = _configure_tesseract()
        langs = pt.get_languages(config="")
        want = [l for l in get_settings().tesseract_langs.split("+") if l]
        missing = [l for l in want if l not in langs]
        return {"available": True, "version": str(pt.get_tesseract_version()), "languages": langs, "missing_requested_languages": missing,
                "detail": "ready" if not missing else f"missing language data: {', '.join(missing)} (OCR will use the available subset)"}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "detail": str(exc)[:200]}


def preprocess(img):
    """Pillow-only cleanup: EXIF rotation, grayscale, autocontrast, upscale small images."""
    from PIL import ImageOps
    img = ImageOps.exif_transpose(img)
    img = ImageOps.grayscale(img)
    img = ImageOps.autocontrast(img, cutoff=1)
    if max(img.size) < 1400:
        k = 1400 / max(img.size)
        img = img.resize((int(img.width * k), int(img.height * k)))
    return img


def _ocr_image(img, page_no: int) -> PageResult:
    pt = _configure_tesseract()
    s = get_settings()
    langs = [l for l in s.tesseract_langs.split("+") if l in pt.get_languages(config="")] or ["eng"]
    flags: list[str] = []
    img = preprocess(img)
    # rotation: Tesseract OSD (needs enough text; failure is normal for sparse/blank pages)
    try:
        osd = pt.image_to_osd(img, output_type=pt.Output.DICT)
        rot = int(osd.get("rotate", 0))
        if rot and float(osd.get("orientation_conf", 0)) >= 2.0:
            img = img.rotate(-rot, expand=True, fillcolor=255)
            flags.append(f"rotated_{rot}")
    except Exception:  # noqa: BLE001
        pass
    data = pt.image_to_data(img, lang="+".join(langs), output_type=pt.Output.DICT, config="--psm 6")
    words, confs, lines = [], [], {}
    for i, w in enumerate(data["text"]):
        w = (w or "").strip()
        try:
            c = float(data["conf"][i])
        except (TypeError, ValueError):
            c = -1
        if not w or c < 0:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append((w, c))
        words.append(w)
        confs.append(c)
    if not words:
        return PageResult(page_no, "", "tesseract", 0.0, flags + ["unreadable_page"])
    out_lines, low_lines = [], 0
    for key in sorted(lines):
        ws = lines[key]
        line_conf = sum(c for _, c in ws) / len(ws)
        text = " ".join(w for w, _ in ws)
        if line_conf < s.ocr_min_confidence:
            low_lines += 1
            text = f"[unclear: {text}]"
        out_lines.append(text)
    mean = sum(confs) / len(confs)
    if low_lines == len(out_lines) and mean < s.ocr_min_confidence:
        # every line is low-confidence: this is noise, not text. Keep garbage out of the record.
        return PageResult(page_no, "", "tesseract", round(mean, 1), flags + ["unreadable_page", "low_confidence_page"])
    if mean < s.ocr_min_confidence:
        flags.append("low_confidence_page")
    if low_lines:
        flags.append(f"unclear_lines:{low_lines}")
    if len(words) < 3:
        flags.append("very_little_text")
    return PageResult(page_no, "\n".join(out_lines), "tesseract", round(mean, 1), flags)


def extract_image(data: bytes) -> DocResult:
    from PIL import Image, UnidentifiedImageError
    Image.MAX_IMAGE_PIXELS = get_settings().max_image_pixels
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise DocumentError("unreadable_image", f"The image could not be read ({type(exc).__name__}). Please upload a clearer photo.") from exc
    page = _ocr_image(img, 1)
    flags = list(page.flags)
    if "unreadable_page" in flags:
        flags.append("needs_clearer_upload")
    return DocResult([page], flags, "tesseract")


def extract_pdf(data: bytes) -> DocResult:
    import pymupdf
    from PIL import Image
    s = get_settings()
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise DocumentError("unreadable_pdf", "This PDF is damaged or not a valid PDF.") from exc
    with doc:
        if doc.needs_pass or doc.is_encrypted:
            raise DocumentError("encrypted_pdf", "This PDF is password-protected. Please upload an unprotected copy.")
        if doc.page_count == 0:
            raise DocumentError("empty_pdf", "This PDF has no pages.")
        if doc.page_count > s.max_pdf_pages:
            raise DocumentError("too_many_pages", f"This PDF has {doc.page_count} pages; the limit is {s.max_pdf_pages}.")
        pages: list[PageResult] = []
        used_ocr = used_text = False
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            if len(text) >= 40:
                pages.append(PageResult(i, text, "pymupdf", None, []))
                used_text = True
                continue
            pix = page.get_pixmap(dpi=s.ocr_dpi)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            try:
                res = _ocr_image(img, i)
            except DocumentError:
                raise
            if text and not res.text.strip():
                res = PageResult(i, text, "pymupdf", None, ["sparse_embedded_text"])
            pages.append(res)
            used_ocr = True
    flags = sorted({f for p in pages for f in p.flags if not f.startswith("unclear_lines")})
    if all("unreadable_page" in p.flags for p in pages):
        flags.append("needs_clearer_upload")
    method = "pymupdf+tesseract" if used_ocr and used_text else "tesseract" if used_ocr else "pymupdf"
    return DocResult(pages, flags, method)
