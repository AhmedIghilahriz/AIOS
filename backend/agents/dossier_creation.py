"""
AIOS — Création de dossier avec validation humaine (LangGraph + interrupt).

Pattern "human-in-the-loop" du cahier des charges (PROPOSE -> validation avocat -> EXECUTE) :

        START
          │
   ┌──────▼──────────┐
   │ preparer        │  construit la proposition (déterministe, sans DB)
   └──────┬──────────┘
   ┌──────▼──────────┐
   │ validation      │  interrupt() -> le graphe se MET EN PAUSE et attend l'avocat
   └──────┬──────────┘     (l'état est persisté par le checkpointer SQLite)
   ┌──────▼──────────┐  decision == "valider" ?
   │  (conditionnel) │ ──── non ──►  rejeter  ──►  END   (aucune écriture en base)
   └──────┬──────────┘
        oui
   ┌──────▼──────────┐
   │ creer           │  crée RÉELLEMENT le client + dossier en base
   └──────┬──────────┘
        END

Le dossier n'est JAMAIS créé tant que l'avocat n'a pas validé. La reprise se fait
via Command(resume="valider"|"rejeter") sur le même thread_id.
"""
import os
import uuid
import random
import string
import sqlite3
from pathlib import Path
from typing import TypedDict, Optional

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver


class CreationState(TypedDict, total=False):
    # entrée (sérialisable — pas d'objet ORM ni de session dans l'état)
    expediteur: str
    sujet: str
    resume_ia: str
    categorie: str
    avocat_id: Optional[str]
    cabinet_id: str
    # étapes
    proposition: dict
    decision: str
    dossier_id: Optional[str]
    statut: str   # PROPOSE | CREE | REJETE


def _parse_expediteur(exp: str) -> tuple[str, str]:
    exp = exp or ""
    if "<" in exp:
        nom = exp.split("<")[0].strip().strip('"')
        email = exp.split("<")[1].rstrip(">").strip()
    else:
        email = exp.strip()
        nom = email.split("@")[0] if "@" in email else email
    return (nom or email), email


# ── Nœud 1 : préparer la proposition (déterministe, sans DB) ──────────

def preparer_proposition(state: CreationState) -> dict:
    nom, email = _parse_expediteur(state.get("expediteur", ""))
    type_map = {
        "client": "consultation",
        "prospect": "prospect",
        "juridiction": "procedure_judiciaire",
        "fournisseur": "contrat_fournisseur_pharmacie",
        "administratif": "litige_ars_pharmacie",
    }
    proposition = {
        "client_nom": nom,
        "client_email": email,
        "titre": f"[Email] {(state.get('sujet') or 'Nouveau dossier')[:80]}",
        "specialite": "affaires",
        "type_dossier": type_map.get(state.get("categorie", "autre"), "consultation"),
        "resume": state.get("resume_ia", ""),
    }
    return {"proposition": proposition, "statut": "PROPOSE"}


# ── Nœud 2 : validation humaine (PAUSE via interrupt) ─────────────────

def validation_humaine(state: CreationState) -> dict:
    decision = interrupt({
        "type": "validation_creation_dossier",
        "message": f"Créer le dossier « {state['proposition']['titre']} » "
                   f"pour {state['proposition']['client_nom']} ({state['proposition']['client_email']}) ?",
        "proposition": state["proposition"],
        "options": ["valider", "rejeter"],
    })
    if isinstance(decision, dict):
        decision = decision.get("decision", "rejeter")
    return {"decision": str(decision or "rejeter").lower().strip()}


def _apres_validation(state: CreationState) -> str:
    return "valider" if state.get("decision") == "valider" else "rejeter"


# ── Nœud 3a : création réelle (après validation) ──────────────────────

def creer_dossier_node(state: CreationState) -> dict:
    from core.database import SessionLocal
    from core.models import (
        Client as ClientModel, Dossier, Specialite, DossierStatus, PrioriteLevel,
    )
    from core.orchestrateur import get_embedding_sync

    db = SessionLocal()
    try:
        prop = state["proposition"]
        client = db.query(ClientModel).filter(ClientModel.email == prop["client_email"]).first()
        if not client:
            client = ClientModel(
                id=str(uuid.uuid4()),
                cabinet_id=state.get("cabinet_id", "default"),
                nom=prop["client_nom"],
                email=prop["client_email"],
                type_client="professionnel",
                notes=f"Créé après validation avocat. {prop.get('resume', '')}",
            )
            db.add(client)
            db.flush()

        ref = "VAL-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        dossier = Dossier(
            id=str(uuid.uuid4()),
            cabinet_id=state.get("cabinet_id", "default"),
            avocat_id=state.get("avocat_id"),
            client_id=client.id,
            reference=ref,
            titre=prop["titre"],
            specialite=Specialite.AFFAIRES,
            status=DossierStatus.NOUVEAU,
            priorite=PrioriteLevel.STANDARD,
            description=prop.get("resume", ""),
            metadonnees={"type_dossier": prop["type_dossier"], "source": "validation_humaine"},
        )
        dossier.embedding = get_embedding_sync(f"{dossier.titre} {ref} {dossier.description}")
        db.add(dossier)
        db.commit()
        db.refresh(dossier)
        return {"dossier_id": dossier.id, "statut": "CREE"}
    finally:
        db.close()


# ── Nœud 3b : rejet (aucune écriture) ─────────────────────────────────

def rejeter_node(state: CreationState) -> dict:
    return {"statut": "REJETE", "dossier_id": None}


# ── Checkpointer SQLite (persiste l'état pendant la pause) ────────────
# Persistant entre redémarrages. En production multi-instances : préférer le
# checkpointer Postgres (langgraph-checkpoint-postgres) sur Supabase.

_DB_PATH = os.getenv(
    "LANGGRAPH_CHECKPOINT_DB",
    str(Path(__file__).resolve().parents[1] / "aios_graph.db"),
)
_conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
_conn.execute("PRAGMA journal_mode=WAL")
_checkpointer = SqliteSaver(_conn)


def _build():
    g = StateGraph(CreationState)
    g.add_node("preparer", preparer_proposition)
    g.add_node("validation", validation_humaine)
    g.add_node("creer", creer_dossier_node)
    g.add_node("rejeter", rejeter_node)
    g.add_edge(START, "preparer")
    g.add_edge("preparer", "validation")
    g.add_conditional_edges("validation", _apres_validation, {"valider": "creer", "rejeter": "rejeter"})
    g.add_edge("creer", END)
    g.add_edge("rejeter", END)
    return g.compile(checkpointer=_checkpointer)


_graph = _build()


def proposer_creation(email_data: dict, thread_id: str) -> dict:
    """Lance le graphe jusqu'à l'interruption (pause). Retourne la proposition à valider."""
    config = {"configurable": {"thread_id": thread_id}}
    result = _graph.invoke(email_data, config=config)
    interrupts = result.get("__interrupt__")
    if interrupts:
        payload = interrupts[0].value
        return {"thread_id": thread_id, "en_attente_validation": True, **payload}
    return {"thread_id": thread_id, "en_attente_validation": False, "statut": result.get("statut")}


def resoudre_creation(thread_id: str, decision: str) -> dict:
    """Reprend le graphe en pause avec la décision de l'avocat (valider/rejeter)."""
    config = {"configurable": {"thread_id": thread_id}}
    result = _graph.invoke(Command(resume=decision), config=config)
    return {
        "thread_id": thread_id,
        "decision": result.get("decision"),
        "statut": result.get("statut"),
        "dossier_id": result.get("dossier_id"),
    }
