"""
AIOS — Module J : Contentieux des pharmaciens (spécialisé officine)

PREMIER JET de règles métier — À VALIDER / AJUSTER par l'avocat.
Toutes les valeurs juridiques sont en constantes éditables en tête de fichier.

Couvre : clause de non-concurrence (cession / contrat d'exercice), recours ARS,
indu / sanction CPAM, discipline ordinale (Section A).

Conventions :
  • Analyse de validité / délais = DÉTERMINISTE (0 % LLM).
  • LLM réservé à la SYNTHÈSE rédigée.
  • AUCUNE nouvelle table : état dans dossier.metadonnees["contentieux_pharma"].
  • Le SUIVI d'instruction ARS (silence = refus à 4 mois) est dans le Module H ;
    ici on traite le CONTENTIEUX (recours contre la décision).
"""
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from core.models import Dossier


# ── Référentiels (ÉDITABLES) ──────────────────────────────────────────

TYPES_CONTENTIEUX_PHARMA: list[str] = [
    "CLAUSE_NON_CONCURRENCE", "RECOURS_ARS", "INDU_CPAM", "SANCTION_ORDRE", "DECONVENTIONNEMENT",
]

# Critères CUMULATIFS de validité d'une clause de non-concurrence (jurisprudence).
# La contrepartie financière n'est exigée que pour un CONTRAT DE TRAVAIL (pharmacien
# adjoint) — pas pour une clause de cession (vendeur), où le prix tient lieu de contrepartie.
CRITERES_BASE: list[dict] = [
    {"id": "limite_temps",     "label": "Limitée dans le temps (durée raisonnable)"},
    {"id": "limite_espace",    "label": "Limitée dans l'espace (zone géographique précise)"},
    {"id": "limite_activite",  "label": "Limitée à l'activité officinale concernée"},
    {"id": "interet_legitime", "label": "Justifiée par un intérêt légitime à protéger"},
    {"id": "proportionnee",    "label": "Proportionnée (n'interdit pas tout exercice)"},
]
CRITERE_CONTREPARTIE = {"id": "contrepartie_financiere", "label": "Contrepartie financière (contrat de travail)"}

# Délais de recours (jours CALENDAIRES). ⚠️ À CONFIRMER selon le cas.
DELAIS_RECOURS: dict[str, dict] = {
    "RECOURS_ARS": {
        "jours": 60, "instance": "Tribunal administratif",
        "base": "recours pour excès de pouvoir, 2 mois (art. R.421-1 CJA)",
    },
    "RECOURS_GRACIEUX_ARS": {
        "jours": 60, "instance": "ARS (recours gracieux/hiérarchique)",
        "base": "recours gracieux conservant le délai contentieux, 2 mois",
    },
    "INDU_CPAM": {
        "jours": 60, "instance": "Commission de Recours Amiable (CRA)",
        "base": "saisine de la CRA, 2 mois (art. R.142-1 CSS)",
    },
    "SANCTION_ORDRE": {
        "jours": 30, "instance": "Chambre de discipline du Conseil national",
        "base": "appel de la décision disciplinaire, 30 jours",
    },
    "DECONVENTIONNEMENT": {
        "jours": 30, "instance": "Procédure conventionnelle CPAM",
        "base": "délai variable selon la convention — À PRÉCISER",
    },
}

NOTE_VERIF = "[VERIFICATION REQUISE PAR L'AVOCAT]"


def _niveau_alerte(jours_restants: int) -> str:
    if jours_restants <= 0:
        return "CRITIQUE"
    if jours_restants <= 10:
        return "ROUGE"
    if jours_restants <= 20:
        return "PRIORITAIRE"
    return "INFORMATIF"


def etat_contentieux_pharma(dossier: Dossier) -> dict:
    """État courant du contentieux pharmacien (lu depuis dossier.metadonnees)."""
    cont = (dossier.metadonnees or {}).get("contentieux_pharma")
    if not cont:
        return {"type": None, "message": "Aucun contentieux pharmacien enregistré."}
    return cont


def criteres_non_concurrence(type_clause: str = "cession", regles: dict | None = None) -> list[dict]:
    """Liste des critères applicables selon le type de clause (defaults ou règles surchargées)."""
    base = (regles or {}).get("criteres_base") or CRITERES_BASE
    contrepartie = (regles or {}).get("critere_contrepartie") or CRITERE_CONTREPARTIE
    crit = list(base)
    if (type_clause or "").lower() == "travail":
        crit = crit + [contrepartie]
    return crit


def analyser_clause_non_concurrence(criteres_remplis: list[str], type_clause: str = "cession",
                                    regles: dict | None = None) -> dict:
    """
    Évalue la validité d'une clause de non-concurrence — DÉTERMINISTE.
    `type_clause` : "cession" (vendeur) ou "travail" (adjoint salarié, contrepartie requise).
    `regles` = règles effectives (defaults ← cabinet ← avocat) ; sinon constantes locales.
    """
    requis = criteres_non_concurrence(type_clause, regles)
    remplis = {c.strip().lower() for c in (criteres_remplis or [])}
    details = [{**c, "rempli": c["id"] in remplis} for c in requis]
    nb_ok = sum(1 for c in details if c["rempli"])
    total = len(details)
    valable = nb_ok == total  # critères cumulatifs : tous requis
    return {
        "type_clause": (type_clause or "cession").lower(),
        "criteres": details,
        "nb_remplis": nb_ok,
        "nb_total": total,
        "appreciation": "VALABLE" if valable else "CONTESTABLE",
        "note": f"{NOTE_VERIF} — critères cumulatifs, appréciation in concreto par le juge.",
    }


def calculer_delai_recours(type_contentieux: str, date_notification: datetime | None,
                           regles: dict | None = None) -> dict:
    """Délai de recours restant — DÉTERMINISTE. `regles` = règles effectives ou defaults."""
    table = (regles or {}).get("delais_recours") or DELAIS_RECOURS
    t = (type_contentieux or "").upper()
    spec = table.get(t)
    if not spec or not date_notification:
        return {
            "type": t,
            "types_disponibles": list(table),
            "message": "Renseigner un type connu + la date de notification.",
        }
    limite = date_notification + timedelta(days=spec["jours"])
    restants = (limite - datetime.utcnow()).days
    return {
        "type": t,
        "instance": spec["instance"],
        "date_notification": date_notification.date().isoformat(),
        "date_limite_recours": limite.date().isoformat(),
        "delai_jours": spec["jours"],
        "jours_restants": restants,
        "niveau_alerte": _niveau_alerte(restants),
        "base_legale": spec["base"],
        "note": NOTE_VERIF,
    }


def enregistrer_contentieux(dossier: Dossier, type_contentieux: str, db: Session, infos: dict | None = None) -> dict:
    """Persiste le contentieux pharmacien dans dossier.metadonnees."""
    t = (type_contentieux or "").upper()
    meta = dict(dossier.metadonnees or {})
    cont = dict(meta.get("contentieux_pharma", {}))
    cont.update({"type": t, "maj": datetime.utcnow().isoformat(), **(infos or {})})
    meta["contentieux_pharma"] = cont
    dossier.metadonnees = meta
    db.commit()
    return cont


async def synthese_contentieux_pharma(dossier: Dossier, contexte: dict | None = None) -> str:
    """Synthèse rédigée du contentieux pharmacien (LLM). À relire par l'avocat."""
    from core.orchestrateur import generer_texte_juridique
    return await generer_texte_juridique(
        "Rédige une note d'analyse d'un contentieux propre à une officine "
        "(non-concurrence, recours ARS, indu CPAM ou discipline ordinale) : enjeux, "
        "chances de succès, délais à respecter. Préfixe toute appréciation par "
        "[VERIFICATION REQUISE PAR L'AVOCAT]. Concis (12 lignes max).",
        {"dossier": dossier.titre, "contentieux": etat_contentieux_pharma(dossier), **(contexte or {})},
        specialite="affaires",
        max_tokens=700,
    )
