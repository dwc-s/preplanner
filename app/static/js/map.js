/* Map view — local-first. Reads all layers from the offline store (window.Store,
 * see store.js) instead of the network, and writes drawn/edited/deleted features
 * straight to the store, which queues them for sync. So the map is fully usable
 * and editable offline. Drawn map features render incrementally (diffed by uuid)
 * so a single edit never rebuilds the whole layer. */
(function () {
  "use strict";

  var FEATURE_COLORS = { "Access Point": "#1c7ed6", "Route": "#e8590c",
    "Hazard Zone": "#e03131", "Custom": "#7048e8" };
  var CATEGORIES = ["Access Point", "Route", "Hazard Zone", "Custom"];

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
    return zoneLayer;
  }

  // Remember the last place the user looked (per department, in this browser)
  // and restore it next time. The saved bounds double as the default clip area
  // for GIS imports — the overlays page reads the same localStorage key.
  var VIEW_KEY = "pp:mapview:" + (window.CURRENT_USER ? window.CURRENT_USER.department_id : "anon");
  function loadSavedView() {
    try { return JSON.parse(localStorage.getItem(VIEW_KEY) || "null"); } catch (e) { return null; }
  }
  var savedView = loadSavedView();

  var map = L.map("map").setView(
    savedView ? [savedView.lat, savedView.lng] : [44.2601, -72.5754],
    savedView ? savedView.zoom : 13);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  }).addTo(map);

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
  map.on("moveend", saveView);

  var occupancyLayer = L.layerGroup().addTo(map);
  var footprintLayer = L.layerGroup().addTo(map);
  var hydrantLayer = L.layerGroup().addTo(map);
  var accessLayer = L.layerGroup().addTo(map);
  var routeLayer = L.layerGroup().addTo(map);
  var zoneLayer = L.layerGroup().addTo(map);

  var rendered = {};  // map_feature uuid -> { layer, group, sig }

  // --- occupancies / footprints / hydrants (small sets: full rebuild) --------
  function renderOccupancies(occs) {
    occupancyLayer.clearLayers();
    footprintLayer.clearLayers();
    occs.forEach(function (o) {
      if (o.latitude != null && o.longitude != null) {
        occupancyLayer.addLayer(L.marker([o.latitude, o.longitude]).bindPopup(
          '<div class="popup"><h3>' + esc(o.name || "Unnamed") + "</h3>" +
          (o.occupancy_type ? "<div>" + esc(o.occupancy_type) + "</div>" : "") +
          (o.construction_type ? "<div>" + esc(o.construction_type) + "</div>" : "") +
          '<p><a href="/occupancies/edit?u=' + encodeURIComponent(o.uuid) + '">Open pre-plan &rarr;</a></p>' +
          "</div>"));
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
    return [f.updated_at, f.geometry_json, f.label, f.category, f.color].join("|");
  }

  function renderFeatures(feats) {
    var seen = {};
    feats.forEach(function (f) {
      seen[f.uuid] = true;
      var cur = rendered[f.uuid];
      var sig = sigOf(f);
      if (!cur) { addFeatureLayer(f, sig); }
      else if (cur.sig !== sig) { cur.group.removeLayer(cur.layer); addFeatureLayer(f, sig); }
    });
    Object.keys(rendered).forEach(function (u) {
      if (!seen[u]) { rendered[u].group.removeLayer(rendered[u].layer); delete rendered[u]; }
    });
  }

  function addFeatureLayer(f, sig) {
    var geom;
    try { geom = JSON.parse(f.geometry_json); } catch (e) { return; }
    var color = f.color || featureColor(f.category);
    var layer;
    if (geom.type === "Point") {
      layer = L.marker([geom.coordinates[1], geom.coordinates[0]]);
    } else {
      layer = L.geoJSON({ type: "Feature", geometry: geom },
        { style: { color: color, weight: 4, fillOpacity: 0.2 } }).getLayers()[0];
    }
    layer.featureUuid = f.uuid;
    layer.bindPopup(featurePopupHtml(f));
    layer.on("pm:update", function () { onGeometryEdited(f, layer); });
    layer.on("pm:dragend", function () { onGeometryEdited(f, layer); });
    var group = groupForCategory(f.category);
    group.addLayer(layer);
    rendered[f.uuid] = { layer: layer, group: group, sig: sig };
  }

  function onGeometryEdited(f, layer) {
    var gj = JSON.stringify(layer.toGeoJSON().geometry);
    Store.update("map_feature", f.uuid, { geometry_json: gj });
    // Keep our rendered signature current so the re-render doesn't rebuild (and
    // disrupt) the layer the user just edited in place.
    if (rendered[f.uuid]) rendered[f.uuid].sig = [f.updated_at, gj, f.label, f.category, f.color].join("|");
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
  map.pm.addControls({
    position: "topleft",
    drawMarker: true, drawPolyline: true, drawPolygon: true, drawRectangle: true,
    drawCircle: false, drawCircleMarker: false, drawText: false,
    editMode: true, dragMode: true, removalMode: true,
    cutPolygon: false, rotateMode: false
  });

  map.on("pm:create", function (e) {
    var gj = e.layer.toGeoJSON();
    var category = categoryForGeometry(gj.geometry.type);
    var label = window.prompt("Label for this " + category + " (optional):", "") || "";
    map.removeLayer(e.layer);  // the store copy will be rendered instead
    Store.create("map_feature", { category: category, label: label,
      geometry_json: JSON.stringify(gj.geometry) });
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
      if (confirm("Delete this feature?")) { Store.remove("map_feature", uuid); map.closePopup(); }
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
        c.classList.toggle("active", placingHydrant);
        map.getContainer().style.cursor = placingHydrant ? "crosshair" : "";
      });
      return c;
    }
  });
  map.addControl(new HydrantControl());
  map.on("click", function (e) {
    if (!placingHydrant) return;
    var label = window.prompt("Hydrant label (optional):", "") || "";
    Store.create("hydrant", { label: label, latitude: +e.latlng.lat.toFixed(6),
      longitude: +e.latlng.lng.toFixed(6), in_service: true });
  });

  // --- layer control + WMS overlays ------------------------------------------
  var layersControl = L.control.layers(null, {
    "Occupancies": occupancyLayer, "Footprints": footprintLayer, "Hydrants": hydrantLayer,
    "Access points": accessLayer, "Routes": routeLayer, "Zones": zoneLayer
  }, { collapsed: false }).addTo(map);

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

  fetch("/api/wms-layers").then(function (r) { return r.ok ? r.json() : []; })
    .then(function (list) {
      list.forEach(function (w) {
        var layer, label;
        if (w.kind === "xyz") {  // slippy-tile basemap (topo / imagery / hillshade)
          layer = L.tileLayer(w.url, {
            opacity: w.opacity != null ? w.opacity : 1,
            maxNativeZoom: w.max_zoom || 19, maxZoom: 22,
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

  // --- boot: render from the store, re-render on store changes ---------------
  var didFit = false;
  function renderAll() {
    return Promise.all([
      Store.list("occupancy"), Store.list("hydrant"), Store.list("map_feature")
    ]).then(function (r) {
      renderOccupancies(r[0]);
      renderHydrants(r[1]);
      renderFeatures(r[2]);
      if (!didFit) { if (!savedView) fit(); didFit = true; }  // saved view wins
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
})();
