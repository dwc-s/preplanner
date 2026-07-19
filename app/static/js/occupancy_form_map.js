/* Mini map on the occupancy form: click to set the building's point (fills the
 * latitude/longitude inputs) and draw/edit its footprint polygon (serialized
 * into the hidden footprint_geojson input, which the server already parses). */
(function () {
  "use strict";

  var mapEl = document.getElementById("occ-map");
  if (!mapEl || typeof L === "undefined") return;

  var latInput = document.querySelector('input[name="latitude"]');
  var lonInput = document.querySelector('input[name="longitude"]');
  var fpInput = document.querySelector('input[name="footprint_geojson"]');

  var lat = parseFloat(latInput.value);
  var lon = parseFloat(lonInput.value);
  var hasPoint = !isNaN(lat) && !isNaN(lon);

  var map = L.map("occ-map").setView(
    hasPoint ? [lat, lon] : [44.2601, -72.5754], hasPoint ? 18 : 13);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors"
  }).addTo(map);

  // --- building point --------------------------------------------------------
  var marker = null;
  function setPoint(latlng) {
    latInput.value = latlng.lat.toFixed(6);
    lonInput.value = latlng.lng.toFixed(6);
  }
  function placeMarker(latlng) {
    if (!marker) {
      marker = L.marker(latlng, { draggable: true }).addTo(map);
      marker.on("dragend", function () { setPoint(marker.getLatLng()); });
    } else {
      marker.setLatLng(latlng);
    }
    setPoint(latlng);
  }
  if (hasPoint) placeMarker(L.latLng(lat, lon));

  map.on("click", function (e) {
    // Don't hijack clicks while drawing/editing/removing the footprint.
    if (map.pm.globalDrawModeEnabled() || map.pm.globalEditModeEnabled() ||
        map.pm.globalRemovalModeEnabled()) return;
    placeMarker(e.latlng);
  });

  // --- footprint polygon -----------------------------------------------------
  map.pm.addControls({
    position: "topleft",
    drawPolygon: true, drawRectangle: true,
    drawMarker: false, drawPolyline: false, drawCircle: false,
    drawCircleMarker: false, drawText: false,
    editMode: true, dragMode: true, removalMode: true,
    cutPolygon: false, rotateMode: false
  });

  var footprintLayer = null;
  function serialize() {
    fpInput.value = footprintLayer
      ? JSON.stringify(footprintLayer.toGeoJSON().geometry) : "";
  }
  function track(layer) {
    footprintLayer = layer;
    layer.on("pm:update", serialize);
    layer.on("pm:dragend", serialize);
    serialize();
  }

  if (fpInput.value) {
    try {
      var geom = JSON.parse(fpInput.value);
      if (geom.type === "Feature") geom = geom.geometry;
      var loaded = L.geoJSON({ type: "Feature", geometry: geom },
        { style: { color: "#c0392b", weight: 2, fillOpacity: 0.1 } }).getLayers()[0];
      loaded.addTo(map);
      track(loaded);
      try { map.fitBounds(loaded.getBounds().pad(0.5)); } catch (e) { /* noop */ }
    } catch (e) { /* ignore malformed footprint */ }
  }

  map.on("pm:create", function (e) {
    if (footprintLayer) map.removeLayer(footprintLayer);  // one footprint per building
    track(e.layer);
  });
  map.on("pm:remove", function (e) {
    if (e.layer === footprintLayer) { footprintLayer = null; serialize(); }
  });

  // The map starts inside a long form; recompute size once laid out.
  setTimeout(function () { map.invalidateSize(); }, 200);
})();
