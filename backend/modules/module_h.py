"""
AIOS — Module H : Cession & acquisition d'officine de pharmacie

Trois briques, conformes à la contrainte « calculs déterministes, LLM seulement
pour la synthèse » :
  H.1  Due diligence officine     → check-list + complétude (100% déterministe)
       Valorisation indicative    → multiple du CA HT       (100% déterministe)
  H.2  Suivi instruction ARS      → délai 4 mois, silence = refus implicite
       (s'appuie sur le moteur déterministe du Module F)

L'état ARS est stocké dans dossier.metadonnees["pharmacie"] : AUCUNE nouvelle
table, donc aucune migration de base de données.
"""
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from core.models import Dossier
from modules import module_f


# ── H.1 — Check-list de due diligence officine ────────────────────────
# Référentiel exhaustif (cf. cahier des charges H.1).
CHECKLIST_DUE_DILIGENCE: dict[str, list[str]] = {
    "juridiques": [
        "Arrêté ARS autorisation d'ouverture (original + copie certifiée)",
        "Licence débit de boissons si applicable",
        "Diplômes pharmacien titulaire + inscription Ordre Section A",
        "K-bis SELARL ou extrait RCS (< 3 mois)",
        "Statuts SEL à jour + tableau associés + registre des décisions",
        "Baux commerciaux original + avenants + état des lieux",
    ],
    "financiers": [
        "3 derniers bilans certifiés expert-comptable",
        "Liasse fiscale 3 ans (2058 A/B/C minimum)",
        "Relevés CA CPAM 12 mois (état 2035 ou équivalent)",
        "Contrats grossistes (OCP, Phoenix, Alliance Healthcare)",
        "Contrat maintenance LGO (Winpharma, Lgpi, Eolis…)",
    ],
    "sociaux": [
        "Contrats de travail pharmaciens adjoints + préparateurs",
        "Convention collective pharmaceutique (IDCC 1555)",
        "DUERP + registre du personnel 3 ans",
    ],
}

# Coefficients de valorisation indicatifs 2024-2025 (multiple du CA HT).
COEFFICIENTS_VALORISATION: dict[str, tuple[float, float]] = {
    "urbaine":  (0.60, 0.80),
    "rurale":   (0.80, 1.10),
    "monopole": (0.80, 1.10),
}

# Étapes chronologiques d'un dossier ARS (cf. H.2).
ETAPES_ARS = ["CONSTITUTION", "DEPOT", "INSTRUCTION", "DECISION"]

DUREE_INSTRUCTION_ARS_JOURS = 120  # 4 mois — art. R.5125-10 CSP
BASE_LEGALE_ARS = "art. R.5125-10 CSP"


def liste_checklist() -> list[dict]:
    """Retourne la check-list canonique, à plat, avec un id stable par pièce."""
    items: list[dict] = []
    i = 1
    for categorie, labels in CHECKLIST_DUE_DILIGENCE.items():
        for label in labels:
            items.append({
                "id": f"DD{i:03d}",
                "categorie": categorie,
                "label": label,
                "obligatoire": True,
            })
            i += 1
    return items


def evaluer_due_diligence(documents_recus: list[str]) -> dict:
    """
    Évalue la complétude de la due diligence (déterministe, 0% LLM).
    `documents_recus` = liste des `label` (ou `id`) de pièces effectivement reçues.
    """
    recus = {d.strip().lower() for d in (documents_recus or [])}
    items = liste_checklist()
    for it in items:
        recu = it["label"].lower() in recus or it["id"].lower() in recus
        it["statut"] = "RECU" if recu else "MANQUANT"

    total = len(items)
    nb_recus = sum(1 for it in items if it["statut"] == "RECU")
    manquants = [it["label"] for it in items if it["statut"] == "MANQUANT"]
    completude = round(100 * nb_recus / total) if total else 0

    if completude >= 85:
        recommandation = "POURSUIVRE"
    elif completude >= 50:
        recommandation = "DILIGENCE_COMPLEMENTAIRE"
    else:
        recommandation = "ABANDON"

    return {
        "completude_pct": completude,
        "nb_recus": nb_recus,
        "nb_total": total,
        "documents": items,
        "points_bloquants": manquants[:10],
        "recommandation": recommandation,
    }


def calculer_valorisation(ca_ht: float, type_officine: str = "urbaine") -> dict:
    """Valorisation indicative par multiple du CA HT (déterministe, 0% LLM)."""
    type_officine = (type_officine or "urbaine").lower()
    coef_bas, coef_haut = COEFFICIENTS_VALORISATION.get(
        type_officine, COEFFICIENTS_VALORISATION["urbaine"]
    )
    ca_ht = float(ca_ht or 0)
    return {
        "ca_ht": round(ca_ht, 2),
        "type_officine": type_officine,
        "coefficient_bas": coef_bas,
        "coefficient_haut": coef_haut,
        "fourchette_bas": round(ca_ht * coef_bas, 2),
        "fourchette_haut": round(ca_ht * coef_haut, 2),
        "note": (
            "Valorisation indicative (multiple du CA HT). Retraitements obligatoires : "
            "masse salariale, loyer de marché, CA CPAM réel. "
            "[VERIFICATION REQUISE PAR L'AVOCAT]"
        ),
    }


# ── H.2 — Suivi de l'instruction ARS ──────────────────────────────────

def suivi_instruction_ars(
    date_depot: datetime | None,
    etape: str = "CONSTITUTION",
    pieces_manquantes: list[str] | None = None,
) -> dict:
    """
    Calcule l'état de l'instruction ARS (déterministe, 0% LLM).
    Silence de l'ARS après 4 mois = refus implicite (art. R.5125-10 CSP).
    """
    pieces_manquantes = pieces_manquantes or []
    etape = (etape or "CONSTITUTION").upper()
    base = {
        "etape_actuelle": etape if etape in ETAPES_ARS else "CONSTITUTION",
        "pieces_manquantes": pieces_manquantes,
        "base_legale": BASE_LEGALE_ARS,
    }

    if not date_depot:
        base.update({
            "date_depot": None,
            "date_limite_decision": None,
            "jours_restants": None,
            "alerte_2_mois": None,
            "risque_silence": "NON",
            "niveau_alerte": "INFORMATIF",
        })
        return base

    date_limite = date_depot + timedelta(days=DUREE_INSTRUCTION_ARS_JOURS)
    jours_restants = (date_limite - datetime.utcnow()).days

    if jours_restants <= 0:
        niveau = "CRITIQUE"        # délai écoulé → silence vaut refus
        risque = "OUI"
    elif jours_restants <= 30:
        niveau = "ROUGE"
        risque = "OUI"
    elif jours_restants <= 60:
        niveau = "PRIORITAIRE"     # alerte 2 mois prévue au CDC
        risque = "NON"
    else:
        niveau = "INFORMATIF"
        risque = "NON"

    base.update({
        "date_depot": date_depot.date().isoformat(),
        "date_limite_decision": date_limite.date().isoformat(),
        "jours_restants": jours_restants,
        "alerte_2_mois": (date_depot + timedelta(days=60)).date().isoformat(),
        "risque_silence": risque,
        "niveau_alerte": niveau,
    })
    return base


def enregistrer_depot_ars(
    dossier: Dossier,
    date_depot: datetime,
    db: Session,
    pieces_manquantes: list[str] | None = None,
) -> dict:
    """
    Enregistre le dépôt ARS dans dossier.metadonnees et crée la deadline des 4 mois
    via le Module F (alertes J-30/J-14/J-7/J-1 gérées par Celery).
    """
    suivi = suivi_instruction_ars(date_depot, etape="DEPOT", pieces_manquantes=pieces_manquantes)

    meta = dict(dossier.metadonnees or {})
    pharma = dict(meta.get("pharmacie", {}))
    pharma["ars"] = {**suivi, "date_depot_iso": date_depot.isoformat()}
    meta["pharmacie"] = pharma
    dossier.metadonnees = meta  # réassignation explicite → SQLAlchemy détecte le changement

    # Crée la deadline déterministe correspondante (idempotent côté Module F sinon doublon)
    try:
        module_f.creer_deadline(dossier, "instruction_ars", date_depot, db)
    except Exception as e:  # ex. type inconnu — ne bloque pas l'enregistrement
        print(f"[Module H] Deadline ARS non créée : {e}")

    db.commit()
    return suivi


def etat_ars(dossier: Dossier) -> dict:
    """État ARS courant, avec `jours_restants` recalculé en direct."""
    ars = (dossier.metadonnees or {}).get("pharmacie", {}).get("ars")
    if not ars:
        return {
            "etape_actuelle": "CONSTITUTION",
            "message": "Aucun dépôt ARS enregistré pour ce dossier.",
        }
    date_iso = ars.get("date_depot_iso")
    if not date_iso:
        return ars
    try:
        date_depot = datetime.fromisoformat(date_iso)
    except ValueError:
        return ars
    return suivi_instruction_ars(
        date_depot,
        etape=ars.get("etape_actuelle", "DEPOT"),
        pieces_manquantes=ars.get("pieces_manquantes", []),
    )


# ── Synthèse rédigée (LLM — optionnelle) ──────────────────────────────

async def synthese_due_diligence(dossier: Dossier, evaluation: dict, valorisation: dict) -> str:
    """Synthèse rédigée de la due diligence (LLM, gratuit via Groq). Optionnel."""
    from core.orchestrateur import generer_texte_juridique
    return await generer_texte_juridique(
        "Rédige une synthèse de due diligence pour une cession d'officine : "
        "complétude documentaire, points bloquants ARS, fourchette de valorisation, "
        "et une recommandation. Préfixe toute appréciation juridique par "
        "[VERIFICATION REQUISE PAR L'AVOCAT]. Sois concis (10 lignes max).",
        {
            "dossier": dossier.titre,
            "completude_pct": evaluation.get("completude_pct"),
            "points_bloquants": evaluation.get("points_bloquants"),
            "recommandation": evaluation.get("recommandation"),
            "valorisation": valorisation,
        },
        specialite="affaires",
        max_tokens=600,
    )
