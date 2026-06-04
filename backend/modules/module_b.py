"""
AIOS — Module B : CRM Juridique + Recherche sémantique
pgvector intégré PostgreSQL (GRATUIT — remplace Pinecone ~70€/mois)
"""
import os
import re
from sqlalchemy.orm import Session
from sqlalchemy import text
from core.models import Client, Dossier, DossierStatus
from core.orchestrateur import get_embedding, generer_texte_juridique
from datetime import datetime, timedelta


async def indexer_dossier(dossier: Dossier, db: Session):
    """Génère et stocke l'embedding d'un dossier pour la recherche sémantique."""
    texte_a_indexer = " ".join(filter(None, [
        dossier.titre,
        dossier.specialite,
        dossier.description,
        str(dossier.metadonnees) if dossier.metadonnees else "",
        dossier.reference,
    ]))
    dossier.embedding = await get_embedding(texte_a_indexer)
    db.commit()


async def indexer_client(client: Client, db: Session):
    """Indexe un client pour la recherche sémantique."""
    texte = " ".join(filter(None, [
        client.nom, client.prenom, client.email,
        client.notes, str(client.tags) if client.tags else "",
        client.siret or "",
    ]))
    client.embedding = await get_embedding(texte)
    db.commit()


async def recherche_semantique_dossiers(
    query: str,
    cabinet_id: str,
    db: Session,
    limite: int = 10,
    seuil_similarite: float = 0.3
) -> list[dict]:
    """
    Recherche en langage naturel dans tous les dossiers du cabinet.
    Exemples :
      "dossiers de cession de fonds de commerce en 2024"
      "divorces en cours avec des biens immobiliers"
    Utilise pgvector — 100% gratuit.
    """
    query_embedding = await get_embedding(query)

    resultats = db.execute(
        text("""
            SELECT
                d.id, d.reference, d.titre, d.specialite, d.status,
                d.priorite, d.created_at,
                c.nom as client_nom, c.prenom as client_prenom,
                1 - (d.embedding <=> CAST(:embedding AS vector)) as similarite
            FROM dossiers d
            LEFT JOIN clients c ON d.client_id = c.id
            WHERE d.cabinet_id = :cabinet_id
              AND d.embedding IS NOT NULL
              AND 1 - (d.embedding <=> CAST(:embedding AS vector)) > :seuil
            ORDER BY d.embedding <=> CAST(:embedding AS vector)
            LIMIT :limite
        """),
        {
            "embedding": str(query_embedding),
            "cabinet_id": cabinet_id,
            "seuil": seuil_similarite,
            "limite": limite
        }
    ).fetchall()

    return [dict(r._mapping) for r in resultats]


async def recherche_hybride(
    query: str,
    cabinet_id: str,
    db: Session,
    limite: int = 10
) -> list[dict]:
    """Recherche hybride : sémantique (pgvector) + texte (pg_trgm)."""
    query_embedding = await get_embedding(query)

    resultats = db.execute(
        text("""
            SELECT
                d.id, d.reference, d.titre, d.specialite, d.status,
                c.nom as client_nom,
                (0.7 * (1 - (d.embedding <=> CAST(:embedding AS vector))))
                + (0.3 * similarity(d.titre, :query)) as score_final
            FROM dossiers d
            LEFT JOIN clients c ON d.client_id = c.id
            WHERE d.cabinet_id = :cabinet_id
              AND d.embedding IS NOT NULL
            ORDER BY score_final DESC
            LIMIT :limite
        """),
        {
            "embedding": str(query_embedding),
            "query": query,
            "cabinet_id": cabinet_id,
            "limite": limite
        }
    ).fetchall()

    return [dict(r._mapping) for r in resultats]


def detecter_dossiers_inactifs(
    db: Session,
    cabinet_id: str,
    seuil_jours: int = 30
) -> list[Dossier]:
    """Détecte les dossiers actifs sans modification depuis X jours."""
    seuil = datetime.utcnow() - timedelta(days=seuil_jours)
    return db.query(Dossier).filter(
        Dossier.cabinet_id == cabinet_id,
        Dossier.status == DossierStatus.EN_COURS,
        Dossier.updated_at < seuil
    ).all()


async def generer_resume_dossier(dossier: Dossier) -> str:
    """Génère un résumé intelligent du dossier pour la fiche avocat."""
    return await generer_texte_juridique(
        "Génère un résumé professionnel de ce dossier en 5 points maximum. "
        "Format : bullet points. Sois factuel et concis.",
        {
            "titre": dossier.titre,
            "specialite": dossier.specialite,
            "status": dossier.status,
            "description": dossier.description,
            "metadonnees": dossier.metadonnees,
            "created_at": str(dossier.created_at),
        },
        specialite=dossier.specialite,
        max_tokens=500
    )


# ── Cas 10 — Détection de conflit d'intérêts (DÉTERMINISTE, 0 % LLM) ──────────────────
# Garde déontologique : avant de proposer un dossier pour un nouveau prospect, on vérifie
# qu'il ne recoupe pas une PARTIE ADVERSE déjà connue du cabinet. Les parties adverses sont
# déclarées dans `dossier.metadonnees["partie_adverse"]` (str) ou `["parties_adverses"]` (list).
# La décision de blocage repose sur un SEUIL NUMÉRIQUE (jamais le LLM).

_CONFLIT_STOPWORDS = {
    "sas", "sarl", "sa", "selarl", "selas", "eurl", "snc", "sci", "scp", "monsieur",
    "madame", "mme", "mr", "pharmacie", "officine", "cabinet", "groupe", "ets", "etablissements",
}


def _normaliser_nom(s: str) -> set[str]:
    """Jeton normalisé d'un nom (minuscule, sans ponctuation, sans formes sociales)."""
    s = (s or "").lower()
    s = re.sub(r"[^a-zà-ÿ0-9 ]", " ", s)
    return {w for w in s.split() if len(w) >= 3 and w not in _CONFLIT_STOPWORDS}


def detecter_conflit_interets(db: Session, nom_prospect: str, cabinet_id: str = "default",
                              email: str | None = None) -> dict:
    """
    Cherche un conflit entre le prospect et une partie adverse déjà connue du cabinet.
    Retourne {"conflit": bool, "score": float, "dossiers": [{dossier_id, reference, score, via}]}.
    Déterministe : recouvrement de jetons (indice de Jaccard) vs seuil CONFLICT_THRESHOLD.
    """
    toks = _normaliser_nom(nom_prospect)
    resultat = {"conflit": False, "score": 0.0, "dossiers": []}
    if not toks:
        return resultat
    seuil = float(os.getenv("CONFLICT_THRESHOLD", "0.85"))
    try:
        dossiers = db.query(Dossier).filter(Dossier.cabinet_id == cabinet_id).all()
    except Exception:
        return resultat
    for d in dossiers:
        meta = d.metadonnees or {}
        adverses = []
        if meta.get("partie_adverse"):
            adverses.append(str(meta["partie_adverse"]))
        adverses += [str(x) for x in (meta.get("parties_adverses") or []) if x]
        for adverse in adverses:
            at = _normaliser_nom(adverse)
            if not at:
                continue
            jacc = len(toks & at) / len(toks | at)
            # Conflit si Jaccard >= seuil OU si le nom prospect est entièrement contenu (sous-ensemble).
            if jacc >= seuil or (toks and toks <= at):
                resultat["dossiers"].append({
                    "dossier_id": d.id, "reference": d.reference,
                    "score": round(max(jacc, 1.0 if toks <= at else jacc), 2), "via": "partie_adverse",
                })
                break
    if resultat["dossiers"]:
        resultat["conflit"] = True
        resultat["score"] = max(x["score"] for x in resultat["dossiers"])
    return resultat


def generer_reference_dossier(specialite: str, annee: int, sequence: int) -> str:
    """Génère une référence unique. Ex: AFF-2025-0042"""
    prefixes = {
        "affaires": "AFF", "social": "SOC", "immobilier": "IMM",
        "famille": "FAM", "penal": "PEN", "fiscal": "FIS", "autre": "DIV",
    }
    prefix = prefixes.get(specialite, "DIV")
    return f"{prefix}-{annee}-{sequence:04d}"
