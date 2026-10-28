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
2. **La Une est calculée à la volée.** Ses chiffres ne sont jamais stockés (étape 7).
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

## Tables (schéma v1)

```
newsletters ─┬─< articles >──── actors
             └─< collection_runs
```
`─<` : « un à plusieurs ». Les relations depuis `newsletters` sont en `ON DELETE CASCADE`.
Les tables du contenu interne (visites d'entrepôts et photos, calendrier, absences,
documents utiles) arrivent avec l'étape 7, par une nouvelle migration.

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

### `articles` : actualités de veille (rubriques 1 et 2)
| Colonne | Type | Règles |
|---|---|---|
| `id` | INTEGER | clé primaire |
| `newsletter_id` | INTEGER | → `newsletters`, CASCADE |
| `rubric` | INTEGER | 1 (actualités de l'immobilier logistique) ou 2 (acteurs du marché) |
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

### `schema_version` : migrations appliquées
`version` (clé primaire), `name` (nom du fichier), `applied_at`.

Index : clés étrangères, `(newsletter_id, rubric, position)` et
`(newsletter_id, is_transaction)` sur `articles`.

## Fonctions de dépôt

| Module | Fonctions |
|---|---|
| `newsletters` | `get_or_create`, `get`, `get_by_id`, `list_all`, `set_status`, `delete` |
| `actors` | `sync_from_yaml`, `list_all`, `get`, `get_by_name`, `get_by_slug` |
| `articles` | `add` (renvoie `None` si l'URL existe déjà ce mois-ci), `get`, `list_for_rubric`, `list_for_newsletter`, `update` (jamais l'URL), `set_selected`, `set_transaction` (à la main), `apply_auto_transaction` (détection), `move_to_rubric`, `reorder`, `delete` |
| `collection_runs` | `start`, `finish`, `get`, `last_for_newsletter`, `list_running` |

Chaque fonction prend la connexion en premier argument. Dans Flask, la connexion de la
requête s'obtient par `news_property.db.connection.get_db()` ; ailleurs, par
`get_connection(settings)` (à fermer après usage). Les connexions sont en autocommit ; pour
grouper plusieurs écritures, utiliser `with transaction(conn):`.

## Migrations livrées

| Fichier | Contenu |
|---|---|
| `0001_initial.sql` | Schéma initial (étape 2) : `newsletters`, `actors`, `articles`, `collection_runs` |

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
