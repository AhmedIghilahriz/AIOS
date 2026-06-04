"""
Module H — tests déterministes (due diligence, valorisation, suivi ARS).
Aucune dépendance réseau / LLM : tout est calculé en Python.
"""
from datetime import datetime, timedelta

from modules import module_h


def test_due_diligence_vide_recommande_abandon():
    res = module_h.evaluer_due_diligence([])
    assert res["completude_pct"] == 0
    assert res["recommandation"] == "ABANDON"
    assert res["nb_recus"] == 0


def test_due_diligence_complete_recommande_poursuivre():
    tous_les_labels = [it["label"] for it in module_h.liste_checklist()]
    res = module_h.evaluer_due_diligence(tous_les_labels)
    assert res["completude_pct"] == 100
    assert res["recommandation"] == "POURSUIVRE"
    assert res["points_bloquants"] == []


def test_valorisation_officine_urbaine():
    res = module_h.calculer_valorisation(1_000_000, "urbaine")
    assert res["fourchette_bas"] == 600_000.0
    assert res["fourchette_haut"] == 800_000.0


def test_ars_sans_depot():
    res = module_h.suivi_instruction_ars(None)
    assert res["jours_restants"] is None
    assert res["risque_silence"] == "NON"


def test_ars_depot_recent_pas_de_risque():
    res = module_h.suivi_instruction_ars(datetime.utcnow(), etape="DEPOT")
    assert res["jours_restants"] >= 110          # ~120 jours
    assert res["risque_silence"] == "NON"
    assert res["etape_actuelle"] == "DEPOT"


def test_ars_delai_depasse_silence_vaut_refus():
    res = module_h.suivi_instruction_ars(datetime.utcnow() - timedelta(days=130), etape="INSTRUCTION")
    assert res["jours_restants"] < 0
    assert res["risque_silence"] == "OUI"
    assert res["niveau_alerte"] == "CRITIQUE"
