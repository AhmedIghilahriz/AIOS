"""
AIOS — Service d'Extraction Unique (centralisé).

UN seul point d'entrée pour extraire le texte brut + métadonnées de tout document,
quel que soit le module appelant (Pharmacie, Qualification, Fiche, Réponses…).
Objectif : zéro duplication + traitement uniforme (RGPD).

Traitement, 100 % sur le backend (aucune donnée envoyée à un tiers par défaut) :
  • PDF avec couche texte   → PyMuPDF (fitz)              [pip install pymupdf]
  • PDF scanné / image      → Tesseract OCR (déjà présent)
  • TXT / MD / CSV          → lecture directe

Dégradation propre : si PyMuPDF n'est pas installé, l'extraction PDF renvoie un
texte vide + une métadonnée explicite (le reste continue de fonctionner).

RGPD : point unique pour appliquer minimisation, journalisation, rétention et droit
à l'effacement (le texte extrait est stocké dans Document.ocr_contenu, même sécurité
que le reste de la base). Aucune OCR cloud par défaut.
"""
from __future__ import annotations
import os
from pathlib import Path

try:
    import fitz  # PyMuPDF
    _HAS_FITZ = True
except Exception:
    _HAS_FITZ = False

try:
    import pytesseract
    from PIL import Image
    _HAS_TESSERACT = True
except Exception:
    _HAS_TESSERACT = False

OCR_LANG = os.getenv("OCR_LANG", "fra")
_SEUIL_TEXTE_PAGE = 20  # en-dessous de N caractères, on considère la page scannée → OCR

_EXT_IMAGE = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}
_EXT_TEXTE = {".txt", ".md", ".csv", ".log"}


def _ocr_pil(img) -> str:
    if not _HAS_TESSERACT:
        return ""
    try:
        return pytesseract.image_to_string(img, lang=OCR_LANG).strip()
    except Exception:
        return ""


def _extraire_pdf(chemin: str) -> tuple[str, dict]:
    if not _HAS_FITZ:
        return "", {"type": "pdf", "pages": 0, "ocr_utilise": False,
                    "avertissement": "PyMuPDF non installé — exécuter : pip install pymupdf"}
    textes: list[str] = []
    pages_ocr = 0
    with fitz.open(chemin) as doc:
        nb_pages = doc.page_count
        for page in doc:
            t = page.get_text("text").strip()
            if len(t) < _SEUIL_TEXTE_PAGE and _HAS_TESSERACT:
                # Page sans couche texte (scan) → rendu image + OCR
                pix = page.get_pixmap(dpi=200)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                t = _ocr_pil(img)
                if t:
                    pages_ocr += 1
            textes.append(t)
    texte = "\n\n".join(filter(None, textes)).strip()
    return texte, {"type": "pdf", "pages": nb_pages, "ocr_utilise": pages_ocr > 0, "pages_ocr": pages_ocr}


def _extraire_image(chemin: str) -> tuple[str, dict]:
    if not _HAS_TESSERACT:
        return "", {"type": "image", "ocr_utilise": False, "avertissement": "Tesseract/Pillow indisponible"}
    try:
        texte = _ocr_pil(Image.open(chemin))
        return texte, {"type": "image", "ocr_utilise": True}
    except Exception as e:
        return "", {"type": "image", "ocr_utilise": False, "erreur": str(e)[:120]}


def _extraire_texte_brut(chemin: str) -> tuple[str, dict]:
    try:
        return Path(chemin).read_text(encoding="utf-8", errors="ignore").strip(), {"type": "texte", "ocr_utilise": False}
    except Exception as e:
        return "", {"type": "texte", "ocr_utilise": False, "erreur": str(e)[:120]}


def extraire_document(chemin: str, filename: str | None = None, mime: str | None = None) -> dict:
    """
    POINT D'ENTRÉE UNIQUE. Renvoie {"texte": str, "metadonnees": {...}}.
    `chemin` : chemin local du fichier. `filename`/`mime` : indices de type (optionnels).
    """
    nom = (filename or os.path.basename(str(chemin))).lower()
    ext = os.path.splitext(nom)[1]
    mime = (mime or "").lower()

    if ext == ".pdf" or "pdf" in mime:
        texte, meta = _extraire_pdf(chemin)
    elif ext in _EXT_IMAGE or mime.startswith("image/"):
        texte, meta = _extraire_image(chemin)
    elif ext in _EXT_TEXTE or mime.startswith("text/"):
        texte, meta = _extraire_texte_brut(chemin)
    else:
        # Best-effort : tenter image (scan) puis texte brut
        texte, meta = _extraire_image(chemin)
        if not texte:
            texte, meta = _extraire_texte_brut(chemin)

    meta["nb_caracteres"] = len(texte)
    meta["fichier"] = os.path.basename(nom)
    meta["moteurs"] = {"pymupdf": _HAS_FITZ, "tesseract": _HAS_TESSERACT}
    return {"texte": texte, "metadonnees": meta}


def extraire_pages(chemin: str, filename: str | None = None, mime: str | None = None) -> list[tuple[int, str]]:
    """
    Comme `extraire_document` mais renvoie le texte PAR PAGE : [(page_1indexée, texte), ...].
    Sert au RAG (Module L) pour produire des citations « pièce + page ». PDF → une entrée par
    page (OCR si page scannée). Image / texte → une seule page n°1. Dégradation propre.
    """
    nom = (filename or os.path.basename(str(chemin))).lower()
    ext = os.path.splitext(nom)[1]
    mime = (mime or "").lower()

    if ext == ".pdf" or "pdf" in mime:
        if not _HAS_FITZ:
            return []
        pages: list[tuple[int, str]] = []
        try:
            with fitz.open(chemin) as doc:
                for i, page in enumerate(doc, start=1):
                    t = page.get_text("text").strip()
                    if len(t) < _SEUIL_TEXTE_PAGE and _HAS_TESSERACT:
                        pix = page.get_pixmap(dpi=200)
                        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                        t = _ocr_pil(img)
                    if t:
                        pages.append((i, t))
        except Exception as e:
            print(f"[extraction] pages PDF indisponibles : {e}")
        return pages

    # Image ou texte : une seule « page ».
    res = extraire_document(chemin, filename, mime)
    texte = (res.get("texte") or "").strip()
    return [(1, texte)] if texte else []


def moteurs_disponibles() -> dict:
    """État des moteurs (diagnostic / health)."""
    return {"pymupdf": _HAS_FITZ, "tesseract": _HAS_TESSERACT, "ocr_lang": OCR_LANG}


def contexte_documents(db, dossier_id: str, max_chars: int = 4000) -> str:
    """
    Agrège le texte déjà extrait des pièces d'un dossier (Document.ocr_contenu),
    pour alimenter Qualification / Fiche / Réponses. Tronqué à `max_chars`.
    """
    from core.models import Document
    docs = (
        db.query(Document)
        .filter(Document.dossier_id == dossier_id, Document.ocr_contenu.isnot(None))
        .all()
    )
    morceaux = []
    for d in docs:
        t = (d.ocr_contenu or "").strip()
        if t:
            morceaux.append(f"[{d.nom}]\n{t}")
    return "\n\n".join(morceaux).strip()[:max_chars]
