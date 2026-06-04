"""
AIOS — Module F : Délais légaux
CRITIQUE : Aucun LLM pour les calculs. 100% déterministe.
Un délai manqué = forclusion potentielle.
⚠️ FAIRE VALIDER CETTE TABLE PAR UN AVOCAT AVANT DÉPLOIEMENT
"""
from datetime import datetime, timedelta
from typing import Optional
import holidays
from sqlalchemy.orm import Session
from core.models import Deadline, Dossier
import resend
import os

resend.api_key = os.getenv("RESEND_API_KEY")
EMAIL_AVOCAT = os.getenv("EMAIL_AVOCAT", "avocat@cabinet.fr")
EMAIL_FROM = os.getenv("EMAIL_FROM", "alertes@votre-domaine.fr")

# ⚠️ BASE DE DÉLAIS LÉGAUX — À VALIDER PAR UN AVOCAT AVANT MISE EN PRODUCTION
DELAIS_LEGAUX = {
    # Délais d'appel
    "appel_civil": {
        "jours": 30, "type": "calendaires",
        "description": "Délai d'appel en matière civile (art. 538 CPC)"
    },
    "appel_penal": {
        "jours": 10, "type": "calendaires",
        "description": "Délai d'appel en matière pénale (art. 498 CPP)"
    },
    "appel_commercial": {
        "jours": 30, "type": "calendaires",
        "description": "Délai d'appel en matière commerciale"
    },
    "appel_prud_homal": {
        "jours": 30, "type": "calendaires",
        "description": "Délai d'appel prud'homal (art. R.1461-1 CT)"
    },
    # Délais prud'homaux
    "saisine_cph_licenciement": {
        "jours": 365, "type": "calendaires",
        "description": "Délai de saisine CPH pour contestation licenciement — 1 an (art. L.1471-1 CT)"
    },
    "saisine_cph_discrimination": {
        "jours": 1825, "type": "calendaires",
        "description": "Délai de saisine CPH pour discrimination — 5 ans (art. L.1134-5 CT)"
    },
    "saisine_cph_salaires": {
        "jours": 1095, "type": "calendaires",
        "description": "Délai de saisine CPH pour rappel de salaires — 3 ans (art. L.3245-1 CT)"
    },
    # Délais administratifs
    "recours_contentieux_admin": {
        "jours": 60, "type": "calendaires",
        "description": "Délai de recours contentieux administratif — 2 mois (art. R.421-1 CJA)"
    },
    "recours_fiscal": {
        "jours": 60, "type": "calendaires",
        "description": "Délai de recours en matière fiscale — 2 mois"
    },
    "recours_gracieux_admin": {
        "jours": 60, "type": "calendaires",
        "description": "Délai de recours gracieux/hiérarchique — 2 mois"
    },
    # Délais immobiliers
    "retractation_vente_immobiliere": {
        "jours": 10, "type": "calendaires",
        "description": "Délai de rétractation SRU (art. L.271-1 CCH) — 10 jours"
    },
    "droit_preemption_urbain": {
        "jours": 60, "type": "calendaires",
        "description": "Délai de purge DPU — 2 mois"
    },
    "purge_hypotheque": {
        "jours": 30, "type": "calendaires",
        "description": "Délai de purge d'hypothèque légale — 1 mois"
    },
    # Prescription
    "prescription_civile_commune": {
        "jours": 1825, "type": "calendaires",
        "description": "Prescription de droit commun — 5 ans (art. 2224 CC)"
    },
    "prescription_penale_delit": {
        "jours": 2190, "type": "calendaires",
        "description": "Prescription délictuelle — 6 ans (art. 8 CPP)"
    },
    "prescription_penale_crime": {
        "jours": 7300, "type": "calendaires",
        "description": "Prescription criminelle — 20 ans (art. 7 CPP)"
    },
    "prescription_contrat_assurance": {
        "jours": 730, "type": "calendaires",
        "description": "Prescription contrat d'assurance — 2 ans (art. L.114-1 C.ass.)"
    },
    # Délais de cassation
    "pourvoi_cassation_civil": {
        "jours": 60, "type": "calendaires",
        "description": "Délai de pourvoi en cassation civile — 2 mois"
    },
    # ── PHARMACIE / OFFICINE (Module H) ───────────────────────────────
    # NB : délais exprimés en jours calendaires pour l'alerting. La précision
    #      "de date à date" (mois) reste à affiner avec l'avocat avant prod.
    "instruction_ars": {
        "jours": 120, "type": "calendaires",
        "description": "Instruction ARS cession/transfert d'officine — 4 mois ; silence = refus implicite (art. R.5125-10 CSP)"
    },
    "agrement_ordre_pharmaciens": {
        "jours": 30, "type": "calendaires",
        "description": "Agrément Ordre des pharmaciens — nouvel associé (art. R.5125-17 CSP)"
    },
    "appel_decision_ars": {
        "jours": 60, "type": "calendaires",
        "description": "Recours contentieux contre une décision ARS — 2 mois (art. R.421-1 CJA)"
    },
    "retractation_promesse_cession_officine": {
        "jours": 10, "type": "calendaires",
        "description": "Rétractation promesse de cession d'officine — 10 jours (art. 1122 C.civ)"
    },
    "prescription_litige_pharmacie": {
        "jours": 1825, "type": "calendaires",
        "description": "Prescription litige pharmaceutique — 5 ans (art. L.110-4 C.com)"
    },
}


def calculer_echeance(
    date_point_depart: datetime,
    type_delai: str,
    pays: str = "FR"
) -> Optional[datetime]:
    """
    Calcule une date d'échéance en tenant compte des jours fériés français.
    DÉTERMINISTE — pas de LLM.
    """
    if type_delai not in DELAIS_LEGAUX:
        raise ValueError(
            f"Type de délai inconnu: '{type_delai}'. "
            f"Délais disponibles : {list(DELAIS_LEGAUX.keys())}"
        )

    delai = DELAIS_LEGAUX[type_delai]
    date_cible = date_point_depart + timedelta(days=delai["jours"])

    # Si tombe un weekend ou jour férié → proroger au prochain jour ouvré
    jours_feries = holidays.France(years=date_cible.year)

    while date_cible.weekday() >= 5 or date_cible.date() in jours_feries:
        date_cible += timedelta(days=1)

    return date_cible


def creer_deadline(
    dossier: Dossier,
    type_delai: str,
    date_point_depart: datetime,
    db: Session,
    description_custom: str = None
) -> Deadline:
    """Crée une deadline avec calcul automatique."""
    date_echeance = calculer_echeance(date_point_depart, type_delai)
    infos = DELAIS_LEGAUX[type_delai]

    deadline = Deadline(
        titre=infos["description"],
        description=description_custom or infos["description"],
        date_echeance=date_echeance,
        type_delai=type_delai,
        dossier_id=dossier.id
    )
    db.add(deadline)
    db.commit()
    return deadline


# AIOS-FIX: cas 8 — création d'une deadline à partir d'un NOMBRE DE JOURS (et non d'un type
# prédéfini), pour les délais détectés automatiquement dans un email. 100 % déterministe (0 % LLM).
def creer_deadline_jours(
    dossier: Dossier,
    jours: int,
    db: Session,
    motif: str = "Délai signalé dans un email",
    point_depart: datetime = None,
    type_delai: str = "delai_detecte_email",
) -> Deadline:
    """Crée une deadline = point_depart + N jours (week-ends/fériés reportés au jour ouvré suivant)."""
    depart = point_depart or datetime.utcnow()
    date_cible = depart + timedelta(days=int(jours))
    jours_feries = holidays.France(years=date_cible.year)
    while date_cible.weekday() >= 5 or date_cible.date() in jours_feries:
        date_cible += timedelta(days=1)
    deadline = Deadline(
        titre=f"[URGENT] {motif}"[:200],
        description=(f"Délai détecté automatiquement à l'analyse d'un email "
                     f"(≈ {jours} jour(s)) — {motif}. [VERIFICATION REQUISE PAR L'AVOCAT]"),
        date_echeance=date_cible,
        type_delai=type_delai,
        dossier_id=dossier.id,
    )
    db.add(deadline)
    db.commit()
    return deadline


async def verifier_et_alerter(db: Session):
    """
    Vérifie toutes les deadlines et envoie les alertes.
    Appelé par Celery Beat toutes les heures.
    """
    maintenant = datetime.utcnow()
    deadlines = db.query(Deadline).filter(Deadline.acquitte == False).all()

    niveaux_alerte = [
        (30, "info",     "#3B82F6", "J-30 : Échéance dans 30 jours"),
        (14, "warning",  "#F59E0B", "J-14 : Échéance dans 14 jours ⚠️"),
        (7,  "high",     "#EF4444", "J-7 : ÉCHÉANCE DANS 7 JOURS 🔴"),
        (1,  "critical", "#7C3AED", "J-1 : ÉCHÉANCE DEMAIN — ACTION REQUISE 🚨"),
    ]

    for deadline in deadlines:
        jours_restants = (deadline.date_echeance - maintenant).days

        for seuil, niveau, couleur, message in niveaux_alerte:
            alerte_key = f"j-{seuil}"
            if (jours_restants <= seuil and
                    alerte_key not in (deadline.alertes_envoyees or [])):

                await _envoyer_alerte(deadline, niveau, couleur, message, jours_restants)

                alertes = list(deadline.alertes_envoyees or [])
                alertes.append(alerte_key)
                deadline.alertes_envoyees = alertes
                db.commit()


async def _envoyer_alerte(deadline, niveau, couleur, message, jours_restants):
    """Envoie l'alerte par email via Resend (gratuit)."""
    resend.Emails.send({
        "from": f"AIOS Alertes <{EMAIL_FROM}>",
        "to": EMAIL_AVOCAT,
        "subject": f"[AIOS] {message} — {deadline.titre}",
        "html": f"""
        <div style="border-left: 4px solid {couleur}; padding: 16px; font-family: Georgia;">
            <h2 style="color: {couleur}; margin: 0 0 12px;">{message}</h2>
            <table style="border-collapse: collapse;">
                <tr><td style="padding: 4px 8px; color: #666;">Dossier</td>
                    <td style="padding: 4px 8px;"><strong>{deadline.dossier_id}</strong></td></tr>
                <tr><td style="padding: 4px 8px; color: #666;">Échéance</td>
                    <td style="padding: 4px 8px;"><strong>{deadline.date_echeance.strftime('%d/%m/%Y')}</strong></td></tr>
                <tr><td style="padding: 4px 8px; color: #666;">Jours restants</td>
                    <td style="padding: 4px 8px;"><strong style="color:{couleur}">{jours_restants} jour(s)</strong></td></tr>
                <tr><td style="padding: 4px 8px; color: #666;">Type</td>
                    <td style="padding: 4px 8px;">{deadline.description}</td></tr>
            </table>
        </div>
        """
    })
