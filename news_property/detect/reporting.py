"""Mise en forme des résultats de détection : bilan texte et rapport de fiabilité."""

from __future__ import annotations

from datetime import datetime

from news_property.detect.runner import DetectionReport
from news_property.detect.text import article_sentences
from news_property.detect.transaction import TransactionDetector
from news_property.models import Article

TRANSACTION_LABEL = "Transaction"


def format_sqm(value: int | None) -> str:
    """« 40 000 m² », ou tiret si inconnu."""
    return f"{value:,}".replace(",", " ") + " m²" if value is not None else "-"


def format_summary(report: DetectionReport, examples: int = 5) -> list[str]:
    """Bilan lisible en français (lignes de texte) avec quelques exemples justifiés."""
    lines = [
        f"Détection {report.month}{' (simulation)' if report.dry_run else ''} : "
        f"{report.articles_analysed} article(s) analysé(s)",
        f"  Transactions détectées              : {len(report.transactions)}",
        f"  Repères modifiés à la main (gardés) : {report.protected_transactions}",
        f"  Surfaces trouvées (m²)              : {report.summary()['surfaces']}",
    ]
    shown = 0
    if examples > 0 and report.transactions:
        lines += ["", "Exemples :"]
    for finding in report.transactions:
        if shown >= examples:
            break
        lines += [
            f"  * Transaction : {finding.article.title}",
            f"    Phrase : « {finding.match.sentence} »",
            f"    Termes : {', '.join(finding.match.terms)}",
            f"    Surface : {format_sqm(finding.surface_sqm)}",
            f"    Lien : {finding.article.url}",
        ]
        shown += 1
    return lines


def _md(text: str) -> str:
    """Texte sûr pour une ligne Markdown."""
    return " ".join(str(text).split()).replace("|", "\\|")


def _article_line(article: Article) -> str:
    """Titre, source et lien d'un article."""
    return f"[{_md(article.title)}]({article.url}) - {_md(article.source_name)}"


def render_evaluation(
    report: DetectionReport, detector: TransactionDetector, articles: list[Article], now: datetime
) -> str:
    """Rapport Markdown de fiabilité : chaque détection avec sa preuve et une case
    « juste / faux » à cocher, puis les articles non marqués contenant un terme (oublis)."""
    marked = {f.article.id for f in report.transactions}
    near_misses: list[tuple[Article, list[tuple[str, list[str]]]]] = []
    for article in articles:
        if article.id in marked:
            continue
        hits = [
            (sentence, terms)
            for sentence in article_sentences(article.title, article.excerpt, article.body_text)
            if (terms := detector.terms_in(sentence))
        ]
        if hits:
            near_misses.append((article, hits))

    lines = [
        f"# Fiabilité de la détection - {report.month}",
        "",
        f"Généré le {now.astimezone():%d/%m/%Y à %H:%M} à partir des articles en base "
        "(simulation : la base n'est pas modifiée).",
        "",
        "## Comment remplir ce rapport",
        "",
        "Pour chaque détection, ouvrez le lien si besoin, puis cochez **juste** ou **faux** "
        "(remplacez `[ ]` par `[x]`). Pour les articles non marqués, cochez **oubli** si "
        "l'article aurait dû être marqué.",
        "",
        "- Précision du repère = nombre de « juste » ÷ nombre de transactions détectées.",
        "- Oublis = nombre d'« oubli » cochés dans la dernière partie.",
        "- Repère à garder si la précision est élevée (par exemple ≥ 80 %) et les oublis rares.",
        "",
        "## Synthèse",
        "",
        "| Mesure | Nombre |",
        "|---|---|",
        f"| Articles analysés | {report.articles_analysed} |",
        f"| Transactions détectées | {len(report.transactions)} |",
        f"| Articles non marqués contenant un terme | {len(near_misses)} |",
        f"| Surfaces trouvées | {report.summary()['surfaces']} |",
        "",
        f"## 1. Transactions détectées ({len(report.transactions)})",
    ]
    if not report.transactions:
        lines += ["", "_Aucune._"]
    for index, finding in enumerate(report.transactions, start=1):
        kind = (
            "expression forte"
            if finding.match.kind == "strong"
            else "terme d'action + terme d'actif"
        )
        lines += [
            "",
            f"### T{index}. {_md(finding.article.title)}",
            "",
            f"- Article : {_article_line(finding.article)}",
            f"- Phrase : « {_md(finding.match.sentence)} »",
            f"- Termes : {', '.join(finding.match.terms)} ({kind})",
            f"- Surface trouvée : {format_sqm(finding.surface_sqm)}",
            "- Verdict proposé : **transaction**",
            "- [ ] juste  - [ ] faux",
        ]

    lines += ["", f"## 2. Surfaces trouvées hors transaction ({len(report.surfaces)})"]
    if not report.surfaces:
        lines += ["", "_Aucune._"]
    for finding in report.surfaces:
        lines += [
            "",
            f"- {_article_line(finding.article)} : {format_sqm(finding.surface_sqm)}",
            "  - [ ] juste  - [ ] faux (ce nombre n'est pas la surface de l'actif)",
        ]

    lines += [
        "",
        f"## 3. Articles non marqués contenant un terme de transaction ({len(near_misses)})",
        "",
        "Ces articles contiennent au moins un terme des listes, sans remplir la règle "
        "(par exemple un terme d'action sans terme d'actif dans la même phrase).",
    ]
    if not near_misses:
        lines += ["", "_Aucun._"]
    for article, hits in near_misses:
        lines += ["", f"- {_article_line(article)}"]
        for sentence, terms in hits[:3]:
            lines.append(f"  - « {_md(sentence)} » - termes : {', '.join(terms)}")
        lines.append("  - [ ] oubli (aurait dû être marqué)")
    return "\n".join(lines) + "\n"
