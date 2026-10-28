# Détection des transactions et des surfaces

La détection repère, dans les articles collectés d'un mois :

- le **repère « Transaction »** : l'article annonce une signature de bail, une acquisition,
  une cession, une livraison ou une mise en chantier d'un actif logistique ;
- la **surface en m²** citée dans l'article (« 40 000 m² »), si elle est écrite.

**Il n'y a aucune IA.** La détection applique des listes de mots écrites dans
`config/regles_detection.yaml`. Elle ne comprend pas le sens des phrases : elle reconnaît
des formulations. Chaque détection garde **la phrase de l'article qui la justifie** et
**les termes qui l'ont déclenchée**, pour être vérifiée en un coup d'œil. Rien n'est inventé :
sans nombre dans l'article, la surface reste vide.

## Quand elle tourne

- automatiquement à la fin de chaque collecte ;
- avec le bouton **Relancer la détection** de la page « Collecter la veille » ;
- en ligne de commande : `python -m news_property.tools.detect --month 2026-10`.

Elle ne fait aucun appel réseau : elle relit le titre, les premières lignes et le texte
(`body_text`) des articles déjà en base. Les articles de Google Actualités dont le lien n'a
pas été résolu n'ont souvent que leur titre : la détection y est donc beaucoup moins efficace.

## Comment marchent les règles

Le texte est découpé en phrases (le titre compte comme une phrase). Les termes sont comparés
sans tenir compte de la casse, des accents ni du type d'apostrophe, et doivent être des mots
entiers.

### Repère « Transaction »

Un article est marqué si **une même phrase** contient :

- un terme d'`action_terms` (« signe un bail », « acquiert », « livre », « pose la première
  pierre »...) **et** un terme d'`asset_terms` (« entrepôt », « plateforme logistique »,
  « m² »...) ;
- **ou** une expression de `strong_terms` (« BEFA », « clé en main logistique »...), qui
  suffit seule.

Une phrase qui contient une expression d'`exclusions` (« ferme son entrepôt », « entrepôt de
données »...) est ignorée. Si les deux termes sont dans deux phrases différentes, l'article
n'est pas marqué : c'est voulu, pour limiter les faux positifs.

### Surface

Un nombre suivi d'une unité de `surface_units` (« 40 000 m² », « 12.500 m2 », « 35 000 mètres
carrés »). Les espaces, points et apostrophes de groupement sont acceptés ; les décimales sont
ignorées. La plus grande surface de la **phrase justificative** est retenue ; à défaut, la
plus grande surface de l'article. Un nombre supérieur à `max_sqm` (1 000 000 m² par défaut)
n'est pas une surface d'actif (surface nationale, chiffre d'affaires) et il est ignoré.

## Le travail de l'utilisateur est protégé

Une nouvelle détection ne modifie jamais :

- un repère « Transaction » posé ou retiré à la main (`transaction_source = manual`) ;
- une surface déjà renseignée (par la détection précédente ou à la main) : elle n'est jamais
  remplacée.

Un repère laissé à la détection est recalculé à chaque passage, avec sa preuve.

## Mesurer la fiabilité sur un mois réel

```bat
.venv\Scripts\python.exe -m news_property.tools.evaluate_detection --month 2026-10
```

Cette commande écrit `%LOCALAPPDATA%\NewsProperty\reports\detection-2026-10-AAAAMMJJ-HHMM.md`,
**sans modifier la base**. Le rapport contient :

1. chaque transaction détectée : article, phrase, termes, surface, une case **juste / faux** ;
2. les surfaces trouvées hors transaction, avec une case **juste / faux** ;
3. les articles **non marqués** qui contiennent au moins un terme, avec une case **oubli**.

Ouvrez-le dans un éditeur de texte, cochez (`[x]`), puis comptez : précision = « juste » ÷
détections. Si la précision du repère reste faible sur deux ou trois mois, mieux vaut
l'abandonner que d'induire les lecteurs en erreur.

## Ajuster un mot-clé

1. Ouvrez `config/regles_detection.yaml` (commenté, un exemple par liste).
2. Ajoutez ou retirez une ligne « - expression » dans la bonne liste.
   - Un faux positif récurrent se corrige par une **exclusion**.
   - Un oubli récurrent (vu dans la partie 3 du rapport) se corrige par un nouveau terme.
3. Relancez la détection, puis le rapport de fiabilité, pour vérifier.

Un fichier invalide (liste vide, doublon, clé inconnue, erreur de syntaxe avec sa ligne) est
refusé avec un message qui nomme la liste fautive, sans rien modifier en base. Attention : en
YAML, `On`, `Oui`, `No`... doivent être écrits entre guillemets (`"On"`).

## Limites connues

- Pas de compréhension du sens : « Le logisticien ne signera pas de bail pour l'entrepôt »
  est marqué (la négation n'est pas comprise).
- Les formulations non prévues ne sont pas détectées (« reprend les murs », « se porte
  acquéreur »...) : la partie 3 du rapport aide à les repérer.
- Le nombre trouvé peut être une autre surface que celle de l'actif (terrain, surface
  cumulée d'un parc) : vérifiez la phrase.
- Les articles réduits à leur titre (Google Actualités non résolu) donnent peu de résultats.
