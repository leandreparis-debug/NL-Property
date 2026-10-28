// Confort facultatif : toutes les pages fonctionnent entièrement sans JavaScript.
(function () {
  "use strict";

  // Champ « Autre acteur » affiché seulement quand « Autre... » est choisi.
  document.querySelectorAll("select.actor-select").forEach(function (select) {
    var field = document.getElementById(select.dataset.other);
    if (!field) { return; }
    var update = function () { field.hidden = select.value !== "autre"; };
    select.addEventListener("change", update);
    update();
  });

  // Cartes modifiées et non enregistrées : signalées, et confirmation avant de quitter.
  var savingForm = null;
  document.querySelectorAll("form.card-form").forEach(function (form) {
    var card = form.closest(".card");
    var badge = card ? card.querySelector(".badge-unsaved") : null;
    var markDirty = function () {
      form.dataset.dirty = "1";
      if (badge) { badge.hidden = false; }
    };
    form.addEventListener("input", markDirty);
    form.addEventListener("change", markDirty);
    form.addEventListener("submit", function () { savingForm = form; });
  });
  window.addEventListener("beforeunload", function (event) {
    var dirty = Array.prototype.some.call(
      document.querySelectorAll('form.card-form[data-dirty="1"]'),
      function (form) { return form !== savingForm; }
    );
    if (dirty) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  // Compteur de caractères des zones de texte limitées (ex. description d'une visite).
  document.querySelectorAll("textarea[data-counter]").forEach(function (area) {
    var output = document.getElementById(area.dataset.counter);
    if (!output) { return; }
    var update = function () {
      output.textContent = area.value.length + " / " + area.maxLength + " caractères";
    };
    area.addEventListener("input", update);
    update();
  });

  // « Tout déplier / Tout replier » sans recharger la page.
  document.querySelectorAll("a.unfold-all").forEach(function (link) {
    link.addEventListener("click", function (event) {
      event.preventDefault();
      var open = link.dataset.unfold === "1";
      document.querySelectorAll("details.card-details").forEach(function (d) { d.open = open; });
      link.dataset.unfold = open ? "0" : "1";
      link.textContent = open ? "Tout replier" : "Tout déplier";
    });
  });
})();
