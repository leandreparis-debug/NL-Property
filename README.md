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

> **État actuel (étape 3 sur 10)** : socle technique (configuration, journal, sécurité,
> page d'accueil, écran Thème aux couleurs du logo Carrefour Property), base de données
> locale avec migrations et référentiel des acteurs suivis, diagnostic des sources de
> veille. Les étapes suivantes ajoutent la collecte, la détection, la relecture, le
> contenu interne, l'aperçu, l'e-mail et la démonstration.

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
├── requirements.txt        # Dépendances d'exécution (versions exactes)
├── requirements-dev.txt    # Dépendances de test et de lint (inclut requirements.txt)
├── pyproject.toml          # Métadonnées du projet, configuration de pytest et de ruff
├── .env.example            # Modèle de configuration commenté
├── .gitignore
├── .gitattributes          # Fins de ligne (CRLF pour les .bat)
├── README.md
├── config/
│   ├── acteurs.yaml        # Référentiel des acteurs suivis (immobilier logistique)
│   ├── sources.example.yaml  # Format documenté de config/sources.yaml
│   ├── theme.yaml          # Charte graphique de la newsletter (couleurs du logo)
│   └── assets/             # Logos Carrefour Property (couleurs, réserve blanche)
├── docs/
│   ├── data-model.md       # Modèle de données et procédure de migration
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
│   ├── models.py           # Dataclasses (Newsletter, Actor, Article, CollectionRun)
│   ├── actor_catalog.py    # Lecture et validation de config/acteurs.yaml
│   ├── db/
│   │   ├── connection.py   # Connexion SQLite, migrations, intégration Flask
│   │   └── migrations/     # 0001_initial.sql...
│   ├── repositories/       # Fonctions d'accès aux données, une par table principale
│   ├── http_client.py      # Client HTTP poli (reprises, débit, robots.txt)
│   ├── sources/
│   │   ├── candidates.py   # Sites candidats, thèmes et acteurs pour Google Actualités
│   │   ├── discovery.py    # Découverte des flux d'un site
│   │   └── evaluation.py   # Évaluation d'un flux, Google Actualités, verdicts
│   ├── tools/
│   │   └── diagnose_sources.py  # Outil de diagnostic des sources
│   ├── ui.py               # Menu, cartes de l'accueil, variables du thème
│   ├── fonts.py            # Police système pour les images générées
│   ├── newsletter/         # Thème (theme.py), formes de la charte (shapes.py), pages
│   ├── templates/          # Gabarits Jinja2
│   └── static/             # Feuille de style de l'application
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
    └── test_diagnose_sources.py
```

## Conventions de développement

- Code et noms techniques en anglais, textes affichés en français, docstrings en français.
- Annotations de type sur toutes les fonctions publiques.
- `pathlib` pour tous les chemins (code multiplateforme).
- Dépendances épinglées en version exacte (`==`).
- Lint et format avec `ruff` (longueur de ligne 100).
- Pas de tirets longs dans les textes : uniquement le tiret court « - ».
