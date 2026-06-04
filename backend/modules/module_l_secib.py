"""
AIOS — Module L : versement du dossier dans SECIB (éditeur Septeo).

Couche d'abstraction `SecibConnector` (cf. docs/CDC_cession_officine.md §3) :
le reste du pipeline ne dépend JAMAIS du mode d'intégration. Une seule
implémentation est livrée pour le MVP — le FALLBACK garanti, sans API :

  SecibExportPackage — « paquet de transfert » : un dossier normalisé
    SECIB_IMPORT/<reference>/
      ├── _index.csv          (1 ligne par pièce, colonnes à mapper sur l'import SECIB)
      ├── 01_<type>.pdf
      ├── 02_<type>.pdf
      └── …                    (pièces renommées de façon déterministe)
  L'avocat (ou un robot RPA) glisse ce dossier dans SECIB. Quand l'API Septeo
  sera confirmée, on ajoute `SecibApiConnector` SANS toucher au reste.

100 % local, zéro dépendance, conforme « pas de Docker / disque limité » (CLAUDE.md).
"""
from __future__ import annotations
import os
import csv
import shutil
import re
from pathlib import Path
from datetime import datetime
from typing import Protocol

from sqlalchemy.orm import Session
from core.models import Dossier, Document

BASE_DIR = Path(__file__).resolve().parents[2]                       # c:\dev\aios
SECIB_EXPORT_DIR = Path(os.getenv("SECIB_EXPORT_DIR", BASE_DIR / "secib_import"))

# Colonnes de l'index — À MAPPER sur le modèle d'import du SECIB du cabinet.
_INDEX_COLONNES = [
    "numero", "reference_dossier", "client", "type_dossier",
    "piece_nom", "type_doc", "statut", "fichier", "date_reception",
]


def _slug(texte: str, defaut: str = "dossier") -> str:
    """Nom de fichier/dossier sûr (sans accents problématiques ni séparateurs)."""
    t = (texte or "").strip().lower()
    t = (t.replace("é", "e").replace("è", "e").replace("ê", "e").replace("à", "a")
           .replace("â", "a").replace("ô", "o").replace("î", "i").replace("ï", "i")
           .replace("ç", "c").replace("ù", "u").replace("û", "u"))
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    return t[:60] or defaut


class ResultatPush(dict):
    """Résultat d'un versement (dict enrichi : statut, mode, chemin, pièces)."""


class SecibConnector(Protocol):
    def pousser_dossier(self, dossier: Dossier, db: Session) -> ResultatPush: ...


# ── Implémentation MVP : paquet de transfert ──────────────────────────
class SecibExportPackage:
    mode = "export_package"

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = Path(base_dir or SECIB_EXPORT_DIR)

    def pousser_dossier(self, dossier: Dossier, db: Session) -> ResultatPush:
        ref = dossier.reference or dossier.id
        dest = self.base_dir / _slug(ref)

        # Idempotence : on reconstruit le paquet à neuf (pas de doublons résiduels).
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True, exist_ok=True)

        # Pièces réellement disponibles sur le disque.
        docs = (
            db.query(Document)
            .filter(Document.dossier_id == dossier.id, Document.chemin_stockage.isnot(None))
            .order_by(Document.created_at.asc())
            .all()
        )

        client = dossier.client
        client_nom = " ".join(filter(None, [
            getattr(client, "prenom", None), getattr(client, "nom", None)
        ])).strip() if client else ""
        type_dossier = str((dossier.metadonnees or {}).get("type_dossier", "")) or \
            getattr(dossier.specialite, "value", dossier.specialite) or ""

        lignes: list[dict] = []
        pieces: list[dict] = []
        numero = 0
        for d in docs:
            src = Path(d.chemin_stockage)
            if not src.is_file():
                continue
            numero += 1
            ext = src.suffix or ".pdf"
            nom_fichier = f"{numero:02d}_{_slug(d.nom, 'piece')}{ext}"
            try:
                shutil.copy2(src, dest / nom_fichier)
            except Exception as e:
                print(f"[SECIB] copie échouée ({src}) : {e}")
                continue
            lignes.append({
                "numero": numero,
                "reference_dossier": ref,
                "client": client_nom,
                "type_dossier": type_dossier,
                "piece_nom": d.nom,
                "type_doc": d.type_doc or "",
                "statut": getattr(d.statut, "value", d.statut) or "",
                "fichier": nom_fichier,
                "date_reception": d.recu_at.date().isoformat() if d.recu_at else "",
            })
            pieces.append({"nom": d.nom, "fichier": nom_fichier})

        # Index CSV (UTF-8 BOM → Excel/SECIB lisent correctement les accents).
        index_path = dest / "_index.csv"
        with open(index_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=_INDEX_COLONNES, delimiter=";")
            w.writeheader()
            w.writerows(lignes)

        resultat = ResultatPush({
            "statut": "OK" if pieces else "VIDE",
            "mode": self.mode,
            "chemin_paquet": str(dest),
            "index": str(index_path),
            "nb_pieces": len(pieces),
            "pieces": pieces,
            "push_at": datetime.utcnow().isoformat(),
            "note": ("Paquet prêt. Glissez le dossier dans SECIB (ou via robot RPA). "
                     "Pour un versement par API, voir docs/CDC_cession_officine.md §3.1."),
        })
        _journaliser(dossier, resultat, db)
        return resultat


def _journaliser(dossier: Dossier, resultat: ResultatPush, db: Session) -> None:
    """Trace le versement dans dossier.metadonnees['secib'] (zéro DDL)."""
    meta = dict(dossier.metadonnees or {})
    meta["secib"] = {
        "mode": resultat["mode"], "statut": resultat["statut"],
        "nb_pieces": resultat["nb_pieces"], "chemin_paquet": resultat["chemin_paquet"],
        "push_at": resultat["push_at"],
    }
    dossier.metadonnees = meta
    db.commit()


def get_connector() -> SecibConnector:
    """
    Sélectionne l'implémentation selon SECIB_MODE (.env).
      export_package (défaut) → paquet de transfert local (aucune API requise).
      api / rpa               → à implémenter une fois Septeo confirmé (cf. CDC §3.2).
    """
    mode = os.getenv("SECIB_MODE", "export_package").lower()
    if mode == "export_package":
        return SecibExportPackage()
    # Placeholders explicites tant que l'intégration n'est pas tranchée (décision §12 du CDC).
    raise NotImplementedError(
        f"SECIB_MODE='{mode}' non implémenté. Le MVP fournit 'export_package'. "
        "Confirmer l'API Septeo (CDC §3.1) avant d'ajouter SecibApiConnector/SecibRpaConnector."
    )
