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


def _is_projected(prj_text):
    """A projected CRS uses PROJCS in WKT1 (.prj files) or PROJCRS in WKT2."""
    t = (prj_text or "").upper()
    return "PROJCS" in t or "PROJCRS" in t


def _shape_bbox(shape):
    """Native-coordinate bounding box (xmin, ymin, xmax, ymax) of a pyshp shape;
    works for points too (pyshp gives no .bbox for single points)."""
    pts = getattr(shape, "points", None)
    if pts:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))
    bb = getattr(shape, "bbox", None)
    return tuple(bb) if bb else None


def _boxes_hit(a, b):
    """Do axis-aligned boxes (xmin, ymin, xmax, ymax) intersect?"""
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _parse_shapefile(shp, dbf=None, shx=None, prj_text=None, bbox=None, limit=None):
    """Parse a shapefile (streaming, so huge statewide files don't blow up memory).

    Reprojects to WGS84 from the ``.prj`` (projected CRS needs pyproj; geographic
    or absent is treated as already lon/lat). ``bbox`` is
    ``(min_lon, min_lat, max_lon, max_lat)`` in WGS84 — when given, only features
    whose bounding box intersects it are kept (the box is projected back into the
    file's own CRS so the cheap filter runs before any per-feature reprojection).
    ``limit`` caps how many features are returned.
    """
    import shapefile  # pyshp

    reader = shapefile.Reader(shp=shp, dbf=dbf, shx=shx)
    fields = [f[0] for f in reader.fields[1:]]
    # Prefer an exact name/label/title field; else a "*name*" field (e.g.
    # TRAIL_NAME), skipping ALT_NAME-style alternates, so features carry a label.
    name_field = (next((f for f in fields if f.lower() in ("name", "label", "title")), None)
                  or next((f for f in fields if "name" in f.lower()
                           and not f.lower().startswith("alt")), None)
                  or next((f for f in fields if "name" in f.lower()), None))

    forward = None       # source CRS -> WGS84 (None => already lon/lat)
    clip_native = None   # bbox expressed in the file's own coordinates
    if _is_projected(prj_text):
        try:
            from pyproj import CRS, Transformer
        except ImportError:
            raise ValueError(
                "This shapefile uses a projected coordinate system (.prj), which needs "
                "the 'pyproj' package to convert to latitude/longitude. Install it "
                "(pip install pyproj) or reproject the data to WGS84 first.")
        src = CRS.from_wkt(prj_text)
        forward = Transformer.from_crs(src, "EPSG:4326", always_xy=True).transform
        if bbox:
            inv = Transformer.from_crs("EPSG:4326", src, always_xy=True).transform
            corners = [inv(x, y) for x in (bbox[0], bbox[2]) for y in (bbox[1], bbox[3])]
            xs = [c[0] for c in corners]
            ys = [c[1] for c in corners]
            # Pad 5%: a lat/lon box projects to a slightly curved quad, so the
            # corner-based native box can under-cover the edges. Pad generously
            # here (the exact WGS84 check below trims to the real area).
            px = (max(xs) - min(xs)) * 0.05 or 1.0
            py = (max(ys) - min(ys)) * 0.05 or 1.0
            clip_native = (min(xs) - px, min(ys) - py, max(xs) + px, max(ys) + py)
    elif bbox:
        clip_native = bbox  # source is already lon/lat

    out = []
    has_dbf = dbf is not None
    # Read by index with per-record error handling: real-world shapefiles contain
    # null / empty-geometry records that stricter pyshp versions raise on — skip
    # those rather than aborting the whole import.
    for i in range(len(reader)):
        try:
            if has_dbf:
                sr = reader.shapeRecord(i)
                shape, record = sr.shape, sr.record
            else:
                shape, record = reader.shape(i), None
        except Exception:
            continue
        sb = _shape_bbox(shape)
        if sb is None:
            continue  # record has no geometry
        if clip_native and not _boxes_hit(sb, clip_native):
            continue
        geom = shape.__geo_interface__
        if forward and geom.get("coordinates") is not None:
            geom = {"type": geom["type"],
                    "coordinates": _map_coords(geom["coordinates"], forward)}
        if bbox:  # exact clip, now in WGS84
            gb = _geom_bbox(geom.get("coordinates"))
            if not gb or not _boxes_hit(gb, bbox):
                continue
        label = None
        if name_field and record is not None:
            try:
                label = str(record[name_field])
            except Exception:
                label = None
        feat = _feature(geom, label)
        if feat:
            out.append(feat)
            if limit and len(out) >= limit:
                break
    return out


def parse_shapefile_zip(raw, bbox=None, limit=None):
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
        prj_text=prj.decode("utf-8", "replace") if prj else None,
        bbox=bbox, limit=limit)


def parse_shapefile_parts(parts, bbox=None, limit=None):
    """Parse a Shapefile from loose component files.

    ``parts`` maps a lowercase extension without the dot ('shp', 'dbf', 'shx',
    'prj', …) to raw bytes. Only ``.shp`` is strictly required, but ``.dbf`` adds
    attribute labels, ``.shx`` the index, and ``.prj`` drives reprojection to
    WGS84. Other sidecars (.sbn/.sbx/.cpg/.xml) are ignored. ``bbox``/``limit`` are
    passed through to clip and cap the import.
    """
    if "shp" not in parts:
        raise ValueError("A shapefile needs at least the .shp file "
                         "(include .dbf and .shx too, and .prj for correct placement).")

    def buf(ext):
        return io.BytesIO(parts[ext]) if ext in parts else None

    prj_text = parts["prj"].decode("utf-8", "replace") if "prj" in parts else None
    return _parse_shapefile(buf("shp"), dbf=buf("dbf"), shx=buf("shx"),
                            prj_text=prj_text, bbox=bbox, limit=limit)


# --- bbox clipping for already-parsed (WGS84) features -----------------------

def _geom_bbox(coords):
    xs, ys = [], []

    def walk(c):
        if not c:
            return
        if isinstance(c[0], (int, float)):
            xs.append(c[0])
            ys.append(c[1])
        else:
            for sub in c:
                walk(sub)

    walk(coords)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def clip_features(features, bbox):
    """Keep features whose geometry intersects ``bbox`` (min_lon, min_lat,
    max_lon, max_lat, WGS84). Used for GeoJSON/KML/GPX, which are already lon/lat."""
    if not bbox:
        return features
    kept = []
    for f in features:
        gb = _geom_bbox(f["geometry"].get("coordinates"))
        if gb and _boxes_hit(gb, bbox):
            kept.append(f)
    return kept


# --- dispatcher --------------------------------------------------------------

def parse_upload(filename, raw, bbox=None):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("geojson", "json"):
        return clip_features(parse_geojson(raw), bbox)
    if ext == "kml":
        return clip_features(parse_kml(raw), bbox)
    if ext == "gpx":
        return clip_features(parse_gpx(raw), bbox)
    if ext == "zip":
        return parse_shapefile_zip(raw, bbox=bbox)
    raise ValueError("Unsupported file type.")
