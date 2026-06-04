"""
Module K — tests déterministes (filtrage, impact, parsing RSS).
Aucun réseau / LLM / DB.
"""
from modules import module_k


def test_filtre_garde_pharma_et_ecarte_le_reste():
    items = [
        {"titre": "Décret transfert d'officines (L.5125 CSP)", "contenu": "officine pharmacie"},
        {"titre": "Convention pharmaceutique CPAM remboursement", "contenu": "remboursement"},
        {"titre": "Nomination d'un ambassadeur", "contenu": "diplomatie, hors sujet"},
    ]
    res = module_k.filtrer_pertinence(items)
    # 2 items pertinents (officine/CSP, CPAM/remboursement) ; la nomination est écartée
    assert len(res) == 2
    assert all(it["mots_cles"] for it in res)


def test_impact_critique_officine_csp():
    assert module_k.classer_impact({"titre": "Décret L.5125 officine", "contenu": ""}) == "CRITIQUE"


def test_impact_eleve_remboursement():
    assert module_k.classer_impact({"titre": "Circulaire CPAM remboursement", "contenu": ""}) == "ELEVE"


def test_impact_moyen_par_defaut():
    assert module_k.classer_impact({"titre": "Pharmacien : note d'information", "contenu": ""}) == "MOYEN"


def test_parse_rss_minimal():
    xml = ("<rss><channel><item><title>Officine ARS Lyon</title>"
           "<link>http://x.fr/1</link><description>cession officine</description>"
           "<pubDate>2026-05-01</pubDate></item></channel></rss>")
    items = module_k._parse_rss(xml)
    assert len(items) == 1
    assert items[0]["titre"] == "Officine ARS Lyon"
    assert items[0]["url"] == "http://x.fr/1"


def test_parse_rss_invalide_ne_crash_pas():
    assert module_k._parse_rss("ceci n'est pas du XML") == []
