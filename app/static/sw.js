/* Pre-Planner service worker — offline VIEWING.
 *
 * Strategy:
 *  - App shell + vendored libs + icons: precached on install, cache-first.
 *  - Same-origin pages & /api GET responses: network-first, fall back to cache
 *    so pre-plans/map data you've viewed online stay readable offline.
 *  - OpenStreetMap tiles: cached at runtime (opaque responses) so already-seen
 *    map areas render offline.
 *
 * Writes (POST/PUT/DELETE) are passed straight through — offline editing with a
 * sync queue is intentionally out of scope for this version.
 */
const APP_CACHE = "preplanner-shell-v9";
const TILE_CACHE = "preplanner-tiles-v1";
const TILE_LIMIT = 600;

const SHELL = [
  "/static/css/style.css",
  "/static/js/map.js",
  "/static/js/occupancy_form_map.js",
  "/static/js/store.js",
  "/static/js/occupancy.js",
  "/static/js/occupancy_list.js",
  "/static/js/conflicts.js",
  "/static/js/overlays.js",
  "/static/vendor/dexie/dexie.min.js",
  "/static/vendor/leaflet/leaflet.css",
  "/static/vendor/leaflet/leaflet.js",
  "/static/vendor/leaflet/images/marker-icon.png",
  "/static/vendor/leaflet/images/marker-icon-2x.png",
  "/static/vendor/leaflet/images/marker-shadow.png",
  "/static/vendor/leaflet/images/layers.png",
  "/static/vendor/leaflet/images/layers-2x.png",
  "/static/vendor/geoman/leaflet-geoman.css",
  "/static/vendor/geoman/leaflet-geoman.min.js",
  "/static/vendor/annotorious/annotorious.css",
  "/static/vendor/annotorious/annotorious.js",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/manifest.webmanifest"
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(APP_CACHE)
      .then(function (cache) { return cache.addAll(SHELL); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys
        .filter(function (k) { return k !== APP_CACHE && k !== TILE_CACHE; })
        .map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (event) {
  var req = event.request;
  if (req.method !== "GET") return;  // never intercept writes
  var url = new URL(req.url);

  if (url.hostname.endsWith("tile.openstreetmap.org")) {
    event.respondWith(tileCache(req));
  } else if (url.origin === self.location.origin && url.pathname.startsWith("/static/")) {
    event.respondWith(cacheFirst(req));
  } else if (url.origin === self.location.origin) {
    event.respondWith(networkFirst(req));
  }
});

function cacheFirst(req) {
  return caches.open(APP_CACHE).then(function (cache) {
    return cache.match(req).then(function (hit) {
      return hit || fetch(req).then(function (res) {
        if (res.ok) cache.put(req, res.clone());
        return res;
      });
    });
  });
}

function networkFirst(req) {
  return caches.open(APP_CACHE).then(function (cache) {
    return fetch(req).then(function (res) {
      if (res.ok) cache.put(req, res.clone());
      return res;
    }).catch(function () {
      return cache.match(req);  // undefined -> browser shows its offline error
    });
  });
}

function tileCache(req) {
  return caches.open(TILE_CACHE).then(function (cache) {
    return cache.match(req).then(function (hit) {
      if (hit) return hit;
      return fetch(req).then(function (res) {
        cache.put(req, res.clone());  // opaque cross-origin tiles cache fine
        trim(cache, TILE_LIMIT);
        return res;
      });
    });
  });
}

function trim(cache, max) {
  cache.keys().then(function (keys) {
    if (keys.length > max) cache.delete(keys[0]);
  });
}
