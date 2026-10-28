# News Property

Application locale qui prépare chaque mois la **newsletter interne de Carrefour Property**
(équipe entrepôts, immobilier logistique) :

1. collecte automatique de la veille (flux RSS, Google Actualités) ;
2. relecture et sélection des articles dans une interface web locale ;
3. ajout de contenu interne (visites d'entrepôts, calendrier, absences, documents) ;
4. deux sorties : un e-mail HTML (brouillon Gmail, créé via un script Google Apps Script) et
   une version A4 imprimable.

L'application est mono-utilisateur, fonctionne uniquement sur le poste (adresse `127.0.0.1`)
et **n'utilise aucune IA** : aucun appel à un modèle de langage, aucun résumé généré. Les
extraits d'articles sont copiés tels quels, jamais reformulés. Aucun lien n'est inventé.
Rien n'est envoyé automatiquement : l'application prépare, l'humain envoie.

Elle reprend l'architecture et les solutions éprouvées de l'application News Franchise
(Carmila), sans son contenu.

> **État actuel (étape 8 sur 10)** : socle technique (configuration, journal, sécurité,
> page d'accueil, écran Thème aux couleurs du logo Carrefour Property), base de données
> locale avec migrations et référentiel des acteurs suivis, diagnostic des sources de
> veille, collecte de la veille, détection des transactions et des surfaces, relecture
> des articles, contenu interne (Une, visites d'entrepôts avec photos, calendrier,
> congés et absences, documents utiles), aperçu de la newsletter avec impression. Les
> étapes suivantes ajoutent l'e-mail et la démonstration.

## Prérequis

- Windows 11 (le code fonctionne aussi sous Linux et macOS) ;
- Python 3.14 installé (avec le lanceur `py`, installé par défaut par l'installateur
  python.org) ;
- une connexion Internet lors de la première installation (téléchargement des dépendances).

Aucun droit administrateur n'est nécessaire.

## Installation et lancement

1. Placez le projet dans un dossier qui n'est pas synchronisé par OneDrive, par exemple
   `C:\Users\<vous>\NewsProperty` (le dossier Documents est à éviter).
2. Double-cliquez sur **`lancer.bat`**.

Au premier lancement, `lancer.bat` crée un environnement Python isolé (dossier `.venv`) et
installe les dépendances. Il les réinstalle automatiquement si `requirements.txt` a été
modifié depuis. Le navigateur s'ouvre ensuite sur <http://127.0.0.1:5000>.

Laissez la fenêtre noire ouverte tant que vous utilisez l'application ; fermez-la (ou faites
`Ctrl+C`) pour l'arrêter.

Lancement manuel, équivalent :

```bat
.venv\Scripts\python.exe -m news_property
```

Si le port 5000 est déjà occupé, l'application l'indique (« L'application tourne peut-être
déjà... ») et s'arrête : ouvrez simplement <http://127.0.0.1:5000>, ou changez de port via
`CP_PORT`.

Vérification rapide : <http://127.0.0.1:5000/health> renvoie `{"status": "ok", "version": "0.1.0"}`.

## Emplacement des données

Les données de travail (base de données, photos, exports, rapports, journaux) sont stockées
**hors du projet** :

| Système | Dossier par défaut |
|---|---|
| Windows | `%LOCALAPPDATA%\NewsProperty` (ex. `C:\Users\<vous>\AppData\Local\NewsProperty`) |
| Linux / macOS | `~/.local/share/news-property` |

Sous-dossiers créés automatiquement : `db\`, `photos\`, `exports\`, `reports\`, `logs\`.

**Pourquoi pas dans Documents ?** Le dossier Documents est souvent synchronisé par OneDrive.
Une base de données modifiée pendant une synchronisation peut être verrouillée, dupliquée
(« conflit ») ou corrompue. `%LOCALAPPDATA%` n'est pas synchronisé et appartient à
l'utilisateur.

**AppData est un dossier caché** dans l'Explorateur. Le chemin exact est affiché dans la
fenêtre noire à chaque démarrage (« Dossier de données : ... »). Pour l'ouvrir : tapez
`%LOCALAPPDATA%\NewsProperty` dans la barre d'adresse de l'Explorateur. Avec le Python du
Microsoft Store, Windows redirige ce dossier vers
`%LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python...\LocalCache\Local\NewsProperty` :
là encore, fiez-vous au chemin affiché au démarrage.

Le journal de l'application se trouve dans `logs\news_property.log` (fichier rotatif :
1 Mo × 5 fichiers). En cas d'erreur, le détail technique y est consigné ; il n'est jamais
affiché dans le navigateur.

## Données

### Base de données

Toutes les données de la newsletter (actualités retenues, contenu interne, historique des
collectes) sont dans une base SQLite locale :

```
%LOCALAPPDATA%\NewsProperty\db\news_property.sqlite3
```

Elle est créée, puis mise à jour (migrations), automatiquement au lancement de
l'application. Le schéma et les règles (aucun lien inventé, Une calculée à la volée...)
sont décrits dans [`docs/data-model.md`](docs/data-model.md).

### Sauvegarder

1. Fermez l'application (fenêtre noire de `lancer.bat`).
2. Copiez **tout le dossier** `%LOCALAPPDATA%\NewsProperty\db\` (et `photos\` pour les
   photos) vers un emplacement de sauvegarde, par exemple Google Drive.

Copiez le dossier entier, pas seulement le fichier `.sqlite3` : si l'application n'a pas
été fermée proprement, les fichiers `-wal` et `-shm` qui l'accompagnent contiennent les
dernières modifications. Pour restaurer, remettez le dossier à sa place, application fermée.

### Modifier la liste des acteurs suivis

Le référentiel des acteurs de l'immobilier logistique suivis (investisseurs et
développeurs, logisticiens, chargeurs et distributeurs, conseils) est le fichier
**`config\acteurs.yaml`** (nom, catégorie, autres écritures du nom). Pour le modifier :

1. ouvrez-le avec le Bloc-notes et respectez l'indentation (2 espaces, pas de tabulation) ;
2. ajoutez, corrigez ou retirez un acteur (champs `name`, `category`, `aliases`,
   `case_sensitive`) ;
3. relancez l'application : la base est synchronisée au démarrage.

Un acteur retiré du fichier n'est jamais effacé de la base (son historique est conservé) :
il n'est simplement plus suivi. En cas d'erreur dans le fichier (nom manquant, doublon,
indentation), l'application ne démarre pas et indique la ligne et l'acteur fautifs.

**À vérifier** : la liste livrée a été constituée sans liste fournie, à partir des acteurs
connus du marché français. Ajoutez vos interlocuteurs, retirez les inutiles.

## Configuration

La configuration se fait par variables d'environnement, ou plus simplement dans un fichier
**`.env`** à la racine du projet :

1. copiez `.env.example` en `.env` ;
2. décommentez et modifiez les lignes voulues.

| Variable | Rôle | Défaut |
|---|---|---|
| `CP_DATA_DIR` | Dossier des données | `%LOCALAPPDATA%\NewsProperty` |
| `CP_PORT` | Port local (1024 à 65535) | `5000` |
| `CP_DEBUG` | Mode debug Flask (`true`/`false`) | `false` |
| `CP_OPEN_BROWSER` | Ouvrir le navigateur au lancement | `true` |
| `CP_LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` | `INFO` |
| `CP_DRIVE_DIR` | dossier Google Drive synchronisé (facultatif) où copier le paquet de l'e-mail | vide (copie manuelle) |
| `CP_BROWSER` | navigateur (Edge ou Chrome) qui fabrique le PDF à imprimer | vide (recherche automatique) |

Une valeur invalide arrête l'application avec un message qui nomme la variable fautive. Le
fichier `.env` n'est pas versionné : n'y mettez rien qui doive être partagé. Aucun secret
n'est écrit dans le code.

## Collecter la veille

La collecte lit les sources activées dans `config\sources.yaml` (flux RSS des sites et, si
activé, Google Actualités pour chaque thème puis pour chaque acteur suivi). Elle ne garde
que les articles **publiés pendant le mois choisi** (heure de Paris) et les classe :

- **rubrique 2** (les acteurs du marché) si le titre ou le résumé cite un acteur de
  `config\acteurs.yaml` (nom ou autre écriture) ;
- **rubrique 1** (actualités de l'immobilier logistique) sinon, s'il contient un terme de
  `config\regles_veille.yaml` (entrepôt, plateforme logistique, parc logistique...) ;
- les autres articles sont écartés.

Pour chaque article retenu, elle enregistre le titre, la source, la date, le **lien
d'origine** (jamais inventé) et les premières lignes de l'article (environ 300 caractères,
copiées telles quelles depuis la page, jamais reformulées). Tout le texte venant des sites
est converti en texte brut.

### Lancer une collecte

- **Depuis l'application** : bouton **Collecter la veille** sur la page d'accueil.
  Choisissez le mois (par défaut le mois précédent, puisque la newsletter est préparée en
  début de mois pour le mois écoulé) puis **Lancer la collecte**. La page de suivi se met à
  jour toute seule, puis affiche le bilan.
- **En double-cliquant sur `collecter_veille.bat`** : il demande le mois (Entrée = mois
  précédent), puis affiche l'avancement et le bilan.
- **En ligne de commande** :

  ```bat
  .venv\Scripts\python.exe -m news_property.tools.collect --month 2026-10
  ```

Une seule collecte peut tourner à la fois ; une deuxième demande est refusée avec un
message. Relancer la collecte d'un mois **ajoute uniquement les nouveaux articles** : la
sélection, les corrections et l'ordre déjà faits ne sont jamais modifiés.

Prérequis : `config\sources.yaml` doit exister (voir « Tester les sources de veille »).
S'il manque, la collecte ne démarre pas et l'application explique comment le créer.

### Lire le bilan

| Ligne | Signification |
|---|---|
| Articles ajoutés, rubrique 1 / 2 | Nouveaux articles enregistrés, prêts à être relus |
| Doublons ignorés | Article déjà présent ce mois-ci : même lien (après nettoyage des paramètres `utm_*`...) ou titre quasi identique pour le même acteur |
| Articles hors période | Publiés en dehors du mois choisi |
| Articles sans date | Le flux ne donne pas de date : écartés, faute de pouvoir vérifier le mois |
| Articles hors sujet | Ni acteur suivi, ni terme de l'immobilier logistique |
| Par source | Entrées lues et articles ajoutés pour chaque source configurée |

### Si une source est en erreur

Une source en erreur n'arrête pas la collecte : les autres sources sont traitées, le bilan
indique « Terminée, avec des sources en erreur » et donne la raison en français (ex. « Le
site n'a pas répondu à temps », « Le site a refusé l'accès (HTTP 403) »).

1. Relancez la collecte un peu plus tard : seuls les articles manquants seront ajoutés.
2. Si l'erreur persiste plusieurs jours, relancez `diagnostiquer_sources.bat` pour vérifier
   la source, puis désactivez-la (`enabled: false`) dans `config\sources.yaml` si elle n'est
   plus exploitable.
3. Le détail technique est dans `%LOCALAPPDATA%\NewsProperty\logs\news_property.log`.

### Ajuster le tri

Les listes de mots-clés sont dans **`config\regles_veille.yaml`** (commenté) :

- `topic_terms` : termes qui envoient un article en rubrique 1 ;
- `exclusions` : expressions à ne pas compter (« entrepôt de données », « logistique
  militaire »...).

Pour un acteur dont le nom est aussi un mot courant ou un sigle court, mettez
`case_sensitive: true` dans `config\acteurs.yaml` : la majuscule initiale sera exigée (c'est
le cas d'Argan, pour ne pas confondre avec l'huile d'argan, ou de GLP). Les thèmes
interrogés sur Google Actualités se règlent dans le bloc `google_news.topics` de
`config\sources.yaml`. Les changements s'appliquent aux collectes suivantes ; les articles
déjà collectés ne sont pas reclassés.

## Relire la newsletter

Une fois la veille collectée, la relecture permet en quelques minutes de choisir ce qui ira
dans la newsletter, de corriger et d'ordonner.

### Pas à pas

1. **Accueil → Relire.** La page « Newsletters » liste les mois, du plus récent au plus
   ancien, avec leur statut (Brouillon, Relue, Exportée) et le nombre d'articles retenus.
   Cliquez sur **Relire** devant un mois, ou choisissez un mois puis **Ouvrir ou créer ce
   mois**.
2. **Sept onglets** : *Une*, puis les deux rubriques de veille *1. Immobilier
   logistique* et *2. Acteurs du marché*, puis le contenu interne *3. Visites
   d'entrepôts*, *4. Calendrier*, *5. Congés et absences* et *6. Documents utiles* (voir
   [Contenu interne](#contenu-interne)). Chaque onglet affiche un compteur
   « retenus / limite ». Il passe **en orange** quand la limite est dépassée : c'est un
   avertissement, rien n'est bloqué. Un rappel indique que la version imprimée doit tenir
   en 4 pages A4.
3. **Filtres** : *Tout*, *Retenus*, *Non retenus*, *Transactions*.
4. **Vue compacte.** Chaque article tient sur une ligne : rang, bouton **Retenir**, acteur,
   titre, source, date, surface, badge « Transaction ».
   - **Modifier** déplie le formulaire complet de l'article.
   - **Tout déplier / Tout replier** ouvre ou ferme tous les formulaires.
5. **Retenir.** Un seul bouton, sur la ligne de l'article : **Retenir** pour garder l'article
   dans la newsletter, **✓ Retenu - ne plus retenir** pour l'en retirer (il reste visible,
   en grisé).
6. **Ordonner** : **⤒ En tête**, **↑ Monter**, **↓ Descendre**, **⤓ En fin**, ou tapez un
   numéro dans **Rang** puis **Placer** (un numéro trop grand place l'article en dernier).
   **Ne retenir que les N premiers** (N = limite de la rubrique) retient les N premiers
   articles dans l'ordre affiché et retire les suivants.
7. **Corriger** (dans **Modifier**) : **Titre**, **Acteur** (les acteurs suivis, ou
   « Autre... » avec un nom libre), **Surface (m²)**, **Date**, **Source**, **Premières
   lignes** (elles peuvent être coupées ou corrigées, **pas réécrites** : elles doivent rester
   fidèles à l'article), la case **Transaction**, puis **Enregistrer cet article**.

   Si le repère « Transaction » a été posé automatiquement, la phrase qui l'a déclenché est
   affichée. Une fois modifié à la main, la détection n'y touche plus. **Le lien de
   l'article n'est pas modifiable.**
8. **Rien ne se perd.** Une carte modifiée et pas encore enregistrée affiche
   « Modifications non enregistrées » ; quitter la page à ce moment-là fait apparaître une
   demande de confirmation du navigateur. Si un enregistrement est refusé (titre vide, date
   impossible...), la page revient avec **toute votre saisie** et le message à côté du champ
   à corriger.
9. **Changer de rubrique** (dans **Modifier**) : **Déplacer vers la rubrique 2** (l'acteur
   est alors obligatoire) ou **vers la rubrique 1**. L'article se place en fin de la rubrique
   choisie.
10. **Supprimer...** demande une confirmation, puis efface l'article. Pour l'écarter
    simplement, préférez « Ne plus retenir ».
11. **+ Ajouter un article à la main** : collez le lien d'un article trouvé vous-même, puis
    **Vérifier le lien**. L'application lit le titre et les premières lignes, vous complétez
    le reste, puis vous enregistrez.
    - Un lien qui ne répond pas, ou qui ne commence pas par http(s), est refusé.
    - Si le site **refuse la lecture automatique** (protection anti-robot) : ouvrez le lien
      dans votre navigateur, saisissez titre et premières lignes, puis cochez **« J'ai ouvert
      ce lien dans mon navigateur et il affiche bien l'article »**. L'article porte alors la
      mention **« Lien vérifié par l'utilisateur »**.
12. **Marquer comme relue** (en haut à droite) passe la newsletter au statut « Relue ». Des
    avertissements s'affichent si une rubrique dépasse sa limite ou si elle est vide (aucun
    article retenu, aucune visite, aucun rendez-vous, aucune absence, aucun document).
    **Repasser en brouillon** permet de revenir en arrière.

Après chaque action, un message confirme ce qui a été fait, ou explique pourquoi c'est
refusé, et la page revient sur l'élément concerné. Tout fonctionne sans JavaScript et au
clavier : touche Tab, avec un lien « Aller au contenu » en premier.

### Limites par rubrique

Les limites sont dans **`config\limites.yaml`** (commenté) : rubrique 1 = 6, rubrique 2 =
8, visites = 4 (3 photos par visite), événements = 8, absences = 12, pages = 4 au départ. Modifiez le fichier puis
rechargez la page : inutile de relancer l'application. Si le fichier est absent, ces valeurs
s'appliquent ; s'il est invalide, un message l'indique en haut de la page.

## Contenu interne

Le contenu interne est tout ce que l'équipe écrit elle-même : la Une, les visites
d'entrepôts, le calendrier, les congés et absences, les documents utiles. Il se saisit dans
les onglets de la relecture (**Accueil → Contenu interne** ouvre directement l'onglet
*Une* du dernier brouillon, ou la liste des mois s'il n'y en a pas).

### Une

L'onglet **Une** affiche les trois chiffres du mois : visites d'entrepôts, rendez-vous à
venir, actualités retenues (rubriques 1 et 2). Ils sont **calculés automatiquement** à
partir des autres onglets et ne se saisissent pas. Chaque chiffre mène à l'onglet qui le
nourrit.

### 3. Visites d'entrepôts

1. **+ Ajouter une visite d'entrepôt**, puis remplissez **Site visité** et **Lieu**
   (obligatoires), **Date de la visite**, **Propriétaire ou exploitant**, **Surface (m²)**
   et une **Description courte**, puis **Ajouter la visite**. Pour corriger ensuite :
   **Modifier** puis **Enregistrer la visite**.
2. **Photos** : choisissez jusqu'à 3 photos (JPEG, PNG ou WebP, 15 Mo au plus chacune) puis
   **Envoyer les photos**. Chaque photo est vérifiée en l'ouvrant, **redressée** selon
   l'orientation de l'appareil, **débarrassée de ses métadonnées** (dont la position GPS),
   réduite à 1600 px et enregistrée en JPEG dans `<données>\photos\<mois>\` sous un nom
   généré par l'application. Une photo illisible ou trop lourde est refusée avec un
   message ; les autres sont gardées.
3. La première photo est la **photo principale** (c'est elle qui illustre la visite) :
   **Choisir comme photo principale** sous une autre photo la fait passer en premier. Le
   champ de légende puis **Enregistrer** ajoute un texte sous la photo. **Supprimer...**
   retire la photo (confirmation demandée) et efface le fichier.
4. **⤒ ↑ ↓ ⤓** ordonnent les visites ; **Supprimer...** efface une visite avec ses
   photos, après confirmation.

### 4. Calendrier

**+ Ajouter un rendez-vous au calendrier** : **Titre** (obligatoire), **Date** et **Date
de fin** (facultatives, la fin ne peut pas précéder le début), **Lieu**, **Résumé** (jusqu'à
1 000 caractères, retours à la ligne conservés), puis **Ajouter**. **Classer par date**
remet la liste dans l'ordre chronologique (les rendez-vous sans date passent en fin) ; les
flèches permettent un ordre à la main.

### 5. Congés et absences

**+ Ajouter un congé ou une absence** : **Nom** et **Du** (obligatoires), **Au**
(facultatif, jamais avant le début), **Motif** court (congés, formation, déplacement...),
puis **Ajouter**. **Classer par date** et flèches comme pour le calendrier.

### 6. Documents utiles

Six liens au plus, chacun avec un **Libellé** (sinon « Voir le document ») et une
**Adresse du document** qui doit commencer par `http://` ou `https://` (SharePoint, Drive,
intranet...). **Enregistrer les liens** vérifie seulement le format des adresses :
**l'application ne les ouvre jamais** (ils peuvent demander une connexion) ; testez-les
vous-même avec **Ouvrir**. Une ligne vide est ignorée ; **Retirer tous les liens** vide la
rubrique après confirmation.

Comme pour les articles, un enregistrement refusé revient avec toute votre saisie et le
message à côté du champ à corriger ; tout fonctionne sans JavaScript et au clavier.

## Aperçu et impression

Depuis l'écran du mois, le bouton **Aperçu** (en haut à droite) affiche la newsletter telle
qu'elle sera publiée : mise en page de la version imprimable, fond blanc, environ 800 px de
large.

- **Seuls les articles retenus** y figurent, dans l'ordre choisi pendant la relecture, avec
  leur titre et leurs premières lignes copiés tels quels, le badge « Transaction » et la
  surface quand ils sont connus. Le contenu interne apparaît tel que saisi.
- En haut, le **bandeau** aux couleurs du logo, puis la **Une** (trois chiffres calculés :
  visites, rendez-vous, actualités retenues) et le **sommaire**.
- Les rubriques sont **numérotées sans trou** (01, 02, 03...) : une rubrique vide est
  masquée et n'est pas comptée. Si tout est vide, l'aperçu l'explique au lieu d'afficher une
  page blanche.
- Chaque article porte un **QR code** de son lien, pour lire l'article depuis la version
  papier. Les QR codes sont mis en cache dans `<données>\exports\qr\`.
- Les dates sont en français (« 1er octobre 2026 », « du 17 au 19 novembre 2026 »).
- Le **pied de page** reprend le logo couleur et le texte de `config\theme.yaml`.

**Imprimer** (dans l'aperçu, ou directement depuis l'écran du mois) ouvre la boîte
d'impression du navigateur : choisissez une imprimante, ou **« Enregistrer au format
PDF »** pour obtenir un fichier. La barre grise de l'aperçu n'est pas imprimée. Le format
est A4 avec des marges de 12 mm ; si le document dépasse 4 pages, réduisez le nombre
d'articles retenus (le compteur de la relecture vous le rappelle).

Si `config\theme.yaml` contient une erreur, l'aperçu l'indique (ligne et valeur fautives)
au lieu de s'afficher avec de mauvaises couleurs.

## Transactions et surfaces

Après chaque collecte, l'application propose automatiquement :

- un **repère « Transaction »** sur les articles qui annoncent une signature de bail, une
  acquisition, une cession, une livraison ou une mise en chantier d'un actif logistique ;
- la **surface en m²** citée dans l'article, si elle est écrite (jamais inventée).

Chaque détection est justifiée par **la phrase de l'article** qui l'a déclenchée. Ce sont des
suggestions à vérifier pendant la relecture.

- **Relancer la détection** : bouton « Relancer la détection » sur la page « Collecter la
  veille », ou :

  ```bat
  .venv\Scripts\python.exe -m news_property.tools.detect --month 2026-10
  ```

  Un repère « Transaction » modifié à la main et une surface déjà renseignée ne sont jamais
  touchés.
- **Mesurer la fiabilité** sur un mois réel :

  ```bat
  .venv\Scripts\python.exe -m news_property.tools.evaluate_detection --month 2026-10
  ```

  Le rapport (`%LOCALAPPDATA%\NewsProperty\reports\detection-...md`) liste chaque détection
  avec sa phrase et une case « juste / faux » à cocher, ainsi que les articles non marqués
  qui contiennent un terme (oublis possibles). Il sert à décider si le repère est assez
  fiable pour être gardé.
- **Ajuster les règles** : `config\regles_detection.yaml` (mots-clés, commenté).
  Fonctionnement et limites : [`docs/detection.md`](docs/detection.md).

## Tester les sources de veille

La veille ne repose que sur des flux d'actualité **réellement exploitables** : aucun lien
n'est inventé, une source n'est retenue que si elle fournit de vrais articles avec leur URL
d'origine. L'outil de diagnostic teste depuis votre PC chaque source candidate : 9 sites
spécialisés (immobilier d'entreprise, logistique, construction) et Google Actualités,
interrogé pour 4 thèmes (« immobilier logistique », « entrepôt logistique », « plateforme
logistique », « parc logistique ») puis pour chacun des acteurs de `config\acteurs.yaml`.
La liste des sites est dans `news_property/sources/candidates.py` : seules leurs pages
d'accueil publiques y figurent, c'est le diagnostic qui cherche et vérifie les flux.

Sites candidats : Business Immo, CFNEWS Immo, Immoweek, Voxlog, Supply Chain Magazine,
Stratégies Logistique, Actu Transport Logistique, Le Moniteur, Batiactu. Ajoutez-y vos
sites habituels (nom et page d'accueil) puis relancez le diagnostic.

### Lancer le diagnostic

Double-cliquez sur **`diagnostiquer_sources.bat`** (connexion Internet requise, compter
quelques minutes : une requête par site et par sujet, avec une seconde d'attente entre deux
requêtes vers un même site). La progression s'affiche dans la fenêtre, puis le rapport
s'ouvre automatiquement.

Équivalent en ligne de commande :

```bat
.venv\Scripts\python.exe -m news_property.tools.diagnose_sources
.venv\Scripts\python.exe -m news_property.tools.diagnose_sources --only "Business Immo"
.venv\Scripts\python.exe -m news_property.tools.diagnose_sources --only "Prologis"
.venv\Scripts\python.exe -m news_property.tools.diagnose_sources --no-google
```

L'outil est « poli » envers les sites : il s'annonce (`NewsProperty/0.1.0 (veille
interne)`), attend 1 seconde entre deux requêtes vers un même site, réessaie 2 fois au plus
en cas d'erreur réseau, et respecte `robots.txt` pour les pages HTML. Une source en erreur
n'interrompt jamais le diagnostic des autres.

### Où trouver le rapport

Dans le dossier `reports` des données (`%LOCALAPPDATA%\NewsProperty\reports`) :

| Fichier | Contenu |
|---|---|
| `sources-AAAAMMJJ-HHMM.md` | Rapport lisible : tableau de synthèse puis détail par source (flux trouvés, chiffres, titres d'exemple, liens testés, erreurs) |
| `sources-AAAAMMJJ-HHMM.json` | Mêmes données, structurées |
| `sources.proposed.yaml` | Proposition de configuration (remplacée à chaque diagnostic) |

Le détail technique des erreurs est dans `logs\news_property.log`.

### Lire les verdicts

- **exploitable** : flux trouvé, au moins 80 % des articles ont un titre, une date et un
  lien, au moins un article publié depuis 30 jours, et les liens testés (3 par flux)
  répondent correctement.
- **partiel** : flux trouvé, mais un critère manque ; la raison indique lequel (ex. « aucune
  date de publication », « aucun article depuis 30 jours »).
- **inexploitable** : aucun flux trouvé, ou erreur (ex. « Le site a refusé l'accès
  (HTTP 403) »).

Pour Google Actualités, le verdict est donné par sujet puis globalement ; un sujet sans
article sur 30 jours n'est pas un défaut de la source. La ligne « Résolution des liens »
indique si les liens Google mènent directement à l'article de l'éditeur (`resolved`) ou
restent des liens `news.google.com` (`google_link_only`) : ce n'est pas bloquant, ces liens
ouvrent bien le vrai article chez le lecteur.

### Valider la proposition

1. Relisez `sources.proposed.yaml` en vous aidant du rapport. Seules les sources
   « exploitable » y sont activées (`enabled: true`) ; une source sans flux a `url: null`.
2. Ajustez si besoin (`enabled: true/false`, `note`), sans inventer d'URL : chaque adresse
   doit venir du diagnostic.
3. Copiez le fichier vers **`config\sources.yaml`** dans le dossier du projet. C'est ce
   fichier que la collecte (étape 4) utilisera.

Le format est documenté, champ par champ, dans `config/sources.example.yaml`.

## Thème et charte graphique

Tout ce qui relève de la charte graphique est dans **`config/theme.yaml`** : nom, phrase
d'accroche, couleurs, couleur de chaque rubrique, polices, logos, pied de page et titres des
rubriques. Le thème reprend la structure de la charte de référence (News Franchise) avec les
**couleurs du logo Carrefour Property** (violet `#8D2562` et ses tons). Détail des choix, et
comment modifier une valeur : [`docs/theme.md`](docs/theme.md).

L'écran **Thème** (bouton sur la page d'accueil) affiche le thème en lecture seule :
couleurs, couleurs des rubriques, polices, logos, chemin du fichier à modifier et
**vérification des contrastes** (4,5:1 pour le texte courant, 3:1 pour le grand texte).
Un contraste insuffisant donne un avertissement, sans bloquer ; une valeur invalide
(couleur qui n'est pas au format `#RRGGBB`, logo introuvable, erreur de syntaxe YAML avec sa
ligne) donne un message d'erreur clair indiquant la clé à corriger.

## Sécurité

Formulaires protégés par jeton CSRF, contrôle des en-têtes Host, Origin et Referer, cookie
`SameSite=Strict`, clé secrète générée au premier lancement dans le dossier de données,
textes externes toujours échappés, liens non modifiables : voir
[`docs/securite.md`](docs/securite.md).

## Tests

Sous Windows, double-cliquez sur **`tests.bat`** : il installe les dépendances de test
(`requirements-dev.txt`) puis lance `pytest`.

Ou en ligne de commande :

```bat
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
```

Les tests n'écrivent que dans des dossiers temporaires (`tmp_path`) : ils ne touchent ni vos
données ni le dossier du projet. Ils n'accèdent jamais au réseau : les sites et flux sont
simulés (`respx`) à partir des fichiers de `tests/fixtures/`, et toute connexion réelle est
bloquée.

Vérification du style du code (longueur de ligne 100) :

```bat
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
```

## Arborescence

```
NewsProperty/
├── lancer.bat              # Lancement en double-clic (crée/met à jour .venv)
├── tests.bat               # Lancement des tests
├── diagnostiquer_sources.bat # Diagnostic des sources de veille
├── collecter_veille.bat    # Collecte de la veille d'un mois
├── requirements.txt        # Dépendances d'exécution (versions exactes)
├── requirements-dev.txt    # Dépendances de test et de lint (inclut requirements.txt)
├── pyproject.toml          # Métadonnées du projet, configuration de pytest et de ruff
├── .env.example            # Modèle de configuration commenté
├── .gitignore
├── .gitattributes          # Fins de ligne (CRLF pour les .bat)
├── README.md
├── config/
│   ├── acteurs.yaml        # Référentiel des acteurs suivis (immobilier logistique)
│   ├── regles_veille.yaml  # Mots-clés de tri de la veille (rubrique 1, exclusions)
│   ├── regles_detection.yaml # Règles « transaction » et surface
│   ├── limites.yaml        # Limites éditoriales par rubrique
│   ├── sources.example.yaml  # Format documenté de config/sources.yaml
│   ├── theme.yaml          # Charte graphique de la newsletter (couleurs du logo)
│   └── assets/             # Logos Carrefour Property (couleurs, réserve blanche)
├── docs/
│   ├── data-model.md       # Modèle de données et procédure de migration
│   ├── detection.md        # Détection des transactions et des surfaces
│   ├── theme.md            # Thème : origine des valeurs, clés modifiables
│   └── securite.md         # CSRF, contrôle du Host, échappement, liens non modifiables
├── news_property/          # Code de l'application
│   ├── __init__.py         # create_app(), routes, pages d'erreur
│   ├── __main__.py         # Point d'entrée : python -m news_property
│   ├── config.py           # Settings et load_settings()
│   ├── logging_setup.py    # Journal rotatif + console
│   ├── dates.py            # Noms de mois en français
│   ├── security.py         # Clé secrète, jeton CSRF, contrôle du Host et de l'origine
│   ├── bootstrap.py        # Migrations et synchronisation des acteurs au démarrage
│   ├── models.py           # Dataclasses (Newsletter, Actor, Article, CollectionRun,
│   │                       # Visit, VisitPhoto, Event, Absence, DocumentLink)
│   ├── actor_catalog.py    # Lecture et validation de config/acteurs.yaml
│   ├── db/
│   │   ├── connection.py   # Connexion SQLite, migrations, intégration Flask
│   │   └── migrations/     # 0001_initial.sql, 0002_internal_content.sql...
│   ├── repositories/       # Fonctions d'accès aux données, une par table principale
│   ├── http_client.py      # Client HTTP poli (reprises, débit, robots.txt)
│   ├── sources/
│   │   ├── candidates.py   # Sites candidats, thèmes et acteurs pour Google Actualités
│   │   ├── discovery.py    # Découverte des flux d'un site
│   │   └── evaluation.py   # Évaluation d'un flux, Google Actualités, verdicts
│   ├── collect/            # Collecteur de veille
│   │   ├── config.py       # Lecture de sources.yaml et regles_veille.yaml
│   │   ├── feeds.py        # Lecture des flux (sites, Google Actualités)
│   │   ├── matching.py     # Détection des acteurs et des mots-clés, classement
│   │   ├── urls.py         # Normalisation des liens (dédoublonnage)
│   │   ├── excerpt.py      # Premières lignes et texte brut des articles
│   │   ├── runner.py       # Orchestrateur run_collection()
│   │   └── views.py        # Pages web /collecte
│   ├── detect/             # Détection : règles, phrases, transactions, surfaces, rapports
│   ├── review/             # Relecture : limites, actions (service.py), pages (views.py)
│   ├── content/            # Contenu interne : photos (photos.py), actions, pages
│   ├── tools/
│   │   ├── diagnose_sources.py  # Outil de diagnostic des sources
│   │   ├── collect.py      # Collecte en ligne de commande
│   │   ├── detect.py       # Détection en ligne de commande
│   │   └── evaluate_detection.py  # Rapport de fiabilité de la détection
│   ├── ui.py               # Menu, cartes de l'accueil, variables du thème
│   ├── fonts.py            # Police système pour les images générées
│   ├── newsletter/         # Thème (theme.py), formes (shapes.py), dates en français,
│   │                       # QR codes (qr.py), vue (view.py), aperçu et pages (views.py)
│   ├── templates/          # Gabarits Jinja2 (newsletter/_*.html : communs à l'aperçu
│   │                       # et aux exports)
│   └── static/             # style.css (application), newsletter.css (newsletter),
│   │                       # review.js et print.js (confort, optionnels)
└── tests/
    ├── conftest.py         # Fixtures (données dans tmp_path, réseau interdit)
    ├── helpers.py          # Application de test, jeton CSRF, thème de test, HTTP simulé
    ├── fixtures/           # Pages HTML et flux RSS enregistrés
    ├── test_config.py
    ├── test_app.py
    ├── test_theme.py
    ├── test_security.py
    ├── test_db.py
    ├── test_actors.py
    ├── test_repositories.py
    ├── test_http_client.py
    ├── test_discovery.py
    ├── test_evaluation.py
    ├── test_diagnose_sources.py
    ├── test_collect_units.py
    ├── test_collect_runner.py
    ├── test_collect_web.py
    ├── test_detection.py   # dont les cas étiquetés de fixtures/detection_cases.yaml
    ├── test_review.py      # Interface de relecture
    ├── test_content.py     # Contenu interne (Une, visites et photos, calendrier...)
    └── test_newsletter.py  # Dates, vue de la newsletter, QR codes, aperçu
```

## Conventions de développement

- Code et noms techniques en anglais, textes affichés en français, docstrings en français.
- Annotations de type sur toutes les fonctions publiques.
- `pathlib` pour tous les chemins (code multiplateforme).
- Dépendances épinglées en version exacte (`==`).
- Lint et format avec `ruff` (longueur de ligne 100).
- Pas de tirets longs dans les textes : uniquement le tiret court « - ».
