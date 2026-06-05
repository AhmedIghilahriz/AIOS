"""
AIOS — Module Email OAuth2
Connecte la boîte Gmail ou Outlook de l'avocat via OAuth2.
On NE demande JAMAIS le mot de passe — uniquement le consentement OAuth2.

Approche : comme Superhuman / Shortwave
  1. L'avocat clique "Connecter Gmail" dans l'interface
  2. Google OAuth2 consent screen s'ouvre (dans le navigateur)
  3. On reçoit un code → on l'échange contre access_token + refresh_token
  4. On chiffre et stocke le refresh_token en base
  5. On synchronise les emails en tâche de fond (Celery)
  6. Claude classifie chaque email et détecte le dossier associé
"""

import os
import json
import base64
import hashlib
import asyncio
from datetime import datetime, timedelta
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session
from core.models import EmailIntegration, EmailClassifie, Dossier, PrioriteLevel

import math
# Item 8a — Isolation : seuil de proximité sémantique pour AUTORISER le rattachement
# d'un email à un dossier existant. En dessous, on force un nouveau dossier.
SEUIL_ISOLATION_DOSSIER = float(os.getenv("SEUIL_ISOLATION_DOSSIER", "0.55"))


def _cosine(a, b):
    if not a or not b or len(a) != len(b):
        return None
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return None
    return dot / (na * nb)


async def _sujets_proches(texte: str, dossier) -> bool:
    """
    Garde d'ISOLATION : n'autorise le rattachement d'un email à un dossier existant
    que si leurs SUJETS sont sémantiquement proches (cosinus des embeddings).
    En cas de doute (pas d'embedding / erreur), on conserve le rattachement (comportement par défaut).
    """
    try:
        emb_dossier = list(dossier.embedding) if getattr(dossier, "embedding", None) is not None else None
        if not emb_dossier:
            return True
        from core.orchestrateur import get_embedding
        emb_email = await get_embedding((texte or "")[:1500])
        sim = _cosine(list(emb_email), emb_dossier)
        if sim is None:
            return True
        return sim >= SEUIL_ISOLATION_DOSSIER
    except Exception as e:
        print(f"[Sync] isolation : similarité non calculée ({e}) — rattachement conservé")
        return True
from core.orchestrateur import generer_texte_juridique, get_embedding
import httpx

# ── Chiffrement des tokens (AES-256 via Fernet) ───────────────────────

def _get_fernet() -> Fernet:
    # On utilise FERNET_KEY exclusivement
    key_str = os.getenv("FERNET_KEY")
    if not key_str:
        raise ValueError("FERNET_KEY manquante !")
    # On dérive une clé 32 octets propre
    key = base64.urlsafe_b64encode(hashlib.sha256(key_str.encode()).digest())
    return Fernet(key)

def chiffrer_token(token: str) -> str:
    return _get_fernet().encrypt(token.encode()).decode()

def dechiffrer_token(token_enc: str) -> str:
    return _get_fernet().decrypt(token_enc.encode()).decode()


# ── OAuth2 Google (Gmail) ─────────────────────────────────────────────

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_GMAIL_MESSAGES_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
GOOGLE_GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/userinfo.email",
]


def get_google_auth_url(redirect_uri: str, state: str = "") -> str:
    """Génère l'URL de consentement Google OAuth2."""
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"


async def echanger_code_google(code: str, redirect_uri: str) -> dict:
    """Échange le code OAuth2 contre access_token + refresh_token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", ""),
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })
        resp.raise_for_status()
        return resp.json()


async def rafraichir_access_token_google(integration: EmailIntegration) -> str:
    """Rafraîchit l'access_token depuis le refresh_token stocké."""
    refresh_token = dechiffrer_token(integration.refresh_token_enc)
    async with httpx.AsyncClient() as client:
        resp = await client.post(GOOGLE_TOKEN_URL, data={
            "refresh_token": refresh_token,
            "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", ""),
            "grant_type": "refresh_token",
        })
        resp.raise_for_status()
        data = resp.json()
        return data["access_token"]


async def envoyer_email_gmail(integration: EmailIntegration, destinataire: str, objet: str, corps: str) -> dict:
    """
    Envoie un email via l'API Gmail au nom de l'avocat (scope gmail.modify suffit pour messages.send).
    Le jeton OAuth est rafraîchi à la volée.
    """
    import base64
    from email.mime.text import MIMEText

    access_token = await rafraichir_access_token_google(integration)
    mime = MIMEText(corps, _charset="utf-8")
    mime["To"] = destinataire
    mime["From"] = integration.email_compte
    mime["Subject"] = objet
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()

    async with httpx.AsyncClient() as client:
        r = await client.post(
            GOOGLE_GMAIL_SEND_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            json={"raw": raw},
        )
        r.raise_for_status()
        return r.json()


async def sauvegarder_integration_google(
    avocat_id: str,
    code: str,
    redirect_uri: str,
    db: Session
) -> EmailIntegration:
    """Crée ou met à jour l'intégration Gmail pour un avocat."""
    tokens = await echanger_code_google(code, redirect_uri)

    # Récupérer l'email du compte connecté
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
        userinfo = resp.json()

    integration = db.query(EmailIntegration).filter(
        EmailIntegration.avocat_id == avocat_id
    ).first()

    refresh_enc = chiffrer_token(tokens["refresh_token"])
    access_enc = chiffrer_token(tokens["access_token"])

    if integration:
        integration.refresh_token_enc = refresh_enc
        integration.access_token_enc = access_enc
        integration.token_expiry = datetime.utcnow() + timedelta(seconds=tokens.get("expires_in", 3600))
        integration.email_compte = userinfo["email"]
        integration.actif = True
        integration.fournisseur = "google"
    else:
        integration = EmailIntegration(
            avocat_id=avocat_id,
            fournisseur="google",
            email_compte=userinfo["email"],
            refresh_token_enc=refresh_enc,
            access_token_enc=access_enc,
            token_expiry=datetime.utcnow() + timedelta(seconds=tokens.get("expires_in", 3600)),
            scopes=GOOGLE_SCOPES,
        )
        db.add(integration)

    db.commit()
    db.refresh(integration)
    return integration


# ── Synchronisation et classification des emails ──────────────────────

CATEGORIES_EMAIL = [
    "client",           # email d'un client existant
    "prospect",         # nouveau contact potentiel
    "juridiction",      # tribunal, greffe, huissier, notaire
    "fournisseur",      # pharmacien, grossiste, fournisseur
    "administratif",    # ARS, ANSM, ordre des pharmaciens, URSSAF
    "interne",          # collègues, cabinet
    "spam",
    "autre",
]

SOUS_CATEGORIES = [
    "demande_piece", "urgence_delai", "relance_paiement",
    "prise_rdv", "transmission_document", "question_juridique",
    "decision_administrative", "notification_audience", "autre",
]

ACTIONS_SUGGEREEES = [
    "répondre", "transmettre_avocat", "créer_dossier",
    "ajouter_deadline", "archiver", "marquer_urgent",
]


async def classifier_email_ia(
    sujet: str,
    corps: str,
    expediteur: str,
    dossiers_actifs: list[dict]
) -> dict:
    """
    Trie un email via le graphe LangGraph (agents.email_triage) :
    garde anti-injection + urgence déterministe (hors LLM) + classification LLM (Groq/Ollama).
    Repli sur un appel LLM direct si le graphe est indisponible.
    """
    try:
        from agents.email_triage import trier_email
        return await trier_email(expediteur, sujet, corps, dossiers_actifs)
    except Exception as e:
        print(f"[triage] graphe indisponible ({e}) — repli LLM direct")

    contexte_dossiers = json.dumps(dossiers_actifs[:20], ensure_ascii=False)

    prompt = f"""Tu es l'assistant IA d'un avocat d'affaires spécialisé pharmacie en France.

Analyse cet email et réponds UNIQUEMENT en JSON valide avec ce format exact :
{{
  "categorie": "{' | '.join(CATEGORIES_EMAIL)}",
  "sous_categorie": "{' | '.join(SOUS_CATEGORIES)}",
  "priorite": "urgent | haute | standard | basse",
  "resume": "Une phrase résumant l'email (max 100 caractères)",
  "action_suggeree": "{' | '.join(ACTIONS_SUGGEREEES)}",
  "dossier_reference": "REF-XXXX ou null si aucun dossier détecté"
}}

Email à analyser :
- Expéditeur : {expediteur}
- Sujet : {sujet}
- Corps : {corps[:1500]}

Dossiers actifs du cabinet pour détection automatique :
{contexte_dossiers}
"""
    reponse = await generer_texte_juridique(
        prompt, {}, specialite="affaires"
    )
    try:
        return json.loads(reponse)
    except Exception:
        return {
            "categorie": "autre",
            "sous_categorie": "autre",
            "priorite": "standard",
            "resume": sujet[:100],
            "action_suggeree": "archiver",
            "dossier_reference": None,
        }


async def creer_proposition_dossier(
    expediteur: str, sujet: str, resume: str, categorie: str,
    avocat_id: str, db: Session, cabinet_id: str = "default",
) -> str | None:
    """
    Crée une PROPOSITION de dossier (HITL LangGraph) au lieu de créer le dossier.
    Le dossier ne sera créé qu'après validation de l'avocat. Retourne le thread_id (ou None).
    `db.flush()` seulement : le commit est géré par l'appelant.
    """
    import uuid as _uuid
    from core.models import PropositionDossier
    from agents.dossier_creation import proposer_creation
    try:
        thread_id = str(_uuid.uuid4())
        email_data = {
            "expediteur": expediteur or "", "sujet": sujet or "",
            "resume_ia": resume or "", "categorie": categorie or "client",
            "avocat_id": avocat_id, "cabinet_id": cabinet_id,
        }
        res = await asyncio.to_thread(proposer_creation, email_data, thread_id)
        if res.get("en_attente_validation"):
            db.add(PropositionDossier(
                id=thread_id, avocat_id=avocat_id, cabinet_id=cabinet_id,
                proposition=res.get("proposition", {}), message=res.get("message", ""),
                statut="EN_ATTENTE",
            ))
            db.flush()
            return thread_id
    except Exception as e:
        print(f"[proposition] création depuis email échouée : {e}")
    return None


_syncs_en_cours: set = set()  # évite deux synchros simultanées (SSE auto + bouton manuel)


async def synchroniser_emails_avocat(integration: EmailIntegration, db: Session, max_emails: int = 50):
    """Verrou anti-exécution concurrente, puis synchronise les emails (cf. _impl)."""
    if not integration.actif:
        return
    if integration.id in _syncs_en_cours:
        print("[Sync] déjà en cours pour cette intégration — ignorée")
        return
    _syncs_en_cours.add(integration.id)
    try:
        await _synchroniser_emails_avocat_impl(integration, db, max_emails)
    finally:
        _syncs_en_cours.discard(integration.id)


def _b64url_decode(data: str) -> bytes:
    import base64
    if not data:
        return b""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _extraire_corps_gmail(payload: dict) -> str:
    """Corps texte complet d'un message Gmail (format=full) : text/plain prioritaire, sinon text/html nettoyé."""
    import re as _re
    plain, html_parts = [], []

    def walk(p):
        mime = p.get("mimeType", "")
        data = (p.get("body") or {}).get("data")
        if data and mime == "text/plain":
            plain.append(_b64url_decode(data).decode("utf-8", "ignore"))
        elif data and mime == "text/html":
            html_parts.append(_re.sub(r"<[^>]+>", " ", _b64url_decode(data).decode("utf-8", "ignore")))
        for sub in (p.get("parts") or []):
            walk(sub)

    walk(payload or {})
    texte = ("\n".join(plain).strip() or "\n".join(html_parts).strip())
    return " ".join(texte.split())


async def _traiter_pieces_jointes(client, headers, msg_id, payload, dossier_id, db) -> list[str]:
    """Télécharge les pièces jointes Gmail, EXTRAIT leur texte (service centralisé) et crée des
    Documents 'reçus' rattachés au dossier. Entièrement gardé : ne casse jamais la synchro."""
    from pathlib import Path
    from core.models import Document, StatutDocument
    from core.extraction import extraire_document
    from modules.module_a import _document_mentionne   # AIOS-FIX: cas 2 — rapprochement nom de PJ ↔ checklist
    up = Path(os.getenv("UPLOAD_DIR", "uploads")) / str(dossier_id)
    noms: list[str] = []

    def parts(p):
        yield p
        for s in (p.get("parts") or []):
            yield from parts(s)

    for p in parts(payload or {}):
        fn = p.get("filename")
        att_id = (p.get("body") or {}).get("attachmentId")
        if not fn or not att_id:
            continue
        chemin = up / fn
        # Idempotence : pièce déjà ingérée pour ce dossier (même fichier) → on ne la retélécharge pas
        # (permet de rejouer le traitement, ex. backfill après validation HITL, sans créer de doublon).
        if db.query(Document).filter(Document.dossier_id == dossier_id,
                                     Document.chemin_stockage == str(chemin)).first():
            noms.append(fn)
            continue
        try:
            r = await client.get(f"{GOOGLE_GMAIL_MESSAGES_URL}/{msg_id}/attachments/{att_id}", headers=headers)
            if r.status_code != 200:
                continue
            data = _b64url_decode(r.json().get("data", ""))
            if not data or len(data) > 8_000_000:    # garde-fou : on ignore > 8 Mo
                continue
            up.mkdir(parents=True, exist_ok=True)
            chemin.write_bytes(data)
            extr = extraire_document(str(chemin), fn, p.get("mimeType"))
            # AIOS-FIX: cas 2 — si le nom de fichier correspond à une pièce ATTENDUE de la checklist,
            # on complète CETTE pièce (→ passe en « Reçu ») au lieu de créer un doublon.
            cible = None
            for d in db.query(Document).filter(Document.dossier_id == dossier_id,
                                               Document.statut == StatutDocument.ATTENDU).all():
                if _document_mentionne(d.nom, fn.lower()):
                    cible = d
                    break
            if cible is None:
                cible = Document(nom=fn, type_doc="email", dossier_id=dossier_id)
                db.add(cible)
            cible.statut = StatutDocument.RECU
            cible.chemin_stockage = str(chemin)
            cible.mime_type = p.get("mimeType")
            cible.ocr_contenu = (extr["texte"] or cible.ocr_contenu)
            cible.recu_at = datetime.utcnow()
            noms.append(fn)
        except Exception as e:
            print(f"[Sync] PJ '{fn}' non traitée : {e}")
    if noms:
        db.commit()
        # RAG (Module L) : indexer les pièces fraîchement versées (best-effort).
        try:
            from modules.module_l_cession import indexer_dossier_si_besoin
            await indexer_dossier_si_besoin(dossier_id, db)
        except Exception as e:
            print(f"[Sync] indexation RAG des PJ ignorée : {e}")
    return noms


async def ingerer_pieces_jointes_dossier(dossier_id: str, db: Session) -> list[str]:
    """
    Backfill : (re)télécharge et ingère les pièces jointes des emails DÉJÀ rattachés à un dossier
    mais dont les PJ n'ont jamais été versées — typiquement quand le dossier est créé APRÈS coup
    (validation d'une proposition HITL), car la sync n'ingère les PJ que si le dossier existe déjà
    au moment de la réception. Idempotent (cf. `_traiter_pieces_jointes`). Entièrement gardé.
    """
    noms: list[str] = []
    emails = (
        db.query(EmailClassifie)
        .filter(EmailClassifie.dossier_id == dossier_id,
                EmailClassifie.message_id_externe.isnot(None))
        .all()
    )
    if not emails:
        return noms
    tokens: dict[str, str] = {}   # cache access_token par intégration
    async with httpx.AsyncClient() as client:
        for e in emails:
            try:
                integ = db.query(EmailIntegration).filter(
                    EmailIntegration.id == e.integration_id).first()
                if not integ:
                    continue
                if integ.id not in tokens:
                    tokens[integ.id] = await rafraichir_access_token_google(integ)
                headers = {"Authorization": f"Bearer {tokens[integ.id]}"}
                msg = await client.get(
                    f"{GOOGLE_GMAIL_MESSAGES_URL}/{e.message_id_externe}",
                    headers=headers, params={"format": "full"},
                )
                if msg.status_code != 200:
                    continue
                payload = msg.json().get("payload", {})
                pj = await _traiter_pieces_jointes(
                    client, headers, e.message_id_externe, payload, dossier_id, db)
                noms.extend(pj)
            except Exception as ex:
                print(f"[Backfill PJ] email {e.id} non traité : {ex}")
    return noms


async def _synchroniser_emails_avocat_impl(integration: EmailIntegration, db: Session, max_emails: int = 50):
    """
    Récupère les derniers emails non lus depuis Gmail et les classifie.
    Appelé par Celery toutes les 15 minutes.
    """
    if not integration.actif:
        return

    access_token = await rafraichir_access_token_google(integration)

    # Dossiers actifs pour aide à la détection
    dossiers = db.query(Dossier).filter(
        Dossier.cabinet_id.isnot(None)
    ).limit(50).all()
    dossiers_ctx = [
        {"reference": d.reference, "titre": d.titre, "client": ""}
        for d in dossiers
    ]

    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient() as client:
        # Récupérer les IDs des messages non lus
        resp = await client.get(
            GOOGLE_GMAIL_MESSAGES_URL,
            headers=headers,
            params={"q": "is:unread", "maxResults": max_emails}
        )
        if resp.status_code != 200:
            return

        messages = resp.json().get("messages", [])

        for msg_info in messages:
            msg_id = msg_info["id"]

            # Vérifier si déjà traité
            existant = db.query(EmailClassifie).filter(
                EmailClassifie.message_id_externe == msg_id
            ).first()
            if existant:
                continue

            # Récupérer le message COMPLET (corps + pièces jointes)
            msg_resp = await client.get(
                f"{GOOGLE_GMAIL_MESSAGES_URL}/{msg_id}",
                headers=headers,
                params={"format": "full"}
            )
            if msg_resp.status_code != 200:
                continue

            msg_data = msg_resp.json()
            payload = msg_data.get("payload", {})
            headers_list = payload.get("headers", [])
            headers_map = {h["name"]: h["value"] for h in headers_list}

            sujet = headers_map.get("Subject", "(sans sujet)")
            expediteur = headers_map.get("From", "")
            snippet = msg_data.get("snippet", "")
            corps = _extraire_corps_gmail(payload) or snippet      # CORPS COMPLET de l'email

            # Classification IA (sur le corps complet, tronqué pour limiter les tokens)
            try:
                classif = await classifier_email_ia(sujet, corps[:1500], expediteur, dossiers_ctx)
            except Exception as e:
                err = str(e)
                if "429" in err or "quota" in err.lower() or "ResourceExhausted" in err or "rate_limit" in err.lower():
                    print(f"[Sync] Quota LLM dépassé — arrêt de la sync (réessayez dans 1 min)")
                    break
                print(f"[Sync] Erreur classification email {msg_id}: {e}")
                continue

            # Ne pas traiter les spams, pubs et emails non professionnels
            if classif.get("categorie") in ("spam", "autre") and classif.get("priorite") == "basse":
                continue

            # Résoudre dossier_id depuis la référence détectée — AVEC garde d'isolation (8a).
            dossier_id = None
            if classif.get("dossier_reference"):
                dossier = db.query(Dossier).filter(
                    Dossier.reference == classif["dossier_reference"]
                ).first()
                if dossier and await _sujets_proches(f"{sujet}\n{corps[:1500]}", dossier):
                    dossier_id = dossier.id
                elif dossier:
                    print(f"[Sync] Isolation : email « {sujet[:40]} » NON rattaché à "
                          f"{dossier.reference} (sujet divergent) → nouveau dossier")

            # Email professionnel sans dossier existant :
            #   mode "propose" (défaut) -> PROPOSITION en attente de validation avocat
            #   mode "auto"             -> création directe du dossier (ancien comportement)
            dossier_cree_auto = False
            proposition_thread = None
            if (
                dossier_id is None
                and classif.get("categorie") in ("client", "prospect", "juridiction", "fournisseur", "administratif")
                and classif.get("action_suggeree") in ("créer_dossier", "répondre", "ajouter_deadline")
            ):
                # Cas 10 — GARDE DÉONTOLOGIQUE : conflit d'intérêts AVANT toute création (déterministe).
                conflit = {"conflit": False}
                try:
                    from modules.module_b import detecter_conflit_interets
                    nom_prospect = expediteur.split("<")[0].strip().strip('"') or expediteur
                    conflit = detecter_conflit_interets(db, nom_prospect, "default")
                except Exception as e:
                    print(f"[Sync] vérification conflit d'intérêts échouée : {e}")

                if conflit.get("conflit"):
                    # On NE crée NI proposition NI dossier : mise en quarantaine pour l'avocat.
                    classif["sous_categorie"] = "conflit_potentiel"
                    classif["action_suggeree"] = "verifier_conflit"
                    classif["resume"] = ("⚠️ CONFLIT D'INTÉRÊTS POTENTIEL — " + (classif.get("resume") or ""))[:240]
                    print(f"[Sync] CONFLIT D'INTÉRÊTS potentiel sur « {sujet[:40]} » "
                          f"(dossiers {[d['reference'] for d in conflit.get('dossiers', [])]}) → quarantaine")
                elif os.getenv("EMAIL_DOSSIER_MODE", "propose").lower() == "auto":
                    dossier_id, dossier_cree_auto = await _creer_dossier_depuis_email(
                        integration, expediteur, sujet, corps[:1500], classif, db
                    )
                else:
                    proposition_thread = await creer_proposition_dossier(
                        expediteur, sujet, classif.get("resume", ""),
                        classif.get("categorie", "client"), integration.avocat_id, db
                    )

            priorite_map = {
                "urgent": PrioriteLevel.URGENT,
                "haute": PrioriteLevel.HAUTE,
                "basse": PrioriteLevel.BASSE,
            }

            email_obj = EmailClassifie(
                integration_id=integration.id,
                message_id_externe=msg_id,
                expediteur=expediteur,
                sujet=sujet,
                corps_extrait=corps[:8000],
                date_reception=datetime.utcnow(),
                categorie=classif.get("categorie", "autre"),
                sous_categorie=classif.get("sous_categorie", "autre"),
                priorite=priorite_map.get(classif.get("priorite", "standard"), PrioriteLevel.STANDARD),
                resume_ia=classif.get("resume", ""),
                action_suggeree=("valider_dossier" if proposition_thread else classif.get("action_suggeree", "archiver")),
                dossier_id=dossier_id,
                dossier_detecte_auto=dossier_id is not None,
                proposition_thread_id=proposition_thread,
            )
            try:
                db.add(email_obj)
                db.commit()
            except Exception as e:
                db.rollback()
                if "duplicate key" in str(e).lower() or "uniqueviolation" in str(e).lower():
                    continue  # email déjà inséré (course concurrente) — ignoré
                print(f"[Sync] insertion email échouée : {e}")
                continue

            # Module A — rapprochement automatique : si l'email rattaché à un dossier
            # évoque une pièce attendue, on bascule son statut sur « Reçu ».
            if dossier_id:
                # Pièces jointes de l'email → Documents reçus + texte extrait (service central).
                try:
                    pj = await _traiter_pieces_jointes(client, headers, msg_id, payload, dossier_id, db)
                    if pj:
                        print(f"[Sync] pièces jointes extraites pour {dossier_id} : {pj}")
                except Exception as e:
                    db.rollback()
                    print(f"[Sync] pièces jointes non traitées : {e}")

                try:
                    from modules.module_a import rapprocher_documents_recus
                    recus = rapprocher_documents_recus(db, dossier_id, f"{sujet}\n{corps[:1500]}")
                    if recus:
                        print(f"[Sync] pièces basculées en reçu pour {dossier_id} : {recus}")
                except Exception as e:
                    db.rollback()
                    print(f"[Sync] rapprochement documents échoué : {e}")

                # Cas 8 — délai déterministe détecté au triage (0 % LLM) → deadline dans le dossier.
                try:
                    jours = classif.get("urgence_delai_jours")
                    if jours:
                        from modules.module_f import creer_deadline_jours
                        d_urg = db.query(Dossier).filter(Dossier.id == dossier_id).first()
                        if d_urg:
                            creer_deadline_jours(d_urg, int(jours), db,
                                                 motif=classif.get("urgence_motif") or "délai signalé dans l'email")
                            print(f"[Sync] deadline urgente J+{jours} créée pour {dossier_id}")
                except Exception as e:
                    db.rollback()
                    print(f"[Sync] création deadline urgente échouée : {e}")

                # Module D (Item 7) — RDV auto : si le client propose une date, on crée
                # un RDV « à confirmer » visible dans la fiche dossier.
                try:
                    from modules.module_d import detecter_et_creer_rdv
                    from core.models import Client as _Client
                    d_obj = db.query(Dossier).filter(Dossier.id == dossier_id).first()
                    cli = (db.query(_Client).filter(_Client.id == d_obj.client_id).first()
                           if d_obj and d_obj.client_id else None)
                    if d_obj:
                        rdv = await detecter_et_creer_rdv(d_obj, cli, f"{sujet}\n{corps[:1500]}", db)
                        if rdv:
                            print(f"[Sync] RDV proposé détecté pour {d_obj.reference} le {rdv.date_heure}")
                except Exception as e:
                    db.rollback()
                    print(f"[Sync] détection RDV échouée : {e}")

    integration.derniere_sync = datetime.utcnow()
    db.commit()


async def _creer_dossier_depuis_email(
    integration: EmailIntegration,
    expediteur: str,
    sujet: str,
    snippet: str,
    classif: dict,
    db: Session
) -> tuple:
    """
    Crée automatiquement un dossier et un client depuis un email professionnel.
    Retourne (dossier_id, True) si créé, (None, False) sinon.
    """
    import uuid, random, string
    from core.models import Client as ClientModel, Cabinet, Avocat, Specialite, DossierStatus

    try:
        # Extraire nom et email de l'expéditeur  (ex: "Jean Dupont <jean@cabinet.fr>")
        if "<" in expediteur:
            nom_exp = expediteur.split("<")[0].strip().strip('"')
            email_exp = expediteur.split("<")[1].rstrip(">").strip()
        else:
            nom_exp = expediteur.split("@")[0]
            email_exp = expediteur.strip()

        # Trouver ou créer le client
        client = db.query(ClientModel).filter(ClientModel.email == email_exp).first()
        if not client:
            client = ClientModel(
                id=str(uuid.uuid4()),
                cabinet_id="default",
                nom=nom_exp or email_exp,
                email=email_exp,
                type_client="professionnel",
                notes=f"Créé automatiquement depuis email : {sujet}"
            )
            db.add(client)
            db.flush()

        # Trouver l'avocat lié à l'intégration
        avocat = db.query(Avocat).filter(Avocat.id == integration.avocat_id).first()
        if not avocat:
            return None, False

        # Déterminer le type de dossier depuis la catégorie IA
        type_map = {
            "client": "consultation",
            "prospect": "prospect",
            "juridiction": "procedure_judiciaire",
            "fournisseur": "contrat_fournisseur_pharmacie",
            "administratif": "litige_ars_pharmacie",
        }
        type_dossier = type_map.get(classif.get("categorie", "autre"), "consultation")

        ref = "AUTO-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        dossier = Dossier(
            id=str(uuid.uuid4()),
            cabinet_id="default",
            avocat_id=avocat.id,
            client_id=client.id,
            reference=ref,
            titre=f"[AUTO] {sujet[:80]}",
            specialite=Specialite.AFFAIRES,
            status=DossierStatus.NOUVEAU,
            priorite=PrioriteLevel.HAUTE if classif.get("priorite") == "urgent" else PrioriteLevel.STANDARD,
            description=f"Dossier créé automatiquement depuis email de {expediteur}.\nRésumé IA : {classif.get('resume', '')}",
            metadonnees={
                "type_dossier": type_dossier,
                "source": "email_auto",
                "email_origine": expediteur,
                "action_suggeree": classif.get("action_suggeree"),
            }
        )
        db.add(dossier)
        db.flush()

        # Indexation sémantique (Module B) — pour que le dossier soit retrouvable
        try:
            texte = " ".join(filter(None, [dossier.titre, dossier.reference, dossier.description]))
            dossier.embedding = await get_embedding(texte)
        except Exception as e:
            print(f"[indexer] embedding dossier auto échoué : {e}")

        return dossier.id, True

    except Exception as e:
        print(f"[ERREUR] Auto-creation dossier echouee : {e}")
        return None, False
