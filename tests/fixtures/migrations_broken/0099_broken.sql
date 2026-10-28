-- Migration volontairement invalide (tests) : les deux premières instructions
-- réussissent, la troisième échoue. Rien ne doit rester appliqué.
CREATE TABLE should_not_exist (id INTEGER PRIMARY KEY);
ALTER TABLE articles ADD COLUMN extra TEXT;
INSERT INTO table_inexistante VALUES (1);
