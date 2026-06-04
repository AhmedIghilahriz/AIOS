"""
AIOS — Module D : Gestion des rendez-vous
Cal.com self-hosted (GRATUIT — remplace Calendly API à 8€/mois)
Génère automatiquement une fiche de préparation avant chaque RDV
"""
import httpx
import os
import json
import html
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from core.models import RendezVous, Dossier, Client, FichePreparation, Cabinet
from core.orchestrateur import generer_texte_juridique, claude, CLAUDE_MODEL

CALCOM_API_URL = os.getenv("CALCOM_API_URL", "http://calcom:5555/api/v1")
CALCOM_API_KEY = os.getenv("CALCOM_API_KEY", "")


# ── Intégration Cal.com ───────────────────────────────────────────────

async def creer_rdv_calcom(
    titre: str,
    date_heure: datetime,
    duree_minutes: int,
    client_email: str,
    client_nom: str,
    description: str = ""
) -> dict:
    """
    Crée un RDV dans Cal.com (self-hosted gratuit).
    Si Cal.com n'est pas configuré, enregistre localement.
    """
    if not CALCOM_API_KEY:
        # Mode dégradé : pas de Cal.com, juste enregistrement local
        return {
            "id": f"local-{datetime.utcnow().timestamp()}",
            "status": "confirmed",
            "note": "Cal.com non configuré — RDV enregistré localement"
        }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{CALCOM_API_URL}/bookings",
            headers={"Authorization": f"Bearer {CALCOM_API_KEY}"},
            json={
                "eventTypeId": 1,
                "start": date_heure.isoformat(),
                "end": (date_heure.replace(minute=date_heure.minute + duree_minutes)).isoformat(),
                "responses": {
                    "name": client_nom,
                    "email": client_email,
                    "notes": description
                },
                "timeZone": "Europe/Paris",
                "language": "fr"
            }
        )
        return response.json()


async def creer_rdv_local(
    dossier: Dossier,
    client: Client,
    type_rdv: str,  # decouverte | approfondi | suivi
    date_heure: datetime,
    duree_minutes: int,
    db: Session
) -> RendezVous:
    """Crée un RDV en base de données. PLANIFICATION SEULE — la trame méthodologique vit
    désormais dans la « Fiche de préparation » (document maître), pas ici."""

    rdv = RendezVous(
        type_rdv=type_rdv,
        duree_minutes=duree_minutes,
        date_heure=date_heure,
        statut="confirme",
        client_id=client.id,
        avocat_id=dossier.avocat_id,
        dossier_id=dossier.id,
        fiche_preparation=None,
    )

    # Essayer Cal.com si configuré
    try:
        calcom_result = await creer_rdv_calcom(
            titre=f"RDV {type_rdv} — {dossier.titre}",
            date_heure=date_heure,
            duree_minutes=duree_minutes,
            client_email=client.email or "",
            client_nom=f"{client.prenom} {client.nom}",
            description=f"Dossier: {dossier.reference}"
        )
        rdv.calcom_booking_id = calcom_result.get("id")
    except Exception:
        pass  # Cal.com optionnel

    db.add(rdv)
    db.commit()
    db.refresh(rdv)
    return rdv


# ── Fiche de préparation ──────────────────────────────────────────────

async def generer_fiche_preparation(
    dossier: Dossier,
    client: Client,
    type_rdv: str,
    contexte_documents: str = "",
    contexte_email: str = "",
    questions_strategiques: list | None = None,
) -> str:
    """
    Génère la FICHE DE PRÉPARATION (document maître) en Markdown, structurée en DEUX parties :
      1. Synthèse des éléments CONNUS (faits, chiffres, montants, clauses, documents analysés) ;
      2. Checklist d'entretien (questions stratégiques + points de vigilance), adaptée à la
         spécialité, avec REMPLISSAGE DYNAMIQUE : ce qui est déjà connu est marqué « ☑ Obtenu »
         (réponse rappelée) ; seules les questions encore SANS réponse restent « ☐ À poser ».
    """
    types_descriptions = {
        "decouverte": "premier rendez-vous de découverte",
        "approfondi": "rendez-vous d'approfondissement du dossier",
        "suivi": "rendez-vous de suivi de dossier en cours",
    }
    questions_strategiques = questions_strategiques or []

    tache = (
        f"Rédige la FICHE DE PRÉPARATION (document maître) pour un "
        f"{types_descriptions.get(type_rdv, type_rdv)}, en **Markdown**, structurée en DEUX parties "
        "OBLIGATOIRES et clairement titrées :\n\n"
        "## Partie 1 — Synthèse des éléments connus\n"
        "À partir des EXTRAITS DE PIÈCES et des ÉCHANGES RÉCENTS fournis, liste de façon factuelle ce "
        "qui est DÉJÀ établi : faits, dates clés, montants/chiffres, parties en présence et CLAUSES "
        "contractuelles importantes — en citant le document/échange source entre parenthèses. "
        "N'invente RIEN : si une information n'est pas dans les éléments fournis, ne l'écris pas.\n\n"
        "## Partie 2 — Checklist d'entretien\n"
        "À partir des « questions stratégiques de référence » (adaptées à la spécialité) ET des points "
        "de vigilance juridiques pertinents, applique cette LOGIQUE DE REMPLISSAGE DYNAMIQUE :\n"
        "- Si la réponse figure déjà dans la Partie 1 (pièces/échanges), inscris la question sous "
        "« ✅ Déjà renseigné » au format « ☑ <question> → <réponse connue, en bref> » et NE la repose PAS.\n"
        "- Sinon, inscris-la sous « ❓ À aborder en priorité » au format « ☐ <question> » : ce sont les "
        "SEULES questions auxquelles il manque encore une réponse.\n"
        "Termine par une courte rubrique « ⚠️ Points de vigilance » (risques/délais à surveiller). "
        "Toute appréciation juridique doit être préfixée [VERIFICATION REQUISE PAR L'AVOCAT]."
    )

    return await generer_texte_juridique(
        tache,
        {
            "client": f"{client.prenom or ''} {client.nom or ''}".strip(),
            "type_client": client.type_client,
            "dossier_titre": dossier.titre,
            "dossier_specialite": getattr(dossier.specialite, "value", dossier.specialite),
            "dossier_status": getattr(dossier.status, "value", dossier.status),
            "dossier_description": dossier.description,
            "metadonnees": dossier.metadonnees,
            "notes_client": client.notes,
            "questions_strategiques_de_reference": questions_strategiques,
            "extraits_pieces": contexte_documents or "(aucune pièce exploitable)",
            "echanges_recents": contexte_email or "(aucun échange disponible)",
        },
        specialite=getattr(dossier.specialite, "value", dossier.specialite),
        max_tokens=1300,
    )


# ── Persistance & versionnement de la fiche de préparation ────────────

def derniere_fiche(dossier_id: str, db: Session) -> FichePreparation | None:
    """Dernière version (la plus récente) de la fiche pour un dossier."""
    return (
        db.query(FichePreparation)
        .filter(FichePreparation.dossier_id == dossier_id)
        .order_by(FichePreparation.version.desc())
        .first()
    )


def lister_fiches(dossier_id: str, db: Session) -> list[FichePreparation]:
    """Toutes les versions, de la plus récente à la plus ancienne."""
    return (
        db.query(FichePreparation)
        .filter(FichePreparation.dossier_id == dossier_id)
        .order_by(FichePreparation.version.desc())
        .all()
    )


def _derniers_emails(db: Session, dossier_id: str, n: int = 3) -> str:
    """Contexte conversationnel : les n derniers emails du dossier (du plus ancien au plus récent)."""
    from core.models import EmailClassifie
    rows = (
        db.query(EmailClassifie)
        .filter(EmailClassifie.dossier_id == dossier_id)
        .order_by(EmailClassifie.date_reception.desc().nullslast())
        .limit(n).all()
    )
    morceaux = []
    for e in reversed(rows):
        quand = e.date_reception.strftime("%d/%m/%Y") if e.date_reception else "?"
        morceaux.append(f"[{quand}] {e.expediteur or ''} — « {e.sujet or ''} » : {(e.corps_extrait or '')[:400]}")
    return "\n".join(morceaux)


async def generer_et_versionner(
    dossier: Dossier, client: Client, type_rdv: str, db: Session, ecraser: bool = False
) -> FichePreparation:
    """
    Génère la fiche maître (Synthèse + Checklist dynamique) via l'IA puis la persiste :
      • ecraser=False → nouvelle version (v_{max+1}), l'historique est conservé ;
      • ecraser=True  → met à jour la dernière version en place (pas de nouvelle entrée).
    """
    from core.extraction import contexte_documents as _ctx_docs
    from modules.module_c import QUESTIONS_PAR_SPECIALITE
    ctx_docs = _ctx_docs(db, dossier.id, max_chars=4000)
    ctx_email = _derniers_emails(db, dossier.id)
    spec = str(getattr(dossier.specialite, "value", dossier.specialite) or "")
    questions = QUESTIONS_PAR_SPECIALITE.get(spec) or QUESTIONS_PAR_SPECIALITE.get("affaires", [])
    contenu = await generer_fiche_preparation(
        dossier, client, type_rdv,
        contexte_documents=ctx_docs, contexte_email=ctx_email, questions_strategiques=questions,
    )
    latest = derniere_fiche(dossier.id, db)

    if ecraser and latest:
        latest.contenu = contenu
        latest.type_rdv = type_rdv
        latest.created_at = datetime.utcnow()
        db.commit()
        db.refresh(latest)
        return latest

    fiche = FichePreparation(
        dossier_id=dossier.id,
        version=(latest.version + 1) if latest else 1,
        type_rdv=type_rdv,
        contenu=contenu,
    )
    db.add(fiche)
    db.commit()
    db.refresh(fiche)
    return fiche


import re as _re


def _md_inline(texte: str) -> str:
    """Convertit le Markdown INLINE (gras/italique/code) d'un texte DÉJÀ échappé HTML."""
    texte = _re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", texte)
    texte = _re.sub(r"__(.+?)__", r"<strong>\1</strong>", texte)
    texte = _re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", texte)
    texte = _re.sub(r"`(.+?)`", r"<code>\1</code>", texte)
    return texte


def markdown_vers_html(texte: str) -> str:
    """
    Rendu Markdown → HTML léger et SANS dépendance (gras, italique, titres #,
    puces - / *, listes numérotées, paragraphes). Le texte est d'abord échappé
    (sécurité), puis les balises Markdown sont converties.
    """
    if not texte:
        return ""
    lignes = html.escape(texte).replace("\r\n", "\n").split("\n")
    html_out: list[str] = []
    liste_ouverte = None  # "ul" | "ol" | None

    def _fermer_liste():
        nonlocal liste_ouverte
        if liste_ouverte:
            html_out.append(f"</{liste_ouverte}>")
            liste_ouverte = None

    for ligne in lignes:
        l = ligne.strip()
        if not l:
            _fermer_liste()
            continue
        m_h = _re.match(r"^(#{1,4})\s+(.*)$", l)
        m_puce = _re.match(r"^[-*•]\s+(.*)$", l)
        m_num = _re.match(r"^(\d+)[.)]\s+(.*)$", l)
        if m_h:
            _fermer_liste()
            niveau = min(len(m_h.group(1)) + 1, 5)
            html_out.append(f"<h{niveau}>{_md_inline(m_h.group(2))}</h{niveau}>")
        elif m_puce:
            if liste_ouverte != "ul":
                _fermer_liste(); html_out.append("<ul>"); liste_ouverte = "ul"
            html_out.append(f"<li>{_md_inline(m_puce.group(1))}</li>")
        elif m_num:
            if liste_ouverte != "ol":
                _fermer_liste(); html_out.append("<ol>"); liste_ouverte = "ol"
            html_out.append(f"<li>{_md_inline(m_num.group(2))}</li>")
        else:
            _fermer_liste()
            html_out.append(f"<p>{_md_inline(l)}</p>")
    _fermer_liste()
    return "\n".join(html_out)


def construire_html_fiche(fiche: FichePreparation, dossier: Dossier, cabinet: Cabinet | None) -> str:
    """
    Template HTML imprimable robuste (en-tête cabinet + mentions légales + CSS d'impression).
    L'impression PDF se fait via le navigateur (Ctrl+P / bouton Imprimer) — aucune dépendance lourde.
    """
    cabinet_nom = (cabinet.nom if cabinet and cabinet.nom else "Cabinet d'Avocats")
    cabinet_adresse = (cabinet.adresse if cabinet and getattr(cabinet, "adresse", None) else "")
    quand = fiche.created_at.strftime("%d/%m/%Y à %Hh%M") if fiche.created_at else ""
    corps_html = markdown_vers_html(fiche.contenu or "")
    mentions = (
        "Document de travail strictement confidentiel, couvert par le secret professionnel de "
        "l'avocat (art. 66-5 de la loi n°71-1130 du 31 décembre 1971). Usage interne au cabinet — "
        "ne pas diffuser au client sans validation."
    )
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<title>Fiche de préparation — {html.escape(dossier.reference or '')} v{fiche.version}</title>
<style>
  @page {{ margin: 2cm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: Georgia, 'Times New Roman', serif; color: #1a1a1a; line-height: 1.5; max-width: 800px; margin: 24px auto; padding: 0 16px; }}
  .entete {{ border-top: 4px solid #1a3a5c; padding-top: 14px; margin-bottom: 8px; }}
  .entete h1 {{ font-size: 20px; color: #1a3a5c; margin: 0; }}
  .entete .meta {{ font-size: 12px; color: #555; margin-top: 4px; }}
  .titre-dossier {{ font-size: 16px; font-weight: bold; margin: 18px 0 4px; }}
  .badges {{ font-size: 12px; color: #444; margin-bottom: 12px; }}
  .contenu {{ font-size: 14px; }}
  .contenu h2, .contenu h3, .contenu h4 {{ color: #1a3a5c; margin: 14px 0 4px; }}
  .contenu ul, .contenu ol {{ margin: 6px 0 6px 22px; }}
  .contenu li {{ margin: 2px 0; }}
  .contenu p {{ margin: 6px 0; }}
  .mentions {{ border-top: 1px solid #ccc; margin-top: 28px; padding-top: 10px; font-size: 11px; color: #777; }}
  .barre {{ margin: 16px 0; }}
  .btn {{ background: #1a3a5c; color: #fff; border: 0; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 13px; }}
  @media print {{ .no-print {{ display: none !important; }} body {{ margin: 0; }} }}
</style></head>
<body>
  <div class="barre no-print"><button class="btn" onclick="window.print()">🖨 Imprimer / Enregistrer en PDF</button></div>
  <div class="entete">
    <h1>{html.escape(cabinet_nom)}</h1>
    <div class="meta">{html.escape(cabinet_adresse)}</div>
  </div>
  <div class="titre-dossier">Fiche de préparation — {html.escape(dossier.titre or '')}</div>
  <div class="badges">Réf. {html.escape(dossier.reference or '')} · Version {fiche.version} · {html.escape(fiche.type_rdv or '')} · Établie le {quand}</div>
  <div class="contenu">{corps_html}</div>
  <div class="mentions">{mentions}</div>
</body></html>"""


# ── Détection automatique de RDV depuis un email client (Item 7) ──────

_SIGNAUX_RDV = (
    "rendez-vous", "rendez vous", "rdv", "disponible", "dispo", "créneau", "creneau",
    "rencontrer", "vous voir", "prendre date", "convenir d'un", "fixer un",
    "je suis libre", "disponibilité", "disponibilites", "passer au cabinet",
)


def _contient_signal_rdv(texte: str) -> bool:
    """Pré-filtre déterministe (gratuit) avant tout appel LLM."""
    t = (texte or "").lower()
    return any(s in t for s in _SIGNAUX_RDV)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip().replace("Z", "")
    if len(s) == 10 and "T" not in s:          # date seule → 09:00 par défaut
        s = s + "T09:00"
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


async def detecter_demande_rdv(texte: str, maintenant: datetime | None = None) -> dict:
    """
    Détecte une demande/disponibilité de RDV et extrait une date EXPLICITE (LLM, jamais d'invention).
    Retourne {"rdv": bool, "date_heure": "YYYY-MM-DDTHH:MM"|None, "motif": str}.
    """
    maintenant = maintenant or datetime.utcnow()
    if not _contient_signal_rdv(texte):
        return {"rdv": False}
    from modules.module_c import _extraire_json
    prompt = (
        f"Date du jour : {maintenant.strftime('%Y-%m-%d')}.\n"
        "Analyse ce message d'un client de cabinet d'avocats. Indique s'il PROPOSE ou DEMANDE un "
        "rendez-vous, et SEULEMENT si une date/heure EXPLICITE est mentionnée, donne-la (résous les "
        "formulations relatives type « mardi prochain » par rapport à la date du jour). "
        "N'INVENTE JAMAIS de date : si rien d'explicite, mets null.\n"
        "Réponds STRICTEMENT en JSON : {\"rdv\": true|false, \"date_heure\": \"YYYY-MM-DDTHH:MM\" ou null, \"motif\": \"...\"}\n\n"
        f"Message :\n\"\"\"\n{(texte or '')[:1200]}\n\"\"\""
    )
    try:
        brut = await llm_chat(
            prompt,
            system="Tu extrais une demande de rendez-vous. JSON strict, jamais d'invention de date.",
            max_tokens=200, fast=True,
        )
        data = _extraire_json(brut)
        return {"rdv": bool(data.get("rdv")), "date_heure": data.get("date_heure"),
                "motif": str(data.get("motif", ""))[:200]}
    except Exception as e:
        print(f"[RDV auto] détection échouée : {e}")
        return {"rdv": False}


def enregistrer_rdv_propose(dossier: Dossier, client: Client | None, date_heure: datetime,
                            db: Session, motif: str = "") -> RendezVous:
    """Crée un RDV « à confirmer » (proposé par le client), idempotent sur (dossier, date)."""
    existe = db.query(RendezVous).filter(
        RendezVous.dossier_id == dossier.id, RendezVous.date_heure == date_heure
    ).first()
    if existe:
        return existe
    rdv = RendezVous(
        type_rdv="suivi", duree_minutes=30, date_heure=date_heure, statut="a_confirmer",
        client_id=(client.id if client else None), avocat_id=dossier.avocat_id,
        dossier_id=dossier.id,
        fiche_preparation=(f"[RDV proposé par le client] {motif}".strip()),
    )
    db.add(rdv)
    db.commit()
    db.refresh(rdv)
    return rdv


async def detecter_et_creer_rdv(dossier: Dossier, client: Client | None, texte: str, db: Session):
    """Chaîne complète : détecte → valide la date (future) → crée le RDV « à confirmer »."""
    res = await detecter_demande_rdv(texte)
    if not res.get("rdv"):
        return None
    dt = _parse_dt(res.get("date_heure"))
    if not dt or dt < datetime.utcnow() - timedelta(days=1):   # pas de date explicite/valide
        return None
    return enregistrer_rdv_propose(dossier, client, dt, db, res.get("motif", ""))


async def envoyer_confirmation_rdv(
    rdv: RendezVous,
    client_email: str,
    client_nom: str,
    dossier: Dossier
) -> bool:
    """Envoie la confirmation de RDV au client via Resend (gratuit)."""
    import resend
    resend.api_key = os.getenv("RESEND_API_KEY")
    EMAIL_FROM = os.getenv("EMAIL_FROM", "cabinet@votre-domaine.fr")
    EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "Cabinet Juridique")

    adresse_cabinet = os.getenv("ADRESSE_CABINET", "Adresse du cabinet à configurer dans .env")

    resend.Emails.send({
        "from": f"{EMAIL_FROM_NAME} <{EMAIL_FROM}>",
        "to": client_email,
        "subject": f"Confirmation de votre rendez-vous — {rdv.date_heure.strftime('%d/%m/%Y à %Hh%M')}",
        "html": f"""
        <div style="font-family: Georgia, serif; max-width: 600px; color: #1a1a1a;">
            <div style="border-top: 3px solid #1a3a5c; padding: 24px 0 16px;">
                <h2 style="color: #1a3a5c;">Votre rendez-vous est confirmé</h2>
            </div>
            <p>Madame, Monsieur {client_nom},</p>
            <p>Nous vous confirmons votre rendez-vous :</p>
            <div style="background: #f8f9fa; padding: 16px; border-radius: 6px; margin: 16px 0;">
                <p><strong>📅 Date :</strong> {rdv.date_heure.strftime('%A %d %B %Y à %Hh%M')}</p>
                <p><strong>⏱ Durée :</strong> {rdv.duree_minutes} minutes</p>
                <p><strong>📁 Dossier :</strong> {dossier.titre}</p>
                <p><strong>📍 Adresse :</strong> {adresse_cabinet}</p>
            </div>
            <p>Merci de vous munir de tous vos documents en rapport avec votre dossier.</p>
            <p>En cas d'empêchement, merci de nous prévenir au moins 24h à l'avance.</p>
            <div style="border-top: 1px solid #e5e7eb; margin-top: 24px; padding-top: 16px;
                        font-size: 12px; color: #9ca3af;">
                {EMAIL_FROM_NAME} — Confidentiel
            </div>
        </div>
        """
    })
    return True


async def envoyer_rappel_rdv(
    rdv: RendezVous,
    client_email: str,
    client_nom: str
) -> bool:
    """Rappel automatique envoyé 24h avant le RDV."""
    import resend
    resend.api_key = os.getenv("RESEND_API_KEY")

    resend.Emails.send({
        "from": f"{os.getenv('EMAIL_FROM_NAME', 'Cabinet')} <{os.getenv('EMAIL_FROM')}>",
        "to": client_email,
        "subject": f"Rappel — Votre rendez-vous demain à {rdv.date_heure.strftime('%Hh%M')}",
        "html": f"""
        <div style="font-family: Georgia, serif; max-width: 600px;">
            <p>Madame, Monsieur {client_nom},</p>
            <p>Rappel : vous avez un rendez-vous <strong>demain
            {rdv.date_heure.strftime('%d/%m/%Y à %Hh%M')}</strong>.</p>
            <p>Durée prévue : {rdv.duree_minutes} minutes.</p>
            <p>N'oubliez pas vos documents.</p>
        </div>
        """
    })
    return True