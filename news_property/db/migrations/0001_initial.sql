-- Schéma initial de News Property (étape 2) : newsletters, acteurs suivis, articles de
-- veille, exécutions de la collecte. Les tables du contenu interne (visites, calendrier,
-- absences, documents) arrivent dans une migration ultérieure.
-- Dates en ISO 8601 (texte) ; mois de newsletter au format AAAA-MM.
-- Règle du projet : aucun lien inventé. Toute URL stockée commence par http:// ou https://.

CREATE TABLE newsletters (
    id         INTEGER PRIMARY KEY,
    month      TEXT NOT NULL UNIQUE
               CHECK (month GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]'
                      AND CAST(substr(month, 6, 2) AS INTEGER) BETWEEN 1 AND 12),
    status     TEXT NOT NULL DEFAULT 'draft'
               CHECK (status IN ('draft', 'reviewed', 'exported')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Référentiel des acteurs de l'immobilier logistique (synchronisé depuis config/acteurs.yaml).
CREATE TABLE actors (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL UNIQUE CHECK (trim(name) <> ''),
    slug           TEXT NOT NULL UNIQUE CHECK (trim(slug) <> ''),
    category       TEXT NOT NULL DEFAULT '',
    aliases        TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(aliases)),
    tracked        INTEGER NOT NULL DEFAULT 1 CHECK (tracked IN (0, 1)),
    -- 1 = majuscule initiale exigée lors de la détection (noms qui sont aussi des mots).
    case_sensitive INTEGER NOT NULL DEFAULT 0 CHECK (case_sensitive IN (0, 1))
);

-- Articles de veille : rubrique 1 (actualités de l'immobilier logistique) ou 2 (acteurs).
CREATE TABLE articles (
    id                    INTEGER PRIMARY KEY,
    newsletter_id         INTEGER NOT NULL REFERENCES newsletters (id) ON DELETE CASCADE,
    rubric                INTEGER NOT NULL CHECK (rubric IN (1, 2)),
    actor_id              INTEGER REFERENCES actors (id),
    -- Acteur « Autre » saisi à la relecture (hors référentiel).
    actor_label           TEXT CHECK (actor_label IS NULL OR length(actor_label) <= 100),
    title                 TEXT NOT NULL CHECK (trim(title) <> ''),
    published_at          TEXT,
    source_name           TEXT NOT NULL CHECK (trim(source_name) <> ''),
    url                   TEXT NOT NULL CHECK (url LIKE 'http://_%' OR url LIKE 'https://_%'),
    -- 1 = lien ajouté à la main que le site refuse de laisser lire ; l'utilisateur a
    -- confirmé l'avoir ouvert dans son navigateur.
    link_verified_by_user INTEGER NOT NULL DEFAULT 0 CHECK (link_verified_by_user IN (0, 1)),
    excerpt               TEXT,
    -- Texte brut de l'article (tronqué à 20 000 caractères) : sert à la détection des
    -- transactions ; jamais affiché tel quel.
    body_text             TEXT CHECK (body_text IS NULL OR length(body_text) <= 20000),
    -- Repère « transaction » (l'article annonce une signature, une acquisition, une
    -- livraison...), avec sa preuve : phrase source et termes déclencheurs.
    -- transaction_source : NULL = jamais évalué, 'auto' = détection, 'manual' = modifié
    -- à la main (la détection n'y touche plus).
    is_transaction        INTEGER NOT NULL DEFAULT 0 CHECK (is_transaction IN (0, 1)),
    transaction_source    TEXT CHECK (transaction_source IS NULL
                                      OR transaction_source IN ('auto', 'manual')),
    transaction_evidence  TEXT CHECK (transaction_evidence IS NULL
                                      OR length(transaction_evidence) <= 400),
    transaction_terms     TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(transaction_terms)),
    -- Surface en m² citée dans l'article (détectée ou saisie), si connue.
    surface_sqm           INTEGER CHECK (surface_sqm IS NULL OR surface_sqm > 0),
    selected              INTEGER NOT NULL DEFAULT 1 CHECK (selected IN (0, 1)),
    position              INTEGER NOT NULL DEFAULT 0 CHECK (position >= 0),
    collected_at          TEXT NOT NULL,
    UNIQUE (newsletter_id, url)
);

CREATE TABLE collection_runs (
    id            INTEGER PRIMARY KEY,
    newsletter_id INTEGER NOT NULL REFERENCES newsletters (id) ON DELETE CASCADE,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL DEFAULT 'running'
                  CHECK (status IN ('running', 'success', 'partial', 'failed')),
    stats         TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(stats)),
    errors        TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(errors))
);

CREATE INDEX idx_articles_rubric ON articles (newsletter_id, rubric, position);
CREATE INDEX idx_articles_actor ON articles (actor_id);
CREATE INDEX idx_articles_transaction ON articles (newsletter_id, is_transaction);
CREATE INDEX idx_collection_runs_newsletter ON collection_runs (newsletter_id, started_at);
