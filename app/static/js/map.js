/* Map view — local-first. Reads all layers from the offline store (window.Store,
 * see store.js) instead of the network, and writes drawn/edited/deleted features
 * straight to the store, which queues them for sync. So the map is fully usable
 * and editable offline. Drawn map features render incrementally (diffed by uuid)
 * so a single edit never rebuilds the whole layer. */
(function () {
  "use strict";

  // This module builds either the standalone area map (index.html sets
  // window.MAP_MODE = "browse"/"operate") or the pre-plan editor map embedded in
  // the occupancy form (occupancy_form.html sets window.MAP_INIT = {el, mode,
  // occupancy}). The whole body is wrapped in buildMap() so the occupancy form can
  // defer it until occupancy.js has populated the inputs (see dispatch at bottom).
  var CFG = window.MAP_INIT || {};
  var MAP_EL = CFG.el || "map";
  var OCC = CFG.occupancy || null;   // {latSel, lonSel, fpSel, id?} on the pre-plan form
  var MODE = CFG.mode || window.MAP_MODE || "operate";
  // "browse" = the lean read-only area map (/map); "operate" = the full toolset.
  var BROWSE = MODE === "browse";

  function buildMap() {

  var FEATURE_COLORS = { "Access Point": "#1c7ed6", "Route": "#e8590c",
    "Hazard Zone": "#e03131", "Custom": "#7048e8", "Symbol": "#495057" };
  var CATEGORIES = ["Access Point", "Route", "Hazard Zone", "Custom", "Symbol"];

  // Placeable point symbols (mirror of app/models.py MAP_SYMBOLS).
  var MAP_SYMBOLS = [
    { key: "fdc", label: "Fire Dept Connection", code: "FDC", color: "#c0392b" },
    { key: "knox", label: "Knox Box", code: "KNOX", color: "#1c7ed6" },
    { key: "standpipe", label: "Standpipe", code: "STP", color: "#c0392b" },
    { key: "sprinkler", label: "Sprinkler Riser", code: "SPR", color: "#c0392b" },
    { key: "gas", label: "Gas Shutoff", code: "GAS", color: "#e8590c" },
    { key: "electric", label: "Electric Shutoff", code: "ELEC", color: "#f59f00" },
    { key: "water", label: "Water Shutoff", code: "H2O", color: "#1c7ed6" },
    { key: "hazmat", label: "Hazmat", code: "HAZ", color: "#e03131" },
    { key: "hazard", label: "Hazard", code: "HZRD", color: "#f76707" },
    { key: "command", label: "Command Post", code: "CMD", color: "#2f9e44" },
    { key: "staging", label: "Staging Area", code: "STG", color: "#7048e8" },
    { key: "watersupply", label: "Water Supply / Draft", code: "DRAFT", color: "#1971c2" },
    // Rotatable directional arrows (point up at rotation 0; `arrow` = style).
    { key: "arrow", label: "Arrow", color: "#343a40", arrow: "solid" },
    { key: "arrow_line", label: "Arrow (line)", color: "#1971c2", arrow: "line" },
    { key: "arrow_double", label: "Arrow (double)", color: "#e03131", arrow: "double" }
  ];
  var SYMBOLS_BY_KEY = {};
  MAP_SYMBOLS.forEach(function (s) { SYMBOLS_BY_KEY[s.key] = s; });
  function arrowSvg(sym) {
    var c = sym.color;
    if (sym.arrow === "line") {
      return '<svg width="28" height="28" viewBox="0 0 32 32" class="map-arrow-svg">' +
        '<g stroke="' + c + '" stroke-width="3.5" fill="none" stroke-linecap="round" stroke-linejoin="round">' +
        '<line x1="16" y1="29" x2="16" y2="7"/><polyline points="8,15 16,6 24,15"/></g></svg>';
    }
    if (sym.arrow === "double") {
      return '<svg width="28" height="28" viewBox="0 0 32 32" class="map-arrow-svg">' +
        '<g stroke="' + c + '" stroke-width="3.5" fill="none" stroke-linecap="round" stroke-linejoin="round">' +
        '<polyline points="7,17 16,7 25,17"/><polyline points="7,26 16,16 25,26"/></g></svg>';
    }
    return '<svg width="28" height="28" viewBox="0 0 32 32" class="map-arrow-svg">' +
      '<path d="M16 3 L27 18 L20 18 L20 29 L12 29 L12 18 L5 18 Z" fill="' + c +
      '" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/></svg>';
  }
  function symbolIcon(sym, rotation, scale, length) {
    if (sym.arrow) {
      var sc = scale || 1, ln = length || 1;
      var tf = "rotate(" + (rotation || 0) + "deg) scale(" + sc + "," + (sc * ln).toFixed(3) + ")";
      return L.divIcon({ className: "map-symbol-icon", iconSize: [28, 28], iconAnchor: [14, 14],
        html: '<span class="map-arrow" style="transform:' + tf + '">' + arrowSvg(sym) + "</span>" });
    }
    return L.divIcon({ className: "map-symbol-icon", iconSize: null, iconAnchor: [14, 14],
      html: '<span class="map-symbol" style="background:' + sym.color + '">' + esc(sym.code) + "</span>" });
  }

  function featureColor(cat) { return FEATURE_COLORS[cat] || "#7048e8"; }
  function hydrantClass(flow) {
    if (flow == null) return { code: null, color: "#adb5bd" };
    if (flow >= 1500) return { code: "AA", color: "#4dabf7" };
    if (flow >= 1000) return { code: "A", color: "#40c057" };
    if (flow >= 500) return { code: "B", color: "#ff922b" };
    return { code: "C", color: "#fa5252" };
  }
  function categoryForGeometry(t) {
    if (t === "Point") return "Access Point";
    if (t === "LineString" || t === "MultiLineString") return "Route";
    return "Hazard Zone";
  }
  function groupForCategory(cat) {
    if (cat === "Access Point") return accessLayer;
    if (cat === "Route") return routeLayer;
    if (cat === "Symbol") return symbolLayer;
    return zoneLayer;
  }

  // Remember the last place the user looked (per department, in this browser)
  // and restore it next time. The saved bounds double as the default clip area
  // for GIS imports — the overlays page reads the same localStorage key.
  var VIEW_KEY = "pp:mapview:" + (window.CURRENT_USER ? window.CURRENT_USER.department_id : "anon");
  function loadSavedView() {
    try { return JSON.parse(localStorage.getItem(VIEW_KEY) || "null"); } catch (e) { return null; }
  }
  var savedView = OCC ? null : loadSavedView();   // the pre-plan map centers on its building
  if (savedView && !(typeof savedView.lat === "number" && typeof savedView.lng === "number"
      && typeof savedView.zoom === "number")) {
    savedView = null;  // ignore a corrupt / partial saved view
  }

  // The pre-plan editor centers on the building's point when it already has one.
  var occStart = null;
  if (OCC) {
    var _la = parseFloat((document.querySelector(OCC.latSel) || {}).value);
    var _lo = parseFloat((document.querySelector(OCC.lonSel) || {}).value);
    if (!isNaN(_la) && !isNaN(_lo)) occStart = [_la, _lo];
  }

  // maxZoom is fixed on the map so adding a tile overlay can't push the zoom
  // range past the OSM base (which stops at 19).
  var map = L.map(MAP_EL, { maxZoom: 19 }).setView(
    occStart || (savedView ? [savedView.lat, savedView.lng] : [44.2601, -72.5754]),
    occStart ? 18 : (savedView ? savedView.zoom : 13));
  // Switchable base layers (radio in the layer control). Same tile sources the admin
  // "Add basemap" presets use (main.py PRESET_BASEMAPS). Street is the default; the
  // chosen basemap is remembered across sessions like the saved view.
  var BASEMAP_KEY = "preplanner.basemap";
  var baseLayers = {
    // Canonical single host — OSM deprecated the a/b/c subdomains, and different
    // browsers coalesce HTTP/2 connections across those hostnames differently
    // (a source of Firefox-only 403s). One host is OSM's current recommendation.
    "Street": L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }),
    "Satellite": L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
      maxZoom: 19, maxNativeZoom: 19,
      attribution: 'Imagery &copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community'
    }),
    "Topographic": L.tileLayer("https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}", {
      maxZoom: 19, maxNativeZoom: 16,
      attribution: 'Tiles &copy; <a href="https://www.usgs.gov/">USGS</a> The National Map'
    })
  };
  var savedBase = null;
  try { savedBase = localStorage.getItem(BASEMAP_KEY); } catch (e) {}
  (baseLayers[savedBase] || baseLayers.Street).addTo(map);
  map.on("baselayerchange", function (e) {
    try { localStorage.setItem(BASEMAP_KEY, e.name); } catch (e2) {}
  });

  function saveView() {
    try {
      var c = map.getCenter(), b = map.getBounds();
      localStorage.setItem(VIEW_KEY, JSON.stringify({
        lat: +c.lat.toFixed(6), lng: +c.lng.toFixed(6), zoom: map.getZoom(),
        south: +b.getSouth().toFixed(6), west: +b.getWest().toFixed(6),
        north: +b.getNorth().toFixed(6), east: +b.getEast().toFixed(6)
      }));
    } catch (e) {}
  }
  if (!OCC) map.on("moveend", saveView);  // don't let the pre-plan map clobber the dept view

  // Browse shows only Occupancies, Zones and Hydrants; the rest are operate-only.
  var occupancyLayer = L.layerGroup().addTo(map);
  var hydrantLayer = L.layerGroup().addTo(map);
  var zoneLayer = L.layerGroup().addTo(map);
  var footprintLayer = L.layerGroup();
  var accessLayer = L.layerGroup();
  var routeLayer = L.layerGroup();
  var symbolLayer = L.layerGroup();
  var libraryLayer = L.layerGroup();  // geotagged library photos (browse map)
  if (!BROWSE) [footprintLayer, accessLayer, routeLayer, symbolLayer].forEach(function (g) { g.addTo(map); });

  var rendered = {};  // map_feature uuid -> { layer, group, sig }

  // --- occupancies / footprints / hydrants (small sets: full rebuild) --------
  function renderOccupancies(occs) {
    occupancyLayer.clearLayers();
    footprintLayer.clearLayers();
    occs.forEach(function (o) {
      if (o.latitude != null && o.longitude != null) {
        var m = L.marker([o.latitude, o.longitude]);
        var summary = '<div class="popup"><h3>' + esc(o.name || "Unnamed") + "</h3>" +
          (o.occupancy_type ? "<div>" + esc(o.occupancy_type) + "</div>" : "") +
          (o.construction_type ? "<div>" + esc(o.construction_type) + "</div>" : "");
        var url = o.id ? "/occupancies/" + o.id
                       : "/occupancies/edit?u=" + encodeURIComponent(o.uuid);
        if (BROWSE) {
          // Summary on hover; clicking the icon opens the full pre-plan.
          m.bindTooltip(summary + "</div>", { direction: "top", offset: [0, -10], opacity: 1 });
          m.on("click", function () { window.location = url; });
        } else {
          m.bindPopup(summary +
            '<p><a href="/occupancies/edit?u=' + encodeURIComponent(o.uuid) +
            '">Open pre-plan &rarr;</a></p></div>');
        }
        occupancyLayer.addLayer(m);
      }
      if (o.footprint_geojson) {
        try {
          var g = JSON.parse(o.footprint_geojson);
          if (g.type === "Feature") g = g.geometry;
          L.geoJSON({ type: "Feature", geometry: g },
            { style: { color: "#c0392b", weight: 2, fillOpacity: 0.08, dashArray: "4" } })
            .addTo(footprintLayer);
        } catch (e) { /* skip malformed */ }
      }
    });
  }

  function renderHydrants(hyds) {
    hydrantLayer.clearLayers();
    hyds.forEach(function (h) {
      if (h.latitude == null || h.longitude == null) return;
      var c = hydrantClass(h.flow_gpm);
      hydrantLayer.addLayer(L.circleMarker([h.latitude, h.longitude], {
        radius: 6, color: "#333", weight: 1, fillColor: c.color,
        fillOpacity: h.in_service === false ? 0.25 : 0.9
      }).bindPopup(
        '<div class="popup"><h3>' + esc(h.label || "Hydrant") + "</h3>" +
        (h.flow_gpm != null ? "<div>" + h.flow_gpm + " GPM" + (c.code ? " (Class " + c.code + ")" : "") + "</div>" : "") +
        (h.hydrant_type ? "<div>" + esc(h.hydrant_type) + "</div>" : "") +
        (h.in_service === false ? "<div><strong>Out of service</strong></div>" : "") + "</div>"));
    });
  }

  // --- map features (large set: incremental diff by uuid) --------------------
  function sigOf(f) {
    return [f.updated_at, f.geometry_json, f.label, f.category, f.color, f.symbol,
      f.rotation, f.scale, f.length, f.label_lat, f.label_lng].join("|");
  }

  function removeRendered(u) {
    var r = rendered[u];
    if (!r) return;
    r.group.removeLayer(r.layer);
    if (r.labelLayer) r.group.removeLayer(r.labelLayer);
    delete rendered[u];
  }

  function renderFeatures(feats) {
    var seen = {};
    feats.forEach(function (f) {
      seen[f.uuid] = true;
      var cur = rendered[f.uuid];
      var sig = sigOf(f);
      if (!cur) { addFeatureLayer(f, sig); }
      else if (cur.sig !== sig) { removeRendered(f.uuid); addFeatureLayer(f, sig); }
    });
    Object.keys(rendered).forEach(function (u) { if (!seen[u]) removeRendered(u); });
  }

  function addFeatureLayer(f, sig) {
    var geom;
    try { geom = JSON.parse(f.geometry_json); } catch (e) { return; }
    var color = f.color || featureColor(f.category);
    var sym = f.symbol && SYMBOLS_BY_KEY[f.symbol];
    var layer;
    if (geom.type === "Point") {  // points are directly draggable
      var ll = [geom.coordinates[1], geom.coordinates[0]];
      layer = L.marker(ll, sym ? { icon: symbolIcon(sym, f.rotation, f.scale, f.length), draggable: true }
                               : { draggable: true });
      layer.on("dragend", function () { onSymbolDragged(f, layer); });
    } else {
      layer = L.geoJSON({ type: "Feature", geometry: geom },
        { style: { color: color, weight: 4, fillOpacity: 0.2 } }).getLayers()[0];
    }
    layer.featureUuid = f.uuid;
    layer.bindPopup(featurePopupHtml(f));
    if (sym && sym.arrow) {
      layer.on("mouseover", function () { showArrowPanel(f.uuid); });
      layer.on("mouseout", scheduleArrowPanelHide);
    }
    layer.on("pm:update", function () { onGeometryEdited(f, layer); });
    layer.on("pm:dragend", function () { onGeometryEdited(f, layer); });
    var group = groupForCategory(f.category);
    group.addLayer(layer);
    var reg = { layer: layer, group: group, sig: sig, f: f };

    // Symbols carry a draggable, always-visible label (defaults to the symbol).
    if (f.category === "Symbol" && f.label) {
      var lpos = (f.label_lat != null && f.label_lng != null)
        ? [f.label_lat, f.label_lng] : [geom.coordinates[1], geom.coordinates[0]];
      var labelLayer = L.marker(lpos, { draggable: true, keyboard: false,
        icon: L.divIcon({ className: "map-label", iconSize: null, iconAnchor: [-8, 9], html: esc(f.label) }) });
      labelLayer.on("dragend", function () { onLabelDragged(f, labelLayer); });
      group.addLayer(labelLayer);
      reg.labelLayer = labelLayer;
    }
    rendered[f.uuid] = reg;
  }

  // Merge an update into the store and keep our render signature current so the
  // re-render doesn't rebuild (and disrupt) the layer being dragged in place.
  function persistFeature(uuid, upd) {
    Store.update("map_feature", uuid, upd);
    var reg = rendered[uuid];
    if (reg) { reg.f = Object.assign({}, reg.f, upd); reg.sig = sigOf(reg.f); }
  }

  function onGeometryEdited(f, layer) {
    persistFeature(f.uuid, { geometry_json: JSON.stringify(layer.toGeoJSON().geometry) });
  }

  function onSymbolDragged(f, layer) {
    var reg = rendered[f.uuid];
    if (!reg) return;
    var oldGeom;
    try { oldGeom = JSON.parse(reg.f.geometry_json); } catch (e) { oldGeom = null; }
    var to = layer.getLatLng();
    var upd = { geometry_json: JSON.stringify({ type: "Point", coordinates: [+to.lng.toFixed(6), +to.lat.toFixed(6)] }) };
    if (reg.labelLayer && oldGeom) {  // carry the label along with its symbol
      var dLat = to.lat - oldGeom.coordinates[1], dLng = to.lng - oldGeom.coordinates[0];
      var lp = reg.labelLayer.getLatLng();
      reg.labelLayer.setLatLng([lp.lat + dLat, lp.lng + dLng]);
      if (reg.f.label_lat != null) {
        upd.label_lat = +(reg.f.label_lat + dLat).toFixed(6);
        upd.label_lng = +(reg.f.label_lng + dLng).toFixed(6);
      }
    }
    persistFeature(f.uuid, upd);
  }

  function onLabelDragged(f, labelLayer) {
    var ll = labelLayer.getLatLng();
    persistFeature(f.uuid, { label_lat: +ll.lat.toFixed(6), label_lng: +ll.lng.toFixed(6) });
  }

  function featurePopupHtml(f) {
    var opts = CATEGORIES.map(function (c) {
      return '<option value="' + c + '"' + (c === f.category ? " selected" : "") + ">" + c + "</option>";
    }).join("");
    return '<div class="popup mf-edit" data-uuid="' + f.uuid + '">' +
      '<label class="mf-row"><span>Label</span><input class="mf-label" value="' + esc(f.label || "") + '"></label>' +
      '<label class="mf-row"><span>Category</span><select class="mf-cat">' + opts + '</select></label>' +
      '<label class="mf-row"><span>Notes</span><input class="mf-notes" value="' + esc(f.notes || "") + '"></label>' +
      '<div class="mf-actions"><button type="button" class="btn btn-sm mf-save">Save</button>' +
      '<button type="button" class="btn btn-sm btn-danger mf-del">Delete</button></div></div>';
  }

  // --- Geoman drawing --------------------------------------------------------
  if (!BROWSE) map.pm.addControls({
    position: "topleft",
    drawMarker: true, drawPolyline: true, drawPolygon: true, drawRectangle: true,
    drawCircle: false, drawCircleMarker: false, drawText: false,
    editMode: true, dragMode: true, removalMode: true,
    cutPolygon: false, rotateMode: false
  });

  // Finish a polyline/polygon on double-click — the instinctive gesture. Geoman
  // otherwise leaves finishOn null, so a double-click falls through to the map's
  // zoom handler: the map lurches to max zoom mid-draw and the shape seems to
  // vanish, which reads as the app "crashing". With finishOn:"dblclick" Geoman
  // suppresses doubleClickZoom while drawing and restores it when the draw ends.
  // continueDrawing:false disarms the tool after one shape — otherwise Geoman keeps
  // it armed, so you place + label a marker/polygon and are still stuck in draw mode.
  map.pm.setGlobalOptions({ finishOn: "dblclick", continueDrawing: false });

  // Harden the finish path. Every draw tool keeps a hidden "Finish" action button
  // whose handler calls Draw[shape]._finishShape(); if that tool was never started
  // its working layer is undefined and Geoman throws "Cannot read properties of
  // undefined (reading 'getLatLngs')", killing the map. Treat a missing working
  // layer as nothing-to-finish rather than letting it crash.
  ["Line", "Polygon", "Rectangle"].forEach(function (shape) {
    var handler = map.pm.Draw[shape];
    if (!handler || handler.__finishGuarded) return;
    var finish = handler._finishShape;
    handler._finishShape = function () {
      if (!this._layer) return;
      return finish.apply(this, arguments);
    };
    handler.__finishGuarded = true;
  });

  map.on("pm:create", function (e) {
    if (OCC && footprintMode) {   // drawn via the Footprint control → this building's outline
      footprintMode = false;
      setFootprint(e.layer);
      return;
    }
    var layer = e.layer;
    var gj = layer.toGeoJSON();  // capture geometry now; act after Geoman finishes
    var category = categoryForGeometry(gj.geometry.type);
    // Defer removal + prompt: removing the layer (or blocking on prompt) inside
    // pm:create crashes Geoman, which still touches the layer as it finishes.
    setTimeout(function () {
      map.removeLayer(layer);  // the store copy will be rendered instead
      var label = window.prompt("Label for this " + category + " (optional):", "") || "";
      Store.create("map_feature", { category: category, label: label,
        geometry_json: JSON.stringify(gj.geometry) });
    }, 0);
  });

  map.on("pm:remove", function (e) {
    if (e.layer.featureUuid) Store.remove("map_feature", e.layer.featureUuid);
  });

  map.on("popupopen", function (e) {
    var root = e.popup.getElement();
    var box = root && root.querySelector(".mf-edit");
    if (!box) return;
    var uuid = box.getAttribute("data-uuid");
    box.querySelector(".mf-save").onclick = function () {
      Store.update("map_feature", uuid, {
        label: box.querySelector(".mf-label").value,
        category: box.querySelector(".mf-cat").value,
        notes: box.querySelector(".mf-notes").value
      });
      map.closePopup();
    };
    box.querySelector(".mf-del").onclick = function () {
      Dialog.confirm("Delete this feature?", { danger: true }).then(function (ok) {
        if (ok) { Store.remove("map_feature", uuid); map.closePopup(); }
      });
    };
  });

  // --- click-to-place hydrant (creates locally, works offline) ---------------
  var placingHydrant = false;
  var HydrantControl = L.Control.extend({
    options: { position: "topleft" },
    onAdd: function () {
      var c = L.DomUtil.create("div", "leaflet-bar hydrant-control");
      var b = L.DomUtil.create("a", "", c);
      b.href = "#"; b.title = "Place a hydrant: click this, then click the map"; b.innerHTML = "&#128167;";
      L.DomEvent.on(b, "click", function (ev) {
        L.DomEvent.preventDefault(ev); L.DomEvent.stopPropagation(ev);
        placingHydrant = !placingHydrant;
        if (placingHydrant) { disarmSymbol(); stopMeasure(); }
        c.classList.toggle("active", placingHydrant);
        map.getContainer().style.cursor = placingHydrant ? "crosshair" : "";
      });
      return c;
    }
  });
  if (!BROWSE) map.addControl(new HydrantControl());
  map.on("click", function (e) {
    if (!placingHydrant) return;
    var label = window.prompt("Hydrant label (optional):", "") || "";
    Store.create("hydrant", { label: label, latitude: +e.latlng.lat.toFixed(6),
      longitude: +e.latlng.lng.toFixed(6), in_service: true });
  });

  // --- symbol palette: place fire-service symbols as point features ----------
  var pendingSymbol = null;
  function disarmSymbol() {
    pendingSymbol = null;
    var p = document.querySelector(".map-symbol-palette");
    if (p) p.querySelectorAll(".map-symbol-btn.active").forEach(function (b) { b.classList.remove("active"); });
    if (!placingHydrant && !measuring) map.getContainer().style.cursor = "";
  }
  var SymbolPalette = L.Control.extend({
    options: { position: "topleft" },
    onAdd: function () {
      var wrap = L.DomUtil.create("div", "leaflet-bar map-symbol-palette");
      var toggle = L.DomUtil.create("a", "", wrap);
      toggle.href = "#"; toggle.title = "Place a symbol"; toggle.innerHTML = "&#9873;";
      var panel = L.DomUtil.create("div", "map-symbol-panel", wrap);
      panel.style.display = "none";
      MAP_SYMBOLS.forEach(function (s) {
        var btn = L.DomUtil.create("button", "map-symbol-btn", panel);
        btn.type = "button"; btn.setAttribute("data-key", s.key);
        btn.innerHTML = (s.arrow ? '<span class="map-arrow-swatch">' + arrowSvg(s) + "</span>"
          : '<span class="map-symbol" style="background:' + s.color + '">' + esc(s.code) + "</span>") +
          "<span>" + esc(s.label) + "</span>";
        L.DomEvent.on(btn, "click", function (ev) {
          L.DomEvent.stop(ev);
          var was = pendingSymbol === s.key;
          disarmSymbol();
          if (!was) {
            pendingSymbol = s.key; placingHydrant = false; stopMeasure();
            btn.classList.add("active");
            map.getContainer().style.cursor = "crosshair";
          }
          panel.style.display = "none";
        });
      });
      L.DomEvent.on(toggle, "click", function (ev) {
        L.DomEvent.stop(ev);
        panel.style.display = panel.style.display === "none" ? "block" : "none";
      });
      L.DomEvent.disableClickPropagation(wrap);
      return wrap;
    }
  });
  if (!BROWSE) map.addControl(new SymbolPalette());
  map.on("click", function (e) {
    if (!pendingSymbol || measuring || map.pm.globalDrawModeEnabled()) return;
    var sym = SYMBOLS_BY_KEY[pendingSymbol];
    var data = { category: "Symbol", symbol: pendingSymbol,
      geometry_json: JSON.stringify({ type: "Point", coordinates: [+e.latlng.lng.toFixed(6), +e.latlng.lat.toFixed(6)] }) };
    if (sym && sym.arrow) {  // arrows carry an always-visible label
      var lbl = window.prompt("Arrow label (optional):", "");
      if (lbl) data.label = lbl;
    }
    Store.create("map_feature", data);
    disarmSymbol();  // one symbol per click; pick again to place another
  });

  // --- ruler: measure ground distance (does not save anything) ---------------
  var measuring = false, measurePts = [], measureLayer = L.layerGroup().addTo(map), measureCtl = null;
  function fmtDist(m) {
    var ft = m * 3.28084;
    return ft >= 5280 ? (ft / 5280).toFixed(2) + " mi" : Math.round(ft) + " ft";
  }
  function stopMeasure() {
    measuring = false;
    map.doubleClickZoom.enable();
    if (measureCtl) measureCtl.classList.remove("active");
    if (!placingHydrant && !pendingSymbol) map.getContainer().style.cursor = "";
  }
  function clearMeasure() { measurePts = []; measureLayer.clearLayers(); }
  function redrawMeasure() {
    measureLayer.clearLayers();
    if (measurePts.length) {
      L.polyline(measurePts, { color: "#111", weight: 2, dashArray: "5,5" }).addTo(measureLayer);
      var total = 0;
      for (var i = 1; i < measurePts.length; i++) total += map.distance(measurePts[i - 1], measurePts[i]);
      measurePts.forEach(function (p) {
        L.circleMarker(p, { radius: 3, color: "#111", fillColor: "#fff", fillOpacity: 1, weight: 1 }).addTo(measureLayer);
      });
      var last = measurePts[measurePts.length - 1];
      L.marker(last, { icon: L.divIcon({ className: "measure-label", iconAnchor: [-8, 8],
        html: fmtDist(total) + (measurePts.length > 1 ? "" : " · click to add points") }) }).addTo(measureLayer);
    }
  }
  var RulerControl = L.Control.extend({
    options: { position: "topleft" },
    onAdd: function () {
      var c = L.DomUtil.create("div", "leaflet-bar ruler-control");
      measureCtl = c;
      var b = L.DomUtil.create("a", "", c);
      b.href = "#"; b.title = "Measure distance: click this, then click points; double-click or Esc to finish"; b.innerHTML = "&#128207;";
      L.DomEvent.on(b, "click", function (ev) {
        L.DomEvent.preventDefault(ev); L.DomEvent.stopPropagation(ev);
        if (measuring) { stopMeasure(); return; }
        clearMeasure(); measuring = true; placingHydrant = false; disarmSymbol();
        map.doubleClickZoom.disable();
        c.classList.add("active"); map.getContainer().style.cursor = "crosshair";
      });
      return c;
    }
  });
  map.addControl(new RulerControl());
  map.on("click", function (e) {
    if (!measuring || map.pm.globalDrawModeEnabled()) return;
    measurePts.push(e.latlng); redrawMeasure();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    if (measuring) stopMeasure(); else if (pendingSymbol) disarmSymbol();
  });

  // --- arrow hover tools: rotate / length / size -----------------------------
  var arrowPanel = null, arrowPanelTarget = null, arrowHideTimer = null;
  function ensureArrowPanel() {
    if (arrowPanel) return;
    arrowPanel = L.DomUtil.create("div", "arrow-tools", map.getContainer());
    arrowPanel.style.display = "none";
    arrowPanel.innerHTML =
      '<button type="button" data-a="rotL" title="Rotate left">&#8634;</button>' +
      '<button type="button" data-a="rotR" title="Rotate right">&#8635;</button>' +
      '<button type="button" data-a="lenU" title="Longer">L+</button>' +
      '<button type="button" data-a="lenD" title="Shorter">L&minus;</button>' +
      '<button type="button" data-a="sizeU" title="Bigger">+</button>' +
      '<button type="button" data-a="sizeD" title="Smaller">&minus;</button>';
    L.DomEvent.disableClickPropagation(arrowPanel);
    arrowPanel.addEventListener("mouseenter", function () { clearTimeout(arrowHideTimer); });
    arrowPanel.addEventListener("mouseleave", scheduleArrowPanelHide);
    arrowPanel.addEventListener("click", function (ev) {
      var a = ev.target.getAttribute && ev.target.getAttribute("data-a");
      if (a && arrowPanelTarget) adjustArrow(arrowPanelTarget, a);
    });
  }
  function scheduleArrowPanelHide() {
    clearTimeout(arrowHideTimer);
    arrowHideTimer = setTimeout(function () {
      if (arrowPanel) arrowPanel.style.display = "none";
      arrowPanelTarget = null;
    }, 400);
  }
  function showArrowPanel(uuid) {
    var reg = rendered[uuid];
    if (!reg) return;
    ensureArrowPanel();
    clearTimeout(arrowHideTimer);
    arrowPanelTarget = uuid;
    var pt = map.latLngToContainerPoint(reg.layer.getLatLng());
    arrowPanel.style.left = (pt.x + 16) + "px";
    arrowPanel.style.top = Math.max(4, pt.y - 42) + "px";
    arrowPanel.style.display = "flex";
  }
  function adjustArrow(uuid, action) {
    var reg = rendered[uuid];
    if (!reg || !reg.f) return;
    var f = reg.f, sym = SYMBOLS_BY_KEY[f.symbol];
    if (!sym || !sym.arrow) return;
    var rot = f.rotation || 0, scale = f.scale || 1, len = f.length || 1;
    if (action === "rotL") rot = (rot - 15 + 360) % 360;
    else if (action === "rotR") rot = (rot + 15) % 360;
    else if (action === "lenU") len = Math.min(5, +(len + 0.25).toFixed(2));
    else if (action === "lenD") len = Math.max(0.5, +(len - 0.25).toFixed(2));
    else if (action === "sizeU") scale = Math.min(4, +(scale + 0.25).toFixed(2));
    else if (action === "sizeD") scale = Math.max(0.5, +(scale - 0.25).toFixed(2));
    reg.layer.setIcon(symbolIcon(sym, rot, scale, len));
    persistFeature(uuid, { rotation: rot, scale: scale, length: len });
  }

  // --- layer control + WMS overlays ------------------------------------------
  // Browse exposes only Occupancies / Zones / Hydrants (+ base layers); operate the full set.
  var overlays = BROWSE
    ? { "Occupancies": occupancyLayer, "Zones": zoneLayer, "Hydrants": hydrantLayer,
        "Library files": libraryLayer }
    : { "Occupancies": occupancyLayer, "Footprints": footprintLayer, "Hydrants": hydrantLayer,
        "Access points": accessLayer, "Routes": routeLayer, "Zones": zoneLayer, "Symbols": symbolLayer };
  var layersControl = L.control.layers(baseLayers, overlays, { collapsed: false }).addTo(map);

  // Geotagged library photos as a toggleable "Library files" layer (browse map).
  // Assets aren't in the offline Store, so fetch them directly; markers open the
  // picture in the lightbox (or link out for non-images).
  if (BROWSE) {
    libraryLayer.addTo(map);
    fetch("/api/library-locations").then(function (r) { return r.ok ? r.json() : []; })
      .then(function (items) {
        items.forEach(function (a) {
          if (a.latitude == null || a.longitude == null) return;
          var m = L.marker([a.latitude, a.longitude], {
            icon: L.divIcon({ className: "lib-marker", html: "📷",
              iconSize: [26, 26], iconAnchor: [13, 13] })
          }).addTo(libraryLayer);
          var t = esc(a.title || "");
          var body = a.is_image
            ? '<img class="lib-pop-img" src="' + a.url + '" alt="" data-lightbox ' +
              'data-src="' + a.url + '" data-title="' + t + '">' +
              '<button type="button" class="btn btn-sm btn-primary" data-lightbox ' +
              'data-src="' + a.url + '" data-title="' + t + '">View full size</button>'
            : '<a class="btn btn-sm btn-primary" href="' + a.url + '" target="_blank" rel="noopener">Open file</a>';
          m.bindPopup('<div class="popup lib-pop"><h3>' + (t || "Photo") + "</h3>" + body + "</div>");
        });
      }).catch(function () { /* offline or none */ });
  }

  // WMS config is admin/online-only; unavailable offline (fetch simply fails).
  // Some servers advertise layers in GetCapabilities that GetMap can't actually
  // render (restricted DB tables, server errors) — those tiles fail to load,
  // which would otherwise be a silent "nothing shows". Surface it instead.
  var wmsToast, wmsToastTimer;
  function showWmsToast(msg) {
    if (!wmsToast) {
      wmsToast = L.DomUtil.create("div", "map-toast", map.getContainer());
      L.DomEvent.disableClickPropagation(wmsToast);
      wmsToast.addEventListener("click", function () { wmsToast.style.display = "none"; });
    }
    wmsToast.textContent = msg;
    wmsToast.style.display = "block";
    clearTimeout(wmsToastTimer);
    wmsToastTimer = setTimeout(function () { wmsToast.style.display = "none"; }, 12000);
  }

  if (!BROWSE) fetch("/api/wms-layers").then(function (r) { return r.ok ? r.json() : []; })
    .then(function (list) {
      list.forEach(function (w) {
        var layer, label;
        if (w.kind === "xyz") {  // slippy-tile basemap (topo / imagery / hillshade)
          layer = L.tileLayer(w.url, {
            opacity: w.opacity != null ? w.opacity : 1,
            maxNativeZoom: w.max_zoom || 19, maxZoom: 19,  // upscale to map max, no further
            attribution: w.attribution || ""
          });
          label = w.name;
        } else {
          layer = L.tileLayer.wms(w.url, {
            layers: w.layers, format: w.format || "image/png",
            transparent: w.transparent !== false, opacity: w.opacity != null ? w.opacity : 0.7
          });
          label = "WMS: " + w.name;
        }
        var warned = false;
        layer.on("tileerror", function () {
          if (warned) return;
          warned = true;
          showWmsToast("“" + w.name + "” couldn’t be displayed — the server rejected the " +
            "request. That layer may be restricted, out of range, or unavailable.");
        });
        layer.on("remove", function () { warned = false; });  // re-check when toggled on again
        layersControl.addOverlay(layer, label);
      });
    }).catch(function () { /* offline — no WMS */ });

  // --- pre-plan editor: building point + footprint (occupancy form only) ------
  // Declared at buildMap scope so the pm:create handler above can branch on them.
  var footprintMode = false, footprintLayer = null, occMarker = null, occReady = false;
  var occLatEl = OCC ? document.querySelector(OCC.latSel) : null;
  var occLonEl = OCC ? document.querySelector(OCC.lonSel) : null;
  var occFpEl = OCC ? document.querySelector(OCC.fpSel) : null;

  function occNotify(el) { if (occReady && el) el.dispatchEvent(new Event("input", { bubbles: true })); }

  // The building marker's label is the address typed into the form (auto-populated
  // when you drop the pin), falling back to a hint until an address is entered.
  function occAddrLabel() {
    var a = document.querySelector('input[name="address"]');
    var c = document.querySelector('input[name="city"]');
    var parts = [a && a.value.trim(), c && c.value.trim()].filter(Boolean);
    return parts.length ? parts.join(", ") : "Building location — drag to move";
  }
  function refreshOccLabel() { if (occMarker) occMarker.setTooltipContent(occAddrLabel()); }

  function setOccPoint(latlng) {
    if (occLatEl) occLatEl.value = latlng.lat.toFixed(6);
    if (occLonEl) occLonEl.value = latlng.lng.toFixed(6);
    if (!occMarker) {
      occMarker = L.marker(latlng, { draggable: true, icon: L.divIcon({
        className: "occ-point-marker", html: "🏢", iconSize: [30, 30], iconAnchor: [15, 28] }) }).addTo(map);
      occMarker.bindTooltip(occAddrLabel());
      occMarker.on("dragend", function () {
        var ll = occMarker.getLatLng();
        if (occLatEl) occLatEl.value = ll.lat.toFixed(6);
        if (occLonEl) occLonEl.value = ll.lng.toFixed(6);
        occNotify(occLatEl);
      });
    } else { occMarker.setLatLng(latlng); }
    occNotify(occLatEl);
  }

  function serializeFootprint() {
    if (occFpEl) occFpEl.value = footprintLayer ? JSON.stringify(footprintLayer.toGeoJSON().geometry) : "";
    occNotify(occFpEl);
  }
  function setFootprint(layer) {
    if (footprintLayer) map.removeLayer(footprintLayer);
    footprintLayer = layer;
    if (layer.setStyle) layer.setStyle({ color: "#c0392b", weight: 2, fillOpacity: 0.1 });
    layer.addTo(map);
    layer.on("pm:update pm:dragend", serializeFootprint);
    serializeFootprint();
  }

  if (OCC) {
    if (occStart) setOccPoint(L.latLng(occStart[0], occStart[1]));

    // Click the map to set the building point — never while another tool is active.
    map.on("click", function (e) {
      // Click sets the point only when there isn't one yet; once placed, the
      // marker is dragged to move it. This keeps clicks meant for the drawing
      // tools or features from ever jerking the building location around.
      if (occMarker) return;
      if (footprintMode || placingHydrant || pendingSymbol || measuring) return;
      if (map.pm.globalDrawModeEnabled() || map.pm.globalEditModeEnabled() || map.pm.globalRemovalModeEnabled()) return;
      var t = e.originalEvent && e.originalEvent.target;
      var cls = (t && t.getAttribute && t.getAttribute("class")) || "";
      if (/leaflet-interactive|hydrant-marker|lib-marker/.test(cls)) return;  // not on a feature
      setOccPoint(e.latlng);
    });

    // Load an existing footprint (editable via Geoman edit/drag).
    if (occFpEl && occFpEl.value) {
      try {
        var g0 = JSON.parse(occFpEl.value); if (g0.type === "Feature") g0 = g0.geometry;
        var loaded = L.geoJSON({ type: "Feature", geometry: g0 }).getLayers()[0];
        footprintLayer = loaded;
        if (loaded.setStyle) loaded.setStyle({ color: "#c0392b", weight: 2, fillOpacity: 0.1 });
        loaded.addTo(map);
        loaded.on("pm:update pm:dragend", serializeFootprint);
      } catch (e) { /* ignore malformed footprint */ }
    }
    map.on("pm:remove", function (e) {
      if (e.layer === footprintLayer) { footprintLayer = null; serializeFootprint(); }
    });

    // A dedicated Footprint control — distinct from the shared zone/route/feature tools.
    var FootprintControl = L.Control.extend({
      options: { position: "topleft" },
      onAdd: function () {
        var c = L.DomUtil.create("div", "leaflet-bar footprint-control");
        var b = L.DomUtil.create("a", "", c);
        b.href = "#"; b.title = "Draw the building footprint"; b.innerHTML = "&#8862;";  // ⊞
        L.DomEvent.on(b, "click", function (ev) {
          L.DomEvent.preventDefault(ev); L.DomEvent.stopPropagation(ev);
          footprintMode = true; disarmSymbol(); stopMeasure(); placingHydrant = false;
          map.getContainer().style.cursor = "";
          map.pm.enableDraw("Polygon", { finishOn: "dblclick" });
        });
        return c;
      }
    });
    map.addControl(new FootprintControl());

    // Keep the marker label in sync as the address is typed.
    ["address", "city"].forEach(function (n) {
      var el = document.querySelector('input[name="' + n + '"]');
      if (el) el.addEventListener("input", refreshOccLabel);
    });

    // Full-screen toggle: expand the embedded map to fill the window for serious
    // drawing, with a "Save & return" bar (data autosaves, so returning is enough).
    var fsBar = null;
    function setFullscreen(on) {
      map.getContainer().classList.toggle("fullscreen", on);
      if (on && !fsBar) {
        fsBar = L.DomUtil.create("div", "map-fs-bar", map.getContainer());
        var done = L.DomUtil.create("button", "btn btn-primary", fsBar);
        done.type = "button"; done.textContent = "Save & return";
        L.DomEvent.on(done, "click", function (ev) { L.DomEvent.stop(ev); setFullscreen(false); });
        L.DomEvent.disableClickPropagation(fsBar);
      }
      if (fsBar) fsBar.style.display = on ? "" : "none";
      setTimeout(function () { map.invalidateSize(); }, 60);
    }
    var FullscreenControl = L.Control.extend({
      options: { position: "topright" },
      onAdd: function () {
        var c = L.DomUtil.create("div", "leaflet-bar fs-control");
        var b = L.DomUtil.create("a", "", c);
        b.href = "#"; b.title = "Full screen"; b.innerHTML = "&#9974;";  // ⛶
        L.DomEvent.on(b, "click", function (ev) {
          L.DomEvent.preventDefault(ev); L.DomEvent.stopPropagation(ev);
          setFullscreen(!map.getContainer().classList.contains("fullscreen"));
        });
        return c;
      }
    });
    map.addControl(new FullscreenControl());

    setTimeout(function () { map.invalidateSize(); }, 200);  // laid out inside a long form
    occReady = true;
  }

  // --- boot: render from the store, re-render on store changes ---------------
  var didFit = false;
  function renderAll() {
    return Promise.all([
      Store.list("occupancy"), Store.list("hydrant"), Store.list("map_feature")
    ]).then(function (r) {
      renderOccupancies(r[0]);
      renderHydrants(r[1]);
      renderFeatures(r[2]);
      if (!didFit) { if (!savedView && !occStart) fit(); didFit = true; }  // saved view / building wins
    });
  }
  function fit() {
    var group = L.featureGroup([occupancyLayer, footprintLayer, hydrantLayer,
      accessLayer, routeLayer, zoneLayer]);
    var b = group.getBounds();
    if (b.isValid()) { map.invalidateSize(); map.fitBounds(b, { padding: [40, 40], maxZoom: 16 }); }
  }

  var renderTimer = null;
  Store.ready.then(function () {
    renderAll();
    Store.subscribe(function () { clearTimeout(renderTimer); renderTimer = setTimeout(renderAll, 250); });
  });

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  }  // end buildMap

  // Dispatch. On the occupancy form (window.MAP_INIT set), expose the build as
  // window.initOccMap and honor occupancy.js's __occDeferMapInit deferral (so the
  // local-first editor can populate the inputs first); otherwise build immediately.
  if (window.MAP_INIT) {
    window.initOccMap = function () {
      var el = document.getElementById(MAP_EL);
      if (!el || el._ppBuilt) return;   // guard double-init
      el._ppBuilt = true;
      buildMap();
    };
    if (!window.__occDeferMapInit) window.initOccMap();
  } else if (document.getElementById("map")) {
    buildMap();
  }
})();
