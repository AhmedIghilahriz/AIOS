"""
HITL — tests du graphe de création de dossier avec interrupt().
On vérifie la mise en pause et le rejet SANS écrire en base ni appeler le LLM
(le chemin 'valider' qui écrit en base est couvert par le smoke test, pas ici).
"""
import uuid

from agents.dossier_creation import proposer_creation, resoudre_creation


def test_proposer_met_en_pause():
    tid = str(uuid.uuid4())
    out = proposer_creation(
        {"expediteur": "Jean Dupont <jean@pharma.fr>", "sujet": "Cession officine", "categorie": "client"},
        tid,
    )
    assert out["en_attente_validation"] is True
    assert out["proposition"]["client_email"] == "jean@pharma.fr"
    assert out["proposition"]["client_nom"] == "Jean Dupont"
    assert "valider" in out["options"]


def test_rejeter_ne_cree_rien():
    tid = str(uuid.uuid4())
    proposer_creation({"expediteur": "spam@x.com", "sujet": "Pub", "categorie": "autre"}, tid)
    res = resoudre_creation(tid, "rejeter")
    assert res["statut"] == "REJETE"
    assert res["dossier_id"] is None


def test_threads_independants():
    t1, t2 = str(uuid.uuid4()), str(uuid.uuid4())
    o1 = proposer_creation({"expediteur": "a@a.fr", "sujet": "A", "categorie": "client"}, t1)
    o2 = proposer_creation({"expediteur": "b@b.fr", "sujet": "B", "categorie": "fournisseur"}, t2)
    assert o1["proposition"]["client_email"] == "a@a.fr"
    assert o2["proposition"]["client_email"] == "b@b.fr"
    assert o2["proposition"]["type_dossier"] == "contrat_fournisseur_pharmacie"
