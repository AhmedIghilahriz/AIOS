from core.celery_app import celery_app
from core.database import SessionLocal


@celery_app.task
def verifier_deadlines():
    """Vérifie toutes les deadlines et envoie les alertes. Toutes les heures."""
    db = SessionLocal()
    try:
        import asyncio
        from modules import module_f
        asyncio.run(module_f.verifier_et_alerter(db))
    finally:
        db.close()


@celery_app.task
def relances_documents():
    """Relances documents non reçus depuis plus de RELANCE_DELAI_JOURS (défaut 5). Chaque matin à 9h."""
    db = SessionLocal()
    try:
        import asyncio, os
        from datetime import datetime, timedelta
        from core.models import Document, Dossier, Client, StatutDocument
        from modules import module_a
        # AIOS-FIX: cas 9 — délai de relance configurable (.env), défaut 5 jours.
        seuil = datetime.utcnow() - timedelta(days=int(os.getenv("RELANCE_DELAI_JOURS", "5")))

        docs_en_attente = db.query(Document).filter(
            Document.statut == StatutDocument.ATTENDU,
            Document.created_at < seuil,
        ).all()

        # Grouper les documents manquants par dossier
        dossiers_a_relancer: dict[str, list[str]] = {}
        for doc in docs_en_attente:
            dossiers_a_relancer.setdefault(doc.dossier_id, []).append(doc.nom)

        for dossier_id, docs_manquants in dossiers_a_relancer.items():
            dossier = db.query(Dossier).filter(Dossier.id == dossier_id).first()
            if not dossier:
                continue
            client = db.query(Client).filter(Client.id == dossier.client_id).first()
            if not client or not client.email:
                continue  # pas d'email → on ne peut pas relancer
            # Compter les relances déjà effectuées (stockées dans metadonnees)
            meta = dict(dossier.metadonnees or {})
            nb_relances = int(meta.get("nb_relances_docs", 0)) + 1
            try:
                asyncio.run(module_a.envoyer_relance(
                    dossier,
                    client.email,
                    f"{client.prenom or ''} {client.nom}".strip(),
                    docs_manquants,
                    nb_relances,
                ))
                meta["nb_relances_docs"] = nb_relances
                dossier.metadonnees = meta
                db.commit()
            except Exception as e:
                print(f"[relances_documents] Échec dossier {dossier_id}: {e}")
    finally:
        db.close()


@celery_app.task
def relances_impayes():
    """Gère les relances d'impayés (J+30 / J+45 / J+60 / J+90). Chaque jour à 10h."""
    db = SessionLocal()
    try:
        import asyncio
        from modules import module_g
        asyncio.run(module_g.gerer_relances_impayes(db))
    finally:
        db.close()


@celery_app.task
def scanner_veille_reglementaire():
    """Veille réglementaire pharmaceutique (sources RSS). Chaque matin à 7h."""
    db = SessionLocal()
    try:
        import asyncio
        from modules import module_k
        asyncio.run(module_k.scanner_veille(source="rss", db=db))
    finally:
        db.close()


@celery_app.task
def radar_inactivite():
    """
    Cas 13 — Radar d'inactivité (DÉTERMINISTE) : marque les dossiers EN_COURS sans activité
    depuis > INACTIVITE_SEUIL_JOURS (défaut 7) via metadonnees["inactif"]. Chaque jour.
    Non destructif : on ne change PAS le statut, on pose un drapeau exploitable par le dashboard.
    """
    db = SessionLocal()
    try:
        import os
        from core.models import Dossier, DossierStatus
        from modules import module_b
        seuil = int(os.getenv("INACTIVITE_SEUIL_JOURS", "7"))
        inactifs = {d.id for d in module_b.detecter_dossiers_inactifs(db, "default", seuil_jours=seuil)}
        actifs = db.query(Dossier).filter(Dossier.status == DossierStatus.EN_COURS).all()
        for d in actifs:
            meta = dict(d.metadonnees or {})
            doit_etre = d.id in inactifs
            if bool(meta.get("inactif")) != doit_etre:
                meta["inactif"] = doit_etre
                d.metadonnees = meta
        db.commit()
        print(f"[radar_inactivite] {len(inactifs)} dossier(s) marqué(s) inactif(s) (seuil {seuil} j)")
    finally:
        db.close()


@celery_app.task
def synchroniser_tous_les_emails():
    """Synchronise les emails de tous les avocats ayant une intégration active. Toutes les 15 min."""
    db = SessionLocal()
    try:
        import asyncio
        from core.models import EmailIntegration
        from modules.module_email_oauth import synchroniser_emails_avocat
        integrations = db.query(EmailIntegration).filter(
            EmailIntegration.actif == True
        ).all()
        for integration in integrations:
            asyncio.run(synchroniser_emails_avocat(integration, db))
    finally:
        db.close()
