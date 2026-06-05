from dotenv import load_dotenv
load_dotenv()  # charge C:\dev\aios\.env avant tout le reste

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Request, Header, Body
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from core.database import get_db, engine, Base
from core.models import Dossier, Facture, Deadline
from modules import module_a, module_b, module_c, module_d, module_e, module_f, module_g, module_h, module_i, module_j
from pydantic import BaseModel
from datetime import datetime
from pathlib import Path
import tempfile
import uvicorn
import os

BASE_DIR = Path(__file__).resolve().parents[1]
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", BASE_DIR / "uploads"))

app = FastAPI(title="AIOS — Cabinet d'Avocats", version="1.0.0")

@app.on_event("startup")
async def startup_event():
    try:
        Base.metadata.create_all(bind=engine)
        print("[OK] Tables creees / verifiees dans Supabase")
    except Exception as e:
        print(f"[ERREUR] Connexion DB echouee au demarrage : {e}")
        print("   -> Verifiez DATABASE_URL dans .env (mot de passe Supabase)")
    # Pré-charge le modèle d'embeddings en tâche de fond (évite un 1er /recherche bloquant)
    try:
        import threading
        from core.orchestrateur import pre_warm_embeddings
        threading.Thread(target=pre_warm_embeddings, daemon=True).start()
    except Exception as e:
        print(f"[warmup] non démarré : {e}")

# Origines autorisées : dérivées de FRONTEND_URL (+ localhost en dev).
# En production, définir FRONTEND_URL=https://votre-front.vercel.app dans l'environnement.
ALLOWED_ORIGINS = list({
    *(o.strip() for o in os.getenv("FRONTEND_URL", "").split(",") if o.strip()),
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://aios-mu.vercel.app",
})

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    # Dev : autorise n'importe quel port localhost (3000, 3001, …) sans reconfigurer.
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Module A — Documents ─────────────────────────────────────────────

@app.post("/api/dossiers/{dossier_id}/documents/generer-liste")
async def generer_liste_docs(dossier_id: str, db: Session = Depends(get_db)):
    dossier = db.query(Dossier).filter(Dossier.id == dossier_id).first()
    if not dossier:
        raise HTTPException(404, "Dossier introuvable")
    docs = await module_a.generer_liste_documents(dossier, db)
    return {"documents": docs, "total": len(docs)}


@app.post("/api/documents/upload/{dossier_id}")
async def upload_document(
    dossier_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    from core.models import Document, StatutDocument
    from modules.module_a import _document_mentionne, checklist_dossier
    from core.extraction import extraire_document
    contenu = await file.read()
    upload_dir = UPLOAD_DIR / dossier_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    chemin = upload_dir / file.filename

    with open(chemin, "wb") as f:
        f.write(contenu)

    # Extraction de texte CENTRALISÉE (PDF natif / scan / image / txt) — uniforme, sur le backend.
    extraction = extraire_document(str(chemin), file.filename, file.content_type)
    texte = extraction["texte"]

    # Rattache le fichier à une pièce ATTENDUE correspondante (par nom), sinon
    # crée une nouvelle pièce REÇUE → la checklist et la prévisualisation fonctionnent.
    cible = None
    for d in db.query(Document).filter(Document.dossier_id == dossier_id,
                                       Document.statut == StatutDocument.ATTENDU).all():
        if _document_mentionne(d.nom, file.filename.lower()):
            cible = d
            break
    if cible is None:
        cible = Document(nom=file.filename, type_doc="upload", dossier_id=dossier_id)
        db.add(cible)
    cible.statut = StatutDocument.RECU
    cible.chemin_stockage = str(chemin)
    cible.mime_type = file.content_type
    cible.recu_at = datetime.utcnow()
    cible.ocr_contenu = texte or None
    db.commit()

    # RAG (Module L) : indexation vectorielle du document (chunks + embeddings). Best-effort.
    try:
        from modules import module_l_cession
        await module_l_cession.indexer_document(cible, db, force=True)
    except Exception as e:
        print(f"[upload] indexation RAG ignorée : {e}")

    return {
        "message": "Document reçu",
        "document_id": cible.id,
        "nom": cible.nom,
        "statut": "recu",
        "extraction": extraction["metadonnees"],
        "texte_extrait": texte[:300] if texte else None,
        "checklist": checklist_dossier(dossier_id, db),
    }


@app.get("/api/dossiers/{dossier_id}/documents")
def lister_checklist(dossier_id: str, db: Session = Depends(get_db)):
    """Checklist complète d'un dossier (id, nom, statut, fichier disponible)."""
    _get_dossier_or_404(dossier_id, db)
    from modules.module_a import checklist_dossier
    docs = checklist_dossier(dossier_id, db)
    return {"documents": docs, "total": len(docs)}


class StatutDocBody(BaseModel):
    statut: str   # "recu" | "attente" | "non_conforme" | "valide"

@app.patch("/api/documents/{document_id}/statut")
def maj_statut_document(document_id: str, body: StatutDocBody, db: Session = Depends(get_db)):
    """Module A — Change manuellement le statut d'une pièce (checklist interactive)."""
    from core.models import Document, StatutDocument
    mapping = {
        "recu": StatutDocument.RECU, "valide": StatutDocument.VALIDE,
        "attente": StatutDocument.ATTENDU, "attendu": StatutDocument.ATTENDU,
        "non_conforme": StatutDocument.REFUSE, "refuse": StatutDocument.REFUSE,
    }
    statut = mapping.get((body.statut or "").lower())
    if not statut:
        raise HTTPException(400, "Statut inconnu (recu | attente | non_conforme | valide)")
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(404, "Document introuvable")
    doc.statut = statut
    if statut in (StatutDocument.RECU, StatutDocument.VALIDE) and not doc.recu_at:
        doc.recu_at = datetime.utcnow()
    db.commit()
    return {"id": doc.id, "nom": doc.nom, "statut": getattr(doc.statut, "value", doc.statut)}


@app.get("/api/documents/{document_id}/texte")
def texte_document(document_id: str, reextraire: bool = False, db: Session = Depends(get_db)):
    """Texte extrait d'une pièce (Service d'Extraction Unique). `reextraire=true` relit le fichier."""
    from core.models import Document
    from core.extraction import extraire_document
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(404, "Document introuvable")
    if (reextraire or not doc.ocr_contenu) and doc.chemin_stockage and Path(doc.chemin_stockage).is_file():
        res = extraire_document(doc.chemin_stockage, doc.nom, doc.mime_type)
        doc.ocr_contenu = res["texte"] or doc.ocr_contenu
        db.commit()
        return {"id": doc.id, "nom": doc.nom, "texte": res["texte"], "metadonnees": res["metadonnees"]}
    return {"id": doc.id, "nom": doc.nom, "texte": doc.ocr_contenu or "",
            "metadonnees": {"source": "cache", "nb_caracteres": len(doc.ocr_contenu or "")}}


@app.delete("/api/documents/{document_id}")
def supprimer_document(document_id: str, db: Session = Depends(get_db)):
    """Module A — Supprime une pièce ajoutée/téléversée PAR ERREUR (+ son texte extrait → elle
    n'influencera plus l'analyse IA, cf. `contexte_documents`).
    - Pièce de la **checklist métier** (`type_doc='checklist'`) : on RETIRE le fichier mais on
      GARDE l'exigence (statut remis « En attente »), pour pouvoir re-téléverser le bon document.
    - Pièce **ajoutée** (upload/email) : suppression complète de la ligne.
    Idempotent : si la pièce n'existe plus, renvoie quand même un succès."""
    from core.models import Document, StatutDocument, DocumentChunk
    from modules.module_a import checklist_dossier
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        return {"message": "Pièce déjà supprimée (introuvable)", "deja_supprime": True}
    dossier_id = doc.dossier_id
    # RAG : purge des chunks vectoriels de cette pièce (qu'elle soit supprimée ou réinitialisée).
    db.query(DocumentChunk).filter(DocumentChunk.document_id == document_id).delete(synchronize_session=False)
    # Effacer le fichier physique (le texte extrait part avec la ligne / le reset ci-dessous).
    try:
        if doc.chemin_stockage and Path(doc.chemin_stockage).is_file():
            Path(doc.chemin_stockage).unlink()
    except Exception as e:
        print(f"[supprimer_document] fichier non supprimé : {e}")
    reset = (doc.type_doc == "checklist")
    if reset:
        doc.statut = StatutDocument.ATTENDU
        doc.chemin_stockage = None
        doc.ocr_contenu = None
        doc.recu_at = None
        doc.mime_type = None
        doc.taille_octets = None
    else:
        db.delete(doc)
    db.commit()
    return {"message": "Fichier retiré (pièce conservée en attente)" if reset else "Pièce supprimée",
            "reset": reset, "checklist": checklist_dossier(dossier_id, db)}


@app.get("/api/documents/{document_id}/fichier")
def telecharger_document(document_id: str, db: Session = Depends(get_db)):
    """Sert le fichier d'une pièce pour prévisualisation (PDF/image)."""
    from fastapi.responses import FileResponse
    from core.models import Document
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(404, "Document introuvable")
    if not doc.chemin_stockage or not Path(doc.chemin_stockage).is_file():
        raise HTTPException(404, "Aucun fichier associé à cette pièce")
    return FileResponse(
        doc.chemin_stockage,
        media_type=doc.mime_type or "application/octet-stream",
        filename=doc.nom,
        content_disposition_type="inline",   # prévisualisation dans le navigateur
    )


class EmailClassifRequest(BaseModel):
    expediteur: str
    sujet: str
    corps: str

@app.post("/api/emails/classifier")
async def classifier_email(body: EmailClassifRequest):
    result = await module_a.classifier_email(body.expediteur, body.sujet, body.corps)
    return result


# ─── Module B — CRM + Recherche sémantique ───────────────────────────

class RechercheQuery(BaseModel):
    query: str
    cabinet_id: str

@app.post("/api/dossiers/recherche")
async def rechercher_dossiers(body: RechercheQuery, db: Session = Depends(get_db)):
    resultats = await module_b.recherche_hybride(body.query, body.cabinet_id, db)
    return {"resultats": resultats, "total": len(resultats)}


# ─── Module F — Délais ────────────────────────────────────────────────

class DeadlineCreate(BaseModel):
    dossier_id: str
    type_delai: str
    date_point_depart: datetime

@app.post("/api/deadlines")
async def creer_deadline(body: DeadlineCreate, db: Session = Depends(get_db)):
    dossier = db.query(Dossier).filter(Dossier.id == body.dossier_id).first()
    if not dossier:
        raise HTTPException(404, "Dossier introuvable")
    deadline = module_f.creer_deadline(
        dossier, body.type_delai, body.date_point_depart, db
    )
    return {
        "id": deadline.id,
        "date_echeance": deadline.date_echeance.isoformat(),
        "description": deadline.description,
        "jours_restants": (deadline.date_echeance - datetime.utcnow()).days
    }

@app.get("/api/deadlines/types")
def lister_types_delais():
    """Liste tous les types de délais disponibles (validés par avocat)."""
    return {k: v["description"] for k, v in module_f.DELAIS_LEGAUX.items()}


# ─── Module G — Facturation ───────────────────────────────────────────

class FactureCreate(BaseModel):
    dossier_id: str
    montant_ht: float
    type_honoraires: str
    description: str

@app.post("/api/factures")
async def creer_facture(body: FactureCreate, db: Session = Depends(get_db)):
    dossier = db.query(Dossier).filter(Dossier.id == body.dossier_id).first()
    if not dossier:
        raise HTTPException(404, "Dossier introuvable")
    facture = module_g.creer_facture(
        dossier, body.montant_ht, body.type_honoraires, body.description, db
    )
    return {
        "numero": facture.numero,
        "montant_ht": facture.montant_ht,
        "montant_ttc": facture.montant_ttc,
        "date_echeance": facture.date_echeance.isoformat(),
        "statut": getattr(facture.statut, "value", facture.statut),
    }

@app.post("/api/factures/relances")
async def lancer_relances_impayes(db: Session = Depends(get_db)):
    """Module G — Déclenche manuellement le cycle de relances des factures impayées
    (relance 1/2, mise en demeure, alerte avocat) — d'ordinaire exécuté par Celery Beat."""
    from core.models import Facture, StatutFacture
    impayees = db.query(Facture).filter(
        Facture.statut.in_([StatutFacture.ENVOYEE, StatutFacture.RETARD]),
        Facture.date_echeance < datetime.utcnow(),
    ).count()
    try:
        await module_g.gerer_relances_impayes(db)
        return {"message": "Cycle de relances exécuté", "factures_impayees_traitees": impayees}
    except Exception as e:
        # L'envoi d'email peut échouer (config Resend / domaine non vérifié) : on ne renvoie
        # pas une 500, on informe l'avocat que le déclenchement a bien eu lieu.
        db.rollback()
        return {
            "message": "Relances déclenchées, mais l'envoi d'au moins un email a échoué (vérifier la configuration Resend).",
            "factures_impayees_traitees": impayees,
            "erreur": str(e)[:200],
        }


# ─── Module E — Transcription ─────────────────────────────────────────

@app.post("/api/reunions/transcrire/{dossier_id}")
async def transcrire_reunion(
    dossier_id: str,
    audio: UploadFile = File(...),
    type_reunion: str = "client",
    db: Session = Depends(get_db)
):
    tmp_path = Path(tempfile.gettempdir()) / audio.filename
    tmp_path.write_bytes(await audio.read())

    transcription = await module_e.transcrire_audio(tmp_path)
    compte_rendu = await module_e.generer_compte_rendu(
        transcription, type_reunion, {}, dossier_id, db
    )
    return {
        "transcription": transcription,
        "compte_rendu": {
            "id": compte_rendu.id,
            "titre": compte_rendu.titre,
            "resume": compte_rendu.resume,
            "decisions": compte_rendu.decisions,
            "prochaines_actions": compte_rendu.prochaines_actions
        }
    }


# ─── Stats dashboard ──────────────────────────────────────────────────

@app.get("/api/stats")
def get_stats(cabinet_id: str = "default", avocat_id: str = None, db: Session = Depends(get_db)):
    # Même périmètre que la liste /api/dossiers : cabinet + (optionnel) avocat.
    base = db.query(Dossier).filter(Dossier.cabinet_id == cabinet_id)
    if avocat_id:
        base = base.filter(Dossier.avocat_id == avocat_id)
    total = base.count()
    urgents = base.filter(Dossier.priorite == "urgent").count()
    return {
        "total": total,
        "urgents": urgents,
        "en_attente_docs": 0,
        "revenus_mois": 0,  # Pas encore de module facturation
    }


@app.get("/api/dossiers")
def lister_dossiers(avocat_id: str = None, cabinet_id: str = "default", db: Session = Depends(get_db)):
    """Liste tous les dossiers — sans embeddings, affichage direct."""
    from core.models import Client
    q = db.query(Dossier).filter(Dossier.cabinet_id == cabinet_id)
    if avocat_id:
        q = q.filter(Dossier.avocat_id == avocat_id)
    dossiers = q.order_by(Dossier.created_at.desc()).limit(100).all()
    resultats = []
    for d in dossiers:
        client_nom = None
        if d.client_id:
            c = db.query(Client).filter(Client.id == d.client_id).first()
            client_nom = f"{c.prenom or ''} {c.nom or ''}".strip() if c else None
        resultats.append({
            "id": str(d.id),
            "reference": d.reference,
            "titre": d.titre,
            "status": d.status,
            "priorite": d.priorite,
            "client_nom": client_nom,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        })
    return {"resultats": resultats, "total": len(resultats)}


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "version": "1.0.0",
        "modules": ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K"],
        "extraction": __import__("core.extraction", fromlist=["moteurs_disponibles"]).moteurs_disponibles(),
    }


# ─── Module Email OAuth2 ──────────────────────────────────────────────

from modules.module_email_oauth import (
    get_google_auth_url,
    sauvegarder_integration_google,
    synchroniser_emails_avocat,
)
from core.models import EmailIntegration, EmailClassifie

REDIRECT_URI = os.getenv("FRONTEND_URL", "http://localhost:3000") + "/api/email/callback/google"

@app.get("/api/email/connect/google")
async def connect_gmail(avocat_id: str):
    """Étape 1 : retourne l'URL de consentement Google OAuth2."""
    redirect_uri = str(os.getenv("BACKEND_URL", "http://localhost:8000")) + "/api/email/callback/google"
    url = get_google_auth_url(redirect_uri, state=avocat_id)
    return {"url": url, "avocat_id": avocat_id}

@app.get("/api/email/callback/google")
async def gmail_callback(code: str, state: str = "", db: Session = Depends(get_db)):
    """Étape 2 : reçoit le code OAuth2 et sauvegarde l'intégration."""
    from fastapi.responses import HTMLResponse
    redirect_uri = str(os.getenv("BACKEND_URL", "http://localhost:8000")) + "/api/email/callback/google"
    frontend_url = str(os.getenv("FRONTEND_URL", "http://localhost:3001"))
    avocat_id = state.strip() if state.strip() else None
    if not avocat_id:
        return HTMLResponse(content="<script>alert('Erreur: avocat_id manquant. Relancez la connexion depuis le dashboard.'); window.location='{}';</script>".format(frontend_url + "/dashboard"), status_code=200)
    try:
        integration = await sauvegarder_integration_google(avocat_id, code, redirect_uri, db)
        return HTMLResponse(content="""
        <html><body style='font-family:sans-serif;text-align:center;padding:60px'>
        <h2 style='color:#16a34a'>✅ Gmail connecté avec succès !</h2>
        <p>Compte : <strong>{}</strong></p>
        <p>Retournez sur le dashboard et cliquez <strong>Sync Gmail</strong>.</p>
        <a href='{}' style='display:inline-block;margin-top:20px;padding:12px 24px;background:#2563eb;color:white;border-radius:8px;text-decoration:none'>← Retour au dashboard</a>
        <script>setTimeout(()=>window.location='{}',3000);</script>
        </body></html>
        """.format(integration.email_compte, frontend_url + "/dashboard", frontend_url + "/dashboard"))
    except Exception as e:
        print(f"[OAuth callback] Erreur : {e}")
        return HTMLResponse(content="""
        <html><body style='font-family:sans-serif;text-align:center;padding:60px'>
        <h2 style='color:#dc2626'>❌ Erreur de connexion Gmail</h2>
        <p>{}</p>
        <a href='{}' style='display:inline-block;margin-top:20px;padding:12px 24px;background:#2563eb;color:white;border-radius:8px;text-decoration:none'>← Retour au dashboard</a>
        </body></html>
        """.format(str(e), frontend_url + "/dashboard"), status_code=200)

@app.post("/api/email/sync/{avocat_id}")
async def sync_emails(avocat_id: str, db: Session = Depends(get_db)):
    """Déclenche la synchronisation manuelle des emails d'un avocat."""
    from cryptography.fernet import InvalidToken
    integration = db.query(EmailIntegration).filter(
        EmailIntegration.avocat_id == avocat_id,
        EmailIntegration.actif == True
    ).first()
    if not integration:
        raise HTTPException(404, "Aucune intégration email active pour cet avocat")
    try:
        await synchroniser_emails_avocat(integration, db)
    except InvalidToken:
        # Jeton Gmail chiffré avec une AUTRE SECRET_KEY (ex. local ≠ Render). On renvoie un message
        # clair (HTTPException → passe par le middleware CORS) plutôt qu'un 500 (faux-« CORS »).
        raise HTTPException(
            400,
            "Jeton Gmail indéchiffrable : la SECRET_KEY de ce serveur diffère de celle utilisée "
            "lors de la connexion Gmail. Alignez SECRET_KEY (local = Render) puis reconnectez Gmail.",
        )
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        raise HTTPException(502, f"Échec de la synchronisation Gmail : {str(e)[:200]}")
    return {"message": "Synchronisation terminée", "derniere_sync": integration.derniere_sync}


@app.post("/api/email/webhook/google")
async def gmail_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Webhook Gmail Push Notifications (temps réel).
    Google appelle cette URL dès qu'un email arrive.
    """
    import base64, json as _json
    try:
        body = await request.json()
        message = body.get("message", {})
        data_b64 = message.get("data", "")
        data = _json.loads(base64.b64decode(data_b64 + "==").decode())
        email_address = data.get("emailAddress")

        # Trouver l'intégration correspondant à cet email
        integration = db.query(EmailIntegration).filter(
            EmailIntegration.email_compte == email_address,
            EmailIntegration.actif == True
        ).first()
        if integration:
            await synchroniser_emails_avocat(integration, db, max_emails=10)
        return {"status": "ok"}
    except Exception as e:
        print(f"[Webhook Gmail] Erreur : {e}")
        return {"status": "error"}


@app.get("/api/emails/actions-en-attente")
def get_emails_actions(avocat_id: str, db: Session = Depends(get_db)):
    """Emails classifiés qui attendent une action de l'avocat (pas encore traités)."""
    integration = db.query(EmailIntegration).filter(
        EmailIntegration.avocat_id == avocat_id
    ).first()
    if not integration:
        return {"emails": [], "total": 0}

    from core.models import EmailClassifie, PropositionDossier
    emails = db.query(EmailClassifie).filter(
        EmailClassifie.integration_id == integration.id,
        EmailClassifie.traite == False,
        EmailClassifie.action_suggeree != "archiver",
        EmailClassifie.categorie != "spam"
    ).order_by(EmailClassifie.date_reception.desc()).limit(40).all()

    # AIOS-FIX (auto-réparation) : ne pas réafficher un email dont la proposition est DÉJÀ résolue
    # (validée/rejetée) même si son flag `traite` n'avait pas été posé. On le marque traité au passage.
    resolues = {pid for (pid,) in db.query(PropositionDossier.id)
                .filter(PropositionDossier.statut != "EN_ATTENTE").all()}
    visibles = []
    for e in emails:
        if e.proposition_thread_id and e.proposition_thread_id in resolues:
            e.traite = True                    # auto-cicatrisation : il ne reviendra plus
            continue
        visibles.append(e)
    if len(visibles) != len(emails):
        db.commit()
    emails = visibles[:20]

    return {
        "emails": [
            {
                "id": e.id,
                "expediteur": e.expediteur,
                "sujet": e.sujet,
                "categorie": e.categorie,
                "priorite": e.priorite.value if e.priorite else "standard",
                "resume_ia": e.resume_ia,
                "action_suggeree": e.action_suggeree,
                "dossier_id": e.dossier_id,
                "dossier_cree_auto": e.dossier_detecte_auto,
                "proposition_thread_id": e.proposition_thread_id,
                "date_reception": e.date_reception.isoformat() if e.date_reception else None,
            }
            for e in emails
        ],
        "total": len(emails)
    }


@app.post("/api/emails/{email_id}/confirmer")
def confirmer_action_email(email_id: str, db: Session = Depends(get_db)):
    """L'avocat confirme l'action suggérée par l'IA sur un email."""
    from core.models import EmailClassifie
    email = db.query(EmailClassifie).filter(EmailClassifie.id == email_id).first()
    if not email:
        raise HTTPException(404, "Email introuvable")
    email.traite = True
    db.commit()
    return {"message": "Action confirmée", "action": email.action_suggeree}


@app.post("/api/emails/{email_id}/ignorer")
def ignorer_email(email_id: str, db: Session = Depends(get_db)):
    """L'avocat ignore/archive un email."""
    from core.models import EmailClassifie
    email = db.query(EmailClassifie).filter(EmailClassifie.id == email_id).first()
    if not email:
        raise HTTPException(404, "Email introuvable")
    email.traite = True
    email.action_suggeree = "archiver"
    db.commit()
    return {"message": "Email ignoré"}

@app.get("/api/emails/classifies")
def get_emails_classifies(
    avocat_id: str,
    traite: bool = False,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """Liste les emails classifiés non traités d'un avocat."""
    integration = db.query(EmailIntegration).filter(
        EmailIntegration.avocat_id == avocat_id
    ).first()
    if not integration:
        return {"emails": []}
    emails = db.query(EmailClassifie).filter(
        EmailClassifie.integration_id == integration.id,
        EmailClassifie.traite == traite
    ).order_by(EmailClassifie.date_reception.desc()).limit(limit).all()
    return {
        "emails": [
            {
                "id": e.id,
                "expediteur": e.expediteur,
                "sujet": e.sujet,
                "categorie": e.categorie,
                "priorite": e.priorite,
                "resume_ia": e.resume_ia,
                "action_suggeree": e.action_suggeree,
                "dossier_id": e.dossier_id,
                "dossier_detecte_auto": e.dossier_detecte_auto,
                "proposition_thread_id": e.proposition_thread_id,
                "date_reception": e.date_reception.isoformat() if e.date_reception else None,
            }
            for e in emails
        ]
    }


# ─── Routes de setup / test ───────────────────────────────────────────

from core.models import Avocat, Cabinet, Client as ClientModel
from core.models import DossierStatus, Specialite, PrioriteLevel

class AvocatSetup(BaseModel):
    nom: str
    prenom: str
    email: str
    specialite: str = "droit des affaires"

@app.post("/api/setup/avocat")
def creer_avocat_test(body: AvocatSetup, db: Session = Depends(get_db)):
    """Crée un cabinet + avocat pour démarrer les tests."""
    import uuid
    # Cabinet par défaut
    cabinet = db.query(Cabinet).filter(Cabinet.id == "default").first()
    if not cabinet:
        cabinet = Cabinet(
            id="default",
            nom="Cabinet Maitre Test",
            adresse="1 rue de la Loi, 75001 Paris",
            email="cabinet@test.fr"
        )
        db.add(cabinet)
        db.flush()

    # Avocat
    avocat = db.query(Avocat).filter(Avocat.email == body.email).first()
    if not avocat:
        avocat = Avocat(
            id=str(uuid.uuid4()),
            cabinet_id="default",
            nom=body.nom,
            prenom=body.prenom,
            email=body.email,
            specialite=Specialite.AFFAIRES,
        )
        db.add(avocat)
        db.commit()
        db.refresh(avocat)
    return {"avocat_id": avocat.id, "email": avocat.email, "message": "Avocat cree avec succes"}


class DossierCreate(BaseModel):
    titre: str
    client_nom: str
    client_email: str = ""
    type_dossier: str = "cession_officine"
    avocat_id: str
    cabinet_id: str = "default"

@app.post("/api/dossiers")
async def creer_dossier(body: DossierCreate, db: Session = Depends(get_db)):
    """Crée un nouveau dossier client."""
    import uuid, random, string
    # Client
    client = db.query(ClientModel).filter(ClientModel.email == body.client_email).first()
    if not client:
        client = ClientModel(
            id=str(uuid.uuid4()),
            cabinet_id=body.cabinet_id,
            nom=body.client_nom,
            email=body.client_email,
            type_client="personne_physique"
        )
        db.add(client)
        db.flush()

    ref = "DOS-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    dossier = Dossier(
        id=str(uuid.uuid4()),
        cabinet_id=body.cabinet_id,
        avocat_id=body.avocat_id,
        client_id=client.id,
        reference=ref,
        titre=body.titre,
        specialite=Specialite.AFFAIRES,
        status=DossierStatus.EN_COURS,
        priorite=PrioriteLevel.STANDARD,
        metadonnees={"type_dossier": body.type_dossier},
    )
    db.add(dossier)
    db.commit()
    db.refresh(dossier)

    # Indexation sémantique (Module B) pour rendre le dossier retrouvable en langage naturel
    try:
        await module_b.indexer_dossier(dossier, db)
    except Exception as e:
        print(f"[indexer] embedding dossier échoué : {e}")

    return {
        "dossier_id": dossier.id,
        "reference": dossier.reference,
        "titre": dossier.titre,
        "client": body.client_nom
    }


# ─── Polling temps réel (SSE) ─────────────────────────────────────────

from fastapi.responses import StreamingResponse
import asyncio

@app.get("/api/email/stream/{avocat_id}")
async def stream_emails(avocat_id: str, db: Session = Depends(get_db)):
    """
    Server-Sent Events : envoie un event à chaque nouveau email détecté.
    Le frontend s'abonne et reçoit les mises à jour en temps réel.
    """
    integration = db.query(EmailIntegration).filter(
        EmailIntegration.avocat_id == avocat_id,
        EmailIntegration.actif == True
    ).first()
    if not integration:
        return StreamingResponse(iter(["data: {\"error\": \"pas_integration\"}\n\n"]), media_type="text/event-stream")

    last_count = db.query(EmailClassifie).filter(
        EmailClassifie.integration_id == integration.id,
        EmailClassifie.traite == False
    ).count()

    async def event_generator():
        nonlocal last_count, integration
        # Envoyer état initial
        yield f"data: {{\"type\": \"init\", \"count\": {last_count}}}\n\n"
        # Heartbeat + check toutes les 20 secondes
        check_count = 0
        while True:
            await asyncio.sleep(20)
            check_count += 1
            db.expire_all()
            # Sync auto toutes les 2 minutes (6 checks × 20s)
            if check_count % 6 == 0:
                try:
                    await synchroniser_emails_avocat(integration, db)
                except Exception as e:
                    print(f"[SSE sync] Erreur : {e}")
                    try:
                        db.rollback()
                    except Exception:
                        pass
            new_count = db.query(EmailClassifie).filter(
                EmailClassifie.integration_id == integration.id,
                EmailClassifie.traite == False
            ).count()
            if new_count != last_count:
                last_count = new_count
                yield f"data: {{\"type\": \"update\", \"count\": {new_count}}}\n\n"
            else:
                yield f"data: {{\"type\": \"heartbeat\"}}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


# ─── Module C — Qualification dossier ─────────────────────────────────

@app.post("/api/dossiers/{dossier_id}/qualifier")
async def qualifier_dossier(dossier_id: str, db: Session = Depends(get_db)):
    """Module C — Score de priorité + questions de qualification pour un dossier."""
    dossier = db.query(Dossier).filter(Dossier.id == dossier_id).first()
    if not dossier:
        raise HTTPException(404, "Dossier introuvable")
    specialite = getattr(dossier.specialite, "value", dossier.specialite)
    # Contexte « email du client » = dernier message rattaché au dossier (pour les questions dynamiques).
    from core.models import EmailClassifie
    dernier = (
        db.query(EmailClassifie)
        .filter(EmailClassifie.dossier_id == dossier_id)
        .order_by(EmailClassifie.date_reception.desc().nullslast())
        .first()
    )
    contexte_email = (dernier.corps_extrait if dernier else "") or ""
    from core.extraction import contexte_documents as _ctx_docs
    contexte_docs = _ctx_docs(db, dossier_id, max_chars=3000)
    try:
        scoring = await module_c.qualifier_nouveau_dossier(
            dossier.description or dossier.titre, specialite, dossier.metadonnees or {}, db,
            contexte_email=contexte_email, contexte_documents=contexte_docs,
        )
    except Exception as e:
        # Ne jamais renvoyer 500 : on dégrade proprement (questions statiques).
        print(f"[qualifier] échec : {e}")
        scoring = {
            "score": 0, "priorite": "standard", "categorie": "A_QUALIFIER",
            "justification": "Qualification IA momentanément indisponible — réessayez.",
            "questions_formulaire": module_c.QUESTIONS_PAR_SPECIALITE.get(specialite, []),
            "questions_source": "fallback",
        }
    try:
        module_c.mettre_a_jour_priorite(dossier, int(scoring.get("score", 0)), db)
    except Exception:
        pass
    return scoring


# ─── Module D — Rendez-vous + fiche de préparation ────────────────────

class RdvCreate(BaseModel):
    dossier_id: str
    type_rdv: str = "decouverte"   # decouverte | approfondi | suivi
    date_heure: datetime
    duree_minutes: int = 60

@app.post("/api/rdv")
async def creer_rdv(body: RdvCreate, db: Session = Depends(get_db)):
    """Module D — Crée un RDV + génère la fiche de préparation avocat."""
    from core.models import Client as ClientModel, Document, StatutDocument
    dossier = db.query(Dossier).filter(Dossier.id == body.dossier_id).first()
    if not dossier:
        raise HTTPException(404, "Dossier introuvable")
    client = db.query(ClientModel).filter(ClientModel.id == dossier.client_id).first()
    if not client:
        raise HTTPException(400, "Dossier sans client : impossible de créer un RDV")

    # D.1 — Jamais de RDV approfondi sans dossier suffisamment instruit (>= 50% docs reçus)
    if body.type_rdv == "approfondi":
        total = db.query(Document).filter(Document.dossier_id == dossier.id).count()
        recus = db.query(Document).filter(
            Document.dossier_id == dossier.id,
            Document.statut.in_([StatutDocument.RECU, StatutDocument.VALIDE]),
        ).count()
        if total > 0 and (recus / total) < 0.5:
            raise HTTPException(
                409,
                f"RDV approfondi impossible : seulement {recus}/{total} documents reçus (< 50%).",
            )

    rdv = await module_d.creer_rdv_local(
        dossier, client, body.type_rdv, body.date_heure, body.duree_minutes, db
    )
    return {
        "rdv_id": rdv.id,
        "type_rdv": rdv.type_rdv,
        "date_heure": rdv.date_heure.isoformat() if rdv.date_heure else None,
        "fiche_preparation": rdv.fiche_preparation,
    }

def _fiche_dict(f) -> dict:
    return {
        "id": f.id, "version": f.version, "type_rdv": f.type_rdv, "contenu": f.contenu,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }

@app.get("/api/dossiers/{dossier_id}/rdv")
def lister_rdv(dossier_id: str, db: Session = Depends(get_db)):
    """Module D — Liste des RDV d'un dossier (dont ceux « à confirmer » détectés automatiquement)."""
    from core.models import RendezVous
    _get_dossier_or_404(dossier_id, db)
    rows = db.query(RendezVous).filter(RendezVous.dossier_id == dossier_id).order_by(RendezVous.date_heure.desc()).all()
    return {"rdvs": [
        {"id": r.id, "type_rdv": r.type_rdv, "statut": r.statut, "duree_minutes": r.duree_minutes,
         "date_heure": r.date_heure.isoformat() if r.date_heure else None, "motif": r.fiche_preparation}
        for r in rows
    ]}

class RdvStatutBody(BaseModel):
    statut: str   # confirme | annule | a_confirmer

@app.patch("/api/rdv/{rdv_id}/statut")
def maj_statut_rdv(rdv_id: str, body: RdvStatutBody, db: Session = Depends(get_db)):
    """Module D — Confirme / annule un RDV (ex. valider un RDV proposé par le client)."""
    from core.models import RendezVous
    if body.statut not in ("confirme", "annule", "a_confirmer"):
        raise HTTPException(400, "statut attendu : confirme | annule | a_confirmer")
    r = db.query(RendezVous).filter(RendezVous.id == rdv_id).first()
    if not r:
        raise HTTPException(404, "RDV introuvable")
    r.statut = body.statut
    db.commit()
    return {"id": r.id, "statut": r.statut, "date_heure": r.date_heure.isoformat() if r.date_heure else None}

@app.delete("/api/rdv/{rdv_id}")
def supprimer_rdv(rdv_id: str, db: Session = Depends(get_db)):
    """Module D — Supprime un RDV (annulation de dernière minute / urgence). Idempotent :
    si le RDV n'existe plus, renvoie quand même un succès."""
    from core.models import RendezVous
    r = db.query(RendezVous).filter(RendezVous.id == rdv_id).first()
    if not r:
        return {"message": "RDV déjà supprimé (introuvable)", "id": rdv_id, "deja_supprime": True}
    db.delete(r)
    db.commit()
    return {"message": "Rendez-vous supprimé", "id": rdv_id}

@app.get("/api/dossiers/{dossier_id}/fiche-preparation")
def derniere_fiche_preparation(dossier_id: str, db: Session = Depends(get_db)):
    """Module D — Dernière fiche PERSISTÉE (retrouvée à la réouverture du dossier)."""
    _get_dossier_or_404(dossier_id, db)
    f = module_d.derniere_fiche(dossier_id, db)
    return {"fiche": _fiche_dict(f) if f else None}

class FichePrepBody(BaseModel):
    type_rdv: str = "decouverte"
    ecraser: bool = False   # True = met à jour la dernière version ; False = nouvelle version

@app.post("/api/dossiers/{dossier_id}/fiche-preparation")
async def generer_fiche_versionnee(dossier_id: str, body: FichePrepBody, db: Session = Depends(get_db)):
    """Module D — Génère une fiche et la PERSISTE (nouvelle version ou écrasement de la dernière)."""
    from core.models import Client as ClientModel
    dossier = _get_dossier_or_404(dossier_id, db)
    client = db.query(ClientModel).filter(ClientModel.id == dossier.client_id).first()
    if not client:
        raise HTTPException(400, "Dossier sans client : impossible de générer la fiche")
    f = await module_d.generer_et_versionner(dossier, client, body.type_rdv, db, ecraser=body.ecraser)
    return _fiche_dict(f)

@app.get("/api/dossiers/{dossier_id}/fiches")
def lister_fiches_preparation(dossier_id: str, db: Session = Depends(get_db)):
    """Module D — Historique des versions de fiche (la plus récente d'abord)."""
    _get_dossier_or_404(dossier_id, db)
    return {"fiches": [
        {"id": f.id, "version": f.version, "type_rdv": f.type_rdv,
         "created_at": f.created_at.isoformat() if f.created_at else None}
        for f in module_d.lister_fiches(dossier_id, db)
    ]}

@app.get("/api/fiches/{fiche_id}")
def get_fiche_preparation(fiche_id: str, db: Session = Depends(get_db)):
    """Module D — Contenu complet d'une version de fiche."""
    from core.models import FichePreparation
    f = db.query(FichePreparation).filter(FichePreparation.id == fiche_id).first()
    if not f:
        raise HTTPException(404, "Fiche introuvable")
    return {**_fiche_dict(f), "dossier_id": f.dossier_id}

@app.get("/api/fiches/{fiche_id}/imprimer")
def imprimer_fiche_preparation(fiche_id: str, db: Session = Depends(get_db)):
    """Module D — Page HTML imprimable (en-tête cabinet + mentions légales) → PDF via le navigateur."""
    from fastapi.responses import HTMLResponse
    from core.models import FichePreparation, Cabinet
    f = db.query(FichePreparation).filter(FichePreparation.id == fiche_id).first()
    if not f:
        raise HTTPException(404, "Fiche introuvable")
    dossier = db.query(Dossier).filter(Dossier.id == f.dossier_id).first()
    cabinet = (db.query(Cabinet).filter(Cabinet.id == dossier.cabinet_id).first()
               if dossier and dossier.cabinet_id else None)
    return HTMLResponse(module_d.construire_html_fiche(f, dossier, cabinet))


# ─── Module H — Cession officine (due diligence + suivi ARS) ──────────

@app.get("/api/pharmacie/checklist")
def pharmacie_checklist():
    """Module H — Check-list de due diligence officine (référentiel)."""
    return {"checklist": module_h.liste_checklist()}

class DueDiligenceBody(BaseModel):
    documents_recus: list[str] = []

@app.post("/api/pharmacie/{dossier_id}/due-diligence")
def pharmacie_due_diligence(dossier_id: str, body: DueDiligenceBody, db: Session = Depends(get_db)):
    """Module H — Évalue la complétude de la due diligence (déterministe)."""
    dossier = db.query(Dossier).filter(Dossier.id == dossier_id).first()
    if not dossier:
        raise HTTPException(404, "Dossier introuvable")
    return module_h.evaluer_due_diligence(body.documents_recus)

@app.get("/api/pharmacie/{dossier_id}/valorisation")
def pharmacie_valorisation(dossier_id: str, ca_ht: float, type_officine: str = "urbaine", db: Session = Depends(get_db)):
    """Module H — Valorisation indicative (multiple du CA HT, déterministe)."""
    dossier = db.query(Dossier).filter(Dossier.id == dossier_id).first()
    if not dossier:
        raise HTTPException(404, "Dossier introuvable")
    return module_h.calculer_valorisation(ca_ht, type_officine)

class ArsDepotBody(BaseModel):
    date_depot: datetime
    pieces_manquantes: list[str] = []

@app.post("/api/pharmacie/{dossier_id}/ars/depot")
def pharmacie_ars_depot(dossier_id: str, body: ArsDepotBody, db: Session = Depends(get_db)):
    """Module H — Enregistre le dépôt ARS, calcule l'échéance (4 mois) et crée la deadline (Module F)."""
    dossier = db.query(Dossier).filter(Dossier.id == dossier_id).first()
    if not dossier:
        raise HTTPException(404, "Dossier introuvable")
    return module_h.enregistrer_depot_ars(dossier, body.date_depot, db, body.pieces_manquantes)

@app.get("/api/pharmacie/{dossier_id}/ars")
def pharmacie_ars_etat(dossier_id: str, db: Session = Depends(get_db)):
    """Module H — État courant de l'instruction ARS (jours restants recalculés en direct)."""
    dossier = db.query(Dossier).filter(Dossier.id == dossier_id).first()
    if not dossier:
        raise HTTPException(404, "Dossier introuvable")
    return module_h.etat_ars(dossier)


# ─── Module L — Cession officine : extraction paramètres + versement SECIB ──

@app.post("/api/cession/{dossier_id}/extraire-fiche")
async def cession_extraire_fiche(dossier_id: str, reindex: bool = False, db: Session = Depends(get_db)):
    """Module L — Pré-remplit la Fiche de cession (RAG single-pass) depuis les pièces + l'appel.
    `reindex=true` : reconstruit les chunks vectoriels (à utiliser après un changement d'embedder)."""
    from modules import module_l_cession
    dossier = db.query(Dossier).filter(Dossier.id == dossier_id).first()
    if not dossier:
        raise HTTPException(404, "Dossier introuvable")
    # Self-healing : récupère d'abord les PJ des emails rattachés non encore versées
    # (ex. dossier créé depuis une proposition HITL, avant ce correctif).
    try:
        from modules.module_email_oauth import ingerer_pieces_jointes_dossier
        pj = await ingerer_pieces_jointes_dossier(dossier_id, db)
        if pj:
            print(f"[extraire-fiche] pièces jointes récupérées : {pj}")
    except Exception as ex:
        print(f"[extraire-fiche] backfill des pièces jointes ignoré : {ex}")
    # Ré-indexation forcée (ex. après bascule du modèle d'embeddings vers e5 multilingue).
    if reindex:
        try:
            n = await module_l_cession.reindexer_dossier(dossier_id, db)
            print(f"[extraire-fiche] ré-indexation : {n} chunks")
        except Exception as ex:
            print(f"[extraire-fiche] ré-indexation ignorée : {ex}")
    fiche = await module_l_cession.extraire_fiche_cession(dossier, db)
    return fiche.model_dump()


@app.get("/api/cession/{dossier_id}/fiche")
def cession_get_fiche(dossier_id: str, db: Session = Depends(get_db)):
    """Module L — Lit la Fiche de cession enregistrée (provenance, conditions, champs incertains)."""
    from modules import module_l_cession
    dossier = db.query(Dossier).filter(Dossier.id == dossier_id).first()
    if not dossier:
        raise HTTPException(404, "Dossier introuvable")
    fiche = module_l_cession.charger_fiche(dossier)
    return fiche.model_dump() if fiche else {"existe": False}


@app.put("/api/cession/{dossier_id}/fiche")
def cession_put_fiche(dossier_id: str, payload: dict = Body(...), db: Session = Depends(get_db)):
    """Module L — Applique les corrections de l'avocat (HITL) sur la Fiche de cession."""
    from modules import module_l_cession
    dossier = db.query(Dossier).filter(Dossier.id == dossier_id).first()
    if not dossier:
        raise HTTPException(404, "Dossier introuvable")
    try:
        fiche = module_l_cession.valider_fiche(dossier, payload, db)
    except Exception as e:
        raise HTTPException(400, f"Fiche invalide : {e}")
    return fiche.model_dump()


@app.post("/api/cession/{dossier_id}/secib")
def cession_secib(dossier_id: str, db: Session = Depends(get_db)):
    """Module L — Verse le dossier dans SECIB (SECIB_MODE, défaut : paquet de transfert local)."""
    from modules import module_l_secib
    dossier = db.query(Dossier).filter(Dossier.id == dossier_id).first()
    if not dossier:
        raise HTTPException(404, "Dossier introuvable")
    connector = module_l_secib.get_connector()
    return connector.pousser_dossier(dossier, db)


# ── Lot 4 — Suivi des conditions suspensives (déterministe) ──

@app.get("/api/cession/{dossier_id}/conditions/etat")
def cession_conditions_etat(dossier_id: str, db: Session = Depends(get_db)):
    """Module L — État global des conditions suspensives (prêtes pour l'acte ?)."""
    from modules import module_l_cession
    dossier = db.query(Dossier).filter(Dossier.id == dossier_id).first()
    if not dossier:
        raise HTTPException(404, "Dossier introuvable")
    fiche = module_l_cession.charger_fiche(dossier)
    if not fiche:
        raise HTTPException(400, "Aucune fiche de cession (extraire d'abord les paramètres)")
    return module_l_cession.etat_conditions(fiche)


class ConditionUpdateBody(BaseModel):
    statut: str | None = None          # EN_ATTENTE | LEVEE | DEFAILLIE
    date_butoir: str | None = None     # ISO "YYYY-MM-DD"
    preuve_doc_id: str | None = None

@app.patch("/api/cession/{dossier_id}/conditions/{code}")
def cession_maj_condition(dossier_id: str, code: str, body: ConditionUpdateBody, db: Session = Depends(get_db)):
    """Module L — Met à jour une condition suspensive (statut/date butoir → Module F). Décision HUMAINE."""
    from modules import module_l_cession
    dossier = db.query(Dossier).filter(Dossier.id == dossier_id).first()
    if not dossier:
        raise HTTPException(404, "Dossier introuvable")
    try:
        fiche = module_l_cession.maj_condition(
            dossier, code, db,
            statut=body.statut, date_butoir=body.date_butoir, preuve_doc_id=body.preuve_doc_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"fiche": fiche.model_dump(), "etat": module_l_cession.etat_conditions(fiche)}


# ── Lot 3 — Génération de la promesse de cession ──

@app.post("/api/cession/{dossier_id}/promesse")
async def cession_generer_promesse(dossier_id: str, db: Session = Depends(get_db)):
    """Module L — Génère une version de la promesse depuis la Fiche de cession validée (gabarit + exposé LLM)."""
    from modules import module_l_actes
    dossier = db.query(Dossier).filter(Dossier.id == dossier_id).first()
    if not dossier:
        raise HTTPException(404, "Dossier introuvable")
    try:
        acte = await module_l_actes.generer_promesse(dossier, db)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": acte.id, "type": acte.type, "sous_type": acte.sous_type,
            "version": acte.version, "statut": acte.statut,
            "created_at": acte.created_at.isoformat() if acte.created_at else None}


# ── Lot 5 — Génération de l'acte définitif (verrouillé sur les conditions) ──

@app.post("/api/cession/{dossier_id}/acte")
async def cession_generer_acte(dossier_id: str, db: Session = Depends(get_db)):
    """Module L — Génère l'acte définitif (bloqué tant que les conditions ne sont pas toutes levées) + formalités (Module F)."""
    from modules import module_l_actes
    dossier = db.query(Dossier).filter(Dossier.id == dossier_id).first()
    if not dossier:
        raise HTTPException(404, "Dossier introuvable")
    try:
        acte, deadlines = await module_l_actes.generer_acte(dossier, db)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": acte.id, "type": acte.type, "sous_type": acte.sous_type,
            "version": acte.version, "statut": acte.statut,
            "created_at": acte.created_at.isoformat() if acte.created_at else None,
            "formalites": deadlines}


@app.get("/api/cession/{dossier_id}/actes")
def cession_lister_actes(dossier_id: str, db: Session = Depends(get_db)):
    """Module L — Historique des promesses/actes générés (le plus récent d'abord)."""
    from modules import module_l_actes
    dossier = db.query(Dossier).filter(Dossier.id == dossier_id).first()
    if not dossier:
        raise HTTPException(404, "Dossier introuvable")
    return {"actes": [
        {"id": a.id, "type": a.type, "sous_type": a.sous_type, "version": a.version,
         "statut": a.statut, "created_at": a.created_at.isoformat() if a.created_at else None}
        for a in module_l_actes.lister_actes(dossier_id, db)
    ]}


@app.get("/api/cession/actes/{acte_id}")
def cession_get_acte(acte_id: str, db: Session = Depends(get_db)):
    """Module L — Contenu complet (Markdown) d'une promesse/acte."""
    from core.models import ActeCession
    a = db.query(ActeCession).filter(ActeCession.id == acte_id).first()
    if not a:
        raise HTTPException(404, "Acte introuvable")
    return {"id": a.id, "dossier_id": a.dossier_id, "type": a.type, "sous_type": a.sous_type,
            "version": a.version, "statut": a.statut, "contenu": a.contenu,
            "created_at": a.created_at.isoformat() if a.created_at else None}


@app.get("/api/cession/actes/{acte_id}/imprimer")
def cession_imprimer_acte(acte_id: str, db: Session = Depends(get_db)):
    """Module L — Page HTML imprimable de la promesse/acte → PDF via le navigateur."""
    from fastapi.responses import HTMLResponse
    from core.models import ActeCession, Cabinet
    from modules import module_l_actes
    a = db.query(ActeCession).filter(ActeCession.id == acte_id).first()
    if not a:
        raise HTTPException(404, "Acte introuvable")
    dossier = db.query(Dossier).filter(Dossier.id == a.dossier_id).first()
    cabinet = (db.query(Cabinet).filter(Cabinet.id == dossier.cabinet_id).first()
               if dossier and dossier.cabinet_id else None)
    return HTMLResponse(module_l_actes.construire_html_acte(a, dossier, cabinet))


# ─── Module I — Contentieux général (suivi de procédure) ──────────────

def _get_dossier_or_404(dossier_id: str, db: Session) -> Dossier:
    d = db.query(Dossier).filter(Dossier.id == dossier_id).first()
    if not d:
        raise HTTPException(404, "Dossier introuvable")
    return d

class StatutDossierBody(BaseModel):
    statut: str   # nouveau | en_cours | en_attente | cloture | archive

@app.patch("/api/dossiers/{dossier_id}/statut")
def maj_statut_dossier(dossier_id: str, body: StatutDossierBody, db: Session = Depends(get_db)):
    """Change le statut d'un dossier (ex. clôture / réouverture)."""
    from core.models import DossierStatus
    d = _get_dossier_or_404(dossier_id, db)
    nouveau = (body.statut or "").lower()
    try:
        d.status = DossierStatus(nouveau)
    except ValueError:
        raise HTTPException(400, "Statut invalide (nouveau|en_cours|en_attente|cloture|archive)")
    db.commit()

    # AIOS-FIX: cas 14 — jalon facturable (clôture) → facture BROUILLON + rappel J+30 (déterministe).
    facture_creee = None
    try:
        f = module_g.jalon_declenche_facture(db, d, nouveau)
        if f:
            facture_creee = {"numero": f.numero, "montant_ht": f.montant_ht,
                             "statut": getattr(f.statut, "value", f.statut)}
    except Exception as e:
        db.rollback()
        print(f"[statut] jalon facturable non déclenché : {e}")

    return {"id": str(d.id), "status": getattr(d.status, "value", d.status), "facture_brouillon": facture_creee}

@app.delete("/api/dossiers/{dossier_id}")
def supprimer_dossier(dossier_id: str, db: Session = Depends(get_db)):
    """Supprime DÉFINITIVEMENT le dossier et ses données liées (documents, délais, factures,
    RDV, fiches, comptes rendus). Les emails sont conservés mais détachés (dossier_id = NULL).
    Idempotent : si le dossier n'existe plus, renvoie quand même un succès."""
    from core.models import (Facture, CompteRendu, RendezVous, FichePreparation,
                             EmailClassifie, PropositionDossier, ActeCession, DocumentChunk)
    d = db.query(Dossier).filter(Dossier.id == dossier_id).first()
    if not d:
        return {"message": "Dossier déjà supprimé (introuvable)", "id": dossier_id, "deja_supprime": True}
    # On garde l'historique de la boîte mail : on détache simplement les emails.
    db.query(EmailClassifie).filter(EmailClassifie.dossier_id == dossier_id).update(
        {EmailClassifie.dossier_id: None}, synchronize_session=False)
    db.query(PropositionDossier).filter(PropositionDossier.dossier_id == dossier_id).update(
        {PropositionDossier.dossier_id: None}, synchronize_session=False)
    # Enfants non cascadés → suppression explicite.
    for Model in (Facture, CompteRendu, RendezVous, FichePreparation, ActeCession, DocumentChunk):
        db.query(Model).filter(Model.dossier_id == dossier_id).delete(synchronize_session=False)
    db.delete(d)   # documents + délais supprimés par cascade (delete-orphan)
    db.commit()
    return {"message": "Dossier supprimé", "id": dossier_id}

@app.get("/api/contentieux/{dossier_id}")
def contentieux_etat(dossier_id: str, db: Session = Depends(get_db)):
    """Module I — État courant de la procédure contentieuse."""
    return module_i.etat_contentieux(_get_dossier_or_404(dossier_id, db))

class ContentieuxEtapeBody(BaseModel):
    etape: str
    infos: dict = {}

@app.post("/api/contentieux/{dossier_id}/etape")
def contentieux_etape(dossier_id: str, body: ContentieuxEtapeBody, db: Session = Depends(get_db)):
    """Module I — Enregistre l'étape courante de la procédure."""
    return module_i.enregistrer_etape(_get_dossier_or_404(dossier_id, db), body.etape, db, body.infos)

@app.get("/api/contentieux/{dossier_id}/delais")
def contentieux_delais(dossier_id: str, juridiction: str, date_decision: datetime | None = None, db: Session = Depends(get_db)):
    """Module I — Délais procéduraux (déterministe, règles defaults ← cabinet ← avocat)."""
    from core.regles import resoudre_regles
    d = _get_dossier_or_404(dossier_id, db)
    regles = resoudre_regles(db, "I", cabinet_id=d.cabinet_id, avocat_id=d.avocat_id)
    return module_i.calculer_delais_procedure(juridiction, date_decision, regles)

@app.get("/api/contentieux/{dossier_id}/synthese")
async def contentieux_synthese(dossier_id: str, db: Session = Depends(get_db)):
    """Module I — Note de stratégie contentieuse (LLM, à relire)."""
    d = _get_dossier_or_404(dossier_id, db)
    return {"synthese": await module_i.synthese_strategie(d)}


# ─── Module J — Contentieux des pharmaciens (spécialisé) ──────────────

@app.get("/api/contentieux-pharma/{dossier_id}")
def cont_pharma_etat(dossier_id: str, db: Session = Depends(get_db)):
    """Module J — État courant du contentieux pharmacien."""
    return module_j.etat_contentieux_pharma(_get_dossier_or_404(dossier_id, db))

class ClauseNonConcurrenceBody(BaseModel):
    criteres_remplis: list[str] = []
    type_clause: str = "cession"   # cession (vendeur) | travail (adjoint salarié)

@app.post("/api/contentieux-pharma/{dossier_id}/clause-non-concurrence")
def cont_pharma_clause(dossier_id: str, body: ClauseNonConcurrenceBody, db: Session = Depends(get_db)):
    """Module J — Analyse de validité d'une clause de non-concurrence (déterministe)."""
    from core.regles import resoudre_regles
    d = _get_dossier_or_404(dossier_id, db)
    regles = resoudre_regles(db, "J", cabinet_id=d.cabinet_id, avocat_id=d.avocat_id)
    return module_j.analyser_clause_non_concurrence(body.criteres_remplis, body.type_clause, regles)

@app.get("/api/contentieux-pharma/{dossier_id}/delai-recours")
def cont_pharma_delai(dossier_id: str, type_contentieux: str, date_notification: datetime | None = None, db: Session = Depends(get_db)):
    """Module J — Délai de recours restant (déterministe, règles defaults ← cabinet ← avocat)."""
    from core.regles import resoudre_regles
    d = _get_dossier_or_404(dossier_id, db)
    regles = resoudre_regles(db, "J", cabinet_id=d.cabinet_id, avocat_id=d.avocat_id)
    return module_j.calculer_delai_recours(type_contentieux, date_notification, regles)

class ContentieuxPharmaBody(BaseModel):
    type_contentieux: str
    infos: dict = {}

@app.post("/api/contentieux-pharma/{dossier_id}")
def cont_pharma_enregistrer(dossier_id: str, body: ContentieuxPharmaBody, db: Session = Depends(get_db)):
    """Module J — Enregistre le type de contentieux pharmacien et ses infos."""
    return module_j.enregistrer_contentieux(_get_dossier_or_404(dossier_id, db), body.type_contentieux, db, body.infos)

@app.get("/api/contentieux-pharma/{dossier_id}/synthese")
async def cont_pharma_synthese(dossier_id: str, db: Session = Depends(get_db)):
    """Module J — Note d'analyse du contentieux pharmacien (LLM, à relire)."""
    d = _get_dossier_or_404(dossier_id, db)
    return {"synthese": await module_j.synthese_contentieux_pharma(d)}


# ─── Règles juridiques configurables (defaults ← cabinet ← avocat) ────
# Fondation pour la future interface de paramétrage. Modules : "I" | "J".

@app.get("/api/regles/{module}")
def get_regles(module: str, scope: str | None = None, scope_id: str | None = None, db: Session = Depends(get_db)):
    """Renvoie defaults + override + valeur effective. Sans scope : seulement les defaults."""
    from core.regles import lire_regles
    if module.upper() not in ("I", "J"):
        raise HTTPException(400, "Module inconnu (attendu : I ou J)")
    return lire_regles(db, module, scope, scope_id)

class ReglesBody(BaseModel):
    scope: str          # "cabinet" | "avocat"
    scope_id: str
    payload: dict = {}  # override partiel (fusionné sur les defaults)

@app.put("/api/regles/{module}")
def put_regles(module: str, body: ReglesBody, db: Session = Depends(get_db)):
    """Enregistre (upsert) une surcharge de règles pour un cabinet ou un avocat."""
    from core.regles import enregistrer_regles
    if module.upper() not in ("I", "J"):
        raise HTTPException(400, "Module inconnu (attendu : I ou J)")
    if body.scope not in ("cabinet", "avocat"):
        raise HTTPException(400, "scope attendu : 'cabinet' ou 'avocat'")
    return enregistrer_regles(db, module, body.scope, body.scope_id, body.payload)


# ─── Module A.4 — Triage email (graphe LangGraph) ─────────────────────

class TriageEmailBody(BaseModel):
    expediteur: str
    sujet: str
    corps: str = ""

@app.post("/api/emails/trier")
async def trier_email_endpoint(body: TriageEmailBody, db: Session = Depends(get_db)):
    """Module A.4 — Trie un email via le graphe LangGraph (anti-injection + urgence déterministe + classification)."""
    from agents.email_triage import trier_email
    dossiers = db.query(Dossier).limit(50).all()
    contexte = [{"reference": d.reference, "titre": d.titre} for d in dossiers]
    return await trier_email(body.expediteur, body.sujet, body.corps, contexte)

@app.post("/api/emails/trier-et-proposer")
async def trier_et_proposer(body: TriageEmailBody, avocat_id: str = None, db: Session = Depends(get_db)):
    """Chaîne complète : triage (A.4) -> si email pro sans dossier détecté, crée une PROPOSITION
    de dossier en attente de validation (visible dans l'onglet Propositions)."""
    from agents.email_triage import trier_email
    from modules.module_email_oauth import creer_proposition_dossier
    dossiers = db.query(Dossier).limit(50).all()
    contexte = [{"reference": d.reference, "titre": d.titre} for d in dossiers]
    triage = await trier_email(body.expediteur, body.sujet, body.corps, contexte)

    proposition = None
    ref = triage.get("dossier_reference")
    dossier_existe = bool(ref) and db.query(Dossier).filter(Dossier.reference == ref).first() is not None
    if (not dossier_existe
            and triage.get("categorie") in ("client", "prospect", "juridiction", "fournisseur", "administratif")
            and "SUSPICIOUS_INJECTION" not in (triage.get("security_flags") or [])):
        thread_id = await creer_proposition_dossier(
            body.expediteur, body.sujet, triage.get("resume", ""),
            triage.get("categorie", "client"), avocat_id, db,
        )
        if thread_id:
            db.commit()
            proposition = {"thread_id": thread_id, "statut": "EN_ATTENTE"}
    return {"triage": triage, "proposition": proposition}


# ─── Création de dossier avec validation humaine (LangGraph interrupt) ─

class ProposerDossierBody(BaseModel):
    expediteur: str
    sujet: str = ""
    resume_ia: str = ""
    categorie: str = "client"
    avocat_id: str | None = None
    cabinet_id: str = "default"

@app.post("/api/dossiers/proposer")
async def proposer_dossier(body: ProposerDossierBody, db: Session = Depends(get_db)):
    """HITL — Prépare une proposition de dossier et MET EN PAUSE jusqu'à validation de l'avocat.
    Persiste la proposition (EN_ATTENTE) ; renvoie un thread_id à rejouer via /valider/{thread_id}."""
    import uuid as _uuid
    from agents.dossier_creation import proposer_creation
    from core.models import PropositionDossier
    thread_id = str(_uuid.uuid4())
    res = await asyncio.to_thread(proposer_creation, body.model_dump(), thread_id)
    if res.get("en_attente_validation"):
        db.add(PropositionDossier(
            id=thread_id, avocat_id=body.avocat_id, cabinet_id=body.cabinet_id,
            proposition=res.get("proposition", {}), message=res.get("message", ""),
            statut="EN_ATTENTE",
        ))
        db.commit()
    return res

class ValiderDossierBody(BaseModel):
    decision: str  # "valider" | "rejeter"

@app.post("/api/dossiers/valider/{thread_id}")
async def valider_dossier(thread_id: str, body: ValiderDossierBody, db: Session = Depends(get_db)):
    """HITL — Reprend le graphe en pause : crée réellement le dossier si 'valider', sinon rejette."""
    from agents.dossier_creation import resoudre_creation
    from core.models import PropositionDossier
    res = await asyncio.to_thread(resoudre_creation, thread_id, body.decision)
    prop = db.query(PropositionDossier).filter(PropositionDossier.id == thread_id).first()
    if prop:
        prop.statut = res.get("statut", prop.statut)
        prop.dossier_id = res.get("dossier_id")
        prop.resolved_at = datetime.utcnow()

    # AIOS-FIX: marquer l'email à l'origine de la proposition comme TRAITÉ — sinon il réapparaît
    # en boucle dans « Emails IA » à chaque re-sync (la proposition était résolue mais pas l'email).
    from core.models import EmailClassifie
    liens = db.query(EmailClassifie).filter(EmailClassifie.proposition_thread_id == thread_id).all()
    for e in liens:
        e.traite = True
        if res.get("dossier_id"):
            e.dossier_id = res["dossier_id"]
            e.action_suggeree = "voir_dossier"
        else:                                  # rejet → on archive l'email
            e.action_suggeree = "archiver"
    db.commit()

    # AIOS-FIX: le dossier vient d'être créé → ingérer les pièces jointes de l'email d'origine
    # (la sync ne les traite que si le dossier existe DÉJÀ à la réception ; ici il n'existait pas).
    if res.get("dossier_id"):
        try:
            from modules.module_email_oauth import ingerer_pieces_jointes_dossier
            pj = await ingerer_pieces_jointes_dossier(res["dossier_id"], db)
            if pj:
                print(f"[Valider] pièces jointes ingérées dans {res['dossier_id']} : {pj}")
        except Exception as ex:
            print(f"[Valider] ingestion des pièces jointes échouée : {ex}")
    return res

@app.get("/api/dossiers/propositions")
def lister_propositions(avocat_id: str = None, statut: str = "EN_ATTENTE", db: Session = Depends(get_db)):
    """Liste les propositions de dossier (par défaut : en attente de validation)."""
    from core.models import PropositionDossier
    q = db.query(PropositionDossier).filter(PropositionDossier.statut == statut)
    if avocat_id:
        q = q.filter(PropositionDossier.avocat_id == avocat_id)
    rows = q.order_by(PropositionDossier.created_at.desc()).limit(50).all()
    return {"propositions": [
        {"thread_id": r.id, "message": r.message, "proposition": r.proposition,
         "statut": r.statut, "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]}


# ─── Module K — Veille réglementaire ──────────────────────────────────

class VeilleScanBody(BaseModel):
    source: str = "auto"   # "auto" (RSS si configuré, sinon corpus interne) | "rss" | "sample"
    resumer: bool = True

@app.post("/api/veille/scan")
async def veille_scan(body: VeilleScanBody, db: Session = Depends(get_db)):
    """Module K — Lance un scan de veille (filtre + impact + résumé LLM + persistance)."""
    from modules import module_k
    alertes = await module_k.scanner_veille(source=body.source, db=db, resumer=body.resumer)
    return {"alertes": alertes, "total": len(alertes)}

@app.get("/api/veille/alertes")
def veille_alertes(limit: int = 50, db: Session = Depends(get_db)):
    """Module K — Liste les alertes de veille (les plus récentes d'abord)."""
    from core.models import VeilleAlerte
    rows = db.query(VeilleAlerte).order_by(VeilleAlerte.created_at.desc()).limit(limit).all()
    return {"alertes": [
        {"id": r.id, "titre": r.titre, "source": r.source, "url": r.url, "source_url": r.url,
         "impact": r.impact, "resume": r.resume, "mots_cles": r.mots_cles,
         "date_publication": r.date_publication, "lu": r.lu}
        for r in rows
    ]}


# ─── Auth Supabase — synchronisation avocat ───────────────────────────

@app.post("/api/auth/sync")
def auth_sync(authorization: str = Header(None), db: Session = Depends(get_db)):
    """Vérifie le JWT Supabase et retourne (ou crée/lie) l'avocat correspondant."""
    from core.auth import verifier_token_supabase, get_or_create_avocat
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "En-tête Authorization Bearer requis")
    token = authorization.split(" ", 1)[1]
    user = verifier_token_supabase(token)
    avocat = get_or_create_avocat(db, user)
    return {"avocat_id": avocat.id, "email": avocat.email, "nom": avocat.nom}


# ─── Réponse client assistée (génération + envoi via Gmail) ───────────

class GenererReponseBody(BaseModel):
    message_client: str = ""

# Détection déterministe de l'intention du dernier message client.
_MOTS_ENVOI_DOC = ("ci-joint", "ci joint", "cijoint", "pièce jointe", "pieces jointes", "pièces jointes",
                   "vous trouverez", "veuillez trouver", "je vous envoie", "je vous transmets",
                   "en pièce", "joint à ce", "comme demandé", "documents demandés", "voici les")
_MOTS_RELANCE = ("relance", "où en est", "ou en est", "des nouvelles", "sans retour", "sans réponse",
                 "toujours pas", "je reviens vers vous", "avez-vous reçu", "suite à mon")
_MOTS_QUESTION = ("comment", "pourquoi", "quand", "est-ce que", "est ce que", "pouvez-vous", "pouvez vous",
                  "pourriez-vous", "quel", "quelle", "combien", "dois-je", "faut-il", "puis-je")

def _prenom_pour_salutation(client, texte: str) -> str:
    """Prénom pour personnaliser la salutation : client en base, sinon signature de l'email."""
    if client and getattr(client, "prenom", None) and client.prenom.strip():
        return client.prenom.strip().split()[0]
    import re as _re
    m = _re.search(
        r"(?:cordialement|bien à vous|bien cordialement|salutations|sincèrement|merci d'avance)\s*[,.]?\s*\n+\s*"
        r"([A-ZÀ-Ÿ][\wÀ-ÿ'\-]+)",
        texte or "", _re.IGNORECASE,
    )
    return m.group(1) if m else ""

def _analyser_intention(texte: str) -> str:
    """Retourne : 'envoi_document' | 'question' | 'relance' | 'indetermine'."""
    t = (texte or "").lower()
    if not t.strip():
        return "indetermine"
    if any(m in t for m in _MOTS_ENVOI_DOC):
        return "envoi_document"
    if "?" in t or any(m in t for m in _MOTS_QUESTION):
        return "question"
    if any(m in t for m in _MOTS_RELANCE):
        return "relance"
    return "indetermine"

@app.post("/api/dossiers/{dossier_id}/generer-reponse")
async def generer_reponse_client(dossier_id: str, body: GenererReponseBody | None = None, db: Session = Depends(get_db)):
    """Rédige une SUGGESTION de réponse email au client (LLM), CONTEXTUALISÉE sur les 3 derniers
    messages du fil lié au dossier. À relire/valider avant envoi (HITL)."""
    from core.models import Client as ClientModel, EmailClassifie
    from core.orchestrateur import llm_chat
    dossier = db.query(Dossier).filter(Dossier.id == dossier_id).first()
    if not dossier:
        raise HTTPException(404, "Dossier introuvable")
    client = db.query(ClientModel).filter(ClientModel.id == dossier.client_id).first() if dossier.client_id else None

    # ── Contexte : 3 derniers messages du fil lié au dossier (le plus récent d'abord) ──
    fil = (
        db.query(EmailClassifie)
        .filter(EmailClassifie.dossier_id == dossier_id)
        .order_by(EmailClassifie.date_reception.desc().nullslast())
        .limit(3)
        .all()
    )
    dernier_client = fil[0] if fil else None  # les emails stockés sont entrants = côté client
    extrait_dernier = ""
    if dernier_client:
        extrait_dernier = (dernier_client.corps_extrait or dernier_client.sujet or "").strip()[:280]

    # Historique du plus ancien au plus récent, pour donner le fil à l'IA
    historique = ""
    for e in reversed(fil):
        quand = e.date_reception.strftime("%d/%m/%Y") if e.date_reception else "?"
        corps_e = (e.corps_extrait or "").strip()[:500]
        historique += f"\n— [{quand}] {e.expediteur or 'client'} — objet « {e.sujet or ''} » :\n  {corps_e}\n"

    message = (body.message_client if body else "") or ""

    # ── État du dossier en base : complétude documentaire ──
    from core.models import Document, StatutDocument
    docs = db.query(Document).filter(Document.dossier_id == dossier_id).all()
    total_docs = len(docs)
    recus = [d for d in docs if d.statut in (StatutDocument.RECU, StatutDocument.VALIDE)]
    manquants = [d.nom for d in docs if d.statut in (StatutDocument.ATTENDU, StatutDocument.REFUSE)]
    nb_recus = len(recus)
    en_collecte = total_docs > 0 and nb_recus == 0
    statut_dossier = getattr(dossier.status, "value", dossier.status)

    # Contenu factuel extrait des pièces (Service d'Extraction Unique) — pour répondre sur du concret.
    from core.extraction import contexte_documents as _ctx_docs
    contexte_docs = _ctx_docs(db, dossier_id, max_chars=2000)

    # ── Intention du client (priorité au message saisi par l'avocat, sinon dernier email) ──
    base_texte = message or (dernier_client.corps_extrait if dernier_client else "") or ""
    intention = _analyser_intention(base_texte)
    prenom = _prenom_pour_salutation(client, dernier_client.corps_extrait if dernier_client else "")

    # ── Choix du scénario (déterministe) ──
    if intention == "question":
        scenario = "REPONDRE_QUESTION"
    elif intention == "envoi_document" or (nb_recus > 0 and intention in ("indetermine", "envoi_document")):
        scenario = "ACCUSER_RECEPTION"
    elif en_collecte:                       # collecte en cours, aucune pièce reçue
        scenario = "DEMANDER_PIECES"
    elif nb_recus > 0:                       # des pièces existent → analyse en cours
        scenario = "ACCUSER_RECEPTION"
    elif not base_texte.strip() and total_docs == 0:
        scenario = "INFOS_INSUFFISANTES"
    else:
        scenario = "INFOS_INSUFFISANTES"

    INSTRUCTIONS = {
        "REPONDRE_QUESTION":
            "Le client pose une question précise. Réponds-y DIRECTEMENT et clairement. Ne réclame PAS de "
            "documents si ce n'est pas indispensable à la réponse. Si les éléments du dossier ne permettent "
            "pas de répondre avec certitude, n'invente RIEN : indique-le et propose un bref rendez-vous "
            "téléphonique (avec deux créneaux indicatifs).",
        "DEMANDER_PIECES":
            "Le dossier est en phase de collecte de pièces et aucune pièce n'a encore été reçue. Demande avec "
            "tact les documents manquants en les listant clairement, explique brièvement pourquoi ils sont "
            "nécessaires pour avancer, et indique un moyen simple de les transmettre. Documents attendus : "
            + (", ".join(manquants[:8]) if manquants else "(à préciser avec le client)") + ".",
        "ACCUSER_RECEPTION":
            "Le client a transmis des documents (ou en accuse l'envoi). Confirme la bonne réception, remercie, "
            "et précise qu'une analyse est en cours ; indique la prochaine étape SANS livrer d'analyse juridique. "
            "Ne redemande pas les pièces déjà reçues.",
        "INFOS_INSUFFISANTES":
            "Tu ne disposes pas d'éléments suffisants pour répondre précisément. Conformément à la déontologie, "
            "NE DEVINE PAS : propose courtoisement un rendez-vous téléphonique pour faire le point (deux créneaux "
            "indicatifs) afin de cerner le besoin.",
    }

    prompt = (
        "Tu rédiges, pour le compte d'un avocat, une réponse email au client.\n"
        f"Dossier : {dossier.titre} (réf {dossier.reference}) — statut : {statut_dossier}.\n"
        f"État documentaire : {nb_recus} pièce(s) reçue(s) sur {total_docs} attendue(s).\n"
        + (f"\nFil de discussion récent (du plus ancien au plus récent) :{historique}\n" if historique else "")
        + (f"\nMessage précis à traiter en priorité : {message}\n" if message else "")
        + (f"\nÉléments FACTUELS extraits des pièces du dossier (utilise-les pour répondre sur du concret "
           f"— dates, montants — sans divulguer d'information confidentielle non destinée au client) :\n{contexte_docs}\n"
           if contexte_docs else "")
        + f"\nINTENTION DÉTECTÉE : {intention}.\nCONSIGNE PRINCIPALE : {INSTRUCTIONS[scenario]}\n"
        + "\nContraintes déontologiques et de forme :\n"
          "- Ton formel, courtois, vouvoiement ; registre soutenu conforme à la déontologie de l'avocat.\n"
          "- NE révèle AUCUNE stratégie juridique, position de négociation ni analyse confidentielle.\n"
          "- Tu peux confirmer des faits objectifs (dates, montants) présents dans les pièces si c'est utile au client.\n"
          + (f"- Commence par la salutation « Bonjour {prenom}, ».\n" if prenom
             else "- Commence par « Madame, Monsieur, ».\n")
          + "- Termine par une formule de politesse professionnelle.\n"
          "- 110 à 180 mots, en français. Rédige uniquement le corps de l'email (pas d'objet)."
    )
    corps = await llm_chat(
        prompt,
        system="Tu es l'assistant d'un avocat. Emails clients sobres, formels, conformes à la déontologie, "
               "sans aucun détail stratégique confidentiel. Tu ne devines jamais : à défaut d'éléments, tu "
               "proposes un rendez-vous téléphonique.",
        max_tokens=500,
    )
    objet = f"Re: {dernier_client.sujet}" if (dernier_client and dernier_client.sujet) else f"Votre dossier {dossier.reference}"
    return {
        "objet": objet,
        "corps": corps,
        "destinataire": (dernier_client.expediteur if dernier_client else None) or (client.email if client else None),
        "extrait_dernier_message": extrait_dernier,
        "nb_messages_contexte": len(fil),
        "intention": intention,
        "scenario": scenario,
        "documents_recus": nb_recus,
        "documents_total": total_docs,
        "documents_manquants": manquants[:8],
    }


class EnvoyerReponseBody(BaseModel):
    dossier_id: str
    objet: str
    corps: str
    destinataire: str | None = None

@app.post("/api/emails/envoyer-reponse")
async def envoyer_reponse(body: EnvoyerReponseBody, db: Session = Depends(get_db)):
    """Envoie la réponse validée par l'avocat via SA boîte Gmail (OAuth)."""
    from core.models import Client as ClientModel, EmailIntegration
    from modules.module_email_oauth import envoyer_email_gmail
    dossier = db.query(Dossier).filter(Dossier.id == body.dossier_id).first()
    if not dossier:
        raise HTTPException(404, "Dossier introuvable")
    client = db.query(ClientModel).filter(ClientModel.id == dossier.client_id).first() if dossier.client_id else None
    destinataire = body.destinataire or (client.email if client else None)
    if not destinataire:
        raise HTTPException(400, "Aucun destinataire (email client manquant)")
    integration = db.query(EmailIntegration).filter(
        EmailIntegration.avocat_id == dossier.avocat_id, EmailIntegration.actif == True
    ).first()
    if not integration:
        raise HTTPException(400, "Gmail non connecté pour cet avocat — connectez la boîte mail d'abord")
    try:
        await envoyer_email_gmail(integration, destinataire, body.objet, body.corps)
    except Exception as e:
        raise HTTPException(502, f"Échec d'envoi Gmail : {e}")

    # AIOS-FIX: cas 6 & 7 — journaliser le message SORTANT dans le fil du dossier + faire avancer
    # le statut (NOUVEAU → EN_COURS). Toléré : si la journalisation échoue, l'email est déjà parti.
    try:
        from core.models import EmailClassifie, DossierStatus
        db.add(EmailClassifie(
            integration_id=integration.id,
            expediteur=integration.email_compte,
            destinataires=[destinataire],
            sujet=body.objet,
            corps_extrait=(body.corps or "")[:8000],
            date_reception=datetime.utcnow(),
            categorie="sortant",
            action_suggeree="envoye",
            dossier_id=body.dossier_id,
            traite=True,
        ))
        if dossier.status == DossierStatus.NOUVEAU:
            dossier.status = DossierStatus.EN_COURS
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[envoyer-reponse] journalisation du message sortant échouée : {e}")

    return {"message": "Email envoyé", "destinataire": destinataire}


# ─── Détails (lecture complète email / dossier) ───────────────────────

@app.get("/api/emails/{email_id}")
def get_email_detail(email_id: str, db: Session = Depends(get_db)):
    """Contenu complet d'un email classifié."""
    from core.models import EmailClassifie
    e = db.query(EmailClassifie).filter(EmailClassifie.id == email_id).first()
    if not e:
        raise HTTPException(404, "Email introuvable")
    return {
        "id": e.id, "expediteur": e.expediteur, "sujet": e.sujet,
        "corps": e.corps_extrait, "categorie": e.categorie, "sous_categorie": e.sous_categorie,
        "priorite": e.priorite.value if e.priorite else "standard",
        "resume_ia": e.resume_ia, "action_suggeree": e.action_suggeree,
        "dossier_id": e.dossier_id, "proposition_thread_id": e.proposition_thread_id,
        "date_reception": e.date_reception.isoformat() if e.date_reception else None,
        "traite": e.traite,
    }

@app.get("/api/dossiers/{dossier_id}/emails")
def lister_emails_dossier(dossier_id: str, db: Session = Depends(get_db)):
    """Cas 6 — Fil chronologique complet du dossier (entrants + sortants), du plus ancien au plus récent."""
    from core.models import EmailClassifie
    _get_dossier_or_404(dossier_id, db)
    rows = (db.query(EmailClassifie)
            .filter(EmailClassifie.dossier_id == dossier_id)
            .order_by(EmailClassifie.date_reception.asc().nullsfirst())
            .all())
    fil = [{
        "id": e.id,
        "direction": "sortant" if e.categorie == "sortant" else "entrant",
        "expediteur": e.expediteur,
        "sujet": e.sujet,
        "corps": e.corps_extrait,
        "resume_ia": e.resume_ia,
        "categorie": e.categorie,
        "sous_categorie": e.sous_categorie,
        "traite": e.traite,
        "date": e.date_reception.isoformat() if e.date_reception else None,
    } for e in rows]
    return {"emails": fil, "total": len(fil)}


@app.get("/api/conflits/check")
def check_conflit_interets(nom: str, cabinet_id: str = "default", db: Session = Depends(get_db)):
    """Cas 10 — Vérifie un conflit d'intérêts potentiel pour un nom (déterministe, 0 % LLM)."""
    return module_b.detecter_conflit_interets(db, nom, cabinet_id)


@app.get("/api/dossiers/inactifs")
def lister_dossiers_inactifs(cabinet_id: str = "default", jours: int = None, db: Session = Depends(get_db)):
    """Cas 13 — Dossiers actifs sans activité depuis > seuil (déterministe : MAX(updated_at))."""
    seuil = jours if jours is not None else int(os.getenv("INACTIVITE_SEUIL_JOURS", "7"))
    inactifs = module_b.detecter_dossiers_inactifs(db, cabinet_id, seuil_jours=seuil)
    return {"seuil_jours": seuil, "total": len(inactifs), "dossiers": [
        {"id": d.id, "reference": d.reference, "titre": d.titre,
         "updated_at": d.updated_at.isoformat() if d.updated_at else None}
        for d in inactifs
    ]}


@app.get("/api/dossiers/{dossier_id}/details")
def get_dossier_detail(dossier_id: str, db: Session = Depends(get_db)):
    """Détail complet d'un dossier : infos, client, documents, délais, factures, comptes rendus."""
    from core.models import Client, Document, Deadline, Facture, CompteRendu, RendezVous
    d = db.query(Dossier).filter(Dossier.id == dossier_id).first()
    if not d:
        raise HTTPException(404, "Dossier introuvable")
    client = db.query(Client).filter(Client.id == d.client_id).first() if d.client_id else None
    documents = db.query(Document).filter(Document.dossier_id == dossier_id).all()
    deadlines = db.query(Deadline).filter(Deadline.dossier_id == dossier_id).all()
    factures = db.query(Facture).filter(Facture.dossier_id == dossier_id).all()
    crs = db.query(CompteRendu).filter(CompteRendu.dossier_id == dossier_id).all()
    rdvs = db.query(RendezVous).filter(RendezVous.dossier_id == dossier_id).order_by(RendezVous.date_heure.desc()).all()
    return {
        "id": str(d.id), "reference": d.reference, "titre": d.titre,
        "specialite": getattr(d.specialite, "value", d.specialite),
        "status": getattr(d.status, "value", d.status),
        "priorite": getattr(d.priorite, "value", d.priorite),
        "description": d.description, "metadonnees": d.metadonnees,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "client": ({"nom": client.nom, "prenom": client.prenom, "email": client.email,
                    "telephone": client.telephone, "type": client.type_client} if client else None),
        "documents": [{"nom": x.nom, "statut": getattr(x.statut, "value", x.statut)} for x in documents],
        "deadlines": [{"titre": x.titre, "date_echeance": x.date_echeance.isoformat() if x.date_echeance else None,
                       "type_delai": x.type_delai, "acquitte": x.acquitte} for x in deadlines],
        "factures": [{"numero": x.numero, "montant_ttc": x.montant_ttc, "statut": getattr(x.statut, "value", x.statut),
                      "date_echeance": x.date_echeance.isoformat() if x.date_echeance else None} for x in factures],
        "comptes_rendus": [{"titre": x.titre, "resume": x.resume,
                            "created_at": x.created_at.isoformat() if x.created_at else None} for x in crs],
        "rdvs": [{"id": x.id, "type_rdv": x.type_rdv, "statut": x.statut, "duree_minutes": x.duree_minutes,
                  "date_heure": x.date_heure.isoformat() if x.date_heure else None} for x in rdvs],
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

