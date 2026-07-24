/* Unified local-first occupancy page (view + edit), addressed by uuid.
 *   /occupancies/edit?u=<uuid>   edit an existing pre-plan
 *   /occupancies/edit?new=1      create a new one
 * Reads/writes window.Store (offline), so the whole record — fields, footprint,
 * hazards, contacts — is editable with no connection. Floor plans are online-
 * only and just linked from here. */
(function () {
  "use strict";
  if (!document.getElementById("occ-form")) return;

  // Defer the footprint mini-map until we've populated lat/lon/footprint inputs.
  window.__occDeferMapInit = true;

  var NUMERIC = ["latitude", "longitude", "stories", "square_footage", "year_built"];
  var params = new URLSearchParams(location.search);
  var isNew = params.get("new") === "1";
  var uuid = params.get("u");

  var form = document.getElementById("occ-form");
  var titleEl = document.getElementById("occ-title");
  var childWrap = document.getElementById("occ-children");
  var vocab = window.VOCAB || {};
  // True while a hazard/contact row is swapped to inline inputs, so a background
  // sync or another store change doesn't re-render the list and wipe the edit.
  var inlineEditing = false;

  function gather() {
    var data = {};
    Array.prototype.forEach.call(form.elements, function (el) {
      if (!el.name || el.name === "csrf_token") return;
      if (el.type === "checkbox") data[el.name] = el.checked;
      else if (NUMERIC.indexOf(el.name) !== -1) data[el.name] = el.value === "" ? null : Number(el.value);
      else data[el.name] = el.value === "" ? null : el.value;
    });
    return data;
  }
  function populate(rec) {
    Array.prototype.forEach.call(form.elements, function (el) {
      if (!el.name || !(el.name in rec)) return;
      if (el.type === "checkbox") el.checked = !!rec[el.name];
      else el.value = rec[el.name] == null ? "" : rec[el.name];
    });
  }

  Store.ready.then(function () {
    if (isNew) {
      titleEl.textContent = "New Pre-Plan";
      childWrap.innerHTML = '<p class="hint">Save the pre-plan first to add hazards, contacts, and floor plans.</p>';
      window.initOccMap && window.initOccMap();
    } else if (uuid) {
      Store.get("occupancy", uuid).then(function (rec) {
        if (!rec) { titleEl.textContent = "Pre-plan not found"; return; }
        populate(rec);
        titleEl.textContent = rec.name || "Pre-Plan";
        window.initOccMap && window.initOccMap();
        renderChildren(rec);
      });
    }
    Store.subscribe(function () {
      if (inlineEditing) return;  // don't clobber an open inline edit
      if (uuid) Store.get("occupancy", uuid).then(function (r) { if (r) renderChildren(r); });
    });
  });

  // --- real-time autosave -----------------------------------------------------
  // Edits save to the offline Store on a short debounce (no Save button needed); the
  // shared pill (autosave.js) shows "Saving…/All changes saved". A brand-new pre-plan
  // is created on the first edit that has a name, then updated in place — the URL is
  // swapped to ?u=<uuid> without a reload so children (hazards/contacts) unlock.
  var pill = window.Autosave ? window.Autosave.pill(form) : null;
  var saveTimer = null, saving = false, dirty = false;

  function doSave() {
    var data = gather();
    if (!data.name) return;  // a pre-plan needs a name; wait until one is typed
    saving = true; dirty = false;
    if (pill) window.Autosave.saving(pill);
    var p = isNew
      ? Store.create("occupancy", data).then(function (rec) {
          isNew = false; uuid = rec.uuid;
          try { history.replaceState(null, "", "/occupancies/edit?u=" + encodeURIComponent(uuid)); } catch (e) {}
          renderChildren(rec);
          return rec;
        })
      : Store.update("occupancy", uuid, data);
    p.then(function () {
      saving = false;
      titleEl.textContent = data.name;
      if (pill) window.Autosave.saved(pill);
      if (dirty) schedule();  // edits landed mid-save — persist them too
    }).catch(function () {
      saving = false;
      if (pill) window.Autosave.error(pill);
    });
  }
  function schedule() {
    dirty = true;
    if (saving) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(doSave, 600);
  }
  form.addEventListener("input", schedule);
  form.addEventListener("change", schedule);
  form.addEventListener("submit", function (e) {
    e.preventDefault();  // "Save pre-plan" just flushes the pending autosave
    clearTimeout(saveTimer);
    if (!gather().name) {
      var nameEl = form.querySelector('[name="name"]');
      if (nameEl) nameEl.focus();
      return;
    }
    if (!saving) doSave();
  });

  // --- hazards, contacts, floor plans (client-rendered) ----------------------
  function renderChildren(occ) {
    Promise.all([Store.list("hazard"), Store.list("contact")]).then(function (r) {
      var hazards = r[0].filter(function (h) { return h.parent_uuid === occ.uuid; });
      var contacts = r[1].filter(function (c) { return c.parent_uuid === occ.uuid; });
      childWrap.innerHTML =
        hazardsCard(hazards) + contactsCard(contacts) + floorplansCard(occ);
      wireChildren(occ, hazards, contacts);
    });
  }

  function opt(list, sel) {
    return (list || []).map(function (v) {
      return '<option value="' + v + '"' + (v === sel ? " selected" : "") + ">" + v + "</option>";
    }).join("");
  }

  function hazardsCard(hazards) {
    var rows = hazards.length ? hazards.map(function (h) {
      return '<tr><td>' + esc(h.hazard_type) + '</td><td>' + esc(h.severity || "—") +
        '</td><td>' + esc(h.location || "—") + '</td><td>' + esc(h.description || "—") +
        '</td><td class="row-actions"><button class="btn btn-ghost btn-sm" data-edit-hazard="' + h.uuid + '">Edit</button>' +
        '<button class="btn btn-ghost btn-sm" data-del-hazard="' + h.uuid + '">✕</button></td></tr>';
    }).join("") : '<tr><td colspan="5" class="subtle">No hazards recorded.</td></tr>';
    return '<section class="card card-wide"><h2>Hazards</h2>' +
      '<table class="table"><thead><tr><th>Type</th><th>Severity</th><th>Location</th><th>Description</th><th></th></tr></thead><tbody>' + rows + '</tbody></table>' +
      '<div class="inline-form" id="hz-form">' +
      '<select id="hz-type"><option value="">Hazard type…</option>' + opt(vocab.hazard_types) + '</select>' +
      '<select id="hz-sev"><option value="">Severity…</option>' + opt(vocab.hazard_severities) + '</select>' +
      '<input id="hz-loc" placeholder="Location"><input id="hz-desc" placeholder="Description">' +
      '<button class="btn" id="hz-add">Add hazard</button></div></section>';
  }

  function contactsCard(contacts) {
    var rows = contacts.length ? contacts.map(function (c) {
      return '<tr><td>' + esc(c.name) + '</td><td>' + esc(c.role || "—") + '</td><td>' +
        esc(c.phone || "—") + '</td><td>' + esc(c.email || "—") +
        '</td><td class="row-actions"><button class="btn btn-ghost btn-sm" data-edit-contact="' + c.uuid + '">Edit</button>' +
        '<button class="btn btn-ghost btn-sm" data-del-contact="' + c.uuid + '">✕</button></td></tr>';
    }).join("") : '<tr><td colspan="5" class="subtle">No contacts recorded.</td></tr>';
    return '<section class="card card-wide"><h2>Contacts</h2>' +
      '<table class="table"><thead><tr><th>Name</th><th>Role</th><th>Phone</th><th>Email</th><th></th></tr></thead><tbody>' + rows + '</tbody></table>' +
      '<div class="inline-form" id="ct-form">' +
      '<input id="ct-name" placeholder="Name"><select id="ct-role"><option value="">Role…</option>' + opt(vocab.contact_roles) + '</select>' +
      '<input id="ct-phone" placeholder="Phone"><input id="ct-email" placeholder="Email">' +
      '<button class="btn" id="ct-add">Add contact</button></div></section>';
  }

  function floorplansCard(occ) {
    if (occ.id) {
      return '<section class="card card-wide"><h2>Floor plans</h2>' +
        '<p class="hint">Floor-plan upload and annotation need a connection.</p>' +
        '<a class="btn" href="/occupancies/' + occ.id + '">Manage floor plans &rarr;</a></section>';
    }
    return '<section class="card card-wide"><h2>Floor plans</h2>' +
      '<p class="hint">Available once this pre-plan has synced to the server.</p></section>';
  }

  function wireChildren(occ, hazards, contacts) {
    var hzAdd = document.getElementById("hz-add");
    if (hzAdd) hzAdd.onclick = function () {
      var t = document.getElementById("hz-type").value;
      if (!t) { Dialog.alert("Hazard type is required."); return; }
      Store.create("hazard", { hazard_type: t, severity: val("hz-sev"), location: val("hz-loc"), description: val("hz-desc") }, occ.uuid);
    };
    var ctAdd = document.getElementById("ct-add");
    if (ctAdd) ctAdd.onclick = function () {
      var n = document.getElementById("ct-name").value;
      if (!n) { Dialog.alert("Contact name is required."); return; }
      Store.create("contact", { name: n, role: val("ct-role"), phone: val("ct-phone"), email: val("ct-email") }, occ.uuid);
    };
    childWrap.querySelectorAll("[data-del-hazard]").forEach(function (b) {
      b.onclick = function () { Dialog.confirm("Remove this hazard?", { danger: true }).then(function (ok) { if (ok) Store.remove("hazard", b.getAttribute("data-del-hazard")); }); };
    });
    childWrap.querySelectorAll("[data-del-contact]").forEach(function (b) {
      b.onclick = function () { Dialog.confirm("Remove this contact?", { danger: true }).then(function (ok) { if (ok) Store.remove("contact", b.getAttribute("data-del-contact")); }); };
    });

    // Inline edit: swap a row to inputs; Save writes to the store (which re-renders).
    childWrap.querySelectorAll("[data-edit-hazard]").forEach(function (b) {
      b.onclick = function () {
        var h = (hazards || []).filter(function (x) { return x.uuid === b.getAttribute("data-edit-hazard"); })[0];
        if (!h) return;
        inlineEditing = true;
        var tr = b.closest("tr");
        tr.innerHTML =
          '<td><select class="e-type">' + opt(vocab.hazard_types, h.hazard_type) + '</select></td>' +
          '<td><select class="e-sev"><option value="">Severity…</option>' + opt(vocab.hazard_severities, h.severity) + '</select></td>' +
          '<td><input class="e-loc" value="' + esc(h.location || "") + '"></td>' +
          '<td><input class="e-desc" value="' + esc(h.description || "") + '"></td>' +
          '<td class="row-actions"><button class="btn btn-sm e-save">Save</button>' +
          '<button class="btn btn-ghost btn-sm e-cancel">Cancel</button></td>';
        tr.querySelector(".e-save").onclick = function () {
          var t = tr.querySelector(".e-type").value;
          if (!t) { Dialog.alert("Hazard type is required."); return; }
          inlineEditing = false;  // saved edit → allow the re-render it triggers
          Store.update("hazard", h.uuid, { hazard_type: t,
            severity: tr.querySelector(".e-sev").value || null,
            location: tr.querySelector(".e-loc").value || null,
            description: tr.querySelector(".e-desc").value || null });
        };
        tr.querySelector(".e-cancel").onclick = function () { inlineEditing = false; renderChildren(occ); };
      };
    });
    childWrap.querySelectorAll("[data-edit-contact]").forEach(function (b) {
      b.onclick = function () {
        var c = (contacts || []).filter(function (x) { return x.uuid === b.getAttribute("data-edit-contact"); })[0];
        if (!c) return;
        inlineEditing = true;
        var tr = b.closest("tr");
        tr.innerHTML =
          '<td><input class="e-name" value="' + esc(c.name || "") + '"></td>' +
          '<td><select class="e-role"><option value="">Role…</option>' + opt(vocab.contact_roles, c.role) + '</select></td>' +
          '<td><input class="e-phone" value="' + esc(c.phone || "") + '"></td>' +
          '<td><input class="e-email" value="' + esc(c.email || "") + '"></td>' +
          '<td class="row-actions"><button class="btn btn-sm e-save">Save</button>' +
          '<button class="btn btn-ghost btn-sm e-cancel">Cancel</button></td>';
        tr.querySelector(".e-save").onclick = function () {
          var n = tr.querySelector(".e-name").value;
          if (!n) { Dialog.alert("Contact name is required."); return; }
          inlineEditing = false;  // saved edit → allow the re-render it triggers
          Store.update("contact", c.uuid, { name: n,
            role: tr.querySelector(".e-role").value || null,
            phone: tr.querySelector(".e-phone").value || null,
            email: tr.querySelector(".e-email").value || null });
        };
        tr.querySelector(".e-cancel").onclick = function () { inlineEditing = false; renderChildren(occ); };
      };
    });
  }

  function val(id) { var el = document.getElementById(id); return el && el.value ? el.value : null; }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
})();
