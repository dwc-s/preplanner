// App-native confirm/alert dialogs — a styled <dialog> replacing the browser's
// window.confirm/alert chrome. Exposes window.Dialog.{confirm,alert} (Promise-based)
// and auto-intercepts any `<form data-confirm="…">` submit (add data-confirm-danger for
// a red confirm button). Loaded globally in base.html.
(function () {
  "use strict";

  var dlg = null;
  function ensure() {
    if (dlg) return dlg;
    dlg = document.createElement("dialog");
    dlg.className = "app-dialog";
    dlg.innerHTML =
      '<div class="app-dialog-box">' +
      '  <h3 class="app-dialog-title"></h3>' +
      '  <p class="app-dialog-msg"></p>' +
      '  <div class="app-dialog-actions">' +
      '    <button type="button" class="btn btn-ghost app-dialog-cancel">Cancel</button>' +
      '    <button type="button" class="btn btn-primary app-dialog-ok">OK</button>' +
      '  </div>' +
      '</div>';
    document.body.appendChild(dlg);
    return dlg;
  }

  function open(opts) {
    // Fallback for the rare browser without <dialog>.
    var d = ensure();
    if (typeof d.showModal !== "function") {
      if (opts.confirm) return Promise.resolve(window.confirm(opts.message));
      window.alert(opts.message);
      return Promise.resolve(true);
    }
    var title = d.querySelector(".app-dialog-title");
    title.textContent = opts.title || "";
    title.style.display = opts.title ? "" : "none";
    d.querySelector(".app-dialog-msg").textContent = opts.message || "";
    var ok = d.querySelector(".app-dialog-ok");
    var cancel = d.querySelector(".app-dialog-cancel");
    ok.textContent = opts.okText || "OK";
    ok.className = "btn " + (opts.danger ? "btn-danger" : "btn-primary") + " app-dialog-ok";
    cancel.style.display = opts.confirm ? "" : "none";
    return new Promise(function (resolve) {
      function done(val) { ok.onclick = cancel.onclick = d.oncancel = null; d.close(); resolve(val); }
      ok.onclick = function () { done(true); };
      cancel.onclick = function () { done(false); };
      d.oncancel = function (e) { e.preventDefault(); done(false); };  // Esc / backdrop
      if (d.open) { try { d.close(); } catch (e) {} }  // guard: showModal throws if already open
      d.showModal();
      ok.focus();
    });
  }

  window.Dialog = {
    confirm: function (message, opts) {
      opts = opts || {};
      return open({ message: message, title: opts.title, confirm: true, danger: opts.danger,
                    okText: opts.okText || (opts.danger ? "Delete" : "OK") });
    },
    alert: function (message, opts) {
      opts = opts || {};
      return open({ message: message, title: opts.title, confirm: false });
    }
  };

  // Any form opting in with data-confirm gets a styled confirmation before it submits.
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form || !form.matches || !form.matches("form[data-confirm]")) return;
    e.preventDefault();
    window.Dialog.confirm(form.getAttribute("data-confirm"),
      { danger: form.hasAttribute("data-confirm-danger") }).then(function (ok) {
      if (ok) form.submit();  // .submit() bypasses this handler — no loop
    });
  }, true);
})();
