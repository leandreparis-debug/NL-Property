-- Contenu interne (étape 7) : visites d'entrepôts et leurs photos (rubrique 3),
-- calendrier à venir (rubrique 4), congés et absences (rubrique 5), documents utiles
-- (rubrique 6). Dates simples au format AAAA-MM-JJ.

CREATE TABLE visits (
    id            INTEGER PRIMARY KEY,
    newsletter_id INTEGER NOT NULL REFERENCES newsletters (id) ON DELETE CASCADE,
    site_name     TEXT NOT NULL CHECK (trim(site_name) <> ''),
    location      TEXT NOT NULL CHECK (trim(location) <> ''),
    visit_date    TEXT,
    owner_name    TEXT,
    surface_sqm   INTEGER CHECK (surface_sqm IS NULL OR surface_sqm > 0),
    description   TEXT,
    position      INTEGER NOT NULL DEFAULT 0 CHECK (position >= 0)
);

CREATE TABLE visit_photos (
    id        INTEGER PRIMARY KEY,
    visit_id  INTEGER NOT NULL REFERENCES visits (id) ON DELETE CASCADE,
    -- Nom relatif au dossier <données>/photos, jamais un chemin absolu.
    file_name TEXT NOT NULL
              CHECK (trim(file_name) <> ''
                     AND file_name NOT LIKE '/%'
                     AND file_name NOT LIKE '%\%'
                     AND file_name NOT LIKE '%:%'
                     AND file_name NOT LIKE '%..%'),
    caption   TEXT,
    position  INTEGER NOT NULL DEFAULT 0 CHECK (position >= 0)
);

CREATE TABLE events (
    id            INTEGER PRIMARY KEY,
    newsletter_id INTEGER NOT NULL REFERENCES newsletters (id) ON DELETE CASCADE,
    title         TEXT NOT NULL CHECK (trim(title) <> ''),
    event_date    TEXT,
    end_date      TEXT,
    location      TEXT,
    summary       TEXT,
    position      INTEGER NOT NULL DEFAULT 0 CHECK (position >= 0)
);

CREATE TABLE absences (
    id            INTEGER PRIMARY KEY,
    newsletter_id INTEGER NOT NULL REFERENCES newsletters (id) ON DELETE CASCADE,
    person_name   TEXT NOT NULL CHECK (trim(person_name) <> ''),
    start_date    TEXT NOT NULL,
    end_date      TEXT,
    reason        TEXT,
    position      INTEGER NOT NULL DEFAULT 0 CHECK (position >= 0)
);

-- Rubrique 6 : jusqu'à 6 liens vers des documents utiles (SharePoint, Drive), chacun
-- avec son libellé (NULL = « Voir le document »). Format vérifié, lien jamais appelé.
CREATE TABLE document_links (
    id            INTEGER PRIMARY KEY,
    newsletter_id INTEGER NOT NULL REFERENCES newsletters (id) ON DELETE CASCADE,
    position      INTEGER NOT NULL CHECK (position BETWEEN 1 AND 6),
    label         TEXT CHECK (label IS NULL OR length(label) <= 100),
    url           TEXT NOT NULL CHECK (url LIKE 'http://_%' OR url LIKE 'https://_%'),
    UNIQUE (newsletter_id, position)
);

CREATE INDEX idx_visits_newsletter ON visits (newsletter_id, position);
CREATE INDEX idx_visit_photos_visit ON visit_photos (visit_id, position);
CREATE INDEX idx_events_newsletter ON events (newsletter_id, position);
CREATE INDEX idx_absences_newsletter ON absences (newsletter_id, position);
