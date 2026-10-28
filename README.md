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

> **État actuel (étape 1 sur 10)** : socle technique (configuration, journal, sécurité,
> page d'accueil, écran Thème aux couleurs du logo Carrefour Property). Les étapes
> suivantes ajoutent la base de données, le diagnostic des sources, la collecte, la
> détection, la relecture, le contenu interne, l'aperçu, l'e-mail et la démonstration.

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
├── requirements.txt        # Dépendances d'exécution (versions exactes)
├── requirements-dev.txt    # Dépendances de test et de lint (inclut requirements.txt)
├── pyproject.toml          # Métadonnées du projet, configuration de pytest et de ruff
├── .env.example            # Modèle de configuration commenté
├── .gitignore
├── .gitattributes          # Fins de ligne (CRLF pour les .bat)
├── README.md
├── config/
│   ├── theme.yaml          # Charte graphique de la newsletter (couleurs du logo)
│   └── assets/             # Logos Carrefour Property (couleurs, réserve blanche)
├── docs/
│   ├── theme.md            # Thème : origine des valeurs, clés modifiables
│   └── securite.md         # CSRF, contrôle du Host, échappement, liens non modifiables
├── news_property/          # Code de l'application
│   ├── __init__.py         # create_app(), routes, pages d'erreur
│   ├── __main__.py         # Point d'entrée : python -m news_property
│   ├── config.py           # Settings et load_settings()
│   ├── logging_setup.py    # Journal rotatif + console
│   ├── dates.py            # Noms de mois en français
│   ├── security.py         # Clé secrète, jeton CSRF, contrôle du Host et de l'origine
│   ├── ui.py               # Menu, cartes de l'accueil, variables du thème
│   ├── fonts.py            # Police système pour les images générées
│   ├── newsletter/         # Thème (theme.py), formes de la charte (shapes.py), pages
│   ├── templates/          # Gabarits Jinja2
│   └── static/             # Feuille de style de l'application
└── tests/
    ├── conftest.py         # Fixtures (données dans tmp_path, réseau interdit)
    ├── helpers.py          # Application de test, jeton CSRF, thème de test
    ├── test_config.py
    ├── test_app.py
    ├── test_theme.py
    └── test_security.py
```

## Conventions de développement

- Code et noms techniques en anglais, textes affichés en français, docstrings en français.
- Annotations de type sur toutes les fonctions publiques.
- `pathlib` pour tous les chemins (code multiplateforme).
- Dépendances épinglées en version exacte (`==`).
- Lint et format avec `ruff` (longueur de ligne 100).
- Pas de tirets longs dans les textes : uniquement le tiret court « - ».
