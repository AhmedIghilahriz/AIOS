"""
AIOS — Module L : génération de la PROMESSE (Lot 3) puis de l'ACTE (Lot 5/S3).

Méthode conforme à CLAUDE.md §2 :
  • GABARIT DÉTERMINISTE : le squelette juridique et l'insertion des paramètres de la
    `FicheCession` (parties, prix, conditions suspensives) sont 100 % code — pas de LLM.
  • EXPOSÉ NARRATIF : seul le « rappel du contexte » est rédigé par le LLM (borné),
    préfixé [VERIFICATION REQUISE PAR L'AVOCAT].
  • Document de TRAVAIL : versionné comme la fiche de préparation (Module D), jamais
    signé/envoyé sans validation de l'avocat.

Le rendu imprimable réutilise `module_d.markdown_vers_html` (zéro dépendance).
"""
from __future__ import annotations
import html
from datetime import datetime
from sqlalchemy.orm import Session

from core.models import Dossier, ActeCession, Cabinet
from core.cession_schema import FicheCession
from modules.module_l_cession import charger_fiche, CHAMPS_CRITIQUES, etat_conditions


# ── Mise en forme déterministe ────────────────────────────────────────

def _eur(n: float | None) -> str:
    return f"{n:,.0f} €".replace(",", " ") if isinstance(n, (int, float)) else "________ €"


def _num(n) -> str:
    """Nombre lisible : entier sans « .0 », décimal conservé (ex. 5 → « 5 », 2.5 → « 2.5 »)."""
    if n is None:
        return "________"
    return str(int(n)) if float(n).is_integer() else str(n)


def _nom_partie(p) -> str:
    if p.type != "personne_physique" and p.denomination:
        base = f"la société **{p.denomination}** ({p.type})"
        if p.rcs_siren:
            base += f", immatriculée sous le n° SIREN {p.rcs_siren}"
        return base
    nom = " ".join(filter(None, [p.prenom, p.nom])) or "________"
    suffixe = f" (Ordre des pharmaciens : {p.inscription_ordre})" if p.inscription_ordre else ""
    return f"**{nom}**{suffixe}"


def _bloc_parties(parties: list, label: str) -> str:
    if not parties:
        return f"- {label} : ________\n"
    return "".join(f"- {label} : {_nom_partie(p)}\n" for p in parties)


def _libelle_titre(fiche: FicheCession) -> str:
    avant = {
        "promesse_synallagmatique": "PROMESSE SYNALLAGMATIQUE",
        "promesse_unilaterale_vente": "PROMESSE UNILATÉRALE DE VENTE",
        "promesse_unilaterale_achat": "PROMESSE UNILATÉRALE D'ACHAT",
    }.get(fiche.avant_contrat, "PROMESSE")
    objet = {
        "cession_fonds": "DE CESSION DE FONDS DE COMMERCE D'OFFICINE DE PHARMACIE",
        "cession_parts": "DE CESSION DE PARTS SOCIALES DE SEL (OFFICINE)",
        "cession_titres": "DE CESSION D'ACTIONS DE SEL (OFFICINE)",
    }.get(fiche.type_operation, "DE CESSION")
    return f"{avant} {objet}"


# ── Contrôle des champs critiques (EF-3.2) ────────────────────────────

def champs_critiques_manquants(fiche: FicheCession) -> list[str]:
    """Renvoie les champs critiques absents qui BLOQUENT la génération."""
    manquants = []
    if fiche.type_operation == "inconnu":
        manquants.append("type_operation")
    if not fiche.cedants:
        manquants.append("cedants")
    if not fiche.cessionnaires:
        manquants.append("cessionnaires")
    if fiche.prix.montant_global is None:
        manquants.append("prix.montant_global")
    return manquants


# ── Squelette déterministe de la promesse ─────────────────────────────

def _designation_objet(fiche: FicheCession) -> str:
    off = fiche.officine
    if fiche.type_operation == "cession_fonds":
        return (
            f"Le fonds de commerce d'**officine de pharmacie** exploité « {off.nom or '________'} », "
            f"sis {off.adresse or '________'}, identifié sous le n° FINESS {off.finess or '________'} "
            f"(licence/autorisation ARS : {off.licence_ars or '________'}), comprenant la clientèle, "
            f"le droit au bail, le nom commercial, le matériel et le mobilier, ainsi que les marchandises en stock."
        )
    return (
        f"Les droits sociaux (parts/actions) de la société d'exercice libéral exploitant l'officine "
        f"« {off.nom or '________'} », sise {off.adresse or '________'} "
        f"(n° FINESS {off.finess or '________'}). Nombre et répartition des titres : ________ "
        f"_(à compléter)_."
    )


def _ventilation(fiche: FicheCession) -> str:
    prix = fiche.prix
    if fiche.type_operation == "cession_fonds":
        return (
            f"- Éléments incorporels (clientèle, droit au bail, licence) : {_eur(prix.part_incorporel)}\n"
            f"- Matériel et mobilier : {_eur(prix.part_materiel)}\n"
            f"- Marchandises en stock (inventaire contradictoire) : {_eur(prix.part_stock)}\n"
        )
    return (
        f"- Prix des droits sociaux : {_eur(prix.montant_global)}\n"
        f"- (Le cas échéant) compte courant d'associé : ________ €\n"
    )


def construire_promesse_markdown(fiche: FicheCession, dossier: Dossier, expose: str) -> str:
    off = fiche.officine
    prix = fiche.prix
    est_fonds = fiche.type_operation == "cession_fonds"
    designation = _designation_objet(fiche)
    ventilation = _ventilation(fiche)

    # Conditions suspensives applicables uniquement.
    cs = [c for c in fiche.conditions_suspensives if c.applicable]
    lignes_cs = "".join(
        f"{i}. {c.libelle}"
        + (f" — date butoir : {c.date_butoir}" if c.date_butoir else "")
        + (f" _(statut : {c.statut})_" if c.statut != "EN_ATTENTE" else "")
        + "\n"
        for i, c in enumerate(cs, 1)
    ) or "_(aucune condition suspensive sélectionnée — à compléter par l'avocat)_\n"

    clause_specifique = (
        "## Article 6 — Déclarations du cédant\n"
        "Le cédant déclare la sincérité du chiffre d'affaires et des marges communiqués, l'absence "
        "de litige ou de passif non révélé, et la régularité de l'exploitation au regard du Code de la "
        "santé publique. [VERIFICATION REQUISE PAR L'AVOCAT]\n\n"
        "## Article 7 — Sort des contrats de travail\n"
        "Les contrats de travail en cours sont transférés de plein droit (art. L.1224-1 C. trav.). "
        "L'information préalable des salariés (loi Hamon) sera assurée dans les délais légaux. "
        "[VERIFICATION REQUISE PAR L'AVOCAT]\n\n"
    ) if est_fonds else (
        "## Article 6 — Agrément et garantie\n"
        "La cession des droits sociaux est soumise à l'agrément des coassociés prévu par les statuts "
        "de la SEL. Une garantie d'actif et de passif sera consentie par le cédant (durée, plafond et "
        "franchise à arrêter). [VERIFICATION REQUISE PAR L'AVOCAT]\n\n"
    )

    date_reit = fiche.date_jouissance_prevue or "________"

    return f"""# {_libelle_titre(fiche)}

**Document de travail** établi par le cabinet à partir des éléments du dossier {dossier.reference or ''}.
**[VERIFICATION REQUISE PAR L'AVOCAT]** — à relire, compléter et adapter avant toute signature.

## Entre les soussignés
{_bloc_parties(fiche.cedants, "Cédant")}{_bloc_parties(fiche.cessionnaires, "Cessionnaire")}

## Exposé préalable
{expose.strip()}

## Article 1 — Objet de la cession
{designation}

## Article 2 — Prix de cession
Le prix de cession est fixé à **{_eur(prix.montant_global)}**, ventilé comme suit :

{ventilation}
Modalités de paiement et séquestre (CARPA / notaire) : ________ _(à compléter)_.

## Article 3 — Conditions suspensives
La présente promesse est conclue sous les conditions suspensives suivantes, devant être réalisées
au plus tard à la date de réitération :

{lignes_cs}
## Article 4 — Réitération
La réitération par acte définitif interviendra une fois toutes les conditions suspensives levées,
au plus tard le **{date_reit}**, sous réserve d'absence de changement substantiel.

## Article 5 — Faculté de rétractation
Le cas échéant, délai de rétractation applicable conformément aux dispositions en vigueur.
[VERIFICATION REQUISE PAR L'AVOCAT]

{clause_specifique}## Article 8 — Formalités
Enregistrement, publicité légale (le cas échéant), déclaration ARS du changement de titulaire et
formalités auprès de l'Ordre seront accomplis selon le calendrier légal.
[VERIFICATION REQUISE PAR L'AVOCAT]

---
*Fait pour servir de projet. Établi le {datetime.utcnow().strftime('%d/%m/%Y')}. Toute appréciation
juridique doit être vérifiée par l'avocat avant signature.*
"""


# ── Formalités post-acte (déterministe, cf. CDC §4.4-D) ───────────────
# `jours` = délai après l'entrée en jouissance (None = pas de deadline, item informatif).
FORMALITES_POST_ACTE: dict[str, list[dict]] = {
    "_commun": [
        {"code": "ENREGISTREMENT", "libelle": "Enregistrement de l'acte au service des impôts", "jours": 30, "base": "CGI"},
        {"code": "DROITS_ENREGISTREMENT",
         "libelle": "Acquitter les droits d'enregistrement (fonds : barème ; parts SARL/SELARL ≈ 3 % ; actions SELAS ≈ 0,1 %)",
         "jours": None, "base": "CGI — [VERIFICATION REQUISE PAR L'AVOCAT]"},
        {"code": "DECLARATION_ARS_TITULAIRE", "libelle": "Déclaration du changement de titulaire à l'ARS", "jours": 7, "base": "CSP"},
        {"code": "FORMALITES_ORDRE", "libelle": "Inscription / radiation auprès de l'Ordre des pharmaciens", "jours": 7, "base": "CSP"},
    ],
    "cession_fonds": [
        {"code": "PUBLICITE_JAL_BODACC", "libelle": "Publicité légale : journal d'annonces légales + BODACC", "jours": 15, "base": "C. com."},
        {"code": "OPPOSITION_CREANCIERS", "libelle": "Fin du délai d'opposition des créanciers (prix sous séquestre)", "jours": 10, "base": "C. com."},
        {"code": "INFO_SALARIES_HAMON", "libelle": "Information préalable des salariés (loi Hamon) — À RÉALISER 2 MOIS AVANT la cession", "jours": None, "base": "art. L.23-10-1 C. com."},
    ],
    "cession_parts": [
        {"code": "MODIF_STATUTS_GREFFE", "libelle": "Mise à jour des statuts + dépôt au greffe", "jours": 30, "base": "C. com."},
        {"code": "REGISTRE_TITRES", "libelle": "Inscription au registre des mouvements de titres / comptes d'associés", "jours": 7, "base": "C. com."},
    ],
}
FORMALITES_POST_ACTE["cession_titres"] = FORMALITES_POST_ACTE["cession_parts"]


def formalites_post_acte(type_op: str) -> list[dict]:
    """Checklist déterministe des formalités post-acte pour un type d'opération."""
    return list(FORMALITES_POST_ACTE["_commun"]) + FORMALITES_POST_ACTE.get(type_op, [])


# ── Clauses spécifiques de l'acte définitif ───────────────────────────

def _titre_acte(fiche: FicheCession) -> str:
    return {
        "cession_fonds": "ACTE DE CESSION DE FONDS DE COMMERCE D'OFFICINE DE PHARMACIE",
        "cession_parts": "ACTE DE CESSION DE PARTS SOCIALES DE SEL (OFFICINE)",
        "cession_titres": "ACTE DE CESSION D'ACTIONS DE SEL (OFFICINE)",
    }.get(fiche.type_operation, "ACTE DE CESSION")


def _clause_gap(fiche: FicheCession) -> str:
    g = fiche.garantie_actif_passif
    if not g or (g.duree_mois is None and g.plafond is None and g.franchise is None):
        return ("Une garantie d'actif et de passif est consentie par le cédant : durée ________ mois, "
                "plafond ________ €, franchise ________ €. _(à compléter)_")
    base = (f"Une garantie d'actif et de passif est consentie par le cédant : "
            f"durée {g.duree_mois or '________'} mois, plafond {_eur(g.plafond)}, franchise {_eur(g.franchise)}")
    if g.sequestre_garantie:
        base += f", séquestre de garantie {_eur(g.sequestre_garantie)}"
    return base + "."


def _clause_nc(fiche: FicheCession) -> str:
    nc = fiche.non_concurrence
    if not nc or (nc.perimetre_km is None and nc.duree_mois is None):
        return ("Le cédant s'engage à une obligation de non-concurrence / non-rétablissement, limitée dans "
                "le temps et l'espace : périmètre ________ km, durée ________ mois. "
                "_(à compléter — la clause doit rester proportionnée)_")
    txt = (f"Le cédant s'engage à ne pas se rétablir : périmètre {_num(nc.perimetre_km)} km, "
           f"durée {_num(nc.duree_mois)} mois (clause devant rester proportionnée).")
    return txt + (f" {nc.note}" if nc.note else "")


def _clause_sequestre(fiche: FicheCession) -> str:
    s = fiche.sequestre
    label = {
        "carpa_avocat": "séquestre entre les mains de l'avocat (CARPA)",
        "notaire": "séquestre entre les mains du notaire",
        "autre": "séquestre", "inconnu": "séquestre (à désigner)",
    }.get((s.type if s else "inconnu"), "séquestre (à désigner)")
    coord = f" — {s.coordonnees}" if s and s.coordonnees else ""
    return (f"Le prix est consigné sous {label}{coord}, et libéré après accomplissement des formalités "
            "et purge des oppositions des créanciers.")


# ── Squelette déterministe de l'acte définitif ────────────────────────

def construire_acte_markdown(fiche: FicheCession, dossier: Dossier, expose: str, formalites: list[dict]) -> str:
    est_fonds = fiche.type_operation == "cession_fonds"
    prix = fiche.prix
    designation = _designation_objet(fiche)
    ventilation = _ventilation(fiche)
    date_jouissance = fiche.date_jouissance_prevue or "________"

    cs = [c for c in fiche.conditions_suspensives if c.applicable]
    rappel_cs = "".join(f"- {c.libelle} : **{c.statut}**\n" for c in cs) or "_(aucune condition suspensive)_\n"

    lignes_form = "".join(
        f"- {f['libelle']}"
        + (f" — sous {f['jours']} jours ({f['base']})" if f.get("jours") else f" ({f['base']})")
        + "\n"
        for f in formalites
    )

    clause_specifique = (
        "## Article 7 — Sort des contrats de travail\n"
        "Les contrats de travail en cours sont transférés de plein droit à l'acquéreur "
        "(art. L.1224-1 C. trav.). L'information préalable des salariés (loi Hamon) a été assurée. "
        "[VERIFICATION REQUISE PAR L'AVOCAT]\n\n"
    ) if est_fonds else (
        "## Article 7 — Agrément, statuts et comptes courants\n"
        "La cession des droits sociaux a reçu l'agrément des coassociés ; les statuts de la SEL et le "
        "registre des mouvements de titres seront mis à jour. Le sort des comptes courants d'associés "
        "est réglé comme suit : ________. [VERIFICATION REQUISE PAR L'AVOCAT]\n\n"
    )

    return f"""# {_titre_acte(fiche)}

**Document de travail** établi à partir des éléments du dossier {dossier.reference or ''}, **après levée
de toutes les conditions suspensives**. **[VERIFICATION REQUISE PAR L'AVOCAT]** avant signature.

## Entre les soussignés
{_bloc_parties(fiche.cedants, "Cédant")}{_bloc_parties(fiche.cessionnaires, "Cessionnaire")}

## Exposé préalable
{expose.strip()}

## Rappel des conditions suspensives (levées)
{rappel_cs}
## Article 1 — Objet de la cession
{designation}

## Article 2 — Prix de cession
Le prix de cession est fixé à **{_eur(prix.montant_global)}**, ventilé comme suit :

{ventilation}
Modalités de paiement : ________. {_clause_sequestre(fiche)}

## Article 3 — Entrée en jouissance et transfert de propriété
Le transfert de propriété et l'entrée en jouissance sont fixés au **{date_jouissance}**, date à laquelle
l'inventaire contradictoire du stock est arrêté.

## Article 4 — Déclarations du cédant
Le cédant déclare la sincérité du chiffre d'affaires et des marges, l'absence de litige ou de passif non
révélé, et la régularité de l'exploitation au regard du Code de la santé publique.
[VERIFICATION REQUISE PAR L'AVOCAT]

## Article 5 — Garantie d'actif et de passif
{_clause_gap(fiche)}

## Article 6 — Non-concurrence / non-rétablissement
{_clause_nc(fiche)}

{clause_specifique}## Article 8 — Formalités à accomplir
{lignes_form}
## Article 9 — Élection de domicile
Pour l'exécution des présentes, les parties élisent domicile en leurs demeures respectives.

---
*Projet établi le {datetime.utcnow().strftime('%d/%m/%Y')}. Toute appréciation juridique doit être
vérifiée par l'avocat avant signature.*
"""


# ── Génération LLM de l'exposé (borné) ────────────────────────────────

async def _generer_expose(fiche: FicheCession, dossier: Dossier) -> str:
    from core.orchestrateur import generer_texte_juridique
    try:
        texte = await generer_texte_juridique(
            "Rédige UNIQUEMENT l'« exposé préalable » d'une promesse de cession d'officine "
            "(contexte de l'opération, intention des parties), en 6 lignes maximum, sobre et factuel. "
            "N'invente aucun chiffre ni clause : appuie-toi seulement sur le contexte fourni. "
            "Préfixe toute appréciation juridique par [VERIFICATION REQUISE PAR L'AVOCAT].",
            {
                "type_operation": fiche.type_operation,
                "officine": fiche.officine.model_dump(),
                "prix_global": fiche.prix.montant_global,
                "dossier": dossier.titre,
            },
            specialite="affaires",
            max_tokens=400,
        )
        return texte.strip() or _expose_fallback()
    except Exception as e:
        print(f"[Module L] exposé LLM indisponible : {e}")
        return _expose_fallback()


def _expose_fallback() -> str:
    return ("Les parties se rapprochent en vue de la cession de l'officine désignée ci-après, "
            "aux conditions précisées dans la présente promesse. "
            "[VERIFICATION REQUISE PAR L'AVOCAT]")


# ── Persistance / versionnement (patron FichePreparation) ─────────────

def derniere_promesse(dossier_id: str, db: Session, type_: str = "promesse") -> ActeCession | None:
    return (
        db.query(ActeCession)
        .filter(ActeCession.dossier_id == dossier_id, ActeCession.type == type_)
        .order_by(ActeCession.version.desc())
        .first()
    )


def lister_actes(dossier_id: str, db: Session) -> list[ActeCession]:
    return (
        db.query(ActeCession)
        .filter(ActeCession.dossier_id == dossier_id)
        .order_by(ActeCession.created_at.desc())
        .all()
    )


async def generer_promesse(dossier: Dossier, db: Session) -> ActeCession:
    """
    Génère une nouvelle version de la promesse à partir de la Fiche de cession validée.
    Lève ValueError si la fiche manque ou si des champs critiques sont absents (EF-3.2).
    """
    fiche = charger_fiche(dossier)
    if not fiche:
        raise ValueError("Aucune fiche de cession : extraire et valider les paramètres d'abord.")
    manquants = champs_critiques_manquants(fiche)
    if manquants:
        raise ValueError(
            "Champs critiques manquants — complétez la fiche avant de générer la promesse : "
            + ", ".join(manquants)
        )

    expose = await _generer_expose(fiche, dossier)
    contenu = construire_promesse_markdown(fiche, dossier, expose)
    derniere = derniere_promesse(dossier.id, db, "promesse")
    acte = ActeCession(
        dossier_id=dossier.id,
        type="promesse",
        sous_type=fiche.type_operation,
        version=(derniere.version + 1) if derniere else 1,
        contenu=contenu,
        statut="BROUILLON",
    )
    db.add(acte)
    db.commit()
    db.refresh(acte)
    return acte


def _creer_deadlines_formalites(dossier: Dossier, fiche: FicheCession, db: Session,
                                formalites: list[dict]) -> list[dict]:
    """
    Crée/maj les deadlines des formalités datées (Module F) à partir de l'entrée en jouissance
    (ou de la date du jour à défaut). Idempotent (repérage par type_delai). 0 % LLM.
    """
    from datetime import datetime as _dt, timedelta
    from core.models import Deadline
    base_date = None
    if fiche.date_jouissance_prevue:
        try:
            base_date = _dt.fromisoformat(fiche.date_jouissance_prevue)
        except ValueError:
            base_date = None
    base_date = base_date or _dt.utcnow()

    creees: list[dict] = []
    for f in formalites:
        jours = f.get("jours")
        if not jours:
            continue
        type_delai = f"formalite_{f['code'].lower()}"
        echeance = base_date + timedelta(days=int(jours))
        titre = f"Formalité cession — {f['libelle']}"[:200]
        desc = f"{f['libelle']} (base : {f['base']}). [VERIFICATION REQUISE PAR L'AVOCAT]"
        existante = (
            db.query(Deadline)
            .filter(Deadline.dossier_id == dossier.id, Deadline.type_delai == type_delai)
            .first()
        )
        if existante:
            existante.date_echeance = echeance
            existante.titre = titre
            existante.acquitte = False
        else:
            db.add(Deadline(titre=titre, description=desc, date_echeance=echeance,
                            type_delai=type_delai, dossier_id=dossier.id))
        creees.append({"code": f["code"], "libelle": f["libelle"],
                       "echeance": echeance.date().isoformat(), "base": f["base"]})
    db.commit()
    return creees


async def generer_acte(dossier: Dossier, db: Session) -> tuple[ActeCession, list[dict]]:
    """
    Génère l'ACTE DÉFINITIF — VERROUILLÉ tant que toutes les conditions suspensives ne sont pas
    levées (EF-4.1). Crée aussi les deadlines des formalités post-acte (Module F).
    Lève ValueError si fiche absente, champ critique manquant, ou conditions non réunies.
    """
    fiche = charger_fiche(dossier)
    if not fiche:
        raise ValueError("Aucune fiche de cession : extraire et valider les paramètres d'abord.")
    manquants = champs_critiques_manquants(fiche)
    if manquants:
        raise ValueError("Champs critiques manquants : " + ", ".join(manquants))

    etat = etat_conditions(fiche)
    if not etat["pretes_pour_acte"]:
        details = []
        if not etat["nb_applicables"]:
            details.append("aucune condition suspensive applicable n'est définie")
        if etat["nb_restantes"]:
            details.append("non levées : " + ", ".join(etat["restantes"]))
        if etat["nb_defaillies"]:
            details.append("défaillies : " + ", ".join(etat["defaillies"]))
        raise ValueError(
            "Acte verrouillé — toutes les conditions suspensives doivent être levées. "
            + " ; ".join(details)
        )

    formalites = formalites_post_acte(fiche.type_operation)
    deadlines = _creer_deadlines_formalites(dossier, fiche, db, formalites)
    expose = await _generer_expose(fiche, dossier)
    contenu = construire_acte_markdown(fiche, dossier, expose, formalites)
    derniere = derniere_promesse(dossier.id, db, "acte")
    acte = ActeCession(
        dossier_id=dossier.id, type="acte", sous_type=fiche.type_operation,
        version=(derniere.version + 1) if derniere else 1,
        contenu=contenu, statut="BROUILLON",
    )
    db.add(acte)
    db.commit()
    db.refresh(acte)
    return acte, deadlines


# ── Rendu HTML imprimable (réutilise module_d) ────────────────────────

def construire_html_acte(acte: ActeCession, dossier: Dossier, cabinet: Cabinet | None) -> str:
    from modules.module_d import markdown_vers_html
    cabinet_nom = (cabinet.nom if cabinet and cabinet.nom else "Cabinet d'Avocats")
    cabinet_adresse = (cabinet.adresse if cabinet and getattr(cabinet, "adresse", None) else "")
    quand = acte.created_at.strftime("%d/%m/%Y à %Hh%M") if acte.created_at else ""
    corps_html = markdown_vers_html(acte.contenu or "")
    libelle = "Promesse de cession" if acte.type == "promesse" else "Acte de cession"
    mentions = (
        "PROJET / document de travail couvert par le secret professionnel de l'avocat "
        "(art. 66-5 de la loi n°71-1130 du 31 décembre 1971). "
        "Ne pas signer sans relecture et validation par l'avocat — [VERIFICATION REQUISE PAR L'AVOCAT]."
    )
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<title>{html.escape(libelle)} — {html.escape(dossier.reference or '')} v{acte.version}</title>
<style>
  @page {{ margin: 2cm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: Georgia, 'Times New Roman', serif; color: #1a1a1a; line-height: 1.55; max-width: 820px; margin: 24px auto; padding: 0 16px; }}
  .entete {{ border-top: 4px solid #3730a3; padding-top: 14px; margin-bottom: 8px; }}
  .entete h1 {{ font-size: 20px; color: #3730a3; margin: 0; }}
  .entete .meta {{ font-size: 12px; color: #555; margin-top: 4px; }}
  .badges {{ font-size: 12px; color: #444; margin: 10px 0 12px; }}
  .contenu {{ font-size: 14px; }}
  .contenu h1 {{ font-size: 18px; color: #3730a3; text-align: center; margin: 8px 0 16px; }}
  .contenu h2 {{ font-size: 15px; color: #3730a3; margin: 16px 0 4px; }}
  .contenu table {{ border-collapse: collapse; width: 100%; margin: 8px 0; }}
  .contenu td, .contenu th {{ border: 1px solid #ccc; padding: 4px 8px; font-size: 13px; }}
  .contenu blockquote {{ border-left: 3px solid #c7d2fe; margin: 8px 0; padding: 4px 12px; color: #555; background: #f5f5ff; }}
  .contenu ul, .contenu ol {{ margin: 6px 0 6px 22px; }}
  .contenu p {{ margin: 6px 0; }}
  .mentions {{ border-top: 1px solid #ccc; margin-top: 28px; padding-top: 10px; font-size: 11px; color: #777; }}
  .barre {{ margin: 16px 0; }}
  .btn {{ background: #3730a3; color: #fff; border: 0; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 13px; }}
  @media print {{ .no-print {{ display: none !important; }} body {{ margin: 0; }} }}
</style></head>
<body>
  <div class="barre no-print"><button class="btn" onclick="window.print()">🖨 Imprimer / Enregistrer en PDF</button></div>
  <div class="entete">
    <h1>{html.escape(cabinet_nom)}</h1>
    <div class="meta">{html.escape(cabinet_adresse)}</div>
  </div>
  <div class="badges">{html.escape(libelle)} — Réf. {html.escape(dossier.reference or '')} · Version {acte.version} · {html.escape(acte.statut or '')} · Établi le {quand}</div>
  <div class="contenu">{corps_html}</div>
  <div class="mentions">{mentions}</div>
</body></html>"""
