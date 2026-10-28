# Modèle de données

Base SQLite locale : `<données>/db/news_property.sqlite3` (par défaut
`%LOCALAPPDATA%\NewsProperty\db\`). Accès par `sqlite3` (bibliothèque standard, sans ORM),
via les fonctions de `news_property/repositories/`, qui manipulent les dataclasses de
`news_property/models.py`.

## Règles

1. **Aucun lien inventé.** Un article n'existe en base qu'avec l'URL réelle de l'article
   d'origine. Toute URL stockée commence par `http://` ou `https://`. C'est contrôlé deux
   fois : à la construction des dataclasses (`ValueError`) et par une contrainte `CHECK` en
   SQL. Le lien d'un article n'est jamais modifié par `articles.update()`.
2. **La Une est calculée à la volée.** Ses chiffres (visites, rendez-vous, actualités
   retenues) ne sont jamais stockés : `newsletters.front_page_figures()` les compte à chaque
   affichage.
3. **Dates en ISO 8601 (texte).** Date-heure en UTC avec fuseau
   (`2026-10-09T12:00:00+00:00`), date simple `AAAA-MM-JJ`. Le mois d'une newsletter est au
   format `AAAA-MM`.
4. **Requêtes toujours paramétrées.** Aucune donnée n'est concaténée dans le SQL. Seuls les
   noms de tables et de colonnes fixés dans le code le sont.
5. **Intégrité.** `PRAGMA foreign_keys = ON` à chaque connexion, journal `WAL`. Supprimer
   une newsletter supprime en cascade tout son contenu.
6. **Ordre d'affichage.** Les colonnes `position` (0, 1, 2...) sont propres à chaque liste
   (rubrique d'une newsletter...). Un ajout sans position va en fin de liste ; `reorder()`
   renumérote.

## Tables (schéma v3)

```
newsletters ─┬─< articles >──── actors
             ├─< collection_runs
             ├─< visits ─< visit_photos
             ├─< events
             ├─< absences
             └─< document_links
```
`─<` : « un à plusieurs ». Toutes les relations depuis `newsletters` (et de `visits` vers
`visit_photos`) sont en `ON DELETE CASCADE`.

### `newsletters` : une newsletter par mois
| Colonne | Type | Règles |
|---|---|---|
| `id` | INTEGER | clé primaire |
| `month` | TEXT | `AAAA-MM`, unique, mois 01 à 12 |
| `status` | TEXT | `draft` (défaut), `reviewed`, `exported` |
| `created_at`, `updated_at` | TEXT | ISO 8601 |

### `actors` : référentiel des acteurs suivis (synchronisé depuis `config/acteurs.yaml`)
| Colonne | Type | Règles |
|---|---|---|
| `id` | INTEGER | clé primaire |
| `name` | TEXT | unique |
| `slug` | TEXT | unique (ex. `kuehne-nagel`) |
| `category` | TEXT | catégorie libre (investisseur, logisticien, chargeur, conseil...) |
| `aliases` | TEXT | liste JSON des autres écritures du nom |
| `tracked` | INTEGER | 1 = suivi ; 0 = retiré du YAML (jamais supprimé) |
| `case_sensitive` | INTEGER | 1 = majuscule initiale exigée lors de la détection dans les articles (noms qui sont aussi des mots courants ou sigles courts : « Argan », « GLP ») |

### `articles` : actualités de veille (rubriques 1 à 3)
| Colonne | Type | Règles |
|---|---|---|
| `id` | INTEGER | clé primaire |
| `newsletter_id` | INTEGER | → `newsletters`, CASCADE |
| `rubric` | INTEGER | 1 (actualités de l'immobilier logistique), 2 (acteurs du marché) ou 3 (réglementation) |
| `actor_id` | INTEGER | → `actors`, nullable |
| `actor_label` | TEXT | acteur « Autre » saisi à la relecture (hors référentiel), ≤ 100 caractères, nullable |
| `title` | TEXT | obligatoire |
| `published_at` | TEXT | nullable, ISO 8601 |
| `source_name` | TEXT | obligatoire (ex. « Business Immo ») |
| `url` | TEXT | obligatoire, http(s) ; **unique par newsletter** (dédoublonnage) ; jamais modifiée |
| `link_verified_by_user` | INTEGER | 1 = ajout manuel d'un lien que le site refuse de laisser lire, confirmé par l'utilisateur |
| `excerpt` | TEXT | premières lignes de l'article, copiées telles quelles, nullable |
| `body_text` | TEXT | texte brut de l'article, tronqué à 20 000 caractères ; sert à la détection, **jamais affiché tel quel** |
| `is_transaction` | INTEGER | 1 = l'article annonce une transaction (signature, acquisition, livraison...) |
| `transaction_source` | TEXT | NULL (jamais évalué), `auto` (détection), `manual` (modifié à la main : la détection n'y touche plus) |
| `transaction_evidence` | TEXT | phrase de l'article qui justifie le repère (texte brut, ≤ 400 caractères) |
| `transaction_terms` | TEXT | termes déclencheurs (liste JSON) |
| `surface_sqm` | INTEGER | surface en m² citée dans l'article (détectée ou saisie), nullable, > 0 |
| `selected` | INTEGER | 1 (défaut) = retenu pour la newsletter |
| `position` | INTEGER | ordre dans la rubrique |
| `collected_at` | TEXT | date de collecte |

### `collection_runs` : exécutions de la collecte
`id`, `newsletter_id` (CASCADE), `started_at`, `finished_at` (nullable), `status`
(`running`, `success`, `partial`, `failed`), `stats` (JSON), `errors` (liste JSON de phrases
en français).

Une ligne `running` sert aussi de verrou : une seule collecte à la fois.

### `visits` : visites d'entrepôts (rubrique 4)
| Colonne | Type | Règles |
|---|---|---|
| `id` | INTEGER | clé primaire |
| `newsletter_id` | INTEGER | → `newsletters`, CASCADE |
| `site_name` | TEXT | obligatoire (nom du site ou de l'entrepôt) |
| `location` | TEXT | obligatoire (commune, département) |
| `visit_date` | TEXT | `AAAA-MM-JJ`, nullable |
| `owner_name` | TEXT | propriétaire ou exploitant, nullable |
| `surface_sqm` | INTEGER | surface en m², nullable, > 0 |
| `description` | TEXT | quelques lignes de description, nullable |
| `position` | INTEGER | ordre dans la rubrique |

### `visit_photos` : photos d'une visite (3 au plus par visite, limite dans `limites.yaml`)
| Colonne | Type | Règles |
|---|---|---|
| `id` | INTEGER | clé primaire |
| `visit_id` | INTEGER | → `visits`, CASCADE |
| `file_name` | TEXT | nom **relatif** au dossier `<données>/photos/` (ex. `2026-10/5f0c....jpg`), généré par l'application ; jamais un chemin absolu, ni `..`, ni `\`, ni `:` (contrôlé en Python et par `CHECK`) |
| `caption` | TEXT | légende, nullable |
| `position` | INTEGER | ordre ; la photo en position 0 est la **photo principale** |

Le fichier image est écrit dans `<données>/photos/<mois>/` après traitement (redressement
EXIF, suppression des métadonnées, 1600 px au plus, JPEG). Supprimer la ligne supprime le
fichier ; supprimer une newsletter supprime son dossier de photos.

### `events` : calendrier à venir (rubrique 5)
| Colonne | Type | Règles |
|---|---|---|
| `id` | INTEGER | clé primaire |
| `newsletter_id` | INTEGER | → `newsletters`, CASCADE |
| `title` | TEXT | obligatoire |
| `event_date` | TEXT | `AAAA-MM-JJ`, nullable |
| `end_date` | TEXT | `AAAA-MM-JJ`, nullable, jamais avant `event_date` |
| `location` | TEXT | lieu, nullable |
| `summary` | TEXT | résumé, retours à la ligne conservés, ≤ 1 000 caractères, nullable |
| `position` | INTEGER | ordre (bouton « Trier par date » renumérote) |

### `absences` : congés et absences de l'équipe (rubrique 6)
| Colonne | Type | Règles |
|---|---|---|
| `id` | INTEGER | clé primaire |
| `newsletter_id` | INTEGER | → `newsletters`, CASCADE |
| `person_name` | TEXT | obligatoire |
| `start_date` | TEXT | obligatoire, `AAAA-MM-JJ` |
| `end_date` | TEXT | nullable, jamais avant `start_date` |
| `reason` | TEXT | motif court (congés, formation, déplacement...), nullable |
| `position` | INTEGER | ordre (bouton « Trier par date » renumérote) |

### `document_links` : documents utiles (rubrique 7)
| Colonne | Type | Règles |
|---|---|---|
| `id` | INTEGER | clé primaire |
| `newsletter_id` | INTEGER | → `newsletters`, CASCADE |
| `position` | INTEGER | 1 à 6, unique par newsletter |
| `label` | TEXT | libellé ≤ 100 caractères, nullable (affiché « Voir le document ») |
| `url` | TEXT | obligatoire, http(s) ; format vérifié, **jamais appelé** par l'application |

Les six emplacements sont enregistrés d'un bloc (`replace_all`) : un emplacement vide
n'est pas stocké.

### `schema_version` : migrations appliquées
`version` (clé primaire), `name` (nom du fichier), `applied_at`.

Index : clés étrangères, `(newsletter_id, rubric, position)` et
`(newsletter_id, is_transaction)` sur `articles`, `(newsletter_id, position)` sur `visits`,
`events` et `absences`, `(visit_id, position)` sur `visit_photos`.

## Fonctions de dépôt

| Module | Fonctions |
|---|---|
| `newsletters` | `get_or_create`, `get`, `get_by_id`, `list_all`, `set_status`, `delete` (avec le dossier de photos), `front_page_figures` |
| `actors` | `sync_from_yaml`, `list_all`, `get`, `get_by_name`, `get_by_slug` |
| `articles` | `add` (renvoie `None` si l'URL existe déjà ce mois-ci), `get`, `list_for_rubric`, `list_for_newsletter`, `update` (jamais l'URL), `set_selected`, `set_transaction` (à la main), `apply_auto_transaction` (détection), `move_to_rubric`, `reorder`, `delete` |
| `collection_runs` | `start`, `finish`, `get`, `last_for_newsletter`, `list_running` |
| `visits` | `add`, `get`, `list_for_newsletter`, `update`, `reorder`, `delete` ; photos : `add_photo`, `get_photo`, `list_photos`, `reorder_photos`, `set_caption`, `delete_photo`, `remove_photo_file` |
| `events` | `add`, `get`, `list_for_newsletter`, `update`, `reorder`, `sort_by_date`, `delete` |
| `absences` | `add`, `get`, `list_for_newsletter`, `update`, `reorder`, `sort_by_date`, `delete` |
| `document_links` | `list_for_newsletter`, `replace_all`, `count` |

Chaque fonction prend la connexion en premier argument. Dans Flask, la connexion de la
requête s'obtient par `news_property.db.connection.get_db()` ; ailleurs, par
`get_connection(settings)` (à fermer après usage). Les connexions sont en autocommit ; pour
grouper plusieurs écritures, utiliser `with transaction(conn):`.

## Migrations livrées

| Fichier | Contenu |
|---|---|
| `0001_initial.sql` | Schéma initial (étape 2) : `newsletters`, `actors`, `articles`, `collection_runs` |
| `0002_internal_content.sql` | Contenu interne (étape 7) : `visits`, `visit_photos`, `events`, `absences`, `document_links` |
| `0003_regulation_rubric.sql` | Rubrique 3 « Réglementation » : la table `articles` accepte `rubric` = 3 (table recréée, données copiées, index refaits) |

## Ajouter une migration

Les migrations sont appliquées automatiquement au démarrage de l'application (`init_db`),
chacune dans une transaction. Si l'une échoue, elle est entièrement annulée, la base reste
dans sa version précédente et l'application refuse de démarrer avec un message clair.

1. Créer `news_property/db/migrations/000N_description.sql` : numéro suivant sur 4
   chiffres, description en minuscules (`a-z`, chiffres, `_`).
2. N'y mettre que du SQL transactionnel (`CREATE`, `ALTER`, `INSERT`, `UPDATE`...). Pas de
   `BEGIN`/`COMMIT`, ni de `PRAGMA` ou `VACUUM`.
3. Ne jamais modifier une migration déjà livrée : en ajouter une nouvelle.
4. Pour modifier une contrainte (SQLite ne sait pas faire `ALTER COLUMN`) : créer la
   nouvelle table, copier les données, supprimer l'ancienne, renommer.
5. Mettre à jour les dataclasses, les dépôts, ce document, et ajouter un test (base vierge
   et base existante).
6. Lancer `pytest -q`.
