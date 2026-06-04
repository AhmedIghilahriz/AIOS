"""
Module F — tests déterministes (OBLIGATOIRES avant tout déploiement).
Un délai manqué = forclusion. Ces tests garantissent qu'aucun LLM n'intervient
et que le calendrier (week-ends/fériés) est correctement géré.
"""
from datetime import datetime, date
import pytest

from modules.module_f import calculer_echeance, DELAIS_LEGAUX


def test_appel_civil_30_jours_jour_ouvre():
    # 1er juin 2026 + 30 jours calendaires, prorogé au prochain jour ouvré
    res = calculer_echeance(datetime(2026, 6, 1), "appel_civil")
    assert res.weekday() < 5                      # jamais un samedi/dimanche
    assert (res.date() - date(2026, 6, 1)).days >= 30


def test_retractation_immo_jamais_weekend():
    res = calculer_echeance(datetime(2026, 6, 1), "retractation_vente_immobiliere")
    assert res.weekday() < 5


def test_instruction_ars_4_mois():
    # Délai pharmacie ajouté pour le Module H : ~120 jours
    res = calculer_echeance(datetime(2026, 1, 5), "instruction_ars")
    assert (res.date() - date(2026, 1, 5)).days >= 120
    assert res.weekday() < 5


def test_type_delai_inconnu_leve_erreur():
    with pytest.raises(ValueError):
        calculer_echeance(datetime(2026, 6, 1), "delai_qui_nexiste_pas")


def test_toutes_les_regles_calculables():
    # Aucune règle de la table ne doit lever d'exception au calcul
    for type_delai in DELAIS_LEGAUX:
        res = calculer_echeance(datetime(2026, 3, 2), type_delai)
        assert res.weekday() < 5
