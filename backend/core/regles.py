"""
AIOS — Résolution des « recettes juridiques » configurables.

Chaque module (I = procédure, J = contentieux pharmacien) définit ses valeurs
PAR DÉFAUT en tête de son fichier (constantes éditables). Cette couche permet de
SURCHARGER ces valeurs par cabinet puis par avocat, depuis la base de données,
sans rien figer dans le code.

Résolution : defaults (code) ← override cabinet ← override avocat.

Robuste : si la table `regles_juridiques` n'existe pas encore (migration non
appliquée), on retombe silencieusement sur les valeurs par défaut. Le comportement
actuel est donc strictement préservé tant qu'aucune surcharge n'est enregistrée.
"""
import copy
from datetime import datetime
from sqlalchemy.orm import Session


def _defaults(module: str) -> dict:
    """Valeurs par défaut, lues depuis les constantes des modules (import paresseux
    pour éviter tout import circulaire)."""
    m = (module or "").upper()
    if m == "I":
        from modules import module_i as mod
        return {
            "etapes_procedure": list(mod.ETAPES_PROCEDURE),
            "recours_par_juridiction": copy.deepcopy(mod.RECOURS_PAR_JURIDICTION),
        }
    if m == "J":
        from modules import module_j as mod
        return {
            "criteres_base": copy.deepcopy(mod.CRITERES_BASE),
            "critere_contrepartie": copy.deepcopy(mod.CRITERE_CONTREPARTIE),
            "delais_recours": copy.deepcopy(mod.DELAIS_RECOURS),
        }
    return {}


def _merge(base: dict, override: dict) -> dict:
    """Fusion profonde : les dicts se fusionnent récursivement ; listes et scalaires
    REMPLACENT. Permet de surcharger une seule clé (ex. delais_recours.RECOURS_ARS)."""
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _lire_override(db: Session, module: str, scope: str, scope_id: str | None) -> dict:
    """Override stocké pour (scope, scope_id, module), ou {} si absent / table absente."""
    if not scope_id:
        return {}
    try:
        from core.models import RegleJuridique
        row = (
            db.query(RegleJuridique)
            .filter(RegleJuridique.scope == scope,
                    RegleJuridique.scope_id == scope_id,
                    RegleJuridique.module == module.upper())
            .first()
        )
        return (row.payload or {}) if row else {}
    except Exception:
        # Table non migrée ou erreur de lecture : on neutralise la transaction et
        # on retombe sur les defaults.
        try:
            db.rollback()
        except Exception:
            pass
        return {}


def resoudre_regles(db: Session, module: str, cabinet_id: str | None = None,
                    avocat_id: str | None = None) -> dict:
    """Règles EFFECTIVES : defaults ← cabinet ← avocat. Lecture seule."""
    eff = _defaults(module)
    eff = _merge(eff, _lire_override(db, module, "cabinet", cabinet_id))
    eff = _merge(eff, _lire_override(db, module, "avocat", avocat_id))
    return eff


# ── API de paramétrage (pour la future interface) ─────────────────────

def lire_regles(db: Session, module: str, scope: str | None = None, scope_id: str | None = None) -> dict:
    """Renvoie defaults + override + valeur effective pour un scope donné
    (ou seulement les defaults si aucun scope)."""
    defaults = _defaults(module)
    if not scope or not scope_id:
        return {"module": module.upper(), "defaults": defaults, "override": {}, "effectif": defaults}
    override = _lire_override(db, module, scope, scope_id)
    return {
        "module": module.upper(), "scope": scope, "scope_id": scope_id,
        "defaults": defaults, "override": override, "effectif": _merge(defaults, override),
    }


def enregistrer_regles(db: Session, module: str, scope: str, scope_id: str, payload: dict) -> dict:
    """Crée ou met à jour l'override (upsert) pour (scope, scope_id, module)."""
    from core.models import RegleJuridique
    m = module.upper()
    row = (
        db.query(RegleJuridique)
        .filter(RegleJuridique.scope == scope, RegleJuridique.scope_id == scope_id, RegleJuridique.module == m)
        .first()
    )
    if row:
        row.payload = payload or {}
        row.updated_at = datetime.utcnow()
    else:
        db.add(RegleJuridique(scope=scope, scope_id=scope_id, module=m, payload=payload or {}))
    db.commit()
    return lire_regles(db, m, scope, scope_id)
