/* Occupancy list — local-first. Renders rows from the offline store (so
 * offline-created/edited pre-plans appear, marked "unsynced" until they reach
 * the server) and searches client-side. Rows open the unified editor by uuid. */
(function () {
  "use strict";
  var tbody = document.getElementById("occ-tbody");
  var search = document.getElementById("occ-search");
  var empty = document.getElementById("occ-empty");
  if (!tbody || !window.Store) return;

  var all = [];

  function render() {
    var q = (search.value || "").trim().toLowerCase();
    var rows = all.filter(function (o) {
      if (!q) return true;
      return [o.name, o.address, o.city].some(function (v) {
        return (v || "").toLowerCase().indexOf(q) !== -1;
      });
    }).sort(function (a, b) { return (a.name || "").localeCompare(b.name || ""); });

    tbody.innerHTML = rows.map(rowHtml).join("");
    empty.hidden = rows.length > 0 || !!q;
    tbody.querySelectorAll("tr[data-u]").forEach(function (tr) {
      tr.onclick = function () {
        location.href = "/occupancies/edit?u=" + encodeURIComponent(tr.getAttribute("data-u"));
      };
    });
  }

  function rowHtml(o) {
    var addr = [o.address, o.city, o.state, o.zip_code].filter(Boolean).join(", ");
    var href = "/occupancies/edit?u=" + encodeURIComponent(o.uuid);
    return '<tr data-u="' + esc(o.uuid) + '">' +
      '<td><a href="' + href + '">' + esc(o.name || "Unnamed") + "</a>" +
      (o.id ? "" : ' <span class="pill">unsynced</span>') + "</td>" +
      "<td>" + esc(addr || "—") + "</td>" +
      "<td>" + esc(o.occupancy_type || "—") + "</td>" +
      "<td>" + esc(o.construction_type || "—") + "</td>" +
      "<td>" + (o.sprinkler_system ? '<span class="pill pill-on">Yes</span>' : "—") + "</td></tr>";
  }

  function load() { Store.list("occupancy").then(function (occs) { all = occs; render(); }); }

  Store.ready.then(function () { load(); Store.subscribe(load); });
  search.addEventListener("input", render);

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
})();
