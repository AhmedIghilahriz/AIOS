"""
AIOS — Module I : Contentieux général (suivi de procédure)

PREMIER JET de règles métier — À VALIDER / AJUSTER par l'avocat.
Toutes les valeurs juridiques (délais, bases légales) sont regroupées dans des
constantes en tête de fichier pour être modifiées facilement.

Conventions du projet :
  • Calculs de délais / état = DÉTERMINISTE (0 % LLM).
  • LLM réservé à la SYNTHÈSE rédigée (via core.orchestrateur).
  • AUCUNE nouvelle table : état dans dossier.metadonnees["contentieux"] → pas de migration.
"""
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from core.models import Dossier


# ── Référentiels (ÉDITABLES) ──────────────────────────────────────────

# Étapes chronologiques d'une procédure civile type.
ETAPES_PROCEDURE: list[str] = [
    "MISE_EN_DEMEURE", "ASSIGNATION", "MISE_EN_ETAT", "CONCLUSIONS",
    "CLOTURE", "PLAIDOIRIE", "DELIBERE", "JUGEMENT", "APPEL",
]

# Voies de recours et délais (jours CALENDAIRES) par juridiction / type de décision.
# ⚠️ Délais de droit commun — À CONFIRMER selon la procédure exacte (point de départ,
#    signification vs notification, jours francs, etc.).
# Format JSON-natif (list de dicts) → directement surchargeable en base via core.regles.
RECOURS_PAR_JURIDICTION: dict[str, list[dict]] = {
    "TJ":  [{"type": "APPEL", "jours": 30, "base": "à compter de la signification (art. 538 CPC)"},
            {"type": "OPPOSITION", "jours": 30, "base": "jugement rendu par défaut (art. 538 CPC)"},
            {"type": "POURVOI", "jours": 60, "base": "pourvoi en cassation (art. 612 CPC)"}],
    "TC":  [{"type": "APPEL", "jours": 30, "base": "à compter de la signification (art. 538 CPC)"},
            {"type": "POURVOI", "jours": 60, "base": "pourvoi en cassation (art. 612 CPC)"}],
    "CPH": [{"type": "APPEL", "jours": 30, "base": "à compter de la notification (art. R.1461-1 C. trav.)"},
            {"type": "POURVOI", "jours": 60, "base": "pourvoi en cassation"}],
    "TA":  [{"type": "APPEL", "jours": 60, "base": "appel devant la cour administrative d'appel (art. R.811-2 CJA)"},
            {"type": "CASSATION", "jours": 60, "base": "pourvoi devant le Conseil d'État (art. R.821-1 CJA)"}],
    "CA":  [{"type": "POURVOI", "jours": 60, "base": "pourvoi en cassation (art. 612 CPC)"}],
    "REFERE": [{"type": "APPEL", "jours": 15, "base": "appel des ordonnances de référé (art. 490 CPC)"}],
}

BASE_NOTE = "[VERIFICATION REQUISE PAR L'AVOCAT] — délais de droit commun, à confirmer."


def _niveau_alerte(jours_restants: int) -> str:
    if jours_restants <= 0:
        return "CRITIQUE"       # délai expiré
    if jours_restants <= 7:
        return "ROUGE"
    if jours_restants <= 15:
        return "PRIORITAIRE"
    return "INFORMATIF"


def etat_contentieux(dossier: Dossier) -> dict:
    """État courant de la procédure (lu depuis dossier.metadonnees)."""
    cont = (dossier.metadonnees or {}).get("contentieux")
    if not cont:
        return {"etape_actuelle": "MISE_EN_DEMEURE", "message": "Aucun contentieux enregistré."}
    return cont


def enregistrer_etape(dossier: Dossier, etape: str, db: Session, infos: dict | None = None) -> dict:
    """Persiste l'étape courante de la procédure dans dossier.metadonnees."""
    etape = (etape or "").upper()
    meta = dict(dossier.metadonnees or {})
    cont = dict(meta.get("contentieux", {}))
    cont.update({
        "etape_actuelle": etape if etape in ETAPES_PROCEDURE else cont.get("etape_actuelle", "MISE_EN_DEMEURE"),
        "maj": datetime.utcnow().isoformat(),
        **(infos or {}),
    })
    meta["contentieux"] = cont
    dossier.metadonnees = meta  # réassignation explicite → SQLAlchemy détecte le changement
    db.commit()
    return cont


def calculer_delais_procedure(juridiction: str, date_decision: datetime | None,
                              regles: dict | None = None) -> dict:
    """
    Calcule TOUTES les voies de recours ouvertes pour une juridiction donnée — DÉTERMINISTE.
    `date_decision` = date de la décision (ou de sa signification/notification).
    `regles` = règles effectives (defaults ← cabinet ← avocat) ; sinon constantes locales.
    """
    table = (regles or {}).get("recours_par_juridiction") or RECOURS_PAR_JURIDICTION
    j = (juridiction or "").upper()
    recours = table.get(j)
    if not recours or not date_decision:
        return {
            "juridiction": juridiction,
            "juridictions_disponibles": list(table),
            "delais": [],
            "message": "Renseigner une juridiction connue + la date de décision/notification.",
        }
    out = []
    for r in recours:
        jours = int(r.get("jours", 0))
        limite = date_decision + timedelta(days=jours)
        restants = (limite - datetime.utcnow()).days
        out.append({
            "type": r.get("type"),
            "delai_jours": jours,
            "echeance": limite.date().isoformat(),
            "jours_restants": restants,
            "niveau_alerte": _niveau_alerte(restants),
            "base_legale": r.get("base"),
        })
    return {
        "juridiction": j,
        "date_decision": date_decision.date().isoformat(),
        "delais": out,
        "note": BASE_NOTE,
    }


async def synthese_strategie(dossier: Dossier, contexte: dict | None = None) -> str:
    """Synthèse stratégique rédigée (LLM via Groq). À relire par l'avocat."""
    from core.orchestrateur import generer_texte_juridique
    return await generer_texte_juridique(
        "Rédige une note de stratégie contentieuse (forces/faiblesses, prochaines étapes, "
        "risques procéduraux et délais à respecter). Préfixe toute appréciation par "
        "[VERIFICATION REQUISE PAR L'AVOCAT]. Concis (12 lignes max).",
        {"dossier": dossier.titre, "etape": etat_contentieux(dossier).get("etape_actuelle"), **(contexte or {})},
        specialite=getattr(dossier.specialite, "value", None),
        max_tokens=700,
    )
