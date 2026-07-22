// Real-time autosave for server-rendered record forms.
//
// Opt a form in with `data-autosave` (optionally `data-autosave="<url>"` to post
// somewhere other than the form's action). On any field change the form is debounced
// and POSTed with an `X-Autosave: 1` header; the route saves leniently and returns
// JSON `{ok: true}` (or `{ok: false, error}`), and a small status pill reflects the
// state. The form's own submit button still works as a no-JS / explicit fallback.
//
// `window.Autosave` is also exposed so the local-first occupancy editor (occupancy.js)
// can render the exact same pill while saving through the offline Store instead.
(function () {
  "use strict";

  var CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";

  function makePill(form, compact) {
    var pill = form.querySelector("[data-autosave-status]");
    if (!pill) {
      pill = document.createElement("span");
      pill.setAttribute("data-autosave-status", "");
      pill.hidden = true;  // stays out of the layout until the first save
      var host = form.querySelector(".form-actions") || form;
      host.appendChild(pill);
    }
    pill.className = "autosave-status" + (compact ? " autosave-compact" : "");
    return pill;
  }

  function set(pill, state, text) {
    if (!pill) return;
    pill.dataset.state = state;
    pill.textContent = text;
    pill.hidden = false;
  }

  var LABELS = {
    saving: ["Saving…", "…"],
    saved: ["All changes saved", "Saved ✓"],
    error: ["Couldn't save — retry", "Not saved ⚠"]
  };

  // Shared API (used here and by occupancy.js). `compact` picks the short labels.
  window.Autosave = {
    pill: makePill,
    saving: function (p) { set(p, "saving", LABELS.saving[p && p.classList.contains("autosave-compact") ? 1 : 0]); },
    saved: function (p) {
      set(p, "saved", LABELS.saved[p && p.classList.contains("autosave-compact") ? 1 : 0]);
    },
    error: function (p) { set(p, "error", LABELS.error[p && p.classList.contains("autosave-compact") ? 1 : 0]); }
  };

  function wire(form) {
    var compact = form.hasAttribute("data-autosave-compact");
    var pill = makePill(form, compact);
    var url = form.getAttribute("data-autosave") || form.getAttribute("action");
    var timer = null, inflight = false, again = false;

    function save() {
      if (inflight) { again = true; return; }
      inflight = true;
      window.Autosave.saving(pill);
      fetch(url, {
        method: "POST",
        headers: { "X-Autosave": "1", "X-CSRFToken": CSRF },
        body: new FormData(form),
        credentials: "same-origin"
      }).then(function (r) {
        return r.ok ? r.json() : { ok: false };
      }).then(function (data) {
        inflight = false;
        if (data && data.ok) window.Autosave.saved(pill);
        else window.Autosave.error(pill);
        if (again) { again = false; save(); }
      }).catch(function () {
        inflight = false;
        window.Autosave.error(pill);
      });
    }

    function schedule() { clearTimeout(timer); timer = setTimeout(save, 600); }
    // `change` fires immediately for selects/checkboxes; `input` covers typing.
    form.addEventListener("input", schedule);
    form.addEventListener("change", function () { clearTimeout(timer); save(); });
  }

  function init() {
    var forms = document.querySelectorAll("form[data-autosave]");
    Array.prototype.forEach.call(forms, wire);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
