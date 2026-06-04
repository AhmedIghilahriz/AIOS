"""
AIOS — Module A : Collecte & relance de documents
Emails via Resend (GRATUIT 3 000/mois — remplace SendGrid ~15€/mois)
OCR via Tesseract (GRATUIT — remplace AWS Textract)
"""
import resend
import pytesseract
from PIL import Image
from sqlalchemy.orm import Session
from core.models import Dossier, Document, StatutDocument
from core.orchestrateur import generer_texte_juridique, get_embedding
from datetime import datetime, timedelta
import os
import json
import re

resend.api_key = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM", "cabinet@votre-domaine.fr")
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "Cabinet Juridique")


# ── Documents requis par type de dossier ─────────────────────────────

DOCUMENTS_PAR_TYPE = {
    "cession_officine": [
        "Trois derniers bilans comptables certifiés",
        "Bail commercial et avenants",
        "Extrait Kbis (moins de 3 mois)",
        "Statuts à jour de la société",
        "Licence / arrêté ARS d'exploitation de l'officine",
        "Relevés de chiffre d'affaires CPAM (12 mois)",
        "Diplôme et inscription à l'Ordre du pharmacien titulaire",
        "Contrats de travail du personnel",
    ],
    "divorce_amiable": [
        "Livret de famille",
        "Justificatifs de revenus des 3 derniers mois",
        "Avis d'imposition N-1 et N-2",
        "Justificatifs de propriété immobilière",
        "Relevés bancaires des 3 derniers mois",
    ],
    "divorce_contentieux": [
        "Livret de famille",
        "Justificatifs de revenus des 3 derniers mois",
        "Avis d'imposition N-1 et N-2",
        "Liste des biens communs",
        "Justificatifs de propriété immobilière",
        "Relevés bancaires des 6 derniers mois",
        "Tout document prouvant les griefs",
    ],
    "licenciement_contester": [
        "Contrat de travail",
        "Bulletins de salaire des 6 derniers mois",
        "Lettre de convocation à entretien préalable",
        "Lettre de licenciement",
        "Solde de tout compte",
        "Attestation Pôle Emploi",
        "Certificat de travail",
    ],
    "pse_plan_sauvegarde": [
        "Procès-verbal CE/CSE",
        "Document unilatéral ou accord collectif",
        "Liste des postes supprimés",
        "Critères d'ordre des licenciements",
        "Plan de reclassement",
    ],
    "cession_parts_sociales": [
        "Kbis de la société (moins de 3 mois)",
        "Statuts à jour",
        "3 derniers bilans comptables",
        "Pacte d'associés en vigueur",
        "Inventaire des actifs et passifs",
        "Contrats clients en cours",
    ],
    "creation_societe": [
        "Pièces d'identité de tous les associés",
        "Justificatifs de domicile des associés",
        "Attestation de dépôt du capital social",
        "Projet de statuts",
        "Déclaration des bénéficiaires effectifs",
    ],
    "bail_commercial": [
        "Kbis preneur",
        "3 derniers bilans du preneur",
        "RIB",
        "Plan des locaux",
        "Attestation assurance responsabilité civile",
        "Justificatif de caution personnelle",
    ],
    "vente_immeuble": [
        "Titre de propriété",
        "Diagnostics immobiliers (DPE, amiante, plomb...)",
        "Règlement de copropriété et état descriptif de division",
        "3 derniers PV d'AG de copropriété",
        "Taxe foncière",
        "Certificat d'urbanisme",
    ],

    # ── Dossiers PHARMACIE / OFFICINE (spécialité cabinet) ────────────
    "cession_officine": [
        "Licence d'exploitation de l'officine (n° FINESS)",
        "3 derniers bilans comptables certifiés de l'officine",
        "Liasse fiscale des 3 derniers exercices",
        "Chiffre d'affaires TTC détaillé (ordonnances / parapharmacie / OTC)",
        "Bail commercial en cours avec avenants",
        "Kbis de la société exploitante (moins de 3 mois)",
        "Statuts à jour de la société",
        "Effectif salarié (préparateurs, rayonnistes) et contrats de travail",
        "Attestation d'inscription à l'Ordre National des Pharmaciens",
        "État des créances et dettes fournisseurs (grossistes répartiteurs)",
        "Tableau d'amortissements des immobilisations",
        "Autorisation ARS d'ouverture de l'officine",
    ],
    "creation_sel_pharmacie": [
        "Pièces d'identité de tous les associés",
        "Justificatifs d'inscription à l'Ordre des Pharmaciens section A ou D",
        "Attestation de diplôme d'État de docteur en pharmacie",
        "Projet de statuts de la SEL (SELARL, SELAS ou SELURL)",
        "Attestation de dépôt du capital social",
        "Déclaration des bénéficiaires effectifs (RBE)",
        "Justificatif de domiciliation du siège social",
        "Certificat d'absence de condamnation des associés",
        "Accord de principe de l'ARS sur la structure juridique",
    ],
    "bail_commercial_officine": [
        "Projet de bail commercial ou bail en cours",
        "Plan cadastral et surface utile de l'officine",
        "Kbis du preneur / Attestation Ordre des Pharmaciens",
        "3 derniers bilans du preneur",
        "Attestation assurance multirisque professionnelle",
        "Garantie bancaire ou caution personnelle du pharmacien",
        "Règlement de copropriété si local en immeuble",
        "PV autorisant le changement de destination si nécessaire",
    ],
    "contrat_fournisseur_pharmacie": [
        "Projet de contrat fournisseur (laboratoire ou grossiste répartiteur)",
        "Conditions générales de vente du fournisseur",
        "Grille tarifaire et remises négociées",
        "Clause de retour et de péremption des produits",
        "Convention Ségur / charte qualité le cas échéant",
        "RIB de l'officine",
        "Attestation de référencement RPPS",
    ],
    "litige_ars_pharmacie": [
        "Décision administrative contestée (arrêté, mise en demeure, fermeture)",
        "Courriers échangés avec l'ARS",
        "Rapport d'inspection le cas échéant",
        "Attestation d'inscription à l'Ordre des Pharmaciens (à jour)",
        "Procès-verbaux de contrôle ANSM ou ARS",
        "Relevés CA sur la période litigieuse",
        "Contrats de travail du personnel concerné",
    ],
    "procedure_disciplinaire_ordre_pharmaciens": [
        "Convocation devant le Conseil régional de l'Ordre des Pharmaciens",
        "Faits reprochés (rapport, plainte ou signalement)",
        "Attestations de témoins favorables",
        "Justificatifs de formation continue DPC",
        "Registre des ordonnances (extraits pertinents anonymisés)",
        "Arrêtés et autorisations en vigueur",
    ],
    "contrat_travail_pharmacie": [
        "Contrat de travail à rédiger ou litigieux",
        "Convention Collective Nationale de la Pharmacie d'officine",
        "Bulletins de salaire (si litige)",
        "Planning et relevés d'heures",
        "Diplôme et certificat d'inscription à l'Ordre (pour pharmaciens adjoints)",
        "Lettre de convocation / avertissement / licenciement si existant",
    ],
    "financement_acquisition_officine": [
        "Offre de prêt bancaire ou term sheet",
        "Business plan de reprise (3 ans)",
        "Rapport d'expertise de la valeur de l'officine",
        "Promesse de vente signée",
        "Attestation de fonds propres de l'acquéreur",
        "Accord de principe ARS pour transfert de licence",
        "Pacte d'associés projeté (si montage en SEL)",
    ],
}


# Mots vides à ignorer lors du rapprochement document ↔ email.
_STOPWORDS_DOC = {
    "des", "les", "du", "de", "la", "le", "un", "une", "et", "ou", "aux", "sur", "par",
    "pour", "avec", "dans", "ans", "mois", "jour", "jours", "derniers", "dernier",
    "trois", "deux", "moins", "tout", "tous", "votre", "vos", "son", "ses", "leur",
    "certifies", "certifie", "certifiees", "societe", "document", "documents",
}


# Mapping spécialité → checklist par défaut (si aucun type explicite détecté).
_SPECIALITE_VERS_TYPE = {
    "social": "licenciement_contester",
    "famille": "divorce_amiable",
}


def _resoudre_type_checklist(dossier: Dossier) -> str | None:
    """
    Détermine le type de checklist métier à partir, par ordre de priorité :
      1. un type explicite dans les métadonnées,
      2. des mots-clés SÉMANTIQUES forts dans le titre/description/contexte,
      3. la spécialité du dossier.
    NB : on ne déduit PLUS « cession d'officine » du seul mot « pharmacie » — un
    pharmacien peut être en litige prud'homal, fiscal, etc.
    """
    meta = dossier.metadonnees or {}
    # 1) type explicite
    for cle in ("type_specifique", "type_operation", "type_dossier", "type"):
        v = str(meta.get(cle, "")).strip().lower()
        if v in DOCUMENTS_PAR_TYPE:
            return v

    indices = " ".join([
        str(meta.get("contexte", "")), str(meta.get("type_operation", "")),
        (dossier.titre or ""), (dossier.description or ""),
    ]).lower()

    # 2) mots-clés sémantiques forts (l'ordre = priorité)
    if any(m in indices for m in ("licenci", "prud'hom", "prudhom", "rupture convention", "contrat de travail", "salari")):
        return "licenciement_contester"
    if "divorce" in indices or "séparation" in indices or "separation" in indices:
        return "divorce_amiable"
    # cession d'officine : exige un signal de cession ET un signal pharmacie
    if (any(m in indices for m in ("cession", "acquisition", "rachat", "vente du fonds", "reprise"))
            and any(m in indices for m in ("officine", "pharmac", "fonds de commerce"))):
        return "cession_officine"

    # 3) spécialité du dossier
    specialite = str(getattr(dossier.specialite, "value", dossier.specialite) or "").lower()
    return _SPECIALITE_VERS_TYPE.get(specialite)


def _tokens_doc(nom: str) -> set[str]:
    """Mots significatifs d'un nom de document (pour le rapprochement avec un email)."""
    mots = re.split(r"[^0-9a-zàâäéèêëïîôöùûüç]+", (nom or "").lower())
    return {m for m in mots if len(m) >= 4 and m not in _STOPWORDS_DOC}


def _document_mentionne(nom: str, texte: str) -> bool:
    """Vrai si le nom de document est évoqué dans le texte (au moins un mot distinctif)."""
    toks = _tokens_doc(nom)
    return bool(toks) and any(t in texte for t in toks)


async def generer_liste_documents(dossier: Dossier, db: Session) -> list[dict]:
    """
    Peuple la table `documents` avec la checklist métier du type de dossier
    (règles codées → LLM si type non reconnu), de façon IDEMPOTENTE,
    puis renvoie la checklist COMPLÈTE (avec statut + présence de fichier).
    """
    type_checklist = _resoudre_type_checklist(dossier)

    if type_checklist:
        docs_requis = DOCUMENTS_PAR_TYPE[type_checklist]
    else:
        texte = await generer_texte_juridique(
            "Liste les 6 à 10 documents justificatifs nécessaires pour ce dossier. "
            "Réponds en JSON : {\"documents\": [\"doc1\", \"doc2\"]}",
            {
                "specialite": getattr(dossier.specialite, "value", dossier.specialite),
                "titre": dossier.titre,
                "description": dossier.description,
            },
            specialite=getattr(dossier.specialite, "value", dossier.specialite),
        )
        try:
            docs_requis = json.loads(texte)["documents"]
        except Exception:
            docs_requis = ["Documents à définir avec l'avocat"]

    # Création idempotente (on n'ajoute que les pièces absentes)
    for nom_doc in docs_requis:
        existant = db.query(Document).filter(
            Document.dossier_id == dossier.id,
            Document.nom == nom_doc,
        ).first()
        if not existant:
            db.add(Document(
                nom=nom_doc,
                statut=StatutDocument.ATTENDU,
                type_doc="checklist",
                dossier_id=dossier.id,
                deadline_reception=datetime.utcnow() + timedelta(days=14),
            ))
    db.commit()

    return checklist_dossier(dossier.id, db)


def checklist_dossier(dossier_id: str, db: Session) -> list[dict]:
    """Checklist complète d'un dossier (id, nom, statut, fichier disponible)."""
    docs = db.query(Document).filter(Document.dossier_id == dossier_id).order_by(Document.created_at).all()
    return [{
        "id": d.id,
        "nom": d.nom,
        "statut": getattr(d.statut, "value", d.statut),
        "type_doc": d.type_doc,
        "a_fichier": bool(d.chemin_stockage),
        "recu_at": d.recu_at.isoformat() if d.recu_at else None,
    } for d in docs]


def rapprocher_documents_recus(db: Session, dossier_id: str, texte: str) -> list[str]:
    """
    Bascule en RECU les pièces ATTENDUES dont le nom est évoqué dans `texte`
    (sujet + corps d'un email rattaché au dossier). Retourne les noms basculés.
    """
    texte_l = (texte or "").lower()
    if not texte_l.strip():
        return []
    attendus = db.query(Document).filter(
        Document.dossier_id == dossier_id,
        Document.statut == StatutDocument.ATTENDU,
    ).all()
    bascules = []
    for d in attendus:
        if _document_mentionne(d.nom, texte_l):
            d.statut = StatutDocument.RECU
            d.recu_at = datetime.utcnow()
            bascules.append(d.nom)
    if bascules:
        db.commit()
    return bascules


async def envoyer_demande_initiale(
    dossier: Dossier,
    client_email: str,
    client_nom: str,
    documents: list[str]
) -> bool:
    """Envoie la demande initiale de documents au client via Resend (GRATUIT)."""
    portal_url = f"{os.getenv('FRONTEND_URL', 'http://localhost:3000')}/portail/{dossier.id}"
    liste_html = "".join([f"<li style='margin:4px 0'>📄 {doc}</li>" for doc in documents])

    resend.Emails.send({
        "from": f"{EMAIL_FROM_NAME} <{EMAIL_FROM}>",
        "to": client_email,
        "subject": f"[Dossier {dossier.reference}] Documents nécessaires pour votre dossier",
        "html": f"""
        <div style="font-family: Georgia, serif; max-width: 600px; margin: 0 auto; color: #1a1a1a;">
            <div style="border-top: 3px solid #1a3a5c; padding: 32px 0 16px;">
                <h2 style="color: #1a3a5c; margin: 0 0 8px;">Votre dossier : {dossier.titre}</h2>
                <p style="color: #666; font-size: 14px;">Référence : {dossier.reference}</p>
            </div>
            <p>Madame, Monsieur {client_nom},</p>
            <p>Afin de traiter votre dossier dans les meilleurs délais,
            nous vous remercions de bien vouloir nous transmettre les documents suivants :</p>
            <ul style="background: #f8f9fa; padding: 16px 16px 16px 32px; border-radius: 6px;">
                {liste_html}
            </ul>
            <div style="margin: 24px 0;">
                <a href="{portal_url}"
                   style="background: #1a3a5c; color: white; padding: 12px 24px;
                          border-radius: 6px; text-decoration: none; font-weight: bold;">
                    📤 Déposer mes documents
                </a>
            </div>
            <p style="font-size: 13px; color: #888;">
                Ce lien est sécurisé et personnel. Vos documents sont chiffrés et
                accessibles uniquement par votre avocat.
            </p>
        </div>
        """
    })
    return True


async def envoyer_relance(
    dossier: Dossier,
    client_email: str,
    client_nom: str,
    documents_manquants: list[str],
    numero_relance: int
) -> bool:
    """Relance personnalisée générée par Claude. Ton progressivement plus urgent."""
    niveaux = {
        1: ("rappel courtois", "Rappel"),
        2: ("second rappel insistant mais professionnel", "Rappel urgent"),
        3: ("rappel d'urgence signalant l'impact sur le dossier", "URGENT — Dossier bloqué"),
    }
    description_ton, sujet_prefix = niveaux.get(numero_relance, niveaux[1])

    corps = await generer_texte_juridique(
        f"Rédige un email de {description_ton} pour demander des documents manquants. "
        "Commence directement par la salutation. Sois concis (150 mots max).",
        {
            "client_nom": client_nom,
            "dossier": dossier.titre,
            "reference": dossier.reference,
            "documents_manquants": documents_manquants,
            "specialite": dossier.specialite,
            "numero_relance": numero_relance,
        }
    )

    portal_url = f"{os.getenv('FRONTEND_URL', 'http://localhost:3000')}/portail/{dossier.id}"

    resend.Emails.send({
        "from": f"{EMAIL_FROM_NAME} <{EMAIL_FROM}>",
        "to": client_email,
        "subject": f"[Dossier {dossier.reference}] {sujet_prefix} — Documents en attente",
        "html": f"""
        <div style="font-family: Georgia, serif; max-width: 600px; color: #1a1a1a;">
            <p>{corps.replace(chr(10), '<br>')}</p>
            <p>
                <a href="{portal_url}"
                   style="background: #1a3a5c; color: white; padding: 10px 20px;
                          border-radius: 4px; text-decoration: none;">
                    Déposer maintenant
                </a>
            </p>
        </div>
        """
    })
    return True


async def ocr_document(chemin_fichier: str) -> str:
    """
    [DÉPRÉCIÉ — conservé pour compatibilité] Délègue au Service d'Extraction Unique.
    Utiliser directement core.extraction.extraire_document() dans le nouveau code.
    """
    from core.extraction import extraire_document
    return extraire_document(str(chemin_fichier)).get("texte", "")


async def classifier_email(expediteur: str, sujet: str, corps: str) -> dict:
    """Classifie un email entrant et suggère l'action."""
    from core.orchestrateur import claude, CLAUDE_MODEL

    prompt = f"""Classifie cet email entrant dans un cabinet d'avocats.
Réponds UNIQUEMENT en JSON valide.

De : {expediteur}
Sujet : {sujet}
Corps : {corps[:1500]}

JSON attendu :
{{
  "categorie": "dossier_client|document_recu|administratif|spam|fournisseur|inconnu",
  "urgence": "haute|normale|basse",
  "dossier_probable": "référence si détectée ou null",
  "action": "archiver|rattacher_dossier|creer_dossier|ignorer|repondre",
  "resume": "2 phrases max résumant l'email"
}}"""

    response = claude.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )
    try:
        return json.loads(response.content[0].text)
    except Exception:
        return {
            "categorie": "inconnu",
            "urgence": "basse",
            "action": "archiver",
            "resume": "Classification automatique échouée"
        }
