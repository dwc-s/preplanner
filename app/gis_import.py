"""Pure-Python GIS import: parse uploaded vector files into feature dicts.

No system GDAL dependency. Supports GeoJSON, KML, GPX, zipped Shapefiles, and
loose Shapefile component files (.shp/.shx/.dbf/.prj). GeoJSON, KML and GPX are
WGS84 by spec. Shapefiles are reprojected to WGS84 from their .prj when the
optional ``pyproj`` package is installed (needed only for *projected* .prj);
without a .prj they're assumed to already be lon/lat.

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


# --- Shapefile ---------------------------------------------------------------

def _map_coords(coords, fn):
    """Recursively apply fn(x, y) -> (x, y) over a GeoJSON coordinate array,
    at any nesting depth (Point / LineString / Polygon / Multi*)."""
    if not coords:
        return coords
    if isinstance(coords[0], (int, float)):          # a single [x, y(, z)] position
        x, y = fn(coords[0], coords[1])
        return [x, y] + list(coords[2:])
    return [_map_coords(c, fn) for c in coords]


def _reproject_features(features, prj_text):
    """Reproject geometries to WGS84 lon/lat using the shapefile's .prj.

    No .prj, or a geographic (lon/lat) one, is treated as already WGS84 (NAD83
    geographic differs by ~1 m — fine for pre-planning). A *projected* .prj
    (``PROJCS``, e.g. State Plane in metres) must be converted, which needs the
    optional ``pyproj`` package.
    """
    txt = (prj_text or "").upper()
    # Projected CRS keyword is PROJCS in WKT1 (.prj files) and PROJCRS in WKT2.
    if not prj_text or ("PROJCS" not in txt and "PROJCRS" not in txt):
        return features
    try:
        from pyproj import CRS, Transformer
    except ImportError:
        raise ValueError(
            "This shapefile uses a projected coordinate system (its .prj), which "
            "needs the 'pyproj' package to convert to latitude/longitude. Install it "
            "(pip install pyproj) or reproject the data to WGS84 (EPSG:4326) first.")
    transformer = Transformer.from_crs(CRS.from_wkt(prj_text), "EPSG:4326", always_xy=True)
    for f in features:
        geom = f["geometry"]
        if "coordinates" in geom:
            geom["coordinates"] = _map_coords(geom["coordinates"], transformer.transform)
    return features


def _parse_shapefile(shp, dbf=None, shx=None, prj_text=None):
    import shapefile  # pyshp

    reader = shapefile.Reader(shp=shp, dbf=dbf, shx=shx)
    fields = [f[0] for f in reader.fields[1:]]
    name_field = next((f for f in fields if f.lower() in ("name", "label", "title")), None)

    out = []
    records = reader.shapeRecords() if dbf else [
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
    return _reproject_features(out, prj_text)


def parse_shapefile_zip(raw):
    zf = zipfile.ZipFile(io.BytesIO(raw))
    names = zf.namelist()
    shp_name = next((n for n in names if n.lower().endswith(".shp")), None)
    if not shp_name:
        raise ValueError("No .shp found inside the archive.")
    base = shp_name[:-4].lower()

    def part(ext):
        match = next((n for n in names if n.lower() == base + ext), None)
        return zf.read(match) if match else None

    shp, dbf, shx, prj = part(".shp"), part(".dbf"), part(".shx"), part(".prj")
    return _parse_shapefile(
        io.BytesIO(shp), dbf=io.BytesIO(dbf) if dbf else None,
        shx=io.BytesIO(shx) if shx else None,
        prj_text=prj.decode("utf-8", "replace") if prj else None)


def parse_shapefile_parts(parts):
    """Parse a Shapefile from loose component files.

    ``parts`` maps a lowercase extension without the dot ('shp', 'dbf', 'shx',
    'prj', …) to raw bytes. Only ``.shp`` is strictly required, but ``.dbf`` adds
    attribute labels, ``.shx`` the index, and ``.prj`` drives reprojection to
    WGS84. Other sidecars (.sbn/.sbx/.cpg/.xml) are ignored.
    """
    if "shp" not in parts:
        raise ValueError("A shapefile needs at least the .shp file "
                         "(include .dbf and .shx too, and .prj for correct placement).")

    def buf(ext):
        return io.BytesIO(parts[ext]) if ext in parts else None

    prj_text = parts["prj"].decode("utf-8", "replace") if "prj" in parts else None
    return _parse_shapefile(buf("shp"), dbf=buf("dbf"), shx=buf("shx"), prj_text=prj_text)


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
