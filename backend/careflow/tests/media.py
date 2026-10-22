"""Synthetic media fixtures, generated on the fly (no real patient data): PDFs, scans, photos, speech."""
from __future__ import annotations

import io
import random
import shutil
import subprocess
from pathlib import Path

REPORT_LINES = ["CITY GENERAL (SYNTHETIC) - DISCHARGE SUMMARY", "Patient: Synthetic Patient", "Age: 58 years",
                "Complaint: chest pain and breathlessness", "BP 150/95 mmHg", "Medicines: clopidogrel 75 mg", "No known allergies"]
FONT = "/System/Library/Fonts/Helvetica.ttc"


def digital_pdf(lines=None) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for ln in lines or REPORT_LINES:
        c.drawString(60, y, ln)
        y -= 28
    c.showPage()
    c.save()
    return buf.getvalue()


def text_image(lines=None, size=(1400, 700), font_size=44, rotate: int = 0, blur: float = 0, noise: bool = False):
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(FONT, font_size)
    y = 40
    for ln in lines or REPORT_LINES:
        d.text((50, y), ln, font=f, fill="black")
        y += font_size + 22
    if rotate:
        img = img.rotate(rotate, expand=True, fillcolor="white")
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    if noise:
        px = img.load()
        rnd = random.Random(3)
        for _ in range(size[0] * size[1] // 6):
            x, y2 = rnd.randrange(img.width), rnd.randrange(img.height)
            v = rnd.randrange(256)
            px[x, y2] = (v, v, v)
    return img


def image_bytes(img, fmt="PNG") -> bytes:
    b = io.BytesIO()
    img.save(b, fmt)
    return b.getvalue()


def scanned_pdf(lines=None) -> bytes:
    """Image-only PDF (no text layer): forces the OCR path."""
    import pymupdf
    png = image_bytes(text_image(lines))
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(20, 20, 575, 300), stream=png)
    out = doc.tobytes()
    doc.close()
    return out


def encrypted_pdf() -> bytes:
    import pymupdf
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret synthetic text " * 5)
    out = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner-pw", user_pw="user-pw")
    doc.close()
    return out


def unreadable_image() -> bytes:
    from PIL import Image, ImageFilter
    img = Image.effect_noise((900, 600), 90).convert("RGB").filter(ImageFilter.GaussianBlur(6))
    return image_bytes(img)


def have_tts() -> bool:
    return bool(shutil.which("say") and shutil.which("ffmpeg"))


def speech_wav(text: str, voice: str, out: Path) -> Path:
    aiff = out.with_suffix(".aiff")
    subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(aiff), "-ar", "16000", "-ac", "1", str(out)], check=True)
    return out


def concat_wav(parts: list[Path], out: Path) -> Path:
    cmd = ["ffmpeg", "-loglevel", "error", "-y"]
    for p in parts:
        cmd += ["-i", str(p)]
    cmd += ["-filter_complex", f"{''.join(f'[{i}:a]' for i in range(len(parts)))}concat=n={len(parts)}:v=0:a=1", str(out)]
    subprocess.run(cmd, check=True)
    return out


def silent_wav(seconds: float = 2.0) -> bytes:
    import struct, wave
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(struct.pack("<h", 0) * int(16000 * seconds))
    return b.getvalue()
