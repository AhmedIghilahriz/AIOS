"""
AIOS — Orchestrateur IA
- LLM texte : bascule transparente Groq (en ligne) <-> Ollama (local), via la librairie openai.
- Transcription audio (Module E) : Groq Whisper.
- Embeddings : fastembed local par défaut (cf. get_embedding).
"""
import os
import re
import json
import time
import asyncio
import httpx
from openai import OpenAI

# Téléchargement Hugging Face stable (évite le backend "xet" parfois très lent / instable).
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

# ── Sélection du fournisseur LLM (bascule transparente) ───────────────
# Mode EN LIGNE : Groq (API compatible OpenAI) — modèle llama-3.3-70b-versatile.
# Mode LOCAL    : Ollama (API compatible OpenAI) sur localhost:11434.
# Règle de bascule : USE_LOCAL_LLM=true  OU  absence de GROQ_API_KEY  -> mode local.
# La librairie `openai` pilote les DEUX (endpoints compatibles) — aucune clé en dur.

langfuse = None  # Langfuse désactivé

GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
# Modèle rapide/économe pour la haute fréquence (classification email) — limites TPM plus élevées.
GROQ_FAST_MODEL = os.getenv("GROQ_FAST_MODEL", "llama-3.1-8b-instant")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")

# Alias de compatibilité (imports historiques module_a/c/d) — non décisifs pour le routage.
LLM_MODEL = GROQ_MODEL
GEMINI_MODEL = LLM_MODEL
CLAUDE_MODEL = LLM_MODEL

_llm_client = None
_llm_model = None
_llm_mode = None


def _mode_local() -> bool:
    """True si on doit utiliser Ollama (forçage explicite ou aucune clé Groq)."""
    forced = os.getenv("USE_LOCAL_LLM", "").strip().lower() in ("1", "true", "yes", "on")
    return forced or not os.getenv("GROQ_API_KEY")


def _get_llm():
    """Retourne (client OpenAI, modèle) selon le mode actif. Mis en cache."""
    global _llm_client, _llm_model, _llm_mode
    if _llm_client is not None:
        return _llm_client, _llm_model

    if _mode_local():
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/") + "/v1"
        _llm_client = OpenAI(base_url=base_url, api_key="ollama")  # clé factice exigée par la lib
        _llm_model = OLLAMA_MODEL
        _llm_mode = "local (ollama)"
    else:
        base_url = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
        _llm_client = OpenAI(base_url=base_url, api_key=os.getenv("GROQ_API_KEY"))
        _llm_model = GROQ_MODEL
        _llm_mode = "en ligne (groq)"

    print(f"[LLM] Fournisseur actif : {_llm_mode} — modèle {_llm_model}")
    return _llm_client, _llm_model


def reset_llm_cache():
    """Réinitialise le client (utile après un changement de variables d'environnement / tests)."""
    global _llm_client, _llm_model, _llm_mode
    _llm_client = _llm_model = _llm_mode = None


def _resolve_model(fast: bool):
    """Retourne (client, modèle). En mode Groq, `fast=True` utilise le modèle 8B (TPM plus large)."""
    client, model = _get_llm()
    if fast and _llm_mode and "groq" in _llm_mode:
        return client, GROQ_FAST_MODEL
    return client, model


# ── Ajout 3 — Politique de données : masquage avant tout LLM EXTERNE (Groq) ──────────
# Les calculs restent déterministes ; le LLM ne reçoit jamais en clair des données
# directement identifiantes ou des secrets. En mode LOCAL (Ollama), aucune donnée ne
# sort de la machine → pas de masquage. Pour traiter des données patients, forcer
# USE_LOCAL_LLM=true (cf. CLAUDE.md §13).
CHAMPS_INTERDITS_LLM_EXTERNE = [
    "numero_secu", "date_naissance", "donnees_bancaires", "mot_de_passe", "token", "refresh_token",
]

_RE_NIR = re.compile(r"\b[12]\s?\d{2}\s?(?:0[1-9]|1[0-2]|[2-9]\d)\s?\d{2}\s?\d{3}\s?\d{3}\s?\d{2}\b")
_RE_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,3})?\b")
_RE_CB = re.compile(r"\b(?:\d{4}[\s-]?){3}\d{4}\b")
_RE_SECRET = re.compile(
    r"\b(?:eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"   # JWT
    r"|gho_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,})\b"     # tokens API
)


def sanitiser_prompt(texte: str) -> str:
    """Masque les données sensibles (NIR, IBAN, carte bancaire, tokens) avant envoi à un LLM externe."""
    if not texte:
        return texte
    texte = _RE_NIR.sub("[NIR_MASQUÉ]", texte)
    texte = _RE_IBAN.sub("[IBAN_MASQUÉ]", texte)
    texte = _RE_CB.sub("[CARTE_MASQUÉE]", texte)
    texte = _RE_SECRET.sub("[SECRET_MASQUÉ]", texte)
    return texte


def _llm_generate(prompt: str, system: str = None, max_tokens: int = 2000, fast: bool = False) -> str:
    """Appel LLM unifié (Groq/Ollama) via openai, avec retry/backoff sur la limite de débit (429)."""
    import re as _re
    client, model = _resolve_model(fast)
    system = system or "Tu es un assistant juridique expert en droit français."
    # Ajout 3 : masquage SYSTÉMATIQUE des données sensibles dès qu'on parle à un LLM externe (Groq).
    if _llm_mode and "groq" in _llm_mode:
        prompt = sanitiser_prompt(prompt)
        system = sanitiser_prompt(system)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, temperature=0.3,
            )
            return response.choices[0].message.content
        except Exception as e:
            msg = str(e)
            if ("rate_limit" in msg or "429" in msg) and attempt < 2:
                m = _re.search(r"try again in ([\d.]+)s", msg)
                wait = (float(m.group(1)) + 0.3) if m else 2.0
                print(f"[LLM] limite de débit atteinte — nouvelle tentative dans {wait:.1f}s")
                time.sleep(min(wait, 10))
                continue
            raise


async def llm_chat(prompt: str, system: str = None, max_tokens: int = 2000, fast: bool = False) -> str:
    """Version async-safe de _llm_generate (ne bloque pas la boucle FastAPI)."""
    return await asyncio.to_thread(_llm_generate, prompt, system, max_tokens, fast)


def pre_warm_embeddings():
    """Pré-charge le modèle d'embeddings local (à lancer au démarrage, en tâche de fond)."""
    try:
        _get_fastembed()
        print("[warmup] modèle d'embeddings prêt")
    except Exception as e:
        print(f"[warmup] embeddings indisponibles : {e}")


# Alias de compatibilité pour module_a.py (API style Anthropic → Groq)
def _make_compat_response(text: str):
    class _Content:
        def __init__(self, t): self.text = t
    class _Resp:
        def __init__(self, t): self.content = [_Content(t)]
    return _Resp(text)

class _MessagesCompat:
    @staticmethod
    def create(model: str, max_tokens: int, messages: list, **kwargs):
        user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")
        sys_msg = next((m["content"] for m in messages if m["role"] == "system"), None)
        text = _llm_generate(user_msg, system=sys_msg, max_tokens=max_tokens)
        return _make_compat_response(text)

class _GroqCompatClient:
    messages = _MessagesCompat()

claude = _GroqCompatClient()


# ── Embeddings — 2 options ────────────────────────────────────────────

def _pad_1536(emb: list[float]) -> list[float]:
    """
    Complète (ou tronque) un vecteur à 1536 dimensions.
    Le zéro-padding préserve EXACTEMENT la similarité cosinus : on peut donc utiliser
    un embedder 768-d (Gemini) sans migrer la colonne pgvector Vector(1536).
    """
    if not emb:
        return [0.0] * 1536
    if len(emb) >= 1536:
        return list(emb[:1536])
    return list(emb) + [0.0] * (1536 - len(emb))


_fastembed_model = None

def _get_fastembed():
    """Charge (une seule fois) le modèle d'embedding local fastembed."""
    global _fastembed_model
    if _fastembed_model is None:
        from fastembed import TextEmbedding
        # Défaut : BAAI/bge-small-en-v1.5 (384-d, ~130 Mo, CPU). Surchageable via EMBEDDING_MODEL.
        _fastembed_model = TextEmbedding(os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"))
    return _fastembed_model


async def get_embedding(texte: str) -> list[float]:
    """
    Génère un embedding (complété à 1536-d pour la colonne pgvector).

    EMBEDDING_PROVIDER (env) : "local" (défaut) | "gemini" | "openai" | "ollama".
    - local  : fastembed, 100% hors-ligne, GRATUIT, AUCUNE clé requise (recommandé).
    - gemini : text-embedding-004 (nécessite une vraie clé AI Studio "AIza...").
    - openai : text-embedding-3-small (payant).
    - ollama : nomic-embed-text (local, si Ollama tourne).
    Le zéro-padding à 1536 préserve la similarité cosinus.
    """
    texte = (texte or "").strip()
    if not texte:
        return [0.0] * 1536

    provider = os.getenv("EMBEDDING_PROVIDER", "local").lower()

    if provider == "gemini" and os.getenv("GEMINI_API_KEY"):
        try:
            import google.generativeai as genai
            genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
            result = await asyncio.to_thread(
                genai.embed_content,
                model="models/text-embedding-004",
                content=texte[:8000],
            )
            return _pad_1536(result["embedding"])
        except Exception as e:
            print(f"[embedding] Gemini indisponible ({e}) — fallback local")

    elif provider == "openai" and os.getenv("OPENAI_API_KEY"):
        try:
            from openai import AsyncOpenAI
            openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            response = await openai_client.embeddings.create(
                model="text-embedding-3-small", input=texte[:8000]
            )
            return _pad_1536(response.data[0].embedding)
        except Exception as e:
            print(f"[embedding] OpenAI indisponible ({e}) — fallback local")

    elif provider == "ollama":
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.post(
                    f"{os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')}/api/embeddings",
                    json={"model": "nomic-embed-text", "prompt": texte[:8000]},
                )
                return _pad_1536(response.json()["embedding"])
        except Exception as e:
            print(f"[embedding] Ollama indisponible ({e}) — fallback local")

    # Défaut / fallback : fastembed local (gratuit, sans clé, hors-ligne)
    try:
        model = await asyncio.to_thread(_get_fastembed)
        vec = await asyncio.to_thread(lambda: list(next(model.embed([texte[:8000]]))))
        return _pad_1536([float(x) for x in vec])
    except Exception as e:
        print(f"[embedding] local indisponible ({e}) — vecteur nul (recherche dégradée)")
        return [0.0] * 1536


def get_embedding_sync(texte: str) -> list[float]:
    """Embedding synchrone (fastembed local) — pour les nœuds de graphe synchrones."""
    texte = (texte or "").strip()
    if not texte:
        return [0.0] * 1536
    try:
        model = _get_fastembed()
        vec = list(next(model.embed([texte[:8000]])))
        return _pad_1536([float(x) for x in vec])
    except Exception as e:
        print(f"[embedding sync] indisponible ({e}) — vecteur nul")
        return [0.0] * 1536


def get_embeddings_batch(textes: list[str]) -> list[list[float]]:
    """
    Embeddings PAR LOT (pour l'indexation RAG de nombreux chunks).
    Provider `local` (défaut) : un seul passage fastembed (rapide). Sinon : repli séquentiel.
    Renvoie une liste de vecteurs 1536-d alignée sur `textes`.
    """
    prepares = [((t or "").strip()[:8000]) for t in textes]
    if not prepares:
        return []
    provider = os.getenv("EMBEDDING_PROVIDER", "local").lower()
    if provider == "local":
        try:
            model = _get_fastembed()
            vecs = list(model.embed(prepares))
            return [_pad_1536([float(x) for x in v]) for v in vecs]
        except Exception as e:
            print(f"[embeddings batch] local indisponible ({e}) — repli séquentiel")
    return [get_embedding_sync(t) for t in prepares]


# ── Orchestrateur principal ───────────────────────────────────────────

async def router_evenement(evenement: dict) -> dict:
    """Route chaque événement vers le bon module AIOS."""
    trace = langfuse.trace(name="router_evenement", metadata={"type": evenement.get("type")}) if langfuse else None

    prompt = f"""Tu es l'orchestrateur AIOS d'un cabinet d'avocats français.
Analyse cet événement et détermine l'action à déclencher.
Réponds UNIQUEMENT en JSON valide.

Événement: {json.dumps(evenement, ensure_ascii=False)}

Format de réponse:
{{
  "module": "A|B|C|D|E|F|G",
  "action": "nom_de_l_action",
  "priorite": "urgent|haute|normale|basse",
  "contexte": {{}},
  "justification": "brève explication"
}}"""

    # AIOS-FIX: règle #6 (jamais de 500) — parsing JSON tolérant + fallback.
    texte = _llm_generate(prompt, max_tokens=400)
    try:
        return json.loads(texte)
    except Exception:
        m = re.search(r"\{.*\}", texte or "", re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return {"module": "A", "action": "revue_manuelle", "priorite": "normale",
                "contexte": {}, "justification": "Sortie LLM non exploitable — à router manuellement."}


async def generer_texte_juridique(
    tache: str,
    contexte: dict,
    specialite: str = None,
    max_tokens: int = 2000
) -> str:
    """Génère tout texte juridique via Claude Sonnet. Tracé dans Langfuse."""
    trace = langfuse.trace(name=f"texte_juridique_{tache[:30]}") if langfuse else None

    system = f"""Tu es un assistant juridique expert en droit français.
Spécialité principale : {specialite or 'droit général'}.
Tu génères des textes professionnels, précis et conformes au droit français.
IMPORTANT : Tu ne fournis JAMAIS de délais légaux ou de règles de droit sans signaler
si tu n'es pas certain à 100% — les délais doivent être vérifiés par l'avocat."""

    texte = _llm_generate(
        f"Tâche : {tache}\n\nContexte : {json.dumps(contexte, ensure_ascii=False, indent=2)}",
        system=system,
        max_tokens=max_tokens
    )

    return texte


async def scorer_dossier(description: str, specialite: str, metadonnees: dict) -> dict:
    """Module C — Score de priorité d'un nouveau dossier."""
    prompt = f"""Analyse ce nouveau dossier et attribue un score de priorité.

Spécialité: {specialite}
Description: {description}
Informations: {json.dumps(metadonnees, ensure_ascii=False)}

Réponds en JSON:
{{
  "score": 0-100,
  "priorite": "urgent|haute|standard|basse",
  "categorie": "URGENT|STANDARD|A_QUALIFIER",
  "ca_potentiel": "faible|moyen|fort",
  "complexite": "simple|moyenne|complexe",
  "justification": "explication courte",
  "questions_cles": ["question 1 à poser au client", "question 2"]
}}"""

    import re as _re
    fallback = {
        "score": 50, "priorite": "standard", "categorie": "A_QUALIFIER",
        "ca_potentiel": "moyen", "complexite": "moyenne",
        "justification": "Analyse IA indisponible — dossier à qualifier manuellement.",
        "questions_cles": [],
    }
    try:
        texte = _llm_generate(prompt, max_tokens=600)
        t = _re.sub(r"^```(?:json)?|```$", "", (texte or "").strip(), flags=_re.MULTILINE).strip()
        m = _re.search(r"\{.*\}", t, _re.DOTALL)
        return json.loads(m.group(0) if m else t)
    except Exception as e:
        print(f"[scorer_dossier] LLM/JSON indisponible : {e}")
        return fallback
