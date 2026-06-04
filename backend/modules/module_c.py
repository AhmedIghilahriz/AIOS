"""
AIOS — Module C : Qualification automatique des dossiers entrants
Analyse le dossier, lui attribue un score de priorité, pose les bonnes questions
"""
from core.orchestrateur import claude, CLAUDE_MODEL, scorer_dossier, llm_chat
from core.models import Dossier, Client, PrioriteLevel, DossierStatus
from sqlalchemy.orm import Session
import json
import re

# Formulaires de qualification par spécialité (FALLBACK statique).
# Utilisés uniquement si l'IA ne produit aucune question exploitable.
QUESTIONS_PAR_SPECIALITE = {
    "affaires": [
        "Quel est le type d'opération ? (cession, création, litige contrat...)",
        "Quel est le montant approximatif en jeu ?",
        "Y a-t-il une urgence ou une date butoir ?",
        "Avez-vous déjà un conseil juridique ?",
    ],
    "social": [
        "Êtes-vous salarié ou employeur ?",
        "Quel est le motif de la procédure ? (licenciement, discrimination, harcèlement...)",
        "Quelle est la date du fait générateur ?",
        "Avez-vous déjà reçu un courrier officiel ?",
    ],
    "immobilier": [
        "S'agit-il d'un achat, vente, bail ou litige ?",
        "Le bien est-il résidentiel ou commercial ?",
        "Quel est le montant approximatif du bien ?",
        "Y a-t-il une promesse ou un compromis déjà signé ?",
    ],
    "famille": [
        "S'agit-il d'un divorce, séparation, succession ou autre ?",
        "Y a-t-il des enfants mineurs concernés ?",
        "Y a-t-il des biens immobiliers en commun ?",
        "La situation est-elle conflictuelle ou amiable ?",
    ],
    "penal": [
        "Êtes-vous mis en cause ou victime ?",
        "Une procédure judiciaire est-elle déjà engagée ?",
        "Avez-vous été convoqué ou entendu par les autorités ?",
        "Y a-t-il une date d'audience prévue ?",
    ],
    "fiscal": [
        "S'agit-il d'un contrôle fiscal, redressement ou autre ?",
        "Le litige porte-t-il sur quel impôt ? (IS, TVA, IR...)",
        "Quel est le montant du redressement éventuel ?",
        "Avez-vous déjà répondu aux services fiscaux ?",
    ],
}


def _extraire_json(texte: str) -> dict:
    """Extrait le premier objet JSON d'une sortie LLM (tolère les ```fences``` et le texte autour)."""
    if not texte:
        return {}
    t = texte.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.MULTILINE).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    m = re.search(r"\{.*\}", t, re.DOTALL)   # premier bloc { ... }
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return {}
    return {}


async def generer_questions_dynamiques(texte_contexte: str, specialite: str) -> dict:
    """
    PRIORITÉ LLM : analyse le message du client et génère 3 questions de qualification
    SPÉCIFIQUES au contexte détecté (ex. pharmacie/officine → questions pharmaceutiques).
    FALLBACK : QUESTIONS_PAR_SPECIALITE, uniquement si l'IA ne renvoie rien d'exploitable.
    Retourne {"questions": [...], "source": "llm"|"fallback", "contexte_detecte": str, "raison"?: str}.
    """
    fallback = QUESTIONS_PAR_SPECIALITE.get(specialite) or QUESTIONS_PAR_SPECIALITE.get("affaires", [])
    texte = (texte_contexte or "").strip()
    if len(texte) < 20:                       # rien d'exploitable en entrée
        return {"questions": fallback[:4], "source": "fallback", "contexte_detecte": "",
                "raison": "contexte client insuffisant"}

    prompt = (
        "Tu prépares la qualification d'un nouveau dossier dans un cabinet d'avocats.\n"
        f"Spécialité du cabinet : {specialite}.\n"
        f"Message / contexte transmis par le client :\n\"\"\"\n{texte[:1500]}\n\"\"\"\n\n"
        "Génère EXACTEMENT 3 questions de qualification SPÉCIFIQUES à CE contexte précis "
        "(évite les questions génériques). Si un domaine particulier ressort (pharmacie / officine, "
        "bail commercial, licenciement, cession de société, divorce…), cible ses spécificités.\n"
        "Réponds STRICTEMENT en JSON, sans aucun texte autour :\n"
        "{\"contexte\": \"<2 à 4 mots>\", \"questions\": [\"q1\", \"q2\", \"q3\"]}"
    )
    try:
        brut = await llm_chat(
            prompt,
            system="Tu génères des questions de qualification juridiques, précises et actionnables. "
                   "Tu réponds uniquement en JSON valide.",
            max_tokens=400,
        )
        data = _extraire_json(brut)
        questions = [q.strip() for q in (data.get("questions") or [])
                     if isinstance(q, str) and q.strip()]
        if len(questions) >= 2:               # sortie LLM exploitable
            return {"questions": questions[:3], "source": "llm",
                    "contexte_detecte": str(data.get("contexte", "")).strip()[:40]}
    except Exception as e:
        print(f"[Module C] génération dynamique de questions échouée : {e}")

    return {"questions": fallback[:4], "source": "fallback", "contexte_detecte": "",
            "raison": "aucune donnée exploitable issue de l'IA"}


async def qualifier_nouveau_dossier(
    description: str,
    specialite: str,
    metadonnees: dict,
    db: Session,
    contexte_email: str = "",
    contexte_documents: str = "",
) -> dict:
    """
    Analyse un nouveau dossier et retourne :
    - Score de priorité (0-100)
    - Questions à poser au client (générées dynamiquement par l'IA, fallback statique)
    - CA potentiel estimé / Complexité
    Le `contexte_documents` (texte extrait des pièces) enrichit catégorisation ET questions.
    """
    # Catégorisation enrichie par le contenu réel des pièces.
    desc_enrichie = description + (f"\n\n[Extraits des pièces du dossier]\n{contexte_documents}"
                                  if contexte_documents else "")
    scoring = await scorer_dossier(desc_enrichie, specialite, metadonnees)

    # Questions : priorité au LLM (analyse email + description + documents), repli statique sinon.
    contexte = "\n".join(filter(None, [description, contexte_email, contexte_documents]))
    q = await generer_questions_dynamiques(contexte, specialite)
    scoring["questions_formulaire"] = q["questions"]
    scoring["questions_source"] = q["source"]
    if q.get("contexte_detecte"):
        scoring["contexte_detecte"] = q["contexte_detecte"]

    return scoring


async def generer_fiche_qualification(dossier: Dossier) -> str:
    """
    Génère une fiche de qualification complète pour l'avocat.
    Résume ce qu'on sait du dossier et ce qu'il faut demander.
    """
    prompt = f"""Tu es l'assistant d'un cabinet d'avocats.
Génère une fiche de qualification synthétique pour ce nouveau dossier.

Dossier : {dossier.titre}
Spécialité : {dossier.specialite}
Description : {dossier.description}
Informations : {json.dumps(dossier.metadonnees, ensure_ascii=False)}

Format de la fiche :
1. RÉSUMÉ DE LA SITUATION (3 lignes max)
2. POINTS CLÉS IDENTIFIÉS
3. RISQUES POTENTIELS
4. QUESTIONS PRIORITAIRES À POSER AU CLIENT
5. DOCUMENTS À DEMANDER EN PRIORITÉ
6. ESTIMATION DE LA COMPLEXITÉ et du temps avocat

Sois concis et professionnel."""

    response = claude.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


async def suggerer_type_dossier(description: str, specialite: str) -> str:
    """
    Suggère le type spécifique de dossier basé sur la description.
    Ex: "divorce_amiable", "licenciement_contester", "bail_commercial"
    """
    types_disponibles = [
        "divorce_amiable", "divorce_contentieux",
        "licenciement_contester", "pse_plan_sauvegarde",
        "cession_parts_sociales", "creation_societe",
        "bail_commercial", "vente_immeuble",
        "autre"
    ]

    prompt = f"""Parmi ces types de dossier : {types_disponibles}
Quel est le type qui correspond le mieux à cette description ?

Spécialité : {specialite}
Description : {description}

Réponds UNIQUEMENT avec le type exact (ex: "divorce_amiable"), rien d'autre."""

    response = claude.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=50,
        messages=[{"role": "user", "content": prompt}]
    )
    type_suggere = response.content[0].text.strip().strip('"')
    return type_suggere if type_suggere in types_disponibles else "autre"


def mettre_a_jour_priorite(dossier: Dossier, score: int, db: Session):
    """Met à jour la priorité du dossier selon le score."""
    if score >= 80:
        dossier.priorite = PrioriteLevel.URGENT
    elif score >= 60:
        dossier.priorite = PrioriteLevel.HAUTE
    elif score >= 30:
        dossier.priorite = PrioriteLevel.STANDARD
    else:
        dossier.priorite = PrioriteLevel.BASSE

    dossier.status = DossierStatus.EN_COURS
    db.commit()
