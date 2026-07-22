"""Asset-library file handling.

On upload each file is given a tidy name, and we extract two searchable things:
  * GPS — from a photo's EXIF (via Pillow), so an asset can be found by location.
  * Text — a PDF's embedded text (via pypdf, always), plus image OCR via pytesseract
    when the `tesseract` binary is present on the host. Everything degrades
    gracefully: a missing binary, a scanned-only PDF, or a corrupt file just yields
    no text rather than failing the upload.
"""
import os
import shutil

from flask import current_app
from werkzeug.utils import secure_filename

from .extensions import db
from .models import Asset

# Teach Pillow to read iPhone HEIC/HEIF photos. They're transcoded to JPEG on upload
# (most browsers can't display HEIC in <img>), keeping GPS + OCR working.
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:  # dependency missing — HEIC uploads are simply rejected
    pass

IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "gif", "tif", "tiff", "bmp"}
HEIC_EXTS = {"heic", "heif"}
DOC_EXTS = {"pdf"}
ALLOWED_ASSET_EXTS = IMAGE_EXTS | HEIC_EXTS | DOC_EXTS


def _tesseract_available():
    """True when image OCR can run (pytesseract importable + the binary present)."""
    try:
        import pytesseract  # noqa: F401
        return shutil.which("tesseract") is not None
    except Exception:
        return False


OCR_AVAILABLE = _tesseract_available()


def ext_of(filename):
    return filename.rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""


def assets_dir(dept_id):
    return os.path.join(current_app.config["UPLOAD_FOLDER"], str(dept_id), "assets")


def asset_file_path(asset):
    return os.path.join(assets_dir(asset.department_id), asset.filename or "")


def _gps_from_exif(exif):
    """(lat, lng) in decimal degrees from a PIL Exif object, or (None, None)."""
    try:
        from PIL import ExifTags
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
        if not gps:
            return None, None

        def to_deg(vals, ref):
            d, m, s = (float(x) for x in vals)
            dec = d + m / 60.0 + s / 3600.0
            return -dec if ref in ("S", "W") else dec

        lat = to_deg(gps[2], gps[1]) if 2 in gps and 1 in gps else None
        lng = to_deg(gps[4], gps[3]) if 4 in gps and 3 in gps else None
        return lat, lng
    except Exception:
        return None, None


def _exif_gps(path):
    """(lat, lng) from an image file's EXIF GPS, or (None, None)."""
    try:
        from PIL import Image
        return _gps_from_exif(Image.open(path).getexif())
    except Exception:
        return None, None


def _extract_pdf_text(path):
    """A PDF's embedded text layer (pure-Python; fast enough to run inline)."""
    try:
        from pypdf import PdfReader
        pages = PdfReader(path).pages
        return "\n".join((p.extract_text() or "") for p in pages).strip()
    except Exception:
        return ""


def ocr_image(path):
    """OCR an image to text via tesseract — the slow step, run out-of-band by
    ``process_pending_ocr``. Empty string if OCR is unavailable or fails."""
    if not OCR_AVAILABLE:
        return ""
    try:
        import pytesseract
        from PIL import Image
        return pytesseract.image_to_string(Image.open(path)).strip()
    except Exception:
        return ""


def save_asset(file, kind, dept_id, uploaded_by, title=None):
    """Store an uploaded file in the department's asset library, extracting GPS and
    searchable text. Returns the committed Asset. The caller validates the extension
    against ALLOWED_ASSET_EXTS first."""
    ext = ext_of(file.filename)
    asset = Asset(
        department_id=dept_id, kind=kind, content_type=file.content_type,
        original_name=(file.filename or "")[:300],
        title=((title or (file.filename or "").rsplit(".", 1)[0]) or kind)[:200],
        uploaded_by=uploaded_by,
    )
    db.session.add(asset)
    db.session.flush()  # assign asset.id so the stored name is collision-free

    base = secure_filename(asset.title) or kind
    dest_dir = assets_dir(dept_id)
    os.makedirs(dest_dir, exist_ok=True)

    if ext in HEIC_EXTS:
        # Transcode iPhone HEIC -> JPEG for universal display; read GPS from the
        # original and bake in the EXIF orientation so portrait photos aren't sideways
        # (the JPEG loses the tag). OCR is deferred (queued below).
        from PIL import Image, ImageOps
        stored = f"{asset.id}_{kind}_{base}"[:190] + ".jpg"
        path = os.path.join(dest_dir, stored)
        try:
            img = Image.open(file.stream)
            asset.latitude, asset.longitude = _gps_from_exif(img.getexif())
            ImageOps.exif_transpose(img).convert("RGB").save(path, format="JPEG", quality=88)
        except Exception:
            db.session.rollback()
            raise ValueError("Could not read that photo — is it a valid HEIC image?")
        asset.filename = stored
        asset.content_type = "image/jpeg"
        asset.ocr_pending = True
    else:
        stored = f"{asset.id}_{kind}_{base}"[:190] + (f".{ext}" if ext else "")
        path = os.path.join(dest_dir, stored)
        file.save(path)
        asset.filename = stored
        if ext in IMAGE_EXTS:
            asset.latitude, asset.longitude = _exif_gps(path)
            asset.ocr_pending = True                    # OCR is slow → do it out-of-band
        elif ext in DOC_EXTS:
            asset.text_content = _extract_pdf_text(path) or None   # PDF text is cheap

    db.session.commit()
    return asset


def process_pending_ocr(limit=None):
    """OCR the queue of image assets (the deferred step). Returns the count processed.
    Runs from the ``flask ocr-pending`` task. A no-op where OCR isn't available, so the
    queue simply waits for a tesseract-capable host to drain it. Commits per asset so an
    interruption never loses completed work."""
    if not OCR_AVAILABLE:
        return 0
    query = Asset.query.filter_by(ocr_pending=True).order_by(Asset.id)
    if limit:
        query = query.limit(limit)
    processed = 0
    for asset in query.all():
        asset.text_content = ocr_image(asset_file_path(asset)) or None
        asset.ocr_pending = False
        db.session.commit()
        processed += 1
    return processed


def delete_asset_file(asset):
    """Remove an asset's file from disk (best effort)."""
    try:
        path = asset_file_path(asset)
        if asset.filename and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass
