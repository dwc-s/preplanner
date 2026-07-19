"""Pure-Python GIS import: parse uploaded vector files into feature dicts.

No system GDAL dependency. Supports GeoJSON, KML, GPX, and zipped Shapefiles.
Inputs are assumed to be WGS84 (EPSG:4326): the GeoJSON, KML and GPX specs
guarantee that, and Shapefiles should be reprojected to WGS84 before upload
(we don't reproject here — that's where optional GDAL would come in later).

Each parser returns a list of ``{"category", "label", "geometry"}`` dicts where
geometry is a GeoJSON geometry. ``category`` is inferred from geometry type.
"""
import io
import json
import zipfile
import xml.etree.ElementTree as ET


def category_for(geom_type):
    if geom_type in ("Point", "MultiPoint"):
        return "Access Point"
    if geom_type in ("LineString", "MultiLineString"):
        return "Route"
    return "Custom"  # Polygon, MultiPolygon, GeometryCollection…


def _feature(geometry, label):
    if not geometry or "type" not in geometry:
        return None
    return {
        "category": category_for(geometry["type"]),
        "label": ((label or "").strip()[:200]) or None,
        "geometry": geometry,
    }


def _local(tag):
    """Strip an XML namespace: '{ns}Point' -> 'Point'."""
    return tag.split("}")[-1]


# --- GeoJSON -----------------------------------------------------------------

def parse_geojson(raw):
    data = json.loads(raw)
    geom_types = ("Point", "LineString", "Polygon",
                  "MultiPoint", "MultiLineString", "MultiPolygon")
    if data.get("type") == "FeatureCollection":
        items = data.get("features", [])
    elif data.get("type") == "Feature":
        items = [data]
    elif data.get("type") in geom_types:
        items = [{"type": "Feature", "geometry": data, "properties": {}}]
    else:
        items = []

    out = []
    for f in items:
        props = f.get("properties") or {}
        label = (props.get("name") or props.get("label") or props.get("title")
                 or props.get("NAME"))
        feat = _feature(f.get("geometry"), label)
        if feat:
            out.append(feat)
    return out


# --- KML ---------------------------------------------------------------------

def _kml_coords(text):
    """'lon,lat[,alt] lon,lat[,alt] …' -> [[lon, lat], …]."""
    pts = []
    for token in (text or "").split():
        parts = token.split(",")
        if len(parts) >= 2:
            try:
                pts.append([float(parts[0]), float(parts[1])])
            except ValueError:
                pass
    return pts


def parse_kml(raw):
    root = ET.fromstring(raw)
    out = []
    for pm in root.iter():
        if _local(pm.tag) != "Placemark":
            continue
        name = next((c.text for c in pm if _local(c.tag) == "name"), None)
        for el in pm.iter():
            t = _local(el.tag)
            if t == "Point":
                pts = _coords_child(el)
                if pts:
                    out.append(_feature({"type": "Point", "coordinates": pts[0]}, name))
            elif t == "LineString":
                pts = _coords_child(el)
                if len(pts) >= 2:
                    out.append(_feature({"type": "LineString", "coordinates": pts}, name))
            elif t == "Polygon":
                ring = next((_kml_coords(c.text) for c in el.iter()
                             if _local(c.tag) == "coordinates"), None)
                if ring and len(ring) >= 3:
                    if ring[0] != ring[-1]:
                        ring.append(ring[0])
                    out.append(_feature({"type": "Polygon", "coordinates": [ring]}, name))
    return [f for f in out if f]


def _coords_child(el):
    for c in el:
        if _local(c.tag) == "coordinates":
            return _kml_coords(c.text)
    return []


# --- GPX ---------------------------------------------------------------------

def parse_gpx(raw):
    root = ET.fromstring(raw)
    out = []

    for wpt in (e for e in root.iter() if _local(e.tag) == "wpt"):
        lat, lon = wpt.get("lat"), wpt.get("lon")
        name = next((c.text for c in wpt if _local(c.tag) == "name"), None)
        if lat and lon:
            out.append(_feature(
                {"type": "Point", "coordinates": [float(lon), float(lat)]}, name))

    for container_tag, point_tag in (("trk", "trkpt"), ("rte", "rtept")):
        for cont in (e for e in root.iter() if _local(e.tag) == container_tag):
            name = None
            pts = []
            for c in cont.iter():
                lt = _local(c.tag)
                if lt == "name" and name is None:
                    name = c.text
                elif lt == point_tag and c.get("lat") and c.get("lon"):
                    pts.append([float(c.get("lon")), float(c.get("lat"))])
            if len(pts) >= 2:
                out.append(_feature({"type": "LineString", "coordinates": pts}, name))
    return [f for f in out if f]


# --- Shapefile (zipped) ------------------------------------------------------

def parse_shapefile_zip(raw):
    import shapefile  # pyshp

    zf = zipfile.ZipFile(io.BytesIO(raw))
    names = zf.namelist()
    shp_name = next((n for n in names if n.lower().endswith(".shp")), None)
    if not shp_name:
        raise ValueError("No .shp found inside the archive.")
    base = shp_name[:-4].lower()

    def part(ext):
        match = next((n for n in names if n.lower() == base + ext), None)
        return io.BytesIO(zf.read(match)) if match else None

    reader = shapefile.Reader(shp=part(".shp"), dbf=part(".dbf"), shx=part(".shx"))
    fields = [f[0] for f in reader.fields[1:]]
    name_field = next((f for f in fields if f.lower() in ("name", "label", "title")), None)

    out = []
    records = reader.shapeRecords() if part(".dbf") else [
        type("R", (), {"shape": s, "record": None})() for s in reader.shapes()]
    for sr in records:
        geom = sr.shape.__geo_interface__
        label = None
        if name_field and sr.record is not None:
            try:
                label = str(sr.record[name_field])
            except Exception:
                label = None
        feat = _feature(geom, label)
        if feat:
            out.append(feat)
    return out


# --- dispatcher --------------------------------------------------------------

def parse_upload(filename, raw):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("geojson", "json"):
        return parse_geojson(raw)
    if ext == "kml":
        return parse_kml(raw)
    if ext == "gpx":
        return parse_gpx(raw)
    if ext == "zip":
        return parse_shapefile_zip(raw)
    raise ValueError("Unsupported file type.")
