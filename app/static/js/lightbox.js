// A minimal image lightbox: view a picture full-window without leaving the page.
// Open declaratively — any element with data-lightbox (data-src, optional
// data-title) opens on click — or imperatively via window.Lightbox.open({src,title}).
// Esc, the close button, or a backdrop click dismisses it. Loaded globally in base.html.
(function () {
  "use strict";

  var box = null, lastFocus = null;

  function ensure() {
    if (box) return box;
    box = document.createElement("div");
    box.className = "lightbox";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "true");
    box.hidden = true;
    box.innerHTML =
      '<button type="button" class="lightbox-close" aria-label="Close (Esc)">&times;</button>' +
      '<figure class="lightbox-fig">' +
      '  <img class="lightbox-img" alt="">' +
      '  <figcaption class="lightbox-cap"></figcaption>' +
      '</figure>';
    document.body.appendChild(box);
    // Backdrop (or the figure margin) closes; clicks on the image itself don't.
    box.addEventListener("click", function (e) {
      if (e.target === box || e.target.classList.contains("lightbox-fig")) close();
    });
    box.querySelector(".lightbox-close").addEventListener("click", close);
    return box;
  }

  function onKey(e) {
    if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "Tab") { e.preventDefault(); box.querySelector(".lightbox-close").focus(); }
  }

  function open(opts) {
    opts = opts || {};
    var b = ensure();
    var img = b.querySelector(".lightbox-img");
    var cap = b.querySelector(".lightbox-cap");
    img.src = opts.src || "";
    img.alt = opts.title || "";
    cap.textContent = opts.title || "";
    cap.style.display = opts.title ? "" : "none";
    lastFocus = document.activeElement;
    b.hidden = false;
    document.body.classList.add("lightbox-open");  // freeze background scroll
    document.addEventListener("keydown", onKey, true);
    b.querySelector(".lightbox-close").focus();
  }

  function close() {
    if (!box || box.hidden) return;
    box.hidden = true;
    box.querySelector(".lightbox-img").src = "";  // stop any in-flight load
    document.body.classList.remove("lightbox-open");
    document.removeEventListener("keydown", onKey, true);
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  // Declarative openers: <button data-lightbox data-src="…" data-title="…">.
  document.addEventListener("click", function (e) {
    var t = e.target.closest ? e.target.closest("[data-lightbox]") : null;
    if (!t) return;
    e.preventDefault();
    open({ src: t.getAttribute("data-src"), title: t.getAttribute("data-title") });
  });

  window.Lightbox = { open: open, close: close };
})();
