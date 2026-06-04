"""
AIOS — Jeu de données de DÉMO.

Crée (de façon idempotente) :
  • un dossier « Cession officine » (Pharmacie) rattaché à votre avocat,
  • des délais variés pour illustrer la timeline F (J-30 / J-14 / J-7 / J-1, échu, acquitté),
  • un dépôt ARS réel (Module H) → délai d'instruction 4 mois + panneau 🏥 alimenté,
  • quelques documents (2 reçus / 4 → règle des 50 % du Module D),
  • une facture EN RETARD (40 j) pour tester les relances du Module G.

Usage (depuis le dossier backend, avec le venv) :
    python scripts/seed_demo.py
    python scripts/seed_demo.py --avocat-id <ID>      # forcer l'avocat
    python scripts/seed_demo.py --email vous@mail.fr  # email du client (destinataire des relances)

Réexécutable : il efface puis recrée le dossier de référence DEMO-PHARMA-001.
"""
import os
import sys
import argparse
from datetime import datetime, timedelta

# Rendre les imports du backend résolvables quel que soit le cwd.
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# Console Windows en cp1252 : forcer UTF-8 pour les emojis/accents des messages.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from core.database import SessionLocal
from core.models import (
    Cabinet, Avocat, Client, Dossier, Document, Deadline, Facture, RendezVous, CompteRendu,
    Specialite, DossierStatus, PrioriteLevel, StatutDocument, StatutFacture,
)
from modules import module_h, module_g

REFERENCE = "DEMO-PHARMA-001"
DEFAULT_EMAIL = "ahmed.ighilahriz29@gmail.com"


def _ensure_cabinet_default(db) -> str:
    """Le dashboard liste les dossiers du cabinet 'default' : on garantit sa présence."""
    cab = db.query(Cabinet).filter(Cabinet.id == "default").first()
    if not cab:
        cab = Cabinet(id="default", nom="Cabinet de démonstration", email="contact@cabinet-demo.fr",
                      specialites=["affaires"])
        db.add(cab)
        db.commit()
    return cab.id


def _resolve_avocat(db, avocat_id: str | None) -> Avocat:
    if avocat_id:
        av = db.query(Avocat).filter(Avocat.id == avocat_id).first()
        if not av:
            raise SystemExit(f"Avocat introuvable : {avocat_id}")
        return av
    av = db.query(Avocat).order_by(Avocat.created_at.desc()).first()
    if av:
        return av
    # Aucun avocat : on en crée un de démo.
    av = Avocat(email="avocat.demo@cabinet-demo.fr", nom="Démo", prenom="Avocat",
                specialite=Specialite.AFFAIRES, cabinet_id="default", actif=True)
    db.add(av)
    db.commit()
    db.refresh(av)
    return av


def _purge_existing(db):
    """Supprime un éventuel dossier de démo précédent (et ses enfants non cascadés)."""
    ancien = db.query(Dossier).filter(Dossier.reference == REFERENCE).first()
    if not ancien:
        return
    # factures, rendez-vous et comptes rendus n'ont pas de cascade delete-orphan → on les retire.
    db.query(Facture).filter(Facture.dossier_id == ancien.id).delete(synchronize_session=False)
    db.query(RendezVous).filter(RendezVous.dossier_id == ancien.id).delete(synchronize_session=False)
    db.query(CompteRendu).filter(CompteRendu.dossier_id == ancien.id).delete(synchronize_session=False)
    db.delete(ancien)  # cascade → documents + deadlines
    db.commit()
    print(f"  • ancien dossier {REFERENCE} supprimé")


def seed(avocat_id: str | None, email_client: str):
    db = SessionLocal()
    now = datetime.utcnow()
    try:
        cabinet_id = _ensure_cabinet_default(db)
        avocat = _resolve_avocat(db, avocat_id)
        print(f"  • avocat utilisé : {avocat.prenom} {avocat.nom} (id={avocat.id})")

        _purge_existing(db)

        # Client de démo
        client = Client(
            nom="Pharmacie du Centre", prenom="M. Bernard", email=email_client,
            telephone="04 78 00 00 00", type_client="professionnel",
            siret="123 456 789 00012", cabinet_id=cabinet_id,
            notes="Officine urbaine — projet de cession (démo).",
        )
        db.add(client)
        db.commit()
        db.refresh(client)

        # Dossier (spécialité AFFAIRES ; contexte pharmacie dans le titre/méta)
        dossier = Dossier(
            reference=REFERENCE,
            titre="Cession officine — Pharmacie du Centre (Lyon 2e)",
            specialite=Specialite.AFFAIRES,
            status=DossierStatus.EN_COURS,
            priorite=PrioriteLevel.HAUTE,
            description="Accompagnement de la cession d'une officine urbaine : due diligence, "
                        "valorisation, dépôt ARS et suivi des délais.",
            metadonnees={"contexte": "pharmacie", "type_operation": "cession_officine"},
            client_id=client.id, avocat_id=avocat.id, cabinet_id=cabinet_id,
        )
        db.add(dossier)
        db.commit()
        db.refresh(dossier)
        print(f"  • dossier créé : {dossier.reference} (id={dossier.id})")

        # ── Module H — dépôt ARS réel : crée le délai d'instruction (4 mois) + méta 🏥 ──
        # Dépôt il y a 100 jours → échéance ~J-20 (alerte ROUGE).
        module_h.enregistrer_depot_ars(
            dossier, now - timedelta(days=100), db,
            pieces_manquantes=["Bilan N-1 certifié manquant"],
        )
        print("  • dépôt ARS enregistré (instruction 4 mois ≈ J-20)")

        # ── Module F — délais variés pour la timeline ──
        extra_delais = [
            ("Dépôt des conclusions", now + timedelta(days=5), "procedure", False),
            ("Audience de plaidoirie", now + timedelta(days=12), "audience", False),
            ("Renouvellement licence officine", now + timedelta(days=75), "administratif", False),
            ("Recours gracieux ARS", now - timedelta(days=3), "recours_ars", False),       # échu
            ("Signification du jugement", now - timedelta(days=10), "signification", True), # acquitté
        ]
        for titre, ech, typ, acq in extra_delais:
            db.add(Deadline(titre=titre, description=titre, date_echeance=ech,
                            type_delai=typ, acquitte=acq, dossier_id=dossier.id))
        db.commit()
        print(f"  • {len(extra_delais)} délais de démo ajoutés (J-5, J-12, J-75, échu, acquitté)")

        # ── Module D — documents (2 reçus / 4 → 50 %, RDV approfondi autorisé) ──
        docs = [
            ("3 derniers bilans certifiés expert-comptable", StatutDocument.RECU),
            ("Baux commerciaux original + avenants", StatutDocument.RECU),
            ("K-bis SELARL ou extrait RCS (< 3 mois)", StatutDocument.ATTENDU),
            ("Relevés CA CPAM 12 mois", StatutDocument.ATTENDU),
        ]
        for nom, statut in docs:
            db.add(Document(nom=nom, statut=statut, type_doc="due_diligence", dossier_id=dossier.id))
        db.commit()
        print(f"  • {len(docs)} documents ajoutés (2 reçus / 4)")

        # ── Module G — facture EN RETARD (40 jours) ──
        facture = module_g.creer_facture(
            dossier, 4500.0, "fixe",
            "Honoraires — accompagnement cession officine (provision)", db,
        )
        facture.date_echeance = now - timedelta(days=40)
        facture.date_emission = now - timedelta(days=70)
        facture.statut = StatutFacture.ENVOYEE
        db.commit()
        print(f"  • facture {facture.numero} créée et placée en retard (échéance J+40)")

        print("\n✅ Démo prête.")
        print(f"   Dossier      : {dossier.reference}  (id {dossier.id})")
        print(f"   Avocat       : {avocat.id}")
        print(f"   Client/email : {client.email}")
        print("\nDans le dashboard (connecté avec CET avocat) :")
        print("   1. Onglet 📁 Dossiers → ouvrez « Cession officine — Pharmacie du Centre ».")
        print("   2. Section « Délais » → la timeline F affiche J-5 (rouge), J-12 (orange), J-20 ARS,")
        print("      J-75 (bleu), un délai échu (rouge) et un acquitté (barré).")
        print("   3. Panneau 🏥 → onglet ARS : J restants + échéance 4 mois.")
        print("   4. Panneau 💶 → « Lancer les relances impayés » → relance J+40 (relance n°1).")
    finally:
        db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Seed de démonstration AIOS")
    p.add_argument("--avocat-id", default=os.getenv("AIOS_AVOCAT_ID"), help="ID de l'avocat (sinon : le plus récent)")
    p.add_argument("--email", default=DEFAULT_EMAIL, help="Email du client (destinataire des relances)")
    args = p.parse_args()
    print("→ Génération du jeu de données de démo…")
    seed(args.avocat_id, args.email)
