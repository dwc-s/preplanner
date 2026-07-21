/* Click-to-sort for dashboard tables (progressive enhancement — the table is
 * perfectly usable without JS). A <th data-sort="text|date|number"> becomes a
 * sortable column; a cell can carry data-value for a sort key distinct from its
 * displayed text (e.g. an ISO date behind a friendly one). */
(function () {
  function cellValue(td, type) {
    if (!td) return type === "number" ? 0 : "";
    var raw = td.getAttribute("data-value");
    if (raw === null) raw = td.textContent.trim();
    if (type === "number") return parseFloat(raw) || 0;
    if (type === "date") return raw;            // ISO strings sort chronologically
    return raw.toLowerCase();
  }

  document.querySelectorAll("table.sortable").forEach(function (table) {
    var headers = Array.prototype.slice.call(table.querySelectorAll("thead th[data-sort]"));
    headers.forEach(function (th, index) {
      th.style.cursor = "pointer";
      th.addEventListener("click", function () {
        var type = th.getAttribute("data-sort");
        var asc = !th.classList.contains("sort-asc");
        headers.forEach(function (o) { o.classList.remove("sort-asc", "sort-desc"); });
        th.classList.add(asc ? "sort-asc" : "sort-desc");

        var tbody = table.querySelector("tbody");
        var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));
        rows.sort(function (a, b) {
          var av = cellValue(a.children[index], type);
          var bv = cellValue(b.children[index], type);
          if (av < bv) return asc ? -1 : 1;
          if (av > bv) return asc ? 1 : -1;
          return 0;
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
      });
    });
  });
})();
