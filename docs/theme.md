# Thème de la newsletter : les couleurs du logo Carrefour Property

Toute la charte graphique de la newsletter tient dans un seul fichier :
**`config/theme.yaml`**. Les gabarits (`news_property/templates/newsletter/_*.html`) et les
feuilles de style n'utilisent que ces valeurs : il n'y a rien d'autre à modifier pour
changer l'apparence.

## D'où viennent les valeurs

Aucune charte Carrefour Property n'a été fournie. Le thème reprend donc :

- la **structure** de la charte de référence (celle de News Franchise, Carmila 2023) :
  bandeau de couleur avec logo en réserve blanche et motif de formes géométriques (arche,
  nœud, heptagone), rubriques numérotées « 01 » dans une arche, encadrés clairs, titres en
  capitales avec filet court, logo couleurs en bas et centré ;
- les **couleurs du logo Carrefour Property** fourni : le violet `#8D2562` est la couleur
  exacte du logo (mesurée sur l'image). Les autres couleurs sont des tons de ce même violet
  et des gris neutres. Rien d'autre n'a été inventé.

| Clé | Valeur | Origine |
| --- | --- | --- |
| `colors.primary` | `#8D2562` | violet du logo (bandeau, filets, grands éléments) |
| `colors.secondary` | `#5E1741` | ton foncé du violet (titres, liens, chiffres de la Une) |
| `colors.text` | `#3F3F3F` | gris foncé neutre (texte courant) |
| `colors.muted` | `#666666` | gris moyen neutre (sources, dates, légendes) |
| `colors.background` | `#FFFFFF` | blanc (impression et e-mail) |
| `colors.surface` | `#F7ECF2` | ton très clair du violet (encadrés) |
| `colors.border` | `#E6CFDC` | ton clair du violet (séparateurs) |
| `colors.highlight` / `highlight_text` | `#E9C6DA` / `#5E1741` | badge « Transaction » |
| `section_colors.*` | tons du violet | accent de la Une et de chaque rubrique |

Si la communication fournit une charte Carrefour Property, remplacez ces valeurs par
celles de la charte : le fichier est commenté et l'écran **Thème** vérifie le résultat.

### Contrastes

Le blanc sur le violet du logo atteint 8,2:1 : le bandeau peut porter du texte courant.
Les titres et liens en violet foncé font 12,6:1 sur blanc, le texte courant 10,4:1. Le
turquoise, l'orange et le jaune de la charte de référence ne sont pas repris : ils ne font
pas partie des tons du logo.

### Polices

Pas de police en ligne : Gmail et l'impression doivent rendre la même chose sur tous les
postes. Le thème utilise **Century Gothic** (installée avec Microsoft Office) pour le texte
et les titres, et **Bahnschrift Condensed** (Windows 10 et 11) pour les chiffres et petits
libellés. Sur un poste sans ces polices, les replis prennent le relais (URW Gothic,
Trebuchet MS, Arial ; Arial Narrow).

### Logos

- `config/assets/logo-carrefour-property.png` : logo couleurs (violet) sur fond
  transparent, pour les fonds blancs (pied de page).
- `config/assets/logo-carrefour-property-blanc.png` : réserve blanche (texte et chevron
  blancs sur fond transparent), uniquement sur le violet du bandeau.

Les deux fichiers sont dérivés de l'image du logo fournie. Si la communication fournit les
fichiers officiels, déposez-les dans `config/assets/` sous le même nom (ou changez `logo` et
`logo_reverse`). Taille d'affichage : 170 px de large dans le bandeau, 120 px en pied de page.

## Valeurs modifiables

| Clé | Rôle | Format |
| --- | --- | --- |
| `name` | Nom affiché dans le bandeau | texte, 80 caractères au plus |
| `tagline` | Phrase sous le bandeau | texte |
| `colors.primary` | Bandeau, filets, grands éléments | `#RRGGBB` |
| `colors.secondary` | Titres, liens, chiffres de la Une | `#RRGGBB` |
| `colors.text` | Texte courant | `#RRGGBB` |
| `colors.muted` | Sources, dates, légendes | `#RRGGBB` |
| `colors.background` | Fond ; **à laisser blanc** (`#FFFFFF`) ; aussi la couleur du texte du bandeau | `#RRGGBB` |
| `colors.surface` | Fond des encadrés (Une, calendrier, documents) | `#RRGGBB` |
| `colors.border` | Séparateurs fins | `#RRGGBB` |
| `colors.highlight` / `highlight_text` | Fond et texte du badge « Transaction » | `#RRGGBB` |
| `section_colors.*` | Couleur d'accent de la Une et de chaque rubrique | `#RRGGBB` |
| `fonts.body` / `headings` / `labels` | Texte, titres, chiffres et petits libellés | polices **système**, liste terminée par `sans-serif`, `serif` ou `monospace` |
| `logo` | Logo couleurs, pour fond blanc (pied de page) | PNG ou JPEG, chemin relatif au dossier du projet |
| `logo_reverse` | Logo en réserve blanche, pour le bandeau | PNG ou JPEG (de préférence PNG transparent) |
| `logo_alt` | Texte alternatif des logos | texte |
| `footer_text` | Texte du pied de page | texte, 300 caractères au plus |
| `section_titles.*` | Titre de la Une et des 6 rubriques | texte, 80 caractères au plus |

Notes :

- **Couleurs** : toujours 6 chiffres hexadécimaux précédés de `#`, entre guillemets
  (`"#8D2562"`). Les formats courts (`#E00`) ou les noms (`violet`) sont refusés.
- **Polices** : uniquement des polices installées sur les postes. Les noms à espaces se
  mettent entre apostrophes : `"'Century Gothic', Arial, sans-serif"`.

## Vérifier après une modification

1. Enregistrez `config/theme.yaml` (encodage UTF-8).
2. Ouvrez l'écran **Thème** depuis la page d'accueil (inutile de relancer l'application :
   le fichier est relu à chaque affichage).
3. En cas d'erreur, le message indique la clé fautive et la valeur lue, par exemple :
   « la couleur « colors.primary » doit être au format #RRGGBB (ex. #8D2562) ; valeur
   lue : '#12' ». Une erreur de syntaxe YAML indique la ligne.
4. Les contrastes insuffisants donnent un avertissement (non bloquant) : 4,5:1 minimum
   pour le texte courant, 3:1 pour le grand texte (nom et mois du bandeau, numéros des
   rubriques).

Tant que le thème est invalide, le reste de l'application reste utilisable avec les
couleurs par défaut ; l'aperçu et les exports, eux, affichent l'erreur.
