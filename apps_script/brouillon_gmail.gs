/**
 * News Property - crée le brouillon Gmail de la newsletter.
 *
 * Lit, dans le dossier Google Drive DOSSIER_DRIVE, le paquet le plus récent produit par
 * l'application (news-property-AAAA-MM.json), puis crée un brouillon dans Gmail avec le
 * message complet préparé par l'application (texte, HTML et images intégrées). Aucun
 * destinataire n'est ajouté : vous les choisissez dans Gmail avant l'envoi. Rien n'est
 * envoyé par ce script.
 *
 * Si l'e-mail porte le lien « voir la version à imprimer », le script dépose d'abord la
 * version PDF dans le même dossier Drive, la partage en lecture (voir PARTAGE_PDF) et met
 * son adresse dans ce lien : un e-mail ne peut pas lancer l'impression lui-même, le lien
 * ouvre donc le PDF, que le lecteur imprime.
 *
 * Le brouillon est créé avec l'API Gmail (service avancé « Gmail API », à ajouter une fois
 * dans l'éditeur : Services +) : c'est ce qui garde les photos et logos visibles dans le
 * brouillon (avec GmailApp.createDraft et inlineImages, elles n'y apparaissent pas toujours).
 *
 * Installation : voir docs/email.md dans le projet News Property.
 */

/** Nom du dossier Google Drive où déposer les paquets (à la racine de « Mon Drive »). */
const DOSSIER_DRIVE = 'News Property';

/** Format de paquet attendu (produit par l'application). */
const FORMAT_PAQUET = 'news-property-email/3';

/**
 * Qui peut ouvrir le PDF à imprimer (lien « voir la version à imprimer ») :
 *   'DOMAINE' : les personnes de votre organisation (comptes Carrefour Property) qui ont le lien ;
 *   'LIEN'    : toute personne qui a le lien, même hors de l'organisation ;
 *   'AUCUN'   : personne d'autre que vous (à partager vous-même dans Drive).
 */
const PARTAGE_PDF = 'DOMAINE';

/** Noms de paquets reconnus : news-property-AAAA-MM.json */
const MOTIF_NOM = /^news-property-\d{4}-(0[1-9]|1[0-2])\.json$/;

/**
 * Crée le brouillon à partir du paquet le plus récent du dossier.
 * Fonction à lancer depuis l'éditeur Apps Script (bouton « Exécuter »).
 */
function creerBrouillon() {
  if (typeof Gmail === 'undefined') {
    throw new Error('Service « Gmail API » non activé. Dans l\'éditeur Apps Script, ' +
      'cliquez sur « Services » (+) à gauche, choisissez « Gmail API », puis « Ajouter ». ' +
      'Relancez ensuite creerBrouillon.');
  }
  const dossier = dossierDrive_();
  const fichier = dernierPaquet_(dossier);
  const paquet = lirePaquet_(fichier);
  let raw = paquet.raw;
  let lienPdf = null;
  if (paquet.printPdf) {
    lienPdf = deposerPdf_(dossier, paquet.printPdf);
    raw = remplacerLien_(raw, paquet.printLinkToken, lienPdf);
  }
  const brouillon = Gmail.Users.Drafts.create({ message: { raw: raw } }, 'me');
  if (lienPdf) {
    Logger.log('Version à imprimer déposée dans Drive : %s', lienPdf);
  }
  Logger.log(
    'Brouillon créé : « %s » (%s images), depuis %s. Ouvrez Gmail > Brouillons, ' +
      'ajoutez les destinataires, relisez, puis envoyez.',
    paquet.subject,
    paquet.images.length,
    fichier.getName()
  );
  return brouillon.id;
}

/** Dossier Google Drive DOSSIER_DRIVE. */
function dossierDrive_() {
  const dossiers = DriveApp.getFoldersByName(DOSSIER_DRIVE);
  if (!dossiers.hasNext()) {
    throw new Error('Dossier Google Drive introuvable : « ' + DOSSIER_DRIVE + ' ». ' +
      'Créez-le à la racine de Mon Drive, ou changez DOSSIER_DRIVE en haut du script.');
  }
  return dossiers.next();
}

/** Paquet le plus récent (date de modification) du dossier. */
function dernierPaquet_(dossier) {
  const fichiers = dossier.getFiles();
  let dernier = null;
  while (fichiers.hasNext()) {
    const fichier = fichiers.next();
    if (MOTIF_NOM.test(fichier.getName()) &&
        (dernier === null || fichier.getLastUpdated() > dernier.getLastUpdated())) {
      dernier = fichier;
    }
  }
  if (dernier === null) {
    throw new Error('Aucun paquet news-property-AAAA-MM.json dans le dossier « ' +
      DOSSIER_DRIVE + ' ». Préparez l\'e-mail dans l\'application, puis déposez le paquet.');
  }
  return dernier;
}

/** Lit et vérifie un paquet. */
function lirePaquet_(fichier) {
  let paquet;
  try {
    paquet = JSON.parse(fichier.getBlob().getDataAsString('UTF-8'));
  } catch (erreur) {
    throw new Error('Paquet illisible : ' + fichier.getName() + ' (' + erreur + ').');
  }
  if (paquet.format !== FORMAT_PAQUET) {
    throw new Error('Format de paquet inattendu (' + paquet.format + ', attendu ' +
      FORMAT_PAQUET + ') : copiez la dernière version de apps_script/brouillon_gmail.gs ' +
      'dans ce projet, ou préparez à nouveau l\'e-mail dans l\'application.');
  }
  ['raw', 'subject', 'htmlBody', 'textBody'].forEach(function (cle) {
    if (typeof paquet[cle] !== 'string' || paquet[cle] === '') {
      throw new Error('Paquet incomplet : « ' + cle + ' » manquant.');
    }
  });
  if (!Array.isArray(paquet.images)) {
    throw new Error('Paquet incomplet : « images » manquant.');
  }
  return paquet;
}

/**
 * Dépose le PDF à imprimer dans le dossier (remplace celui du même nom), le partage selon
 * PARTAGE_PDF et renvoie son lien.
 */
function deposerPdf_(dossier, pdf) {
  const anciens = dossier.getFilesByName(pdf.fileName);
  while (anciens.hasNext()) {
    anciens.next().setTrashed(true);
  }
  const contenu = Utilities.newBlob(Utilities.base64Decode(pdf.data), pdf.mimeType, pdf.fileName);
  const fichier = dossier.createFile(contenu);
  if (PARTAGE_PDF !== 'AUCUN') {
    const acces = PARTAGE_PDF === 'LIEN'
      ? DriveApp.Access.ANYONE_WITH_LINK
      : DriveApp.Access.DOMAIN_WITH_LINK;
    try {
      fichier.setSharing(acces, DriveApp.Permission.VIEW);
    } catch (erreur) {
      throw new Error('Le PDF « ' + pdf.fileName + ' » est dans Drive mais n\'a pas pu être ' +
        'partagé (' + erreur.message + '). Votre organisation limite peut-être le partage : ' +
        'changez PARTAGE_PDF en haut du script, ou partagez le fichier vous-même, puis ' +
        'relancez creerBrouillon.');
    }
  }
  return fichier.getUrl();
}

/**
 * Remplace l'adresse provisoire du lien « voir la version à imprimer » par celle du PDF, dans le message
 * complet (texte et HTML sont en UTF-8 lisible ; les images, en base64, ne changent pas).
 */
function remplacerLien_(raw, jeton, lien) {
  const message = Utilities.newBlob(Utilities.base64DecodeWebSafe(raw)).getDataAsString('UTF-8');
  if (!jeton || message.indexOf(jeton) < 0) {
    throw new Error('Paquet incohérent : lien « voir la version à imprimer » introuvable. ' +
      'Préparez à nouveau l\'e-mail dans l\'application.');
  }
  const corrige = message.split(jeton).join(lien);
  return Utilities.base64EncodeWebSafe(Utilities.newBlob(corrige).getBytes());
}
