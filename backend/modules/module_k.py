"""
AIOS — Module K : Veille réglementaire pharmaceutique.

Pipeline : récupération -> filtrage par mots-clés (déterministe) -> classement
d'impact (déterministe) -> résumé/portée (LLM Groq/Ollama, anti-hallucination) -> persistance.

Sources :
- "sample" : jeu d'items représentatifs intégré (démo hors-ligne, déterministe).
- "rss"    : flux RSS/Atom KEYLESS configurés via VEILLE_RSS_URLS (parsés en stdlib, sans feedparser).
- DILA/Légifrance (api.piste.gouv.fr) : nécessite un compte PISTE + OAuth (gratuit mais
  avec inscription). Adaptateur volontairement OPTIONNEL — activé seulement si PISTE_* est défini.
"""
import os
import re
import uuid
import httpx
import xml.etree.ElementTree as ET
from sqlalchemy.orm import Session

from core.orchestrateur import llm_chat


# ── Référentiels déterministes ────────────────────────────────────────

MOTS_CLES_PHARMA = [
    "officine", "pharmacie", "pharmacien", "ars", "csp", "l5125", "l. 5125",
    "plfss", "cpam", "cnam", "remboursement", "monopole pharmaceutique",
    "ordre des pharmaciens", "grossiste répartiteur", "médicament", "dispensation",
    "convention pharmaceutique", "ségur", "selarl", "selas",
]

# Impact CRITIQUE (cœur officine/cession), ELEVE (remboursement/conventionnel), sinon MOYEN.
_CRITIQUE = ("csp", "l5125", "l. 5125", "officine", "plfss", "monopole", "selarl", "selas")
_ELEVE = ("cpam", "cnam", "remboursement", "convention pharmaceutique", "ségur")

# Plus AUCUN corpus de démonstration : la veille n'utilise que des FLUX RÉELS.
# Flux RSS/Atom officiels et publics utilisés par défaut (si VEILLE_RSS_URLS non défini).
# Vérifiés valides ; le contenu est ensuite filtré sur pharma + sujets de vos dossiers.
DEFAULT_RSS_URLS = [
    "https://www.village-justice.com/articles/spip.php?page=backend",  # actualité juridique (RSS 2.0)
    "https://www.senat.fr/rss/textes.xml",                              # textes législatifs Sénat (Atom)
]


def _texte_item(item: dict) -> str:
    return f"{item.get('titre', '')} {item.get('contenu', '')}".lower()


_STOP_DOSSIER = {
    "dossier", "client", "affaire", "monsieur", "madame", "pharmacie", "pharmacien",
    "officine", "cession", "litige", "contrat", "projet", "demande", "lyon", "paris",
    "nouveau", "autre", "avec", "pour", "dans", "des", "les", "une", "sur",
}


def _mots_cles_dossiers(db: Session, limite: int = 40) -> list[str]:
    """Mots-clés dérivés des dossiers ACTIFS (titre/spécialité/contexte) pour cibler la veille."""
    from core.models import Dossier, DossierStatus
    mots: set[str] = set()
    try:
        rows = db.query(Dossier).filter(Dossier.status != DossierStatus.ARCHIVE).limit(100).all()
    except Exception:
        return []
    for d in rows:
        meta = d.metadonnees or {}
        blob = " ".join(filter(None, [
            d.titre or "", str(getattr(d.specialite, "value", d.specialite) or ""),
            str(meta.get("contexte", "")), str(meta.get("type_operation", "")),
        ])).lower()
        for w in re.split(r"[^0-9a-zàâäéèêëïîôöùûüç]+", blob):
            if len(w) >= 4 and w not in _STOP_DOSSIER:
                mots.add(w)
    return list(mots)[:limite]


def filtrer_pertinence(items: list[dict], mots_cles_extra: list[str] | None = None) -> list[dict]:
    """
    Ne garde que les items pertinents : mot-clé pharma OU mot-clé issu des dossiers actifs.
    Les items liés aux dossiers sont marqués `lie_dossiers=True`. Déterministe.
    """
    extra = [m.lower() for m in (mots_cles_extra or [])]
    resultat = []
    for it in items:
        texte = _texte_item(it)
        pharma = [m for m in MOTS_CLES_PHARMA if m in texte]
        dossier_kw = [m for m in extra if m in texte]
        if pharma or dossier_kw:
            enrichi = dict(it)
            enrichi["mots_cles"] = (pharma + dossier_kw)[:8]
            enrichi["lie_dossiers"] = bool(dossier_kw)
            resultat.append(enrichi)
    return resultat


def classer_impact(item: dict) -> str:
    """Impact déterministe (jamais le LLM)."""
    texte = _texte_item(item)
    if any(k in texte for k in _CRITIQUE):
        return "CRITIQUE"
    if any(k in texte for k in _ELEVE):
        return "ELEVE"
    return "MOYEN"


# ── Récupération RSS/Atom (stdlib, sans feedparser) ───────────────────

def _parse_rss(xml_text: str) -> list[dict]:
    """Parse minimal RSS 2.0 et Atom via xml.etree. Résilient (retourne [] si invalide)."""
    items: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return items
    for el in root.iter():
        tag = el.tag.lower().split("}")[-1]
        if tag == "item":  # RSS 2.0
            d = {c.tag.lower().split("}")[-1]: (c.text or "") for c in el}
            items.append({
                "titre": d.get("title", ""), "url": (d.get("link") or d.get("guid") or ""),
                "date_publication": d.get("pubdate", ""), "contenu": d.get("description", ""),
            })
        elif tag == "entry":  # Atom
            d, link = {}, ""
            for c in el:
                ct = c.tag.lower().split("}")[-1]
                if ct == "link":
                    link = c.attrib.get("href", link)
                else:
                    d[ct] = c.text or ""
            items.append({
                "titre": d.get("title", ""), "url": (link or d.get("id") or ""),
                "date_publication": d.get("updated", ""), "contenu": d.get("summary", ""),
            })
    return items


async def _fetch_rss(url: str) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "AIOS-Veille/1.0"})
            if r.status_code == 200:
                items = _parse_rss(r.text)
                for it in items:
                    it.setdefault("source", "RSS")
                return items
    except Exception as e:
        print(f"[veille] flux injoignable {url} : {e}")
    return []


def _sources_rss() -> list[str]:
    """Flux configurés (VEILLE_RSS_URLS) sinon flux réels par défaut."""
    env = os.getenv("VEILLE_RSS_URLS", "")
    configures = [u.strip() for u in env.split(",") if u.strip()]
    return configures or DEFAULT_RSS_URLS


async def resumer_item(item: dict) -> str:
    """Résumé d'impact via le LLM (Groq/Ollama). Anti-hallucination."""
    try:
        return await llm_chat(
            "Résume en 1 à 2 phrases l'impact, pour un cabinet d'avocats spécialisé pharmacie, "
            "du texte réglementaire suivant. N'invente RIEN (aucun numéro d'article non fourni). "
            "Commence par [VERIFICATION REQUISE PAR L'AVOCAT].\n\n"
            f"Titre : {item.get('titre', '')}\nContenu : {item.get('contenu', '')[:1500]}",
            system="Tu es juriste. Factuel, concis, jamais d'invention.",
            max_tokens=200,
        )
    except Exception:
        return f"[VERIFICATION REQUISE PAR L'AVOCAT] {item.get('titre', '')}"


async def scanner_veille(source: str = "rss", db: Session | None = None, resumer: bool = True) -> list[dict]:
    """
    Récupère (FLUX RÉELS uniquement), filtre (pharma + DOSSIERS ACTIFS), classe, résume, persiste.
    Aucune donnée de démonstration : si aucun flux n'est joignable, renvoie une liste vide.
    """
    items: list[dict] = []
    for url in _sources_rss():
        items += await _fetch_rss(url)

    # Veille CONTEXTUELLE : on cible les sujets des dossiers actifs du cabinet.
    mots_dossiers = _mots_cles_dossiers(db) if db is not None else []
    pertinents = filtrer_pertinence(items, mots_dossiers)
    alertes: list[dict] = []
    for it in pertinents:
        data = {
            "titre": it.get("titre", ""),
            "source": it.get("source", "RSS"),
            "url": it.get("url", ""),
            "date_publication": it.get("date_publication", ""),
            "impact": classer_impact(it),
            "resume": (await resumer_item(it)) if resumer else it.get("contenu", "")[:200],
            "mots_cles": it.get("mots_cles", []),
        }
        if db is not None:
            from core.models import VeilleAlerte
            existe = db.query(VeilleAlerte).filter(
                VeilleAlerte.titre == data["titre"], VeilleAlerte.url == data["url"]
            ).first()
            if not existe:
                db.add(VeilleAlerte(id=str(uuid.uuid4()), lu=False, **data))
                db.flush()
        alertes.append({**data, "source_url": data.get("url", ""), "lie_dossiers": it.get("lie_dossiers", False)})

    if db is not None:
        db.commit()

    ordre = {"CRITIQUE": 0, "ELEVE": 1, "MOYEN": 2}
    # Priorité aux alertes liées aux dossiers actifs, puis par impact.
    alertes.sort(key=lambda a: (not a.get("lie_dossiers"), ordre.get(a["impact"], 3)))
    return alertes
