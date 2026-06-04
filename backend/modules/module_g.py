"""
AIOS — Module G : Facturation intelligente
Calculs 100% déterministes (aucun LLM pour les chiffres)
Emails de relance via Resend (GRATUIT)
"""
import resend
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from core.models import Facture, Dossier, Client, StatutFacture
from core.orchestrateur import generer_texte_juridique
import os

resend.api_key = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM", "facturation@votre-domaine.fr")
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "Cabinet Juridique")
EMAIL_AVOCAT = os.getenv("EMAIL_AVOCAT", "avocat@cabinet.fr")

TVA_AVOCAT = 0.20  # Les avocats sont soumis à la TVA à 20%


def generer_numero_facture(cabinet_id: str, sequence: int) -> str:
    """Numéro de facture conforme obligations légales françaises (séquence chronologique)."""
    annee = datetime.now().year
    mois = datetime.now().month
    return f"FAC-{annee}{mois:02d}-{sequence:04d}"


def creer_facture(
    dossier: Dossier,
    montant_ht: float,
    type_honoraires: str,  # fixe | horaire | resultat
    description: str,
    db: Session,
    sequence: int = None
) -> Facture:
    """Crée une facture. DÉTERMINISTE — tous les calculs sont algorithmiques."""
    if sequence is None:
        sequence = db.query(Facture).count() + 1

    facture = Facture(
        numero=generer_numero_facture(dossier.cabinet_id, sequence),
        montant_ht=round(montant_ht, 2),
        taux_tva=TVA_AVOCAT,
        statut=StatutFacture.BROUILLON,
        type_honoraires=type_honoraires,
        description=description,
        date_emission=datetime.utcnow(),
        date_echeance=datetime.utcnow() + timedelta(days=30),
        dossier_id=dossier.id,
        relances=[]
    )

    db.add(facture)
    db.commit()
    db.refresh(facture)
    return facture


# AIOS-FIX: cas 14 — facturation déclenchée par un JALON (événement métier).
# Statuts considérés comme facturables (déterministe). Le montant provient de la convention
# d'honoraires si renseignée dans dossier.metadonnees, sinon facture BROUILLON à 0 € à compléter.
JALONS_FACTURABLES = ("cloture",)


def jalon_declenche_facture(db: Session, dossier: Dossier, nouveau_statut: str):
    """
    Si `nouveau_statut` est un jalon facturable, crée une facture BROUILLON + un rappel J+30.
    Idempotent : ne crée rien si une facture existe déjà pour ce dossier. 0 % LLM (déterministe).
    Retourne la Facture créée ou None.
    """
    if (nouveau_statut or "").lower() not in JALONS_FACTURABLES:
        return None
    if db.query(Facture).filter(Facture.dossier_id == dossier.id).first():
        return None  # déjà facturé / brouillon existant
    meta = dossier.metadonnees or {}
    montant_ht = float(meta.get("honoraires_ht", 0) or 0)
    type_honoraires = str(meta.get("type_honoraires", "fixe") or "fixe")
    facture = creer_facture(
        dossier, montant_ht, type_honoraires,
        f"Honoraires — clôture du dossier {dossier.reference} ({dossier.titre})", db,
    )
    # Rappel de paiement à J+30 (Module F, déterministe).
    try:
        from modules.module_f import creer_deadline_jours
        creer_deadline_jours(dossier, 30, db, motif=f"Rappel paiement facture {facture.numero}",
                             type_delai="rappel_paiement")
    except Exception as e:
        print(f"[jalon facture] rappel J+30 non créé : {e}")
    return facture


def calculer_interets_retard(
    montant_ht: float,
    date_echeance: datetime,
    date_calcul: datetime = None
) -> dict:
    """
    Calcule les intérêts de retard légaux. DÉTERMINISTE — aucun LLM.
    ⚠️ Taux légal 2025 S1 — À METTRE À JOUR chaque semestre
    Source : https://www.legifrance.gouv.fr
    """
    if date_calcul is None:
        date_calcul = datetime.utcnow()

    jours_retard = max(0, (date_calcul - date_echeance).days)
    TAUX_LEGAL_2025_S1 = 0.0591  # 5,91% annuel

    montant_interets = round(
        montant_ht * TAUX_LEGAL_2025_S1 * (jours_retard / 365), 2
    )

    return {
        "jours_retard": jours_retard,
        "taux_applique": TAUX_LEGAL_2025_S1,
        "montant_interets": montant_interets,
        "total_du": round(montant_ht * (1 + TVA_AVOCAT) + montant_interets, 2)
    }


async def gerer_relances_impayes(db: Session):
    """Vérifie toutes les factures impayées. Appelé par Celery Beat chaque jour à 10h."""
    maintenant = datetime.utcnow()

    factures_impayees = db.query(Facture).filter(
        Facture.statut.in_([StatutFacture.ENVOYEE, StatutFacture.RETARD]),
        Facture.date_echeance < maintenant
    ).all()

    for facture in factures_impayees:
        jours_retard = (maintenant - facture.date_echeance).days
        relances_faites = {r["type"] for r in (facture.relances or [])}

        dossier = db.query(Dossier).filter(Dossier.id == facture.dossier_id).first()
        if not dossier:
            continue

        client = db.query(Client).filter(Client.id == dossier.client_id).first()
        client_email = client.email if client and client.email else None
        # Sans email client, on ne peut pas relancer : on alerte directement l'avocat.
        if not client_email:
            if "client_sans_email" not in relances_faites:
                await _alerter_avocat_impaye(facture, dossier, jours_retard)
                _enregistrer_relance(facture, "client_sans_email", db)
            continue

        if jours_retard >= 30 and "relance_1" not in relances_faites:
            await _envoyer_relance_impayee(facture, dossier, jours_retard, 1, client_email)
            _enregistrer_relance(facture, "relance_1", db)

        elif jours_retard >= 45 and "relance_2" not in relances_faites:
            await _envoyer_relance_impayee(facture, dossier, jours_retard, 2, client_email)
            _enregistrer_relance(facture, "relance_2", db)
            facture.statut = StatutFacture.RETARD

        elif jours_retard >= 60 and "mise_en_demeure" not in relances_faites:
            await _envoyer_mise_en_demeure(facture, dossier, jours_retard, client_email)
            _enregistrer_relance(facture, "mise_en_demeure", db)

        elif jours_retard >= 90 and "alerte_avocat" not in relances_faites:
            await _alerter_avocat_impaye(facture, dossier, jours_retard)
            _enregistrer_relance(facture, "alerte_avocat", db)

        db.commit()


def _enregistrer_relance(facture: Facture, type_relance: str, db: Session):
    relances = list(facture.relances or [])
    relances.append({"type": type_relance, "date": datetime.utcnow().isoformat()})
    facture.relances = relances
    db.commit()


async def _envoyer_relance_impayee(facture, dossier, jours_retard, numero, client_email):
    interets = calculer_interets_retard(facture.montant_ht, facture.date_echeance)
    ton = "courtois, rappel amical" if numero == 1 else "ferme, mentionnant les intérêts de retard légaux"

    corps = await generer_texte_juridique(
        f"Rédige un email de relance de paiement {ton}. "
        "Commence directement par la formule de politesse. 150 mots max.",
        {
            "facture_numero": facture.numero,
            "montant_ht": facture.montant_ht,
            "montant_ttc": facture.montant_ttc,
            "date_echeance": facture.date_echeance.strftime("%d/%m/%Y"),
            "jours_retard": jours_retard,
            "interets_courus": interets["montant_interets"],
            "dossier": dossier.titre,
        }
    )

    resend.Emails.send({
        "from": f"{EMAIL_FROM_NAME} <{EMAIL_FROM}>",
        "to": client_email,
        "subject": f"Facture {facture.numero} — {'Rappel' if numero == 1 else 'Rappel urgent'}",
        "html": f"<div style='font-family:Georgia;max-width:600px;'>{corps.replace(chr(10),'<br>')}</div>",
    })


async def _envoyer_mise_en_demeure(facture, dossier, jours_retard, client_email):
    """Mise en demeure formelle générée par Claude."""
    interets = calculer_interets_retard(facture.montant_ht, facture.date_echeance)

    corps = await generer_texte_juridique(
        "Rédige une mise en demeure formelle de payer sous 8 jours. "
        "Mentionne les intérêts de retard et la possibilité de saisir le tribunal. "
        "Format professionnel de courrier avec objet, corps et formule de clôture.",
        {
            "facture_numero": facture.numero,
            "montant_ttc": interets["total_du"],
            "date_echeance_originale": facture.date_echeance.strftime("%d/%m/%Y"),
            "jours_retard": jours_retard,
            "interets": interets["montant_interets"],
            "taux_legal": f"{interets['taux_applique']*100:.2f}%",
        },
        specialite="fiscal"
    )

    resend.Emails.send({
        "from": f"{EMAIL_FROM_NAME} <{EMAIL_FROM}>",
        "to": client_email,
        "subject": f"MISE EN DEMEURE — Facture {facture.numero}",
        "html": f"<div style='font-family:Georgia;max-width:600px;'>{corps.replace(chr(10),'<br>')}</div>",
    })


async def _alerter_avocat_impaye(facture, dossier, jours_retard):
    """Alerte l'avocat pour décision après 90 jours d'impayé."""
    interets = calculer_interets_retard(facture.montant_ht, facture.date_echeance)

    resend.Emails.send({
        "from": f"AIOS <{EMAIL_FROM}>",
        "to": EMAIL_AVOCAT,
        "subject": f"[ACTION REQUISE] Impayé {jours_retard}j — {facture.numero}",
        "html": f"""
        <div style="font-family:Georgia; max-width:600px; border-left:4px solid #DC2626; padding:16px;">
            <h2 style="color:#DC2626;">⚠️ Décision requise — Impayé {jours_retard} jours</h2>
            <p><strong>Facture :</strong> {facture.numero}</p>
            <p><strong>Dossier :</strong> {dossier.titre} ({dossier.reference})</p>
            <p><strong>Montant dû :</strong> {interets['total_du']}€ TTC
               (dont {interets['montant_interets']}€ d'intérêts)</p>
            <p>Actions possibles :</p>
            <ul>
                <li>Injonction de payer (art. 1405 CPC)</li>
                <li>Assignation en référé-provision</li>
                <li>Négociation d'un plan de paiement</li>
            </ul>
        </div>
        """,
    })
