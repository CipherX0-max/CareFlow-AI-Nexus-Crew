"""PDF / image / upload handling: success paths and honest failures."""
import pytest

from ..services import ocr, uploads as U
from . import media


def pages_text(res):
    return " ".join(p.text for p in res.pages)


def test_digital_pdf_uses_embedded_text_not_ocr(env):
    res = ocr.extract_pdf(media.digital_pdf())
    assert res.method == "pymupdf" and "chest pain" in pages_text(res).lower()
    assert res.pages[0].confidence is None  # embedded text, no OCR confidence


def test_scanned_pdf_goes_through_tesseract(env):
    res = ocr.extract_pdf(media.scanned_pdf())
    assert "tesseract" in res.method
    t = pages_text(res).lower()
    assert "chest pain" in t and "clopidogrel" in t


def test_photo_of_report_is_ocrd_with_confidence(env):
    res = ocr.extract_image(media.image_bytes(media.text_image()))
    assert res.method == "tesseract" and res.mean_confidence and res.mean_confidence > 60
    assert "discharge" in pages_text(res).lower()


def test_rotated_photo_is_auto_oriented_or_flagged(env):
    res = ocr.extract_image(media.image_bytes(media.text_image(rotate=90)))
    t = pages_text(res).lower()
    assert "chest pain" in t or res.flags or res.mean_confidence < 60  # either fixed, or honestly flagged


def test_unreadable_image_is_flagged_not_trusted(env):
    res = ocr.extract_image(media.unreadable_image())
    assert "needs_clearer_upload" in res.flags or res.mean_confidence is None or res.mean_confidence < 60


def test_encrypted_pdf_rejected_with_clear_message(env):
    with pytest.raises(ocr.DocumentError) as e:
        ocr.extract_pdf(media.encrypted_pdf())
    assert e.value.code == "encrypted_pdf"


def test_corrupt_pdf_and_image_do_not_crash(env):
    with pytest.raises(ocr.DocumentError):
        ocr.extract_pdf(b"%PDF-1.7 this is not a real pdf")
    with pytest.raises(ocr.DocumentError):
        ocr.extract_image(b"\x89PNG\r\n\x1a\n garbage")


def test_sniffing_ignores_extension_and_declared_mime():
    assert U.sniff(media.digital_pdf())[0] == "pdf"
    assert U.sniff(media.image_bytes(media.text_image()))[0] == "image"
    assert U.sniff(media.silent_wav())[0] == "audio"
    for bad in (b"MZ\x90\x00 executable", b"<html><script>alert(1)</script>", b"#!/bin/sh\nrm -rf /"):
        with pytest.raises(U.UploadRejected):
            U.sniff(bad)


def test_display_filename_is_sanitised():
    assert U.safe_display_name("../../etc/passwd") == "passwd"
    assert "<" not in U.safe_display_name("<script>.pdf")
    assert len(U.safe_display_name("a" * 500)) == 120
