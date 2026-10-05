/* The Review item picker's page script (picker spec §3.2). It opens and closes
   the dialog, keeps the tick set, draws the tray, and on Done copies the set
   into the decision form and asks the server for the panel's summary. The lists
   are server-rendered over htmx. Delegated on document, like base.html's
   handlers, so fragments swapped in later work without re-binding. Cancel (and
   Esc) just closes: the set is rebuilt from the form's hidden inputs on every
   open, so what Done last kept is what comes back. */
(function () {
  "use strict";
  var sets = new WeakMap();

  function dialogOf(el) { return el && el.closest ? el.closest("dialog.item-picker") : null; }
  function ticks(d) { if (!sets.has(d)) { sets.set(d, new Map()); } return sets.get(d); }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text !== undefined) { e.textContent = text; }
    return e;
  }

  function sync(d) {
    var t = ticks(d);
    d.querySelectorAll("input[data-pick]").forEach(function (cb) {
      if (!cb.disabled) { cb.checked = t.has(cb.dataset.pick); }
      cb.closest("tr").classList.toggle("picked", cb.checked && !cb.disabled);
    });
    d.querySelectorAll("[data-picker-group]").forEach(function (g) {
      var boxes = Array.prototype.slice.call(g.querySelectorAll("input[data-pick]:not(:disabled)"));
      var btn = g.querySelector("[data-picker-tick-all]");
      if (!btn) { return; }
      btn.hidden = boxes.length === 0;
      var all = boxes.length > 0 && boxes.every(function (b) { return b.checked; });
      btn.textContent = (all ? "Untick all " : "Tick all ") + boxes.length;
    });
    tray(d);
  }

  function tray(d) {
    var t = ticks(d), box = d.querySelector(".picker-tray");
    if (!box) { return; }
    box.textContent = "";
    box.append(el("strong", "t-n", t.size === 1 ? "1 item ticked" : t.size + " items ticked"));
    if (!t.size) { box.append(el("span", "hint", "Tick items or a whole group.")); }
    t.forEach(function (name, ref) {
      var chip = el("span", "tchip", ref + " ");
      chip.title = name;
      var x = el("button", "", "×");
      x.type = "button";
      x.dataset.untick = ref;
      x.setAttribute("aria-label", "Remove " + ref);
      chip.append(x);
      box.append(chip);
    });
    if (t.size) {
      var clear = el("button", "linkbtn", "Clear all");
      clear.type = "button";
      clear.dataset.clearTicks = "";
      box.append(clear);
    }
    var add = d.querySelector("[data-picker-add]");
    if (add) {
      add.disabled = t.size === 0;
      add.textContent = t.size === 1 ? "Add this item" : "Add these " + t.size + " items";
    }
  }

  function open(btn) {
    var d = document.getElementById(btn.dataset.pickerOpen);
    if (!d) { return; }
    var t = ticks(d), into = document.getElementById(d.dataset.itemsInto || "");
    t.clear();
    if (into) {
      into.querySelectorAll("input[name=items]").forEach(function (i) { t.set(i.value, i.dataset.name || ""); });
    }
    // The last opening's body would otherwise show, tray included, until the
    // server answers: stale ticks the reviewer did not keep.
    var body = d.querySelector(".picker-body");
    body.textContent = "";
    body.append(el("p", "hint", "Loading\u2026"));
    d.showModal();
    htmx.ajax("GET", btn.dataset.pickerUrl, { target: body, swap: "innerHTML" });
  }

  function hidden(name, value, label) {
    var i = el("input");
    i.type = "hidden";
    i.name = name;
    i.value = value;
    if (label) { i.dataset.name = label; }
    return i;
  }

  function done(d) {
    var t = ticks(d), into = document.getElementById(d.dataset.itemsInto);
    var mfr = d.querySelector("[data-picker-manufacturer]");
    var params = new URLSearchParams();
    into.textContent = "";
    t.forEach(function (name, ref) { into.append(hidden("items", ref, name)); params.append("items", ref); });
    if (mfr && mfr.value) { into.append(hidden("manufacturer", mfr.value)); params.append("manufacturer", mfr.value); }
    d.close();
    htmx.ajax("GET", d.dataset.summaryUrl + "?" + params.toString(),
              { target: "#" + d.dataset.summaryInto, swap: "innerHTML" });
  }

  function fillAdd(d) {
    var box = d.querySelector(".picker-add-items"), mfr = d.querySelector("[data-picker-manufacturer]");
    box.textContent = "";
    ticks(d).forEach(function (name, ref) { box.append(hidden("items", ref)); });
    if (mfr && mfr.value) { box.append(hidden("manufacturer", mfr.value)); }
  }

  document.addEventListener("click", function (e) {
    var hit = e.target.closest("[data-picker-open]");
    if (hit) { open(hit); return; }
    var d = dialogOf(e.target);
    if (!d) { return; }
    var t = ticks(d);
    if (e.target.closest("[data-picker-cancel]")) { d.close(); return; }
    if (e.target.closest("[data-picker-done]")) { done(d); return; }
    if (e.target.closest("[data-picker-add]")) { fillAdd(d); return; }   // the form then submits
    if ((hit = e.target.closest("[data-picker-tick-all]"))) {
      var boxes = Array.prototype.slice.call(
        hit.closest("[data-picker-group]").querySelectorAll("input[data-pick]:not(:disabled)"));
      var all = boxes.every(function (b) { return t.has(b.dataset.pick); });
      boxes.forEach(function (b) { if (all) { t.delete(b.dataset.pick); } else { t.set(b.dataset.pick, b.dataset.name || ""); } });
      sync(d);
      return;
    }
    if ((hit = e.target.closest("[data-untick]"))) { t.delete(hit.dataset.untick); sync(d); return; }
    if (e.target.closest("[data-clear-ticks]")) { t.clear(); sync(d); return; }
    if ((hit = e.target.closest("[data-picker-q]"))) {
      var q = d.querySelector("input[name=q]");
      q.value = hit.dataset.pickerQ;
      htmx.trigger(q, "search");
    }
  });

  document.addEventListener("change", function (e) {
    var cb = e.target.closest ? e.target.closest("input[data-pick]") : null;
    var d = dialogOf(cb);
    if (!d) { return; }
    if (cb.checked) { ticks(d).set(cb.dataset.pick, cb.dataset.name || ""); } else { ticks(d).delete(cb.dataset.pick); }
    sync(d);
  });

  document.addEventListener("htmx:afterSwap", function (e) {
    var d = dialogOf(e.target);
    if (d) { sync(d); }
  });
})();
