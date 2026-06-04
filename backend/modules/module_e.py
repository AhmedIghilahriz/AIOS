"""
AIOS — Module E : Comptes rendus automatiques

- Transcription : Groq Whisper large-v3 (GRATUIT 28h/mois), via la librairie openai.
  Fallback local optionnel : faster-whisper (si installé et aucune clé Groq).
- Structuration : LLM hybride de l'orchestrateur (Groq en ligne OU Ollama local).
  -> Plus aucune dépendance à Gemini.
"""
import os
import re
import json
from openai import OpenAI
from sqlalchemy.orm import Session
from core.models import CompteRendu, Dossier
from core.orchestrateur import get_embedding, llm_chat


def _groq_audio_client() -> OpenAI | None:
    """Client OpenAI pointé sur l'endpoint Groq (Whisper). None si pas de clé."""
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return None
    base_url = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    return OpenAI(base_url=base_url, api_key=key)


async def transcrire_audio(chemin_fichier) -> str:
    """
    Transcrit un fichier audio en français.
    Principal : Groq Whisper large-v3 (gratuit). Formats : mp3/mp4/m4a/wav/webm (max 25 Mo).
    Fallback : faster-whisper local si aucune clé Groq n'est configurée.
    """
    client = _groq_audio_client()
    if client is not None:
        with open(chemin_fichier, "rb") as f:
            transcription = client.audio.transcriptions.create(
                model=os.getenv("WHISPER_MODEL", "whisper-large-v3"),
                file=f,
                language="fr",
                response_format="text",
                temperature=0.0,
            )
        return transcription if isinstance(transcription, str) else getattr(transcription, "text", str(transcription))

    # Aucune clé Groq -> transcription 100% locale si faster-whisper est installé
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(
            "Transcription indisponible : définissez GROQ_API_KEY (Groq Whisper) "
            "ou installez faster-whisper (`pip install faster-whisper`) pour le mode local."
        )
    model = WhisperModel(os.getenv("FASTER_WHISPER_MODEL", "small"), device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(chemin_fichier), language="fr")
    return " ".join(seg.text for seg in segments).strip()


async def generer_compte_rendu(
    transcription: str,
    type_reunion: str,
    dossier_contexte: dict,
    dossier_id: str,
    db: Session,
) -> CompteRendu:
    """Génère un compte rendu structuré depuis la transcription (LLM hybride Groq/Ollama)."""
    prompt = f"""Tu es un assistant juridique d'un cabinet d'avocats français.
Analyse cette transcription de réunion et génère un compte rendu structuré.
Type de réunion : {type_reunion}
Contexte dossier : {json.dumps(dossier_contexte, ensure_ascii=False)}

Transcription :
{transcription[:8000]}

Réponds UNIQUEMENT en JSON valide :
{{
  "titre": "titre court de la réunion",
  "resume_executif": "2-3 phrases maximum résumant l'essentiel",
  "points_discutes": ["point 1 discuté", "point 2 discuté"],
  "decisions_prises": ["décision 1", "décision 2"],
  "prochaines_actions": [
    {{
      "action": "description de l'action",
      "responsable": "avocat|client|tierce partie",
      "deadline": "YYYY-MM-DD ou null si non précisé",
      "priorite": "haute|normale|basse"
    }}
  ],
  "questions_en_suspens": ["question non résolue 1"],
  "elements_facturables": ["prestation réalisée pour facturation"]
}}"""

    texte = await llm_chat(
        prompt,
        system="Tu es un assistant juridique. Réponds UNIQUEMENT en JSON valide, sans texte avant ni après.",
        max_tokens=2000,
    )

    try:
        # Extraire le JSON même s'il est entouré de ```json ... ```
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", texte or "", re.DOTALL)
        data = json.loads(m.group(1) if m else texte)
    except (json.JSONDecodeError, TypeError):
        data = {
            "titre": "Compte rendu",
            "resume_executif": (transcription or "")[:200],
            "points_discutes": [], "decisions_prises": [],
            "prochaines_actions": [], "questions_en_suspens": [],
            "elements_facturables": [],
        }

    compte_rendu = CompteRendu(
        titre=data.get("titre", "Compte rendu"),
        type_reunion=type_reunion,
        transcription=transcription,
        resume=data.get("resume_executif", ""),
        points_discutes=data.get("points_discutes", []),
        decisions=data.get("decisions_prises", []),
        prochaines_actions=data.get("prochaines_actions", []),
        dossier_id=dossier_id,
    )

    texte_a_indexer = f"{compte_rendu.resume} {' '.join(compte_rendu.points_discutes)}"
    compte_rendu.embedding = await get_embedding(texte_a_indexer)

    db.add(compte_rendu)
    db.commit()
    db.refresh(compte_rendu)

    # AIOS-FIX: cas 11 — engagements datés → deadlines (DÉTERMINISTE : parse de date ISO, 0 % LLM
    # sur le calcul). Le LLM n'a fait que STRUCTURER ; la création d'échéance reste algorithmique.
    try:
        from modules.module_f import creer_deadline_jours
        dossier_obj = db.query(Dossier).filter(Dossier.id == dossier_id).first()
        if dossier_obj:
            from datetime import datetime as _dt
            for action in (compte_rendu.prochaines_actions or []):
                if not isinstance(action, dict):
                    continue
                dl = str(action.get("deadline") or "").strip()
                if not dl or dl.lower() == "null":
                    continue
                try:
                    cible = _dt.fromisoformat(dl[:10])
                except Exception:
                    continue
                jours = (cible.date() - _dt.utcnow().date()).days
                if jours < 0:
                    continue
                creer_deadline_jours(
                    dossier_obj, max(jours, 1), db,
                    motif=f"[Compte rendu] {action.get('action', 'engagement')}"[:160],
                    point_depart=_dt.utcnow(), type_delai="engagement_reunion",
                )
    except Exception as e:
        print(f"[Module E] création de deadlines depuis le compte rendu échouée : {e}")

    return compte_rendu


async def rechercher_dans_comptes_rendus(
    query: str,
    cabinet_id: str,
    db: Session,
) -> list[dict]:
    """Recherche sémantique dans l'historique des comptes rendus."""
    from sqlalchemy import text
    query_embedding = await get_embedding(query)

    resultats = db.execute(
        text("""
            SELECT
                cr.id, cr.titre, cr.type_reunion, cr.resume,
                cr.created_at, cr.decisions, cr.prochaines_actions,
                d.reference as dossier_ref, d.titre as dossier_titre,
                1 - (cr.embedding <=> CAST(:embedding AS vector)) as similarite
            FROM compte_rendus cr
            JOIN dossiers d ON cr.dossier_id = d.id
            WHERE d.cabinet_id = :cabinet_id
              AND cr.embedding IS NOT NULL
              AND 1 - (cr.embedding <=> CAST(:embedding AS vector)) > 0.3
            ORDER BY cr.embedding <=> CAST(:embedding AS vector)
            LIMIT 5
        """),
        {"embedding": str(query_embedding), "cabinet_id": cabinet_id},
    ).fetchall()

    return [dict(r._mapping) for r in resultats]
