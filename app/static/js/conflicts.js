/* Conflicts page — lists records the server rejected because they changed while
 * you edited them offline, and lets you keep mine / keep theirs. Reads and
 * resolves via window.Store (see store.js resolveConflict). */
(function () {
  "use strict";
  var wrap = document.getElementById("conflicts-wrap");
  if (!wrap || !window.Store) return;

  var SKIP = { uuid: 1, id: 1, updated_at: 1, parent_uuid: 1 };

  function fields(rec) {
    if (!rec) return "<em>(deleted on the server)</em>";
    var keys = Object.keys(rec).filter(function (k) {
      return !SKIP[k] && rec[k] != null && rec[k] !== "";
    });
    if (!keys.length) return "<em>(empty)</em>";
    return keys.map(function (k) {
      return '<div class="row"><dt>' + esc(k) + "</dt><dd>" + esc(rec[k]) + "</dd></div>";
    }).join("");
  }

  function render() {
    Store.conflicts().then(function (list) {
      if (!list.length) {
        wrap.innerHTML = '<div class="empty"><p>No conflicts to resolve. 🎉</p></div>';
        return;
      }
      wrap.innerHTML = list.map(function (c) {
        var name = (c.mine && (c.mine.name || c.mine.label)) ||
                   (c.theirs && (c.theirs.name || c.theirs.label)) || c.uuid;
        return '<section class="card card-wide">' +
          "<h2>" + esc(c.entity) + " — " + esc(name) + "</h2>" +
          '<div class="cards">' +
            '<section class="card"><h2>Your version</h2><dl>' + fields(c.mine) + "</dl></section>" +
            '<section class="card"><h2>Server version</h2><dl>' + fields(c.theirs) + "</dl></section>" +
          "</div>" +
          '<div class="mf-actions" style="margin-top:.7rem">' +
            '<button class="btn btn-primary" data-keep="mine" data-cid="' + c.id + '">Keep mine</button>' +
            '<button class="btn" data-keep="theirs" data-cid="' + c.id + '">Keep theirs</button>' +
          "</div></section>";
      }).join("");
      wrap.querySelectorAll("[data-keep]").forEach(function (b) {
        b.onclick = function () {
          Store.resolveConflict(Number(b.getAttribute("data-cid")), b.getAttribute("data-keep"))
            .then(render);
        };
      });
    });
  }

  Store.ready.then(function () { render(); Store.subscribe(render); });

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
})();
