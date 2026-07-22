/* Drag-and-drop reordering of pre-plan elements. Progressive enhancement — without
 * JS the elements still render in order; only reordering needs this. Persists the
 * new order to the server (CSRF via the X-CSRFToken header, like floor-plan
 * annotations). */
(function () {
  var list = document.getElementById("element-list");
  if (!list) return;
  var url = list.getAttribute("data-reorder");
  var meta = document.querySelector('meta[name="csrf-token"]');
  var csrf = meta ? meta.getAttribute("content") : "";
  var dragging = null;

  function wire(li) {
    li.addEventListener("dragstart", function () {
      dragging = li;
      setTimeout(function () { li.classList.add("dragging"); }, 0);
    });
    li.addEventListener("dragend", function () {
      li.classList.remove("dragging");
      dragging = null;
      persist();
    });
  }
  list.querySelectorAll(".element").forEach(wire);

  list.addEventListener("dragover", function (e) {
    e.preventDefault();
    if (!dragging) return;
    var after = afterElement(e.clientY);
    if (after == null) list.appendChild(dragging);
    else list.insertBefore(dragging, after);
  });

  function afterElement(y) {
    var items = Array.prototype.slice.call(list.querySelectorAll(".element:not(.dragging)"));
    var closest = { offset: -Infinity, el: null };
    items.forEach(function (child) {
      var box = child.getBoundingClientRect();
      var offset = y - box.top - box.height / 2;
      if (offset < 0 && offset > closest.offset) closest = { offset: offset, el: child };
    });
    return closest.el;
  }

  function persist() {
    var order = Array.prototype.map.call(list.querySelectorAll(".element"), function (li) {
      return parseInt(li.getAttribute("data-id"), 10);
    });
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify({ order: order })
    }).catch(function () { /* offline / transient — order re-persists on next drag */ });
  }
})();
