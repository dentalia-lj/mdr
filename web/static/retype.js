/* Change type on Review (spec 2026-11-12 §4). When Type changes, "What it
   covers" moves to what that type usually covers: each Type option carries it
   as `data-default-scope`, written by the server from `app.retype.default_scope`.
   She can still choose the other one; the server takes what is posted. Nothing
   else lives here. Delegated on document, like base.html's handlers, so a
   panel swapped in later needs no re-binding. */
(function () {
  "use strict";
  document.addEventListener("change", function (evt) {
    var type = evt.target.closest ? evt.target.closest("select[data-retype-type]") : null;
    if (!type) { return; }
    var scope = type.form && type.form.querySelector("select[name=coverage_scope]");
    var chosen = type.selectedOptions[0];
    if (scope && chosen && chosen.dataset.defaultScope) {
      scope.value = chosen.dataset.defaultScope;
    }
  });
})();
