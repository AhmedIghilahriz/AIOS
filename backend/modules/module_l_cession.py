"""
AIOS — Module L : Cession d'officine — extraction des paramètres par RAG.

Architecture « recherche documentaire intelligente » (et non plus lecture globale) :
  1. INDEXATION : chaque document est segmenté en chunks (avec recouvrement) et vectorisé
     (pgvector). Voir `indexer_document`.
  2. RETRIEVAL CIBLÉ : pour CHAQUE champ de la FicheCession, on fait une recherche de
     similarité sémantique et on ne récupère que les quelques chunks les plus pertinents.
  3. EXTRACTION ATOMIQUE PAR CHAMP : un prompt dédié par champ (anti-hallucination globale),
     avec CITATION (pièce + page) et gestion stricte du `null` (jamais d'invention).

Garde-fous (cf. CLAUDE.md §2) :
  • Embeddings 100 % LOCAUX (fastembed, gratuit) — aucune donnée ne sort pour l'indexation.
  • Le LLM ne voit QUE des extraits ciblés ; s'il n'y a pas de chunk pertinent → `null`.
  • Qualification juridique PROPOSÉE, jamais décidée seule → `champs_incertains` + citations.
  • Jamais de 500 sur sortie LLM : parsing tolérant + fallback fiche vide.

Persistance : dossiers.metadonnees["fiche_cession"] (JSON) + table `document_chunks` (vecteurs).
"""
from __future__ import annotations
import os
import re
import asyncio
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.models import Dossier, CompteRendu, Document, DocumentChunk
from core.extraction import contexte_documents, extraire_pages
from core.orchestrateur import llm_chat, get_embedding, get_embeddings_batch, sanitiser_prompt
from core.cession_schema import (
    FicheCession, Officine, Partie, PrixCession, ConditionSuspensive,
    conditions_standard,
)
from modules.module_c import _extraire_json


META_KEY = "fiche_cession"

# Champs critiques : sans eux, on NE génère NI promesse NI acte (cf. Lot 3 EF-3.2).
CHAMPS_CRITIQUES = ["type_operation", "cedants", "cessionnaires", "prix.montant_global"]


# ── Extraction déterministe (indices fiables passés au LLM) ────────────

_RE_MONTANT = re.compile(
    r"(\d{1,3}(?:[ . ]\d{3})+|\d{4,})(?:[.,]\d{2})?\s*(?:€|EUR\b|euros?\b)",
    re.IGNORECASE,
)
_RE_NUM9 = re.compile(r"\b\d{9}\b")          # SIREN ou FINESS (9 chiffres)
_RE_NUM14 = re.compile(r"\b\d{14}\b")        # SIRET


def _to_float(brut: str) -> float | None:
    s = brut.replace(" ", "").replace(" ", "").replace(".", "")
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def extraire_montants(texte: str) -> list[float]:
    """Montants en euros trouvés dans le texte (déduplique, trie décroissant)."""
    vals = {v for m in _RE_MONTANT.findall(texte or "") if (v := _to_float(m)) is not None}
    return sorted(vals, reverse=True)


def extraire_numeros(texte: str) -> dict:
    """Numéros candidats (SIREN/FINESS = 9 chiffres, SIRET = 14)."""
    t = texte or ""
    return {
        "num9": sorted(set(_RE_NUM9.findall(t)))[:8],
        "num14": sorted(set(_RE_NUM14.findall(t)))[:8],
    }


# ── Sources textuelles ────────────────────────────────────────────────

def _transcription_appel(db: Session, dossier_id: str, max_chars: int = 3000) -> str:
    """Transcription / résumé du 1er appel (Module E), s'il existe."""
    cr = (
        db.query(CompteRendu)
        .filter(CompteRendu.dossier_id == dossier_id)
        .order_by(CompteRendu.created_at.asc())
        .first()
    )
    if not cr:
        return ""
    return ((cr.transcription or cr.resume or "") or "").strip()[:max_chars]


# ── RAG : configuration ───────────────────────────────────────────────
CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "1100"))         # caractères / chunk
CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "150"))    # recouvrement (contexte)
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "3"))                  # chunks récupérés par requête
RAG_SEUIL = float(os.getenv("RAG_SEUIL_SIMILARITE", "0.25"))  # similarité cosinus minimale


def _is_e5() -> bool:
    """Les modèles e5 attendent un préfixe 'query:' / 'passage:' pour une qualité optimale."""
    return "e5" in os.getenv("EMBEDDING_MODEL", "").lower()


def _emb_query(t: str) -> str:
    return f"query: {t}" if _is_e5() else t


def _emb_passage(t: str) -> str:
    return f"passage: {t}" if _is_e5() else t


def _chunker(texte: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Fenêtre glissante sur le texte (coupe de préférence sur un espace), avec recouvrement."""
    texte = (texte or "").strip()
    if not texte:
        return []
    if len(texte) <= size:
        return [texte]
    out: list[str] = []
    start = 0
    n = len(texte)
    while start < n:
        end = min(start + size, n)
        if end < n:
            sp = texte.rfind(" ", start + int(size * 0.6), end)
            if sp != -1:
                end = sp
        morceau = texte[start:end].strip()
        if morceau:
            out.append(morceau)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return out


# ── RAG : indexation vectorielle ──────────────────────────────────────

def compter_chunks(document_id: str, db: Session) -> int:
    return db.query(DocumentChunk).filter(DocumentChunk.document_id == document_id).count()


def _a_des_chunks(dossier_id: str, db: Session) -> bool:
    return db.query(DocumentChunk).filter(DocumentChunk.dossier_id == dossier_id).first() is not None


async def indexer_document(doc: Document, db: Session, force: bool = False) -> int:
    """
    Segmente un document en chunks (par page si possible, pour la citation) et stocke leurs
    embeddings. Idempotent : ne réindexe pas si des chunks existent déjà (sauf `force`).
    """
    if not force and compter_chunks(doc.id, db) > 0:
        return 0
    db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).delete(synchronize_session=False)

    from pathlib import Path
    pages: list[tuple[int | None, str]] = []
    if doc.chemin_stockage and Path(doc.chemin_stockage).is_file():
        pages = extraire_pages(doc.chemin_stockage, doc.nom, doc.mime_type)  # [(page, texte)]
    if not pages and doc.ocr_contenu:
        pages = [(None, doc.ocr_contenu)]
    if not pages:
        db.commit()
        return 0

    morceaux: list[tuple[int | None, str]] = []
    for page, txt in pages:
        for piece in _chunker(txt):
            morceaux.append((page, piece))
    if not morceaux:
        db.commit()
        return 0

    embeddings = await asyncio.to_thread(get_embeddings_batch, [_emb_passage(m[1]) for m in morceaux])
    for idx, ((page, txt), emb) in enumerate(zip(morceaux, embeddings)):
        db.add(DocumentChunk(
            document_id=doc.id, dossier_id=doc.dossier_id, chunk_index=idx,
            page=page, contenu=txt, embedding=emb,
        ))
    db.commit()
    return len(morceaux)


async def indexer_dossier_si_besoin(dossier_id: str, db: Session) -> int:
    """Indexe (à la demande) tous les documents du dossier qui n'ont pas encore de chunks."""
    docs = (
        db.query(Document)
        .filter(Document.dossier_id == dossier_id, Document.ocr_contenu.isnot(None))
        .all()
    )
    total = 0
    for d in docs:
        if compter_chunks(d.id, db) == 0:
            try:
                total += await indexer_document(d, db)
            except Exception as e:
                print(f"[Module L][RAG] indexation document {d.id} échouée : {e}")
    return total


async def reindexer_dossier(dossier_id: str, db: Session) -> int:
    """
    Force la RÉ-INDEXATION de tous les documents du dossier (purge + re-chunk + ré-embed).
    À utiliser après un changement de modèle d'embeddings (ex. bascule vers e5 multilingue),
    car les anciens vecteurs ne sont plus comparables aux nouvelles requêtes.
    """
    db.query(DocumentChunk).filter(DocumentChunk.dossier_id == dossier_id).delete(synchronize_session=False)
    db.commit()
    return await indexer_dossier_si_besoin(dossier_id, db)


# ── RAG : retrieval (recherche de similarité pgvector) ────────────────

async def rechercher_chunks(dossier_id: str, query: str, db: Session,
                            k: int = RAG_TOP_K, seuil: float = RAG_SEUIL,
                            mots: list[str] | None = None) -> list[dict]:
    """
    Retrieval HYBRIDE : sémantique (pgvector) + lexical (ILIKE sur des ancres exactes).
    Le lexical compense la faible recall des embeddings anglais sur du texte juridique français
    (FINESS, n° SIREN, « prix »…). Les chunks lexicaux (ancre exacte) sont prioritaires.
    """
    # 1) Sémantique
    emb = await get_embedding(_emb_query(query))
    rows = db.execute(
        text("""
            SELECT dc.contenu, dc.page, d.nom AS doc_nom,
                   1 - (dc.embedding <=> CAST(:emb AS vector)) AS sim
            FROM document_chunks dc
            JOIN documents d ON dc.document_id = d.id
            WHERE dc.dossier_id = :dossier_id AND dc.embedding IS NOT NULL
            ORDER BY dc.embedding <=> CAST(:emb AS vector)
            LIMIT :k
        """),
        {"emb": str(emb), "dossier_id": dossier_id, "k": k},
    ).fetchall()
    vect = [dict(r._mapping) for r in rows if (dict(r._mapping).get("sim") or 0) >= seuil]

    # 2) Lexical (ancres exactes) — robuste aux limites de l'embedding sur le français
    lex: list[dict] = []
    mots = [m for m in (mots or []) if m and len(m) >= 3]
    if mots:
        clauses = " OR ".join(f"dc.contenu ILIKE :m{i}" for i in range(len(mots)))
        params = {"dossier_id": dossier_id, "k": k, **{f"m{i}": f"%{m}%" for i, m in enumerate(mots)}}
        lrows = db.execute(
            text(f"""
                SELECT dc.contenu, dc.page, d.nom AS doc_nom, 1.0 AS sim
                FROM document_chunks dc
                JOIN documents d ON dc.document_id = d.id
                WHERE dc.dossier_id = :dossier_id AND ({clauses})
                ORDER BY dc.page NULLS LAST
                LIMIT :k
            """),
            params,
        ).fetchall()
        lex = [dict(r._mapping) for r in lrows]

    # 3) Fusion (lexical d'abord), dédupliquée
    seen: set = set()
    out: list[dict] = []
    for c in lex + vect:
        cle = (c.get("doc_nom"), c.get("page"), (c.get("contenu") or "")[:60])
        if cle in seen:
            continue
        seen.add(cle)
        out.append(c)
    return out[: max(k, len(lex))]


# ── RAG : spécification des champs (1 requête + 1 prompt atomique par champ) ──
_CHAMPS_RAG: list[dict] = [
    {"cle": "type_operation",
     "query": "Type d'opération : cession de fonds de commerce ou cession de parts/actions de société (SEL) ?",
     "mots": ["fonds de commerce", "parts sociales", "cession"],
     "valeur_desc": "\"cession_fonds\" (vente du fonds de commerce) | \"cession_parts\" (vente de parts/actions de SEL) | \"cession_titres\""},
    {"cle": "prix",
     "query": "Prix de cession du fonds de commerce et sa ventilation (éléments incorporels, matériel, stock de marchandises)",
     "mots": ["prix", "incorporel", "matériel", "stock", "valorisation"],
     "valeur_desc": ("objet {\"montant_global\": nombre, \"part_incorporel\": nombre|null, "
                     "\"part_materiel\": nombre|null, \"part_stock\": nombre|null} — montants en euros "
                     "(sans symbole, sans espaces). montant_global = PRIX DE CESSION du fonds (PAS un prêt, "
                     "un apport ou un capital social). Si seul le prix global est connu, renvoie-le et mets "
                     "les parts à null. Ne renvoie null QUE si AUCUN prix de cession n'apparaît.")},
    {"cle": "cedant",
     "query": "Identité du cédant / vendeur : nom, prénom, société exploitante, numéro SIREN/RCS",
     "mots": ["cédant", "vendeur", "SIREN", "gérant", "SELARL"],
     "valeur_desc": "objet {\"type\": \"personne_physique|SELARL|SELAS|SARL|autre\", \"denomination\": ..., \"nom\": ..., \"prenom\": ..., \"rcs_siren\": ..., \"inscription_ordre\": ...}"},
    {"cle": "cessionnaire",
     "query": "Identité du cessionnaire / acquéreur / repreneur : nom, prénom, inscription à l'Ordre des pharmaciens",
     "mots": ["cessionnaire", "acquéreur", "repreneur", "emprunteur"],
     "valeur_desc": "objet {\"type\": ..., \"denomination\": ..., \"nom\": ..., \"prenom\": ..., \"rcs_siren\": ..., \"inscription_ordre\": ...}"},
    {"cle": "officine_finess",
     "query": "Numéro FINESS de l'officine de pharmacie (FINESS établissement)",
     "mots": ["FINESS"],
     "valeur_desc": "le numéro FINESS établissement (chaîne de chiffres) de l'officine"},
    {"cle": "officine",
     "query": "Nom (enseigne), adresse d'exploitation et licence/autorisation ARS de l'officine",
     "mots": ["officine", "enseigne", "licence", "adresse d'exploitation"],
     "valeur_desc": "objet {\"nom\": enseigne, \"adresse\": ..., \"licence_ars\": n° de licence ARS}"},
    {"cle": "date_jouissance",
     "query": "Date d'entrée en jouissance ou de réalisation de la cession",
     "mots": ["jouissance", "entrée en", "réalisation"],
     "valeur_desc": "la date au format \"YYYY-MM-DD\""},
    {"cle": "conditions",
     "query": "Conditions suspensives de la cession : financement, agrément, déclaration ARS, droit de préemption, accord du bailleur",
     "mots": ["condition suspensive", "suspensive", "financement", "préemption", "agrément"],
     "valeur_desc": ("LISTE JSON de CODES (peut être vide []) parmi : FINANCEMENT (prêt/financement "
                     "bancaire), INSCRIPTION_ORDRE (inscription Ordre/Section A), DECLARATION_ARS "
                     "(accord/non-opposition ARS), AUDIT_SATISFAISANT, MAINTIEN_CONTRATS, "
                     "PAS_DE_CHANGEMENT_SUBSTANTIEL, PURGE_PREEMPTION_COMMUNE (préemption commune/mairie), "
                     "ACCORD_BAILLEUR (agrément du bailleur), AGREMENT_ASSOCIES, PURGE_PREEMPTION_STATUTAIRE "
                     "— inclus chaque condition explicitement évoquée dans les extraits")},
]


async def _gather_chunks(dossier_id: str, db: Session, max_chunks: int = 14) -> list[dict]:
    """
    Rassemble les chunks pertinents pour TOUS les champs (union des retrievals hybrides par
    requête, dédupliquée) → contexte borné et ciblé pour un UNIQUE appel d'extraction.
    """
    vus: set = set()
    out: list[dict] = []
    for spec in _CHAMPS_RAG:
        for c in await rechercher_chunks(dossier_id, spec["query"], db, mots=spec.get("mots")):
            cle = (c.get("doc_nom"), c.get("page"), (c.get("contenu") or "")[:60])
            if cle in vus:
                continue
            vus.add(cle)
            out.append(c)
    out.sort(key=lambda c: (str(c.get("doc_nom")), c.get("page") or 0))
    return out[:max_chunks]


# Schéma JSON strict imposé au modèle (SINGLE-PASS) : valeur + n° d'extrait source par champ.
_SCHEMA_SINGLE = """{
  "type_operation": {"valeur": "cession_fonds|cession_parts|cession_titres|null", "src": <n ou null>},
  "cedant":        {"valeur": {"type":"personne_physique|SELARL|SELAS|SARL|autre","denomination":null,"nom":null,"prenom":null,"rcs_siren":null,"inscription_ordre":null} | null, "src": <n ou null>},
  "cessionnaire":  {"valeur": {"type":null,"denomination":null,"nom":null,"prenom":null,"rcs_siren":null,"inscription_ordre":null} | null, "src": <n ou null>},
  "officine":      {"valeur": {"nom":null,"adresse":null,"finess":null,"licence_ars":null} | null, "src": <n ou null>},
  "prix":          {"valeur": {"montant_global":null,"part_incorporel":null,"part_materiel":null,"part_stock":null} | null, "src": <n ou null>},
  "date_jouissance": {"valeur": "YYYY-MM-DD|null", "src": <n ou null>},
  "conditions":    {"valeur": ["CODES"], "src": <n ou null>}
}"""


async def _extraire_single_pass(chunks: list[dict], montants: list[float]) -> dict:
    """
    SINGLE-PASS : un SEUL appel LLM extrait TOUS les champs depuis les extraits numérotés.
    Anti-hallucination strict (null hors extraits). `src` = n° d'extrait justifiant la valeur
    (la pièce/page réelle est ensuite dérivée par nous → citation infalsifiable).
    """
    blocs = [
        f"[{i}] (pièce: {c.get('doc_nom')}, page {c.get('page') or '?'}) :\n{c.get('contenu')}"
        for i, c in enumerate(chunks, 1)
    ]
    contexte = "\n\n".join(blocs)
    indice = (f"INDICE — montants € repérés (le prix de cession en fait partie ; ignore prêt/apport/"
              f"capital) : {montants[:8]}\n" if montants else "")
    prompt = (
        "Tu analyses une CESSION D'OFFICINE de pharmacie (avocat d'affaires). Extrais TOUS les "
        "champs demandés EN UN SEUL passage, à partir des SEULS extraits numérotés ci-dessous.\n"
        "RÈGLE ANTI-HALLUCINATION : si une information n'est pas EXPLICITEMENT présente dans les "
        "extraits, mets sa \"valeur\" à null — n'invente JAMAIS.\n"
        "Pour chaque champ, \"src\" = le NUMÉRO [n] de l'extrait qui justifie la valeur (ou null).\n"
        "type_operation : vente du fonds de commerce → \"cession_fonds\" ; vente de parts/actions "
        "de SEL → \"cession_parts\". Montants en euros sans symbole ni espace. montant_global = "
        "PRIX DE CESSION du fonds. Codes de conditions admis : FINANCEMENT, INSCRIPTION_ORDRE, "
        "DECLARATION_ARS, AUDIT_SATISFAISANT, MAINTIEN_CONTRATS, PAS_DE_CHANGEMENT_SUBSTANTIEL, "
        "PURGE_PREEMPTION_COMMUNE, ACCORD_BAILLEUR, AGREMENT_ASSOCIES, PURGE_PREEMPTION_STATUTAIRE.\n"
        f"{indice}\n"
        f"=== EXTRAITS ===\n{contexte}\n\n"
        f"Réponds EXCLUSIVEMENT par CE JSON (mêmes clés, rien d'autre) :\n{_SCHEMA_SINGLE}"
    )
    system = ("Tu extrais des paramètres factuels d'une cession d'officine. Tu réponds "
              "EXCLUSIVEMENT en JSON valide. Jamais d'invention : hors des extraits, valeur=null.")
    try:
        brut = await llm_chat(prompt, system=system, max_tokens=1400)
        return _extraire_json(brut)
    except Exception as e:
        # FAILOVER (quota 429 / connexion Groq) → Gemini 1.5 Flash. Même schéma JSON donc le
        # mapper vers Supabase reste identique. Immunise contre les limites journalières.
        print('Failover triggered: Groq quota exceeded, switching to Gemini 1.5 Flash')
        print(f"[Module L][Failover] cause Groq : {str(e)[:160]}")
        try:
            brut = await _gemini_single_pass(prompt, system)
            return _extraire_json(brut)
        except Exception as e2:
            print(f"[Module L][Failover] Gemini indisponible : {str(e2)[:160]}")
            return {}


async def _gemini_single_pass(prompt: str, system: str) -> str:
    """
    Secours : extraction single-pass via Google Gemini 1.5 Flash (SDK google-generativeai).
    Sortie JSON forcée (response_mime_type) → parsée par le même `_extraire_json`. Clé via
    GEMINI_API_KEY (jamais en dur). Masquage des données sensibles avant envoi (RGPD, comme Groq).
    """
    cle = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not cle:
        raise RuntimeError("GEMINI_API_KEY absente — failover impossible")

    def _call() -> str:
        import google.generativeai as genai
        genai.configure(api_key=cle)
        # NB : gemini-1.5-flash est retiré côté API → on cible un Flash actuel (2.x), overridable.
        modele = genai.GenerativeModel(
            os.getenv("GEMINI_MODEL_FAILOVER", "gemini-2.0-flash"),
            system_instruction=sanitiser_prompt(system),
        )
        resp = modele.generate_content(
            sanitiser_prompt(prompt),
            generation_config={
                "response_mime_type": "application/json",
                "temperature": 0.2,
                "max_output_tokens": 1400,
            },
        )
        return resp.text or ""

    return await asyncio.to_thread(_call)


def _mapper_single_pass(res: dict, chunks: list[dict], montants: list[float]) -> FicheCession:
    """
    Mappe la réponse JSON single-pass → `FicheCession` (validée Pydantic), citations {pièce, page}
    dérivées de l'extrait cité (`src`). C'est l'objet sérialisé tel quel dans Supabase
    (dossiers.metadonnees['fiche_cession']) par `sauver_fiche`. Testable hors LLM/DB.
    """
    def _cit(src):
        if isinstance(src, int) and 1 <= src <= len(chunks):
            c = chunks[src - 1]
            return {"piece": c.get("doc_nom"), "page": c.get("page"),
                    "extrait": (c.get("contenu") or "")[:200]}
        return None

    def _champ(cle):
        f = res.get(cle) or {}
        return (f.get("valeur"), f.get("src")) if isinstance(f, dict) else (None, None)

    data: dict = {"officine": {}, "prix": {}}
    citations: dict = {}

    v, s = _champ("type_operation")
    if isinstance(v, str):
        data["type_operation"] = v
        citations["type_operation"] = _cit(s)

    v, s = _champ("cedant")
    if isinstance(v, dict):
        data["cedants"] = [v]
        citations["cedants"] = _cit(s)

    v, s = _champ("cessionnaire")
    if isinstance(v, dict):
        data["cessionnaires"] = [v]
        citations["cessionnaires"] = _cit(s)

    v, s = _champ("officine")
    if isinstance(v, dict):
        for k in ("nom", "adresse", "finess", "licence_ars"):
            if v.get(k) is not None:
                data["officine"][k] = v.get(k)
        citations["officine"] = _cit(s)
        if v.get("finess") is not None:
            citations["officine.finess"] = _cit(s)

    v, s = _champ("prix")
    if isinstance(v, dict):
        data["prix"] = v
        citations["prix.montant_global"] = _cit(s)

    v, s = _champ("date_jouissance")
    if isinstance(v, str):
        data["date_jouissance_prevue"] = v
        citations["date_jouissance_prevue"] = _cit(s)

    v, s = _champ("conditions")
    if isinstance(v, list):
        data["conditions_detectees"] = v
        citations["conditions_suspensives"] = _cit(s)

    fiche = _construire_fiche(data, montants)
    fiche.citations = {k: c for k, c in citations.items() if c}
    fiche.note_methode = (
        "Fiche pré-remplie par RAG SINGLE-PASS : retrieval ciblé (vectoriel + lexical) puis "
        "extraction de tous les champs en UN appel, avec citations vérifiables. "
        "[VERIFICATION REQUISE PAR L'AVOCAT]."
    )
    return fiche


# ── Construction de la fiche (fusion déterministe + LLM) ───────────────

_PARTIE_TYPES = {"personne_physique", "SELARL", "SELAS", "SELURL", "SELAFA", "SNC", "SARL", "autre"}


def _str_or_none(v) -> str | None:
    """
    Coerce une valeur LLM vers str|None — le LLM renvoie parfois un bool/nombre pour un champ
    texte (ex. `licence_ars: true`), ce qui ferait planter la validation Pydantic.
    bool → None (sans valeur exploitable) ; nombre → str ; chaîne vide → None.
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return v.strip() or None
    return None


def _parties(data: dict, cle: str, role: str, source: str) -> list[Partie]:
    out: list[Partie] = []
    for p in (data.get(cle) or []):
        if not isinstance(p, dict):
            continue
        t = p.get("type") if p.get("type") in _PARTIE_TYPES else "personne_physique"
        try:
            out.append(Partie(
                role=role, source=source, confiance=0.5, type=t,
                denomination=_str_or_none(p.get("denomination")),
                nom=_str_or_none(p.get("nom")),
                prenom=_str_or_none(p.get("prenom")),
                rcs_siren=_str_or_none(p.get("rcs_siren")),
                inscription_ordre=_str_or_none(p.get("inscription_ordre")),
            ))
        except Exception as e:
            print(f"[Module L] partie ignorée (validation) : {e}")
            continue
    return out


def _construire_fiche(data: dict, montants: list[float]) -> FicheCession:
    src = "pièces + appel (auto)"
    type_op = data.get("type_operation") if data.get("type_operation") in (
        "cession_fonds", "cession_parts", "cession_titres"
    ) else "inconnu"

    off = data.get("officine") or {}
    officine = Officine(
        source=src, confiance=0.5,
        nom=_str_or_none(off.get("nom")), adresse=_str_or_none(off.get("adresse")),
        finess=_str_or_none(off.get("finess")), licence_ars=_str_or_none(off.get("licence_ars")),
        type_zone=(off.get("type_zone") if off.get("type_zone") in
                   ("urbaine", "rurale", "monopole") else "inconnue"),
        ca_ht=_num(off.get("ca_ht")),
    )

    pr = data.get("prix") or {}
    prix = PrixCession(
        source=src, confiance=0.5,
        montant_global=_num(pr.get("montant_global")),
        part_incorporel=_num(pr.get("part_incorporel")),
        part_materiel=_num(pr.get("part_materiel")),
        part_stock=_num(pr.get("part_stock")),
    )

    # Conditions suspensives : catalogue DÉTERMINISTE, enrichi du flag « vu dans les pièces ».
    detectees = {str(c).upper() for c in (data.get("conditions_detectees") or [])}
    conditions = [
        ConditionSuspensive(
            code=c["code"], libelle=c["libelle"],
            detecte_dans_pieces=c["code"] in detectees,
        )
        for c in conditions_standard(type_op if type_op != "inconnu" else "cession_parts")
    ]

    avant = data.get("avant_contrat")
    fiche = FicheCession(
        type_operation=type_op,
        avant_contrat=avant if avant in (
            "promesse_synallagmatique", "promesse_unilaterale_vente", "promesse_unilaterale_achat"
        ) else "inconnu",
        officine=officine,
        cedants=_parties(data, "cedants", "cedant", src),
        cessionnaires=_parties(data, "cessionnaires", "cessionnaire", src),
        prix=prix,
        conditions_suspensives=conditions,
        date_jouissance_prevue=_str_or_none(data.get("date_jouissance_prevue")),
    )
    fiche.champs_incertains = _champs_incertains(fiche, montants)
    return fiche


def _num(v) -> float | None:
    try:
        return float(v) if v is not None and str(v) != "" else None
    except (TypeError, ValueError):
        return None


def _champs_incertains(fiche: FicheCession, montants: list[float]) -> list[str]:
    """Liste, à l'attention de l'avocat, ce qui reste à vérifier/compléter."""
    manquants: list[str] = []
    if fiche.type_operation == "inconnu":
        manquants.append("type_operation (fonds de commerce ? parts de SEL ?)")
    if not fiche.cedants:
        manquants.append("cedants (identité du/des vendeur(s))")
    if not fiche.cessionnaires:
        manquants.append("cessionnaires (identité du/des acquéreur(s))")
    if fiche.prix.montant_global is None:
        if montants:
            manquants.append(f"prix.montant_global (montants détectés à vérifier : {montants[:5]})")
        else:
            manquants.append("prix.montant_global (aucun montant détecté)")
    if fiche.officine.finess is None:
        manquants.append("officine.finess")
    if fiche.avant_contrat == "inconnu":
        manquants.append("avant_contrat (synallagmatique ? unilatérale ?)")
    return manquants


# ── Persistance (metadonnees, zéro DDL) ───────────────────────────────

def charger_fiche(dossier: Dossier) -> FicheCession | None:
    brut = (dossier.metadonnees or {}).get(META_KEY)
    if not brut:
        return None
    try:
        return FicheCession.model_validate(brut)
    except Exception:
        return None


def sauver_fiche(dossier: Dossier, fiche: FicheCession, db: Session) -> FicheCession:
    meta = dict(dossier.metadonnees or {})
    meta[META_KEY] = fiche.model_dump()
    dossier.metadonnees = meta            # réassignation explicite → SQLAlchemy détecte le changement
    db.commit()
    return fiche


# ── Point d'entrée ────────────────────────────────────────────────────

async def extraire_fiche_cession(dossier: Dossier, db: Session) -> FicheCession:
    """
    Pré-remplit la Fiche de cession par RAG SINGLE-PASS : indexation (si besoin) → collecte des
    chunks pertinents (retrieval hybride) → UN SEUL appel d'extraction (tous les champs) avec
    citations {pièce, page} et gestion stricte du null. Persiste la fiche dans Supabase.
    """
    # 1. Indexation à la demande (pièces ingérées par email ou antérieures au RAG).
    try:
        n = await indexer_dossier_si_besoin(dossier.id, db)
        if n:
            print(f"[Module L][RAG] {n} chunks indexés pour {dossier.id}")
    except Exception as e:
        print(f"[Module L][RAG] indexation échouée : {e}")

    # 2. Aucun chunk → aucune pièce exploitable : fiche vide (pas d'appel LLM, pas d'hallucination).
    if not _a_des_chunks(dossier.id, db):
        fiche = FicheCession()
        fiche.champs_incertains = _champs_incertains(fiche, [])
        fiche.note_methode = (
            "Aucune pièce exploitable indexée : saisie manuelle requise. "
            "[VERIFICATION REQUISE PAR L'AVOCAT]"
        )
        return sauver_fiche(dossier, fiche, db)

    # Indices déterministes (montants €) — désambiguïsation du prix + champs incertains.
    montants = extraire_montants(contexte_documents(db, dossier.id, max_chars=20000))

    # 3. SINGLE-PASS : collecte des chunks pertinents → UN seul appel d'extraction.
    chunks = await _gather_chunks(dossier.id, db)
    try:
        res = await _extraire_single_pass(chunks, montants)
    except Exception as e:
        print(f"[Module L][RAG] extraction single-pass échouée : {e}")
        res = {}

    # 4. Mapping JSON → FicheCession (validée) → persistance Supabase (dossiers.metadonnees).
    fiche = _mapper_single_pass(res, chunks, montants)
    return sauver_fiche(dossier, fiche, db)


def valider_fiche(dossier: Dossier, payload: dict, db: Session) -> FicheCession:
    """
    Applique les corrections de l'avocat (HITL) : fusionne `payload` sur la fiche existante,
    revalide via Pydantic, recalcule les champs incertains. L'avocat fait foi (confiance=1).
    """
    courante = charger_fiche(dossier) or FicheCession()
    fusion = {**courante.model_dump(), **(payload or {})}
    fiche = FicheCession.model_validate(fusion)
    fiche.champs_incertains = _champs_incertains(fiche, [])
    return sauver_fiche(dossier, fiche, db)


# ── Lot 4 — Suivi des conditions suspensives (déterministe, 0 % LLM) ────

STATUTS_CONDITION = ("EN_ATTENTE", "LEVEE", "DEFAILLIE")


def _deadline_type(code: str) -> str:
    return f"condition_{code.lower()}"


def _maj_deadline_condition(dossier: Dossier, cond, db: Session) -> None:
    """
    Branche la date butoir d'une condition sur le Module F (alertes J-30/14/7/1).
    EN_ATTENTE + date butoir → (re)crée/maj la deadline ; LEVEE/DEFAILLIE → acquitte.
    Idempotent (une seule deadline par condition, repérée par son type_delai).
    """
    from datetime import datetime as _dt
    from core.models import Deadline
    type_delai = _deadline_type(cond.code)
    existante = (
        db.query(Deadline)
        .filter(Deadline.dossier_id == dossier.id, Deadline.type_delai == type_delai)
        .first()
    )

    if cond.statut in ("LEVEE", "DEFAILLIE"):
        if existante:
            existante.acquitte = True
            db.commit()
        return

    if not cond.date_butoir:
        return
    try:
        echeance = _dt.fromisoformat(cond.date_butoir)
    except ValueError:
        return

    titre = f"Condition suspensive — {cond.libelle}"[:200]
    desc = (f"Date butoir de la condition suspensive « {cond.libelle} » "
            f"(cession). [VERIFICATION REQUISE PAR L'AVOCAT]")
    if existante:
        existante.date_echeance = echeance
        existante.titre = titre
        existante.acquitte = False
    else:
        db.add(Deadline(
            titre=titre, description=desc, date_echeance=echeance,
            type_delai=type_delai, dossier_id=dossier.id,
        ))
    db.commit()


def maj_condition(dossier: Dossier, code: str, db: Session,
                  statut: str | None = None, date_butoir: str | None = None,
                  preuve_doc_id: str | None = None) -> FicheCession:
    """
    Met à jour UNE condition suspensive (statut / date butoir / preuve). 0 % LLM.
    Le passage à LEVEE/DEFAILLIE est une décision HUMAINE (jamais déduite par le LLM).
    """
    fiche = charger_fiche(dossier)
    if not fiche:
        raise ValueError("Aucune fiche de cession : extraire d'abord les paramètres.")
    cible = next((c for c in fiche.conditions_suspensives if c.code == code), None)
    if cible is None:
        raise ValueError(f"Condition suspensive inconnue : {code}")

    if statut is not None:
        if statut not in STATUTS_CONDITION:
            raise ValueError(f"Statut invalide : {statut} (attendu : {STATUTS_CONDITION})")
        cible.statut = statut
    if date_butoir is not None:
        cible.date_butoir = date_butoir or None
    if preuve_doc_id is not None:
        cible.preuve_doc_id = preuve_doc_id or None

    sauver_fiche(dossier, fiche, db)
    try:
        _maj_deadline_condition(dossier, cible, db)
    except Exception as e:   # ne jamais bloquer la mise à jour métier pour une deadline
        print(f"[Module L] deadline condition non synchronisée : {e}")
    return fiche


def etat_conditions(fiche: FicheCession) -> dict:
    """
    État global des conditions APPLICABLES (déterministe). `pretes_pour_acte` = toutes
    levées et aucune défaillie → débloque la génération de l'acte définitif (Lot 5/S3).
    """
    applicables = [c for c in fiche.conditions_suspensives if c.applicable]
    levees = [c for c in applicables if c.statut == "LEVEE"]
    defaillies = [c for c in applicables if c.statut == "DEFAILLIE"]
    restantes = [c for c in applicables if c.statut == "EN_ATTENTE"]
    return {
        "nb_applicables": len(applicables),
        "nb_levees": len(levees),
        "nb_defaillies": len(defaillies),
        "nb_restantes": len(restantes),
        "pretes_pour_acte": bool(applicables) and not restantes and not defaillies,
        "restantes": [c.libelle for c in restantes],
        "defaillies": [c.libelle for c in defaillies],
    }
