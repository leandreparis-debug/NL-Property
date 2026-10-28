// Bouton « Imprimer » de l'aperçu : ouvre la boîte d'impression du navigateur
// (choisir une imprimante, ou « Enregistrer au format PDF »). Avec ?imprimer=1 dans
// l'adresse (boutons Imprimer de la relecture et de l'export), elle s'ouvre toute seule.
(function () {
  "use strict";
  var button = document.getElementById("imprimer");
  if (button) {
    button.addEventListener("click", function () { window.print(); });
  }
  if (new URLSearchParams(window.location.search).get("imprimer") === "1") {
    // Attendre les images (logos, photos) avant d'imprimer.
    window.addEventListener("load", function () { window.print(); });
  }
})();
