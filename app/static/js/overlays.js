/* WMS layer picker (admin, online-only).
 *
 * Paste a WMS base URL -> the browser fetches GetCapabilities directly and lists
 * the server's layers so an admin can search and check the ones they want,
 * without knowing any layer names. Reading capabilities client-side means the
 * app server makes no outbound call (works on restricted hosts); it needs the
 * WMS server to allow cross-origin reads (most GeoServer/ArcGIS do). If it
 * can't, the manual "add by name" form below still works.
 */
(function () {
  var meta = document.querySelector('meta[name="csrf-token"]');
  var CSRF = meta ? meta.content : "";

  var urlInput = document.getElementById("wms-url");
  var loadBtn = document.getElementById("wms-load");
  var statusEl = document.getElementById("wms-load-status");
  var picker = document.getElementById("wms-picker");
  var searchInput = document.getElementById("wms-search");
  var listEl = document.getElementById("wms-list");
  var addBtn = document.getElementById("wms-add-selected");
  var countEl = document.getElementById("wms-selected-count");
  if (!loadBtn || !urlInput) return;

  var allLayers = [];   // [{name, title}]
  var selected = {};    // name -> title
  var RENDER_CAP = 300; // don't paint thousands of rows at once

  // WMS request params Leaflet supplies per-tile; strip them from a pasted URL
  // so we store a clean base (but keep others, e.g. MapServer's map=...).
  var WMS_PARAMS = ["service", "version", "request", "layers", "styles", "srs",
    "crs", "bbox", "width", "height", "format", "transparent", "exceptions"];

  function cleanBase(raw) {
    raw = (raw || "").trim();
    if (!raw) return "";
    if (!/^https?:\/\//i.test(raw)) raw = "https://" + raw;
    var u;
    try { u = new URL(raw); } catch (e) { return raw; }
    Array.prototype.slice.call(u.searchParams.keys()).forEach(function (k) {
      if (WMS_PARAMS.indexOf(k.toLowerCase()) !== -1) u.searchParams.delete(k);
    });
    return u.toString().replace(/\?$/, "");
  }

  function capabilitiesUrl(base) {
    return base + (base.indexOf("?") === -1 ? "?" : "&") +
      "service=WMS&version=1.3.0&request=GetCapabilities";
  }

  function directChildText(el, localName) {
    for (var i = 0; i < el.children.length; i++) {
      if (el.children[i].localName === localName) {
        return (el.children[i].textContent || "").trim();
      }
    }
    return "";
  }

  // Handles WMS 1.3.0 (namespaced) and 1.1.1 (no namespace) alike.
  function parseCapabilities(xmlText) {
    var doc = new DOMParser().parseFromString(xmlText, "application/xml");
    if (doc.getElementsByTagName("parsererror").length) {
      throw new Error("the response wasn't valid XML.");
    }
    var exc = doc.getElementsByTagNameNS("*", "ServiceException");
    if (exc.length) {
      throw new Error((exc[0].textContent || "service exception").trim());
    }
    var nodes = doc.getElementsByTagNameNS("*", "Layer");
    var out = [], seen = {};
    for (var i = 0; i < nodes.length; i++) {
      var name = directChildText(nodes[i], "Name");
      if (!name || seen[name]) continue;   // unnamed = group/container; skip
      seen[name] = true;
      out.push({ name: name, title: directChildText(nodes[i], "Title") || name });
    }
    out.sort(function (a, b) { return a.title.toLowerCase() < b.title.toLowerCase() ? -1 : 1; });
    return out;
  }

  function setStatus(msg, isError) {
    statusEl.textContent = msg || "";
    statusEl.className = "hint" + (isError ? " error" : "");
  }

  function updateCount() {
    var n = Object.keys(selected).length;
    countEl.textContent = n ? n + " selected" : "";
    addBtn.disabled = !n;
  }

  function render() {
    var q = (searchInput.value || "").trim().toLowerCase();
    var frag = document.createDocumentFragment();
    var shown = 0, matches = 0;
    for (var i = 0; i < allLayers.length; i++) {
      var l = allLayers[i];
      if (q && (l.title + " " + l.name).toLowerCase().indexOf(q) === -1) continue;
      matches++;
      if (shown >= RENDER_CAP) continue;
      shown++;
      frag.appendChild(rowFor(l));
    }
    listEl.innerHTML = "";
    if (!matches) {
      listEl.innerHTML = '<p class="subtle" style="padding:.6rem .7rem;margin:0;">' +
        (q ? "No layers match your search." : "No layers found.") + "</p>";
      return;
    }
    listEl.appendChild(frag);
    if (matches > shown) {
      var more = document.createElement("p");
      more.className = "subtle";
      more.style.cssText = "padding:.5rem .7rem;margin:0;";
      more.textContent = "Showing first " + shown + " of " + matches + " matches — refine your search.";
      listEl.appendChild(more);
    }
  }

  function rowFor(l) {
    var row = document.createElement("label");
    row.className = "wms-item";
    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = l.name;
    cb.checked = !!selected[l.name];
    cb.addEventListener("change", function () {
      if (cb.checked) selected[l.name] = l.title; else delete selected[l.name];
      updateCount();
    });
    var title = document.createElement("span");
    title.className = "wms-item-title";
    title.textContent = l.title;
    var code = document.createElement("code");
    code.className = "wms-item-name";
    code.textContent = l.name;
    row.appendChild(cb);
    row.appendChild(title);
    row.appendChild(code);
    return row;
  }

  function loadLayers() {
    var base = cleanBase(urlInput.value);
    if (!base) { setStatus("Enter a WMS URL first.", true); return; }
    urlInput.value = base;
    allLayers = []; selected = {}; updateCount();
    picker.hidden = true;
    loadBtn.disabled = true;
    setStatus("Loading layers…");
    // credentials omitted: cross-origin WMS use Access-Control-Allow-Origin: *.
    fetch(capabilitiesUrl(base), { credentials: "omit" })
      .then(function (r) {
        if (!r.ok) throw new Error("the server returned HTTP " + r.status + ".");
        return r.text();
      })
      .then(function (txt) {
        allLayers = parseCapabilities(txt);
        if (!allLayers.length) { setStatus("No named layers were found at that URL.", true); return; }
        setStatus("Found " + allLayers.length + " layers. Search, check the ones you want, then Add selected.");
        searchInput.value = "";
        picker.hidden = false;
        render();
        searchInput.focus();
      })
      .catch(function (e) {
        setStatus("Couldn't read layers automatically — " + e.message +
          " The server may block cross-site requests; use “Add a layer manually” below.", true);
      })
      .then(function () { loadBtn.disabled = false; });
  }

  function addSelected() {
    var base = cleanBase(urlInput.value);
    var layers = Object.keys(selected).map(function (name) {
      return { name: name, title: selected[name] };
    });
    if (!base || !layers.length) return;
    addBtn.disabled = true;
    setStatus("Adding " + layers.length + " overlay" + (layers.length > 1 ? "s" : "") + "…");
    fetch("/overlays/add-bulk", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF },
      body: JSON.stringify({ url: base, layers: layers })
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error((res.body && res.body.error) || "server error");
        // Reload so the new overlays show in the configured list above.
        window.location.reload();
      })
      .catch(function (e) {
        setStatus("Couldn't add overlays: " + e.message, true);
        addBtn.disabled = false;
      });
  }

  loadBtn.addEventListener("click", loadLayers);
  urlInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); loadLayers(); }
  });
  addBtn.addEventListener("click", addSelected);
  var searchTimer;
  searchInput.addEventListener("input", function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(render, 120);
  });
})();


/* GIS import: clip-to-area, prefilled from the last map view (saved by map.js
 * to localStorage under the same per-department key). */
(function () {
  var form = document.getElementById("gis-import-form");
  if (!form) return;
  var toggle = document.getElementById("clip-toggle");
  var summary = document.getElementById("clip-summary");
  var refresh = document.getElementById("clip-refresh");
  var out = {
    min_lat: document.getElementById("clip-min-lat"),
    max_lat: document.getElementById("clip-max-lat"),
    min_lon: document.getElementById("clip-min-lon"),
    max_lon: document.getElementById("clip-max-lon")
  };
  var VIEW_KEY = "pp:mapview:" + (window.CURRENT_USER ? window.CURRENT_USER.department_id : "anon");

  function fmt(n) { return n >= 0 ? n.toFixed(3) : "−" + Math.abs(n).toFixed(3); }
  function loadView() {
    try { return JSON.parse(localStorage.getItem(VIEW_KEY) || "null"); } catch (e) { return null; }
  }

  function apply() {
    var v = loadView();
    if (v && typeof v.south === "number") {
      out.min_lat.value = v.south; out.max_lat.value = v.north;
      out.min_lon.value = v.west;  out.max_lon.value = v.east;
      summary.textContent = "Area (lat, lon): " + fmt(v.south) + ", " + fmt(v.west) +
        "  →  " + fmt(v.north) + ", " + fmt(v.east);
      toggle.disabled = false;
    } else {
      out.min_lat.value = out.max_lat.value = out.min_lon.value = out.max_lon.value = "";
      summary.textContent = "No saved area yet — open the Map and zoom to your area, then come back.";
      toggle.checked = false;
      toggle.disabled = true;
    }
    dim();
  }
  function dim() { summary.style.opacity = (toggle.checked && !toggle.disabled) ? "" : ".5"; }

  toggle.addEventListener("change", dim);
  refresh.addEventListener("click", apply);
  window.addEventListener("storage", function (e) { if (e.key === VIEW_KEY) apply(); });
  apply();
})();
