"""
AIOS — Schéma de la « Fiche de cession » (Module L).

Contrat de données TYPÉ qui consolide les paramètres d'une cession d'officine,
quelle que soit la source (saisie avocat > extraction des pièces > transcription appel).

Principe (cf. docs/CDC_cession_officine.md §4 et CLAUDE.md §2) :
  • Chaque sous-objet porte sa PROVENANCE (`source`) et un niveau de CONFIANCE (`confiance`).
  • Ce qui n'a pas pu être déterminé va dans `champs_incertains` → l'avocat tranche (HITL).
  • Les montants/dates/numéros sont, autant que possible, extraits de façon DÉTERMINISTE
    (regex) ; le LLM ne fait que LOCALISER/QUALIFIER, jamais décider d'un point de droit.

Aucune table dédiée : la fiche est sérialisée dans `dossiers.metadonnees["fiche_cession"]`
(comme l'état ARS du Module H) → zéro migration.
"""
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Literal, Optional


# ── Provenance commune ────────────────────────────────────────────────
class Provenance(BaseModel):
    """D'où vient l'information et à quel point on y croit (0..1)."""
    source: str = ""            # ex. "document:bilan_2024.pdf p.4" | "appel" | "avocat"
    confiance: float = 0.0      # 0 = inconnu, 1 = certain (validé avocat)


# ── Sous-objets métier ────────────────────────────────────────────────
class Partie(Provenance):
    role: Literal["cedant", "cessionnaire"]
    type: Literal[
        "personne_physique", "SELARL", "SELAS", "SELURL", "SELAFA", "SNC", "SARL", "autre"
    ] = "personne_physique"
    denomination: Optional[str] = None        # raison sociale si personne morale
    nom: Optional[str] = None
    prenom: Optional[str] = None
    rcs_siren: Optional[str] = None
    inscription_ordre: Optional[str] = None   # n° / Section A (titulaire d'officine)
    domicile_siege: Optional[str] = None


class Officine(Provenance):
    nom: Optional[str] = None
    adresse: Optional[str] = None
    finess: Optional[str] = None              # n° FINESS de l'officine
    licence_ars: Optional[str] = None         # réf. autorisation/licence ARS
    type_zone: Literal["urbaine", "rurale", "monopole", "inconnue"] = "inconnue"
    ca_ht: Optional[float] = None             # dernier CA HT connu (sert à la valorisation H.1)


class PrixCession(Provenance):
    montant_global: Optional[float] = None
    part_incorporel: Optional[float] = None   # clientèle / droit au bail / licence
    part_materiel: Optional[float] = None      # mobilier, agencement, matériel
    part_stock: Optional[float] = None         # marchandises (inventaire contradictoire)
    devise: str = "EUR"


class ConditionSuspensive(BaseModel):
    """Une condition suspensive de la promesse. Le suivi (Lot 4) réutilise ce schéma."""
    code: str                                  # ex. "FINANCEMENT" (cf. CONDITIONS_SUSPENSIVES_STANDARD)
    libelle: str
    applicable: bool = True                    # l'avocat peut désactiver une condition non pertinente
    statut: Literal["EN_ATTENTE", "LEVEE", "DEFAILLIE"] = "EN_ATTENTE"
    date_butoir: Optional[str] = None          # ISO "YYYY-MM-DD"
    preuve_doc_id: Optional[str] = None        # Document.id prouvant la levée
    detecte_dans_pieces: bool = False          # le LLM a vu une mention dans les pièces


class GAP(Provenance):
    """Garantie d'actif et de passif (surtout cession de parts)."""
    duree_mois: Optional[int] = None
    plafond: Optional[float] = None
    franchise: Optional[float] = None
    sequestre_garantie: Optional[float] = None


class NonConcurrence(Provenance):
    perimetre_km: Optional[float] = None
    duree_mois: Optional[int] = None
    note: Optional[str] = None                 # doit rester proportionnée (temps + espace)


class Sequestre(Provenance):
    type: Literal["carpa_avocat", "notaire", "autre", "inconnu"] = "inconnu"
    coordonnees: Optional[str] = None


# ── Fiche consolidée ──────────────────────────────────────────────────
class FicheCession(BaseModel):
    type_operation: Literal[
        "cession_fonds", "cession_parts", "cession_titres", "inconnu"
    ] = "inconnu"
    avant_contrat: Literal[
        "promesse_synallagmatique", "promesse_unilaterale_vente",
        "promesse_unilaterale_achat", "inconnu"
    ] = "inconnu"

    officine: Officine = Field(default_factory=lambda: Officine())
    cedants: list[Partie] = Field(default_factory=list)
    cessionnaires: list[Partie] = Field(default_factory=list)
    prix: PrixCession = Field(default_factory=lambda: PrixCession())
    conditions_suspensives: list[ConditionSuspensive] = Field(default_factory=list)
    garantie_actif_passif: Optional[GAP] = None
    non_concurrence: Optional[NonConcurrence] = None
    sequestre: Optional[Sequestre] = None
    date_jouissance_prevue: Optional[str] = None    # ISO

    # Pilotage HITL : ce que l'avocat DOIT vérifier / compléter.
    champs_incertains: list[str] = Field(default_factory=list)
    # Citations RAG : pour chaque champ extrait, la source vérifiable par l'avocat
    # (clé = chemin du champ ; valeur = {"piece", "page", "extrait"}). Cliquable côté UI.
    citations: dict = Field(default_factory=dict)
    # Métadonnées de génération (jamais un point de droit).
    note_methode: str = (
        "Fiche pré-remplie automatiquement (pièces + appel). "
        "[VERIFICATION REQUISE PAR L'AVOCAT] avant toute génération d'acte."
    )


# ── Référentiel déterministe des conditions suspensives (droit pharma) ──
# Catalogue par type d'opération (cf. docs/CDC_cession_officine.md §4.4-B).
# DÉTERMINISTE : c'est le socle proposé ; l'avocat coche/décoche, le LLM ne fait
# que signaler `detecte_dans_pieces`.
CONDITIONS_SUSPENSIVES_STANDARD: dict[str, list[dict]] = {
    "_commun": [
        {"code": "FINANCEMENT",
         "libelle": "Obtention par l'acquéreur de son financement (prêt bancaire)"},
        {"code": "INSCRIPTION_ORDRE",
         "libelle": "Inscription de l'acquéreur au tableau de l'Ordre (Section A, titulaire)"},
        {"code": "DECLARATION_ARS",
         "libelle": "Déclaration d'exploitation / non-opposition de l'ARS (changement de titulaire)"},
        {"code": "AUDIT_SATISFAISANT",
         "libelle": "Résultat satisfaisant de l'audit / due diligence (absence de passif caché)"},
        {"code": "MAINTIEN_CONTRATS",
         "libelle": "Maintien des contrats essentiels (grossiste-répartiteur, LGO, assurances)"},
        {"code": "PAS_DE_CHANGEMENT_SUBSTANTIEL",
         "libelle": "Absence de changement substantiel entre la promesse et la réitération (MAC)"},
    ],
    "cession_fonds": [
        {"code": "PURGE_PREEMPTION_COMMUNE",
         "libelle": "Purge du droit de préemption de la commune (art. L.214-1 C. urb.)"},
        {"code": "ACCORD_BAILLEUR",
         "libelle": "Accord du bailleur (clause d'agrément au bail commercial)"},
    ],
    "cession_parts": [
        {"code": "AGREMENT_ASSOCIES",
         "libelle": "Agrément des coassociés (clause d'agrément des statuts de la SEL)"},
        {"code": "PURGE_PREEMPTION_STATUTAIRE",
         "libelle": "Purge du droit de préemption statutaire des associés"},
    ],
}
# Les titres (SELAS) suivent les mêmes conditions que les parts.
CONDITIONS_SUSPENSIVES_STANDARD["cession_titres"] = CONDITIONS_SUSPENSIVES_STANDARD["cession_parts"]


def conditions_standard(type_operation: str) -> list[dict]:
    """Catalogue déterministe des conditions suspensives pour un type d'opération donné."""
    base = list(CONDITIONS_SUSPENSIVES_STANDARD["_commun"])
    base += CONDITIONS_SUSPENSIVES_STANDARD.get(type_operation, [])
    return base
