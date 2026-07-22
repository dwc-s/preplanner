"""Pre-plan PDF export.

Renders an :class:`Occupancy` (the pre-plan) into a formatted, printable PDF: the
structured record, contacts, hazards, and the builder's ordered element list, then
**appendices** — floor-plan and photo images embedded inline, and any attached PDF
documents (e.g. SDS) merged onto the end. Pure-Python (reportlab + pypdf + Pillow);
no system libraries required.

Entry point: ``build_preplan_pdf(occupancy) -> bytes``.
"""
import io
import os
from xml.sax.saxutils import escape as _xesc

from flask import current_app
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak,
)

from .assets import asset_file_path

RED = colors.HexColor("#c0392b")
LINE = colors.HexColor("#e2e6ea")
_MARGIN = 0.75 * inch
_CONTENT_W = letter[0] - 2 * _MARGIN
_MAX_IMG_PX = 1600  # downscale big field photos so the PDF stays a sane size


def _esc(v):
    """XML-escape for reportlab Paragraph markup; keep line breaks."""
    return _xesc(str(v)).replace("\n", "<br/>")


def _blank(v):
    return v is None or v == "" or v is False


def _fmt(v):
    if v is True:
        return "Yes"
    return _esc(v)


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("PPTitle", parent=ss["Title"], textColor=RED, spaceAfter=2))
    ss.add(ParagraphStyle("PPSub", parent=ss["Normal"], fontSize=10,
                          textColor=colors.grey, spaceAfter=10))
    ss.add(ParagraphStyle("PPSection", parent=ss["Heading2"], textColor=RED,
                          fontSize=13, spaceBefore=14, spaceAfter=4))
    ss.add(ParagraphStyle("PPBody", parent=ss["Normal"], fontSize=9.5, leading=13))
    ss.add(ParagraphStyle("PPCell", parent=ss["Normal"], fontSize=9, leading=12))
    return ss


def _status_label(status):
    return {"draft": "Draft", "in_review": "In review",
            "approved": "Approved", "needs_changes": "Needs changes"}.get(
        status, (status or "").replace("_", " ").title())


def _kv_section(story, styles, title, rows):
    """A titled block of label/value rows; omitted entirely if nothing is filled in."""
    rows = [(lbl, val) for lbl, val in rows if not _blank(val)]
    if not rows:
        return
    story.append(Paragraph(_esc(title), styles["PPSection"]))
    data = [[Paragraph("<b>%s</b>" % _esc(lbl), styles["PPCell"]),
             Paragraph(_fmt(val), styles["PPCell"])] for lbl, val in rows]
    t = Table(data, colWidths=[1.9 * inch, _CONTENT_W - 1.9 * inch])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(t)


def _grid_table(story, styles, title, headers, rows):
    """A bordered table (contacts / hazards); skipped when there are no rows."""
    if not rows:
        return
    story.append(Paragraph(_esc(title), styles["PPSection"]))
    data = [[Paragraph("<b>%s</b>" % _esc(h), styles["PPCell"]) for h in headers]]
    for r in rows:
        data.append([Paragraph(_esc(c or "—"), styles["PPCell"]) for c in r])
    t = Table(data, colWidths=[_CONTENT_W / len(headers)] * len(headers), repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f4f6f8")),
        ("GRID", (0, 0), (-1, -1), 0.25, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)


def _element_desc(el):
    if el.kind == "map":
        return "Map — see the interactive map"
    if el.kind == "inspection":
        return "Inspection report — %s" % (el.caption or "external software")
    label = {"floorplan": "Floor plan", "photo": "Photo", "sds": "SDS"}.get(
        el.kind, el.kind.title() if el.kind else "Attachment")
    title = el.asset.title if el.asset else "(missing file)"
    return "%s — %s%s" % (label, title, (" · %s" % el.caption) if el.caption else "")


def _floorplan_path(occ, fp):
    return os.path.join(current_app.config["UPLOAD_FOLDER"],
                        str(occ.department_id), str(occ.id), fp.image_filename or "")


def _is_pdf(asset):
    return (asset.content_type or "").lower() == "application/pdf" or \
        (asset.filename or "").lower().endswith(".pdf")


def _scaled_image(path, max_w=_CONTENT_W, max_h=8.2 * inch):
    """A reportlab Image scaled to fit the page, downsampled if large. None on failure."""
    try:
        from PIL import Image as PILImage, ImageOps
        with PILImage.open(path) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            iw, ih = im.size
            if max(iw, ih) > _MAX_IMG_PX:
                im.thumbnail((_MAX_IMG_PX, _MAX_IMG_PX))
                iw, ih = im.size
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
        buf.seek(0)
        ratio = min(max_w / iw, max_h / ih, 1.0)
        return Image(buf, width=iw * ratio, height=ih * ratio)
    except Exception:
        return None


def _appendix_label(i):
    return "Appendix %s" % (chr(ord("A") + i) if i < 26 else str(i + 1))


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.grey)
    canvas.drawString(_MARGIN, 0.5 * inch, getattr(doc, "_pp_title", "Pre-Plan"))
    canvas.drawRightString(letter[0] - _MARGIN, 0.5 * inch, "Page %d" % doc.page)
    canvas.restoreState()


def build_preplan_pdf(occ):
    """Build the pre-plan PDF for ``occ`` and return it as ``bytes``.

    Image attachments (floor plans, photos) are embedded inline; attached PDFs (SDS,
    other documents) are merged onto the end as appendices via pypdf.
    """
    styles = _styles()
    story = []

    # --- header ---
    story.append(Paragraph(_esc(occ.name or "Pre-Plan"), styles["PPTitle"]))
    sub = " · ".join(p for p in [
        occ.full_address, "Status: %s" % _status_label(occ.status)] if p)
    story.append(Paragraph(_esc(sub), styles["PPSub"]))

    _kv_section(story, styles, "Identification", [
        ("Address", occ.address), ("City", occ.city), ("State", occ.state),
        ("ZIP", occ.zip_code)])
    _kv_section(story, styles, "Location", [
        ("Latitude", occ.latitude), ("Longitude", occ.longitude)])
    _kv_section(story, styles, "Building", [
        ("Occupancy type", occ.occupancy_type), ("Construction", occ.construction_type),
        ("Condition", occ.building_condition), ("Stories", occ.stories),
        ("Square footage", occ.square_footage), ("Year built", occ.year_built),
        ("Roof construction", occ.roof_construction)])
    _kv_section(story, styles, "Fire-protection systems", [
        ("Sprinklered", occ.sprinkler_system), ("Sprinkler details", occ.sprinkler_details),
        ("Standpipe", occ.standpipe_system), ("Standpipe details", occ.standpipe_details),
        ("Fire alarm", occ.fire_alarm_system), ("FDC location", occ.fdc_location)])
    _kv_section(story, styles, "Access & security", [
        ("Knox Box", occ.knox_box_location), ("Annunciator", occ.annunciator_location),
        ("Gate code", occ.gate_code), ("Alarm PIN", occ.alarm_pin)])
    _kv_section(story, styles, "Utility shutoffs", [
        ("Electric", occ.electric_shutoff_location), ("Gas", occ.gas_shutoff_location),
        ("Water", occ.water_shutoff_location)])
    _kv_section(story, styles, "Hazards, access & notes", [
        ("Water supply", occ.water_supply_notes), ("Hazards summary", occ.hazards_summary),
        ("Access / approach", occ.access_notes), ("General notes", occ.notes)])

    _grid_table(story, styles, "Contacts", ["Name", "Role", "Phone", "Email"],
                [(c.name, c.role, c.phone, c.email) for c in occ.contacts])
    _grid_table(story, styles, "Hazards", ["Type", "Severity", "Location", "Description"],
                [(h.hazard_type, h.severity, h.location, h.description) for h in occ.hazards])

    elements = sorted(occ.elements, key=lambda e: e.position)
    if elements:
        story.append(Paragraph("Pre-plan contents", styles["PPSection"]))
        for i, el in enumerate(elements, 1):
            story.append(Paragraph("%d. %s" % (i, _esc(_element_desc(el))), styles["PPBody"]))

    # --- image appendices (floor plans + attached photos) ---
    images = []  # (title, path)
    for fp in occ.floor_plans:
        if fp.image_filename:
            images.append((fp.title or "Floor plan", _floorplan_path(occ, fp)))
    for el in elements:
        a = el.asset
        if a and (a.content_type or "").startswith("image/"):
            images.append((a.title or el.kind.title(), asset_file_path(a)))

    # --- PDF appendices (SDS / documents) to merge after the rendered doc ---
    pdfs = []  # (title, path)
    for el in elements:
        a = el.asset
        if a and _is_pdf(a):
            pdfs.append((a.title or "Document", asset_file_path(a)))

    appendix_i = 0
    for title, path in images:
        img = _scaled_image(path)
        story.append(PageBreak())
        story.append(Paragraph("%s — %s" % (_appendix_label(appendix_i), _esc(title)),
                               styles["PPSection"]))
        story.append(img if img is not None else
                     Paragraph("<i>(image could not be embedded)</i>", styles["PPBody"]))
        appendix_i += 1

    # Render the main document.
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter, title="Pre-Plan — %s" % (occ.name or ""),
        leftMargin=_MARGIN, rightMargin=_MARGIN, topMargin=_MARGIN, bottomMargin=_MARGIN)
    doc._pp_title = "Pre-Plan — %s" % (occ.name or "")
    if not story:
        story.append(Spacer(1, 1))
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    rendered = buf.getvalue()

    if not pdfs:
        return rendered

    # Merge PDF appendices onto the end, each bookmarked.
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.append(io.BytesIO(rendered))
    for i, (title, path) in enumerate(pdfs):
        label = "%s — %s" % (_appendix_label(appendix_i + i), title)
        try:
            writer.append(path, outline_item=label)
        except Exception:
            continue  # a missing/corrupt PDF just gets skipped, not fatal
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
