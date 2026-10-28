# Sécurité de l'application locale

News Property n'écoute que sur `127.0.0.1` : elle n'est pas joignable depuis le réseau. Mais le navigateur qui l'affiche visite aussi d'autres sites, et une page malveillante pourrait tenter de l'utiliser à l'insu de l'utilisateur. Les protections ci-dessous s'appliquent à toutes les pages (code : `news_property/security.py`).

## Jeton CSRF sur tous les formulaires

Chaque formulaire contient un jeton secret (Flask-WTF `CSRFProtect`). Un formulaire envoyé sans jeton valide, par exemple depuis un autre site, est refusé avec une page « Formulaire expiré » (erreur 400).

- Le jeton est signé avec une **clé secrète générée au premier lancement**. Elle est stockée dans `%LOCALAPPDATA%\NewsProperty\secret_key`, jamais dans le code ni dans le dépôt. Supprimer ce fichier en crée un nouveau au lancement suivant : les pages ouvertes doivent alors être rechargées.
- Le jeton reste valable tant que le navigateur est ouvert : une page de relecture laissée ouverte longtemps reste utilisable.
- En plus, un formulaire dont l'en-tête `Origin` (ou, à défaut, `Referer`) désigne un autre site est refusé (403). Le cookie de session est en `SameSite=Strict`.

## Contrôle de l'en-tête Host (DNS rebinding)

Seules les requêtes adressées à `127.0.0.1:<port>` ou `localhost:<port>` sont acceptées. Toute autre valeur de l'en-tête `Host` reçoit une page « Adresse non autorisée » (erreur 400). C'est ce qui empêche un site malveillant de faire pointer son propre nom de domaine vers `127.0.0.1` pour lire les pages de l'application.

## Échappement automatique

Tous les textes venant des sites (titres, premières lignes, noms, phrases justificatives) sont :

1. convertis en texte brut à la collecte (balises et scripts retirés) ;
2. échappés à l'affichage par Jinja2. Aucun gabarit n'utilise `|safe` sur ces contenus : un titre contenant `<script>` s'affiche comme du texte, sans jamais s'exécuter.

Les liens vers les articles s'ouvrent dans un nouvel onglet avec `rel="noopener noreferrer"`.

## Liens non modifiables, aucun lien inventé

- L'URL d'un article collecté n'est **pas modifiable** : le formulaire de relecture ne la lit jamais, même si un champ « url » est ajouté à la requête.
- Un lien ajouté à la main n'est enregistré qu'après vérification : il doit répondre (code 2xx après redirections) au moment de l'enregistrement.
- **Exception `robots.txt` pour les ajouts manuels.** La collecte automatique respecte toujours `robots.txt`. Mais un lien ajouté à la main est une demande explicite de l'utilisateur : l'application lit alors cette seule page, comme le ferait son navigateur, sans consulter `robots.txt`.
- **« Lien vérifié par l'utilisateur ».** Si le site refuse quand même la lecture automatique (réponse 401, 403, 406, 429 ou 503, typique des protections anti-robot), l'article ne peut être enregistré que si l'utilisateur coche « J'ai ouvert ce lien dans mon navigateur et il affiche bien l'article ». Il saisit alors lui-même le titre et les premières lignes. L'article est marqué en base (`articles.link_verified_by_user = 1`) et la mention est visible sur sa carte.
- Ces exceptions ne s'appliquent jamais à une adresse qui ne commence pas par `http://` ou `https://`, ni à un lien qui ne répond pas (introuvable, erreur réseau) : ils sont toujours refusés.
- Les liens des documents utiles (SharePoint, Google Drive) ne sont jamais appelés : seule la forme `http(s)://` est vérifiée, car ils demandent souvent une connexion.
- Toute URL stockée commence par `http://` ou `https://` : contrôle en Python et contrainte `CHECK` en base.

## Saisies

Toutes les saisies sont validées côté serveur : longueurs maximales, dates valides, nombres entiers positifs, liens http/https. Une saisie refusée laisse la base inchangée et affiche un message en français.
