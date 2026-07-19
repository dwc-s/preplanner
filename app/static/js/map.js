/* Map view: renders department-scoped layers and lets users draw features with
 * Leaflet-Geoman. Drawn features (access points, routes, zones) are saved to the
 * MapFeature API; occupancy footprints, occupancy points, and hydrants are read
 * here but edited elsewhere. Plain Leaflet + Geoman, no framework. */
(function () {
  "use strict";

  var CSRF = document.querySelector('meta[name="csrf-token"]').content;
  var CATEGORIES = ["Access Point", "Route", "Hazard Zone", "Custom"];
  var DEFAULT_CENTER = [44.2601, -72.5754];
  var DEFAULT_ZOOM = 13;

  var map = L.map("map").setView(DEFAULT_CENTER, DEFAULT_ZOOM);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  }).addTo(map);

  // --- read-only layers ------------------------------------------------------
  var occupancyLayer = L.geoJSON(null, {
    onEachFeature: function (feature, layer) {
      var p = feature.properties || {};
      layer.bindPopup(
        '<div class="popup"><h3>' + esc(p.name || "Unnamed") + "</h3>" +
        (p.address ? '<p class="meta">' + esc(p.address) + "</p>" : "") +
        (p.occupancy_type ? "<div>" + esc(p.occupancy_type) + "</div>" : "") +
        (p.construction_type ? "<div>" + esc(p.construction_type) + "</div>" : "") +
        (p.sprinkler ? "<div>Sprinklered</div>" : "") +
        '<p><a href="' + p.url + '">Open pre-plan &rarr;</a></p></div>');
    }
  });

  var footprintLayer = L.geoJSON(null, {
    style: { color: "#c0392b", weight: 2, fillOpacity: 0.08, dashArray: "4" },
    onEachFeature: function (feature, layer) {
      var p = feature.properties || {};
      layer.bindPopup('<div class="popup"><a href="' + p.url + '">' +
        esc(p.name || "Building") + "</a></div>");
    }
  });

  var hydrantLayer = L.geoJSON(null, {
    pointToLayer: function (feature, latlng) {
      var p = feature.properties || {};
      return L.circleMarker(latlng, {
        radius: 6, color: "#333", weight: 1,
        fillColor: p.color || "#adb5bd",
        fillOpacity: p.in_service === false ? 0.25 : 0.9
      });
    },
    onEachFeature: function (feature, layer) {
      var p = feature.properties || {};
      layer.bindPopup(
        '<div class="popup"><h3>' + esc(p.label || "Hydrant") + "</h3>" +
        (p.flow_gpm != null ? "<div>" + p.flow_gpm + " GPM" +
          (p.flow_class ? " (Class " + p.flow_class + ")" : "") + "</div>" : "") +
        (p.type ? "<div>" + esc(p.type) + "</div>" : "") +
        (p.in_service === false ? "<div><strong>Out of service</strong></div>" : "") +
        "</div>");
    }
  });

  // --- editable drawn-feature layers (one per category group) ----------------
  var accessLayer = L.featureGroup();
  var routeLayer = L.featureGroup();
  var zoneLayer = L.featureGroup();
  [accessLayer, routeLayer, zoneLayer].forEach(function (l) { l.addTo(map); });

  var featureRegistry = {};  // id -> { layer, group }

  function groupForCategory(cat) {
    if (cat === "Access Point") return accessLayer;
    if (cat === "Route") return routeLayer;
    return zoneLayer;  // Hazard Zone, Custom
  }

  function categoryForGeometry(type) {
    if (type === "Point") return "Access Point";
    if (type === "LineString" || type === "MultiLineString") return "Route";
    return "Hazard Zone";  // Polygon / Rectangle
  }

  function addFeatureToMap(feature) {
    var p = feature.properties || {};
    var color = p.color || "#7048e8";
    var layer;
    if (feature.geometry.type === "Point") {
      var c = feature.geometry.coordinates;
      layer = L.marker([c[1], c[0]]);
    } else {
      layer = L.geoJSON(feature, {
        style: { color: color, weight: 4, fillOpacity: 0.2 }
      }).getLayers()[0];
    }
    layer.featureId = p.id;
    layer.bindPopup(featurePopupHtml(p));
    layer.on("pm:update", function () { persistGeometry(layer); });
    layer.on("pm:dragend", function () { persistGeometry(layer); });

    var group = groupForCategory(p.category);
    group.addLayer(layer);
    featureRegistry[p.id] = { layer: layer, group: group };
  }

  function removeFeatureLayer(id) {
    var entry = featureRegistry[id];
    if (entry) {
      entry.group.removeLayer(entry.layer);
      delete featureRegistry[id];
    }
  }

  function persistGeometry(layer) {
    apiJson("PUT", "/api/map-features/" + layer.featureId,
      { geometry: layer.toGeoJSON().geometry });
  }

  function featurePopupHtml(p) {
    var opts = CATEGORIES.map(function (c) {
      return '<option value="' + c + '"' + (c === p.category ? " selected" : "") +
        ">" + c + "</option>";
    }).join("");
    return '<div class="popup mf-edit" data-id="' + p.id + '">' +
      '<label class="mf-row"><span>Label</span><input class="mf-label" value="' + esc(p.label || "") + '"></label>' +
      '<label class="mf-row"><span>Category</span><select class="mf-cat">' + opts + '</select></label>' +
      '<label class="mf-row"><span>Notes</span><input class="mf-notes" value="' + esc(p.notes || "") + '"></label>' +
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
    map.removeLayer(e.layer);  // replace with the server-canonical feature
    apiJson("POST", "/api/map-features",
      { category: category, label: label, geometry: gj.geometry })
      .then(function (r) { if (!r.ok) throw new Error(); return r.json(); })
      .then(addFeatureToMap)
      .catch(function () { alert("Could not save feature."); });
  });

  map.on("pm:remove", function (e) {
    var id = e.layer.featureId;
    if (id != null) {
      apiJson("DELETE", "/api/map-features/" + id);
      delete featureRegistry[id];
    }
  });

  // Wire the in-popup edit form when a feature popup opens.
  map.on("popupopen", function (e) {
    var root = e.popup.getElement();
    var box = root && root.querySelector(".mf-edit");
    if (!box) return;
    var id = box.getAttribute("data-id");
    box.querySelector(".mf-save").onclick = function () {
      apiJson("PUT", "/api/map-features/" + id, {
        label: box.querySelector(".mf-label").value,
        category: box.querySelector(".mf-cat").value,
        notes: box.querySelector(".mf-notes").value
      }).then(function (r) { return r.json(); }).then(function (feat) {
        removeFeatureLayer(id);
        addFeatureToMap(feat);
        map.closePopup();
      });
    };
    box.querySelector(".mf-del").onclick = function () {
      if (!confirm("Delete this feature?")) return;
      apiJson("DELETE", "/api/map-features/" + id).then(function () {
        removeFeatureLayer(id);
        map.closePopup();
      });
    };
  });

  // --- click-to-place hydrant ------------------------------------------------
  var placingHydrant = false;
  var HydrantControl = L.Control.extend({
    options: { position: "topleft" },
    onAdd: function () {
      var c = L.DomUtil.create("div", "leaflet-bar hydrant-control");
      var b = L.DomUtil.create("a", "", c);
      b.href = "#";
      b.title = "Place a hydrant: click this, then click the map";
      b.innerHTML = "&#128167;";  // droplet
      L.DomEvent.on(b, "click", function (ev) {
        L.DomEvent.preventDefault(ev);
        L.DomEvent.stopPropagation(ev);
        placingHydrant = !placingHydrant;
        c.classList.toggle("active", placingHydrant);
        map.getContainer().style.cursor = placingHydrant ? "crosshair" : "";
      });
      return c;
    }
  });
  map.addControl(new HydrantControl());
  map.on("click", function (e) {
    if (placingHydrant) {
      window.location = "/hydrants/new?lat=" + e.latlng.lat.toFixed(6) +
        "&lon=" + e.latlng.lng.toFixed(6);
    }
  });

  // --- layer control ---------------------------------------------------------
  var layersControl = L.control.layers(null, {
    "Occupancies": occupancyLayer,
    "Footprints": footprintLayer,
    "Hydrants": hydrantLayer,
    "Access points": accessLayer,
    "Routes": routeLayer,
    "Zones": zoneLayer
  }, { collapsed: false }).addTo(map);

  // WMS overlays (e.g. state parcel data). Added to the control but off by
  // default — the user toggles them on.
  fetch("/api/wms-layers").then(function (r) { return r.ok ? r.json() : []; })
    .then(function (list) {
      list.forEach(function (w) {
        var wms = L.tileLayer.wms(w.url, {
          layers: w.layers,
          format: w.format || "image/png",
          transparent: w.transparent !== false,
          opacity: w.opacity != null ? w.opacity : 0.7
        });
        layersControl.addOverlay(wms, "WMS: " + w.name);
      });
    }).catch(logErr("/api/wms-layers"));

  // --- load everything -------------------------------------------------------
  Promise.all([
    loadInto("/api/occupancies", occupancyLayer),
    loadInto("/api/footprints", footprintLayer),
    loadInto("/api/hydrants", hydrantLayer),
    loadFeatures("/api/map-features")
  ]).then(function () {
    var group = L.featureGroup([
      occupancyLayer, footprintLayer, hydrantLayer,
      accessLayer, routeLayer, zoneLayer
    ]);
    var bounds = group.getBounds();
    if (bounds.isValid()) {
      map.invalidateSize();
      map.fitBounds(bounds, { padding: [40, 40], maxZoom: 16 });
    }
  });

  function loadInto(url, layer) {
    return fetch(url).then(okJson).then(function (geojson) {
      layer.addData(geojson);
      layer.addTo(map);
    }).catch(logErr(url));
  }

  function loadFeatures(url) {
    return fetch(url).then(okJson).then(function (geojson) {
      (geojson.features || []).forEach(addFeatureToMap);
    }).catch(logErr(url));
  }

  // --- helpers ---------------------------------------------------------------
  function apiJson(method, url, body) {
    return fetch(url, {
      method: method,
      headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF },
      body: body ? JSON.stringify(body) : undefined
    });
  }
  function okJson(r) { return r.ok ? r.json() : { features: [] }; }
  function logErr(url) { return function (err) { console.error("Failed:", url, err); }; }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
})();
