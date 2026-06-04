# Cahier des charges — Chaîne « Appel → Promesse → Acte de cession → SECIB »

> Module **L** (proposé) d'AIOS Legal — automatisation de la cession d'officine de pharmacie.
> Domaine : avocat d'affaires, droit de la pharmacie (officine).
> À lire avec [CLAUDE.md](CLAUDE.md) (contraintes non négociables : déterminisme juridique, RGPD, tier gratuit, pas de Docker).

---

## 0. Le scénario client (reformulé)

```
1. APPEL TÉLÉPHONIQUE   → le client raconte l'affaire / le problème.
2. EMAIL + PIÈCES       → il envoie un mail avec des PDF volumineux (50 p., 45 p., 20 p., …).
3. ATTENDU PAR LE CLIENT (le cabinet) :
   a) Verser ces documents AUTOMATIQUEMENT dans le logiciel métier SECIB.
   b) Rédiger une PROMESSE (de cession/vente) à partir de ces documents.
   c) Étape CESSION : à partir de paramètres (conditions suspensives, financement
      obtenu ?, agrément ?, …) → générer un ACTE DE CESSION.
```

**Principe directeur (déontologie AIOS).** Aucun acte n'est produit « tout seul ». La chaîne **pré‑remplit** une fiche de paramètres, l'**avocat valide** (HITL), puis le document est généré par **gabarit déterministe** ; le LLM ne rédige que les passages narratifs. Tout document porte `[VERIFICATION REQUISE PAR L'AVOCAT]` et n'est **jamais transmis sans validation humaine**.

---

## 1. Vue d'ensemble du pipeline cible

```
┌────────────┐   ┌─────────────┐   ┌────────────────────┐   ┌──────────────────┐
│ 1. APPEL   │   │ 2. EMAIL    │   │ 3. INGESTION       │   │ 4. EXTRACTION    │
│ (audio)    │──▶│ + PJ (PDF)  │──▶│ extraction texte   │──▶│ STRUCTURÉE       │
│ Module E   │   │ Gmail OAuth │   │ core/extraction.py │   │ → Fiche cession  │
│ transcript │   │ (existe)    │   │ (existe)           │   │ (JSON validé)    │
└────────────┘   └─────────────┘   └─────────┬──────────┘   └────────┬─────────┘
                                             │                       │
                                             ▼                       ▼
                                   ┌──────────────────┐   ┌────────────────────┐
                                   │ 5. SECIB         │   │ 6. PROMESSE        │
                                   │ versement auto   │   │ génération gabarit  │
                                   │ (connecteur)     │   │ + validation avocat │
                                   └──────────────────┘   └────────┬───────────┘
                                                                   │
                                              ┌────────────────────▼───────────┐
                                              │ 7. CONDITIONS SUSPENSIVES       │
                                              │ suivi déterministe (levées?)    │
                                              │ financement / Ordre / ARS / …   │
                                              └────────────────────┬───────────┘
                                                                   │ toutes levées
                                                                   ▼
                                              ┌────────────────────────────────┐
                                              │ 8. ACTE DE CESSION définitif    │
                                              │ génération gabarit + validation │
                                              │ + checklist formalités post-acte│
                                              └─────────────────────────────────┘
```

---

## 2. Ce qui existe déjà (à réutiliser) vs à construire

| Besoin | Existe dans AIOS | Fichier | À construire |
|---|---|---|---|
| Transcription appel | ✅ Module E | `modules/module_e.py`, `TranscriptionUpload.tsx` | — |
| Réception email + PJ | ✅ Gmail OAuth | `modules/module_email_oauth.py` | — |
| Extraction texte/OCR | ✅ Service unique | `core/extraction.py` | — |
| Checklist cession officine | ✅ | `module_a.py` (`DOCUMENTS_PAR_TYPE["cession_officine"]`) | enrichir |
| Due diligence / valorisation / ARS | ✅ Module H | `modules/module_h.py` | réutiliser tel quel |
| Délais + alertes Celery | ✅ Module F | `modules/module_f.py` | brancher sur conditions suspensives |
| Document maître versionné | ✅ Module D (fiche prépa) | `modules/module_d.py` (`generer_et_versionner`, `construire_html_fiche`) | **patron à copier** pour promesse/acte |
| Validation humaine (HITL) | ✅ | `agents/dossier_creation.py` (`interrupt()`) | réutiliser le patron |
| **Extraction structurée des paramètres** | ❌ | — | **Lot 2** |
| **Génération promesse / acte (gabarits)** | ❌ | — | **Lots 3 & 5** |
| **Suivi conditions suspensives** | ❌ | — | **Lot 4** |
| **Connecteur SECIB** | ❌ | — | **Lot 1** |

> **Conclusion** : le « cœur » LLM/extraction/HITL/versionnement existe. Le travail neuf = **4 lots** (SECIB, extraction structurée, génération promesse, génération acte + suivi des conditions).

---

## 3. LOT 1 — Versement automatique dans SECIB

### 3.1 Point bloquant à lever AVANT de coder
SECIB est édité par **Septeo**. Il existe deux familles :
- **SECIB néo** (cloud) — susceptible d'exposer une **API REST** (interconnexions Septeo), mais **l'accès est contractuel/partenaire**.
- **SECIB on‑premise** (installé) — pas d'API publique self‑service.

👉 **Action n°1 (non technique)** : demander à Septeo / à l'administrateur SECIB du cabinet :
1. Version exacte (néo cloud ? on‑premise ?).
2. Existe‑t‑il une **API d'import de documents/dossiers** ? Documentation ? Clé ?
3. À défaut : import par **GED / dépôt e‑mail vers dossier** ? Format d'import par lot (CSV/XML) ?

La réponse détermine l'implémentation. **On ne code pas en supposant l'API.**

### 3.2 Architecture découplée (quelle que soit la réponse)
Créer une **interface** `SecibConnector` (couche d'abstraction) avec plusieurs implémentations interchangeables, pour que le reste du pipeline ne dépende **jamais** du mode d'intégration :

```python
# backend/modules/module_l_secib.py  (nouveau)
class SecibConnector(Protocol):
    def pousser_dossier(self, dossier, documents: list[Document]) -> ResultatPush: ...

# Implémentations possibles (choisir selon réponse Septeo) :
#  - SecibApiConnector        → si API REST disponible (idéal)
#  - SecibExportPackage       → FALLBACK garanti (voir 3.3) : aucune dépendance
#  - SecibRpaConnector        → automatisation UI (Power Automate Desktop / Playwright)
```

| Mode | Conditions | Effort | Robustesse |
|---|---|---|---|
| **API REST** (`SecibApiConnector`) | API Septeo accordée | Moyen | ★★★★★ |
| **Paquet d'import** (`SecibExportPackage`) | Toujours possible | Faible | ★★★★ (semi‑auto) |
| **RPA UI** (`SecibRpaConnector`) | Pas d'API, version web/desktop | Élevé | ★★ (fragile) |

### 3.3 Fallback garanti — « paquet de transfert » (MVP recommandé)
Tant que l'API n'est pas confirmée, livrer un **paquet normalisé importable en 1 clic** :
```
SECIB_IMPORT/<reference_dossier>/
  ├── _index.csv         (réf, client, type, date, liste pièces — colonnes mappées SECIB)
  ├── 01_bilan_N.pdf
  ├── 02_bail_commercial.pdf
  └── …                  (pièces renommées selon convention : NN_typepiece.pdf)
```
- 100 % gratuit, zéro dépendance, conforme « pas de Docker / disque limité ».
- L'avocat glisse le dossier dans SECIB (ou le robot RPA le fait).
- Quand l'API arrive, on bascule l'implémentation **sans toucher au reste**.

### 3.4 Exigences fonctionnelles Lot 1
- **EF‑1.1** Déclencheur : à la validation d'un dossier (ou bouton « Verser dans SECIB »).
- **EF‑1.2** Renommage déterministe des pièces (`NN_type.pdf`) à partir du rapprochement `module_a.rapprocher_documents_recus`.
- **EF‑1.3** Idempotence : un même dossier poussé deux fois ne crée pas de doublons (clé = `reference` + hash pièce).
- **EF‑1.4** Journalisation : `dossier.metadonnees["secib"] = {push_at, mode, statut, pieces:[…]}`.
- **EF‑1.5** RGPD : aucune pièce ne sort vers un tiers non maîtrisé ; si API cloud Septeo → vérifier localisation/DPA.

---

## 4. LOT 2 — Extraction structurée des paramètres (le cœur de votre question)

> *« Comment récupérer ces paramètres, surtout en droit de la pharmacie ? »*

### 4.1 Principe : 3 sources, 1 fiche consolidée
Les paramètres ne viennent **jamais d'une seule source**. On agrège, par ordre de fiabilité décroissante :

| # | Source | Mécanisme | Fiabilité |
|---|---|---|---|
| 1 | **Saisie/validation avocat** | Formulaire guidé (HITL) | ★★★★★ (fait foi) |
| 2 | **Pièces jointes (PDF)** | Extraction LLM **structurée** sur `Document.ocr_contenu` | ★★★ |
| 3 | **Transcription de l'appel** | Module E → mêmes champs | ★★ (indices) |

Les sources 2 et 3 **pré‑remplissent** ; la source 1 **tranche**. Chaque champ extrait porte **sa provenance** (`source: document_x p.4` / `appel` / `avocat`) et un **niveau de confiance**, pour que l'avocat voie d'où vient chaque valeur.

### 4.2 Le schéma `FicheCession` (contrat de données — Pydantic)
C'est la pièce maîtresse. Un schéma typé que le LLM doit **remplir** (extraction structurée), validé champ par champ.

```python
# backend/core/cession_schema.py  (nouveau) — extrait
class Partie(BaseModel):
    role: Literal["cedant", "cessionnaire"]
    type: Literal["personne_physique", "SELARL", "SELAS", "SELURL", "SNC", "autre"]
    denomination: str | None
    nom: str | None; prenom: str | None
    rcs_siren: str | None
    inscription_ordre: str | None          # n° / section A
    domicile_siege: str | None
    source: str; confiance: float           # provenance + 0..1

class FicheCession(BaseModel):
    type_operation: Literal["cession_fonds", "cession_parts", "cession_titres"]
    officine: Officine                       # FINESS, licence ARS, adresse, CA, type (urbaine/rurale)
    cedants: list[Partie]; cessionnaires: list[Partie]
    prix: PrixCession                        # global + ventilation incorporel/matériel/stock
    conditions_suspensives: list[ConditionSuspensive]
    garantie_actif_passif: GAP | None        # durée, plafond, franchise
    non_concurrence: NonConcurrence | None   # périmètre, durée
    sequestre: Sequestre | None              # CARPA / notaire
    date_jouissance_prevue: date | None
    avant_contrat: Literal["promesse_synallagmatique", "promesse_unilaterale_vente",
                           "promesse_unilaterale_achat"]
    champs_incertains: list[str]             # ce que l'avocat DOIT vérifier
```

### 4.3 Règle déterministe vs LLM (obligatoire — cf. CLAUDE.md §2)
- **Montants, dates, n° (FINESS, SIREN, IBAN)** → extraction **déterministe** (regex) quand le motif est fiable ; le LLM ne fait que *localiser*.
- **Qualification (type d'opération, nature des clauses)** → LLM **proposé**, jamais décidé seul → `champs_incertains`.
- **Données patients / NIR / bancaire** → `sanitiser_prompt` avant Groq, ou `USE_LOCAL_LLM=true` (Ollama) si pièces sensibles.

### 4.4 Référentiel juridique des paramètres (droit de la pharmacie)
> **C'est la réponse de fond à « comment récupérer ces paramètres en pharmacie ».** Le tableau ci‑dessous dit *quel paramètre*, *où le trouver dans les pièces*, *pourquoi il est nécessaire*.

#### A. Choix de structure (détermine TOUT le reste)
| Paramètre | Valeurs | Où le lire | Impact |
|---|---|---|---|
| **Type d'opération** | cession de **fonds de commerce** *vs* cession de **parts/titres de SEL** | Kbis + statuts : exploitation en nom propre → fonds ; SELARL/SELAS → parts | Documents, fiscalité, formalités **totalement différents** |

#### B. Paramètres de la PROMESSE (avant‑contrat) — les conditions suspensives
| Condition suspensive | Comment la « récupérer » | Source pièce |
|---|---|---|
| **Financement de l'acquéreur** (montant, banque, taux plafond, date butoir) | offre de prêt / term sheet / business plan | `financement_acquisition_officine` |
| **Inscription de l'acquéreur à l'Ordre** (Section A, titulaire) | attestation Ordre national des pharmaciens | DD juridique |
| **Déclaration / non‑opposition ARS** (changement de titulaire) | autorisation ARS, courriers ARS | Module H (`etat_ars`) |
| **Audit/due diligence satisfaisant** (pas de passif caché) | rapport DD → `module_h.evaluer_due_diligence` | bilans, liasses |
| **Purge du droit de préemption** (commune, art. L.214‑1 C. urb.) | bail, situation urbanistique | bail commercial |
| **Agrément des coassociés** (si parts de SEL, clause d'agrément) | statuts (clause d'agrément), PV | statuts SEL |
| **Accord du bailleur** (clause d'agrément au bail) | bail commercial + avenants | bail |
| **Maintien des contrats essentiels** (grossiste, LGO) | contrats grossistes (OCP/Phoenix…), contrat LGO | DD financiers |
| **Absence de changement substantiel** (MAC) entre promesse et acte | situation comptable intermédiaire | comptable |

#### C. Paramètres de l'ACTE définitif
| Paramètre | Récupération | Source |
|---|---|---|
| **Prix + ventilation** (incorporel / matériel / **stock évalué contradictoirement**) | bilans + inventaire à la date d'entrée en jouissance | bilans, inventaire |
| **Modalités de paiement / séquestre** (CARPA avocat ou notaire) | term sheet, instructions client | appel + avocat |
| **Date d'entrée en jouissance / transfert** | promesse + agenda | promesse |
| **Garantie d'actif et de passif (GAP)** : durée, plafond, franchise | négociation | avocat (rarement dans les PJ) |
| **Clause de non‑concurrence / non‑rétablissement** : périmètre + durée (doit être **proportionnée**, limitée temps + espace) | négociation | avocat |
| **Sort des contrats de travail** (transfert automatique art. L.1224‑1 C. trav.) | contrats de travail, effectif | DD sociaux |
| **Déclarations du cédant** (sincérité CA/marge, absence de litige) | bilans, relevés CPAM | DD financiers |

#### D. Formalités POST‑acte (checklist déterministe à générer)
| Formalité | Délai | Base |
|---|---|---|
| **Enregistrement aux impôts** | 1 mois | CGI |
| **Droits d'enregistrement** | — | fonds : barème par tranches ; parts SARL/SELARL ≈ 3 % après abattement ; actions SELAS ≈ 0,1 % — `[VERIFICATION REQUISE PAR L'AVOCAT]` |
| **Publicité** : JAL + **BODACC** (cession de fonds) | ~15 j | C. com. |
| **Séquestre du prix + opposition des créanciers** (solidarité fiscale art. 1684 CGI) | 10 j opposition / indispo. 3–5 mois | C. com. / CGI |
| **Déclaration ARS** (nouveau titulaire) + **inscription/radiation Ordre** | sans délai | CSP (Module H) |
| **Information préalable des salariés** (loi Hamon, < 250 sal.) | **2 mois avant** la cession | art. L.23‑10‑1 C. com. |
| **Modification statuts + greffe** (cession de parts) | — | C. com. |

> Ces délais alimentent **Module F** (`creer_deadline` + alertes J‑30/14/7/1). ⚠️ La table des délais doit être **validée par l'avocat** avant prod (cf. README).

### 4.5 Exigences fonctionnelles Lot 2
- **EF‑2.1** `extraire_fiche_cession(dossier_id)` → agrège `contexte_documents()` + transcription, appelle le LLM en **mode extraction structurée** (sortie = `FicheCession` JSON), parsing tolérant + fallback (jamais de 500).
- **EF‑2.2** Chaque champ porte `source` + `confiance` ; les champs non trouvés vont dans `champs_incertains`.
- **EF‑2.3** Les montants/dates/numéros passent par un extracteur **déterministe** (regex) prioritaire sur le LLM.
- **EF‑2.4** Persistance : `dossier.metadonnees["fiche_cession"]` (JSON, **pas de DDL**, comme Module H).
- **EF‑2.5** Écran de **validation avocat** (réutiliser le patron HITL) : chaque champ éditable, provenance affichée.

---

## 5. LOT 3 — Génération de la PROMESSE

### 5.1 Méthode (gabarit déterministe + LLM narratif)
- **Gabarit** (squelette juridique fixe, versionné en repo) : `backend/templates/promesse_*.md.j2` (Jinja2).
  - `promesse_cession_fonds.md.j2`
  - `promesse_cession_parts_sel.md.j2`
- Le gabarit insère **mécaniquement** les paramètres de la `FicheCession` (parties, prix, officine, **conditions suspensives** cochées).
- Le **LLM** ne rédige que les **passages narratifs** (exposé préalable, motifs) — borné, et préfixé `[VERIFICATION REQUISE PAR L'AVOCAT]`.
- Versionnement **identique au Module D** (`fiches_preparation`) : réutiliser `generer_et_versionner` + `construire_html_fiche` → HTML imprimable / export.

### 5.2 Exigences
- **EF‑3.1** `generer_promesse(dossier_id, type_avant_contrat)` → document versionné (table `actes_cession`, voir §8).
- **EF‑3.2** Bloque la génération si `champs_incertains` contient un champ **critique** (parties, prix, type d'opération) non validé.
- **EF‑3.3** Les conditions suspensives sélectionnées dans la fiche deviennent une **liste suivie** (Lot 4).
- **EF‑3.4** Aucun envoi automatique : génération → validation avocat → (option) envoi via Gmail OAuth existant.

---

## 6. LOT 4 — Suivi des conditions suspensives (déterministe)

### 6.1 Principe
Chaque condition suspensive de la promesse devient un **objet suivi** avec statut + date butoir :
```json
{ "code": "FINANCEMENT", "libelle": "Obtention prêt 850 000 € — Banque X",
  "statut": "EN_ATTENTE", "date_butoir": "2026-09-30", "preuve_doc_id": null }
```
- Statuts : `EN_ATTENTE | LEVEE | DEFAILLIE`.
- **0 % LLM** : le passage à `LEVEE` exige une **action humaine** ou la **réception d'une pièce probante** (rapprochement `module_a`).
- Date butoir → **Module F** (alerte avant échéance ; défaillance = condition non levée = caducité possible → `[VERIFICATION REQUISE PAR L'AVOCAT]`).

### 6.2 Exigence clé
- **EF‑4.1** L'**acte définitif (Lot 5) est verrouillé** tant que **toutes** les conditions suspensives ne sont pas `LEVEE`. C'est exactement votre besoin : *« on passe à l'étape cession : est‑ce que le financement est prêt ? est‑ce que… »*.

---

## 7. LOT 5 — Génération de l'ACTE DE CESSION définitif

- Même méthode que Lot 3 (gabarits `acte_cession_fonds.md.j2` / `acte_cession_parts_sel.md.j2`).
- **Pré‑condition dure** : toutes conditions suspensives `LEVEE` (Lot 4).
- Reprend la `FicheCession` **enrichie** des clauses de l'acte (GAP, non‑concurrence, séquestre, ventilation stock).
- Génère **en plus** la **checklist des formalités post‑acte** (§4.4‑D) → injectée dans Module F (délais) + Module A (pièces à produire).
- **EF‑5.1** Génération → validation avocat (HITL) → versionnement → export → (Lot 1) versement SECIB.

---

## 8. Modèle de données (sans casser l'existant)

Conformément à CLAUDE.md (champs métier portés par `dossiers.metadonnees`, peu de DDL) :

| Donnée | Stockage | DDL ? |
|---|---|---|
| `FicheCession` (paramètres) | `dossiers.metadonnees["fiche_cession"]` | ❌ |
| Conditions suspensives + statuts | `dossiers.metadonnees["conditions_suspensives"]` | ❌ |
| État SECIB | `dossiers.metadonnees["secib"]` | ❌ |
| Promesse / Acte (versionnés) | **nouvelle table** `actes_cession` (id, dossier_id, type[`promesse`\|`acte`], sous_type, version, contenu, statut, created_at) | ✅ (1 table, idempotent comme `fiches_preparation`) |

> Patron exact à copier : `FichePreparation` dans `core/models.py` + SQL idempotent dans `infra/` (cf. `migration_fiches_preparation.sql`).

---

## 9. API (routes à ajouter dans `main.py`)

```
POST /api/cession/{dossier_id}/extraire-fiche        # Lot 2 — pré-remplit FicheCession
GET  /api/cession/{dossier_id}/fiche                  # lecture fiche (provenance + confiance)
PUT  /api/cession/{dossier_id}/fiche                  # validation/édition avocat (HITL)
POST /api/cession/{dossier_id}/promesse               # Lot 3 — génère la promesse
GET  /api/cession/{dossier_id}/conditions             # Lot 4 — état des conditions suspensives
PATCH /api/cession/{dossier_id}/conditions/{code}     # marquer LEVEE/DEFAILLIE (humain)
POST /api/cession/{dossier_id}/acte                   # Lot 5 — génère l'acte (si toutes levées)
POST /api/cession/{dossier_id}/secib                  # Lot 1 — verse le dossier dans SECIB
```

---

## 10. Contraintes déontologiques & RGPD (rappel impératif)

1. **Le LLM ne décide jamais** d'une qualification juridique : il propose, l'avocat tranche (`champs_incertains`).
2. **Tout document généré** porte `[VERIFICATION REQUISE PAR L'AVOCAT]` et **n'est jamais signé/envoyé sans validation**.
3. **Calculs** (délais, complétude DD, droits d'enregistrement) = **déterministes**, jamais LLM.
4. **Données sensibles** (NIR, bancaire, données patients dans les pièces) : `sanitiser_prompt` avant Groq, ou `USE_LOCAL_LLM=true`.
5. **Extraction 100 % backend** (PyMuPDF/Tesseract) — aucune OCR cloud.

---

## 11. Phasage recommandé (MVP → V2)

| Sprint | Contenu | Valeur |
|---|---|---|
| **S1 (MVP)** ✅ *livré* | Lot 2 (extraction `FicheCession`) + Lot 1 fallback « paquet de transfert » SECIB + **écran de validation** (`CessionPanel.tsx`, monté dans la modale dossier). | L'avocat voit les paramètres pré‑remplis, les valide et exporte vers SECIB |
| **S2** ✅ *livré* | Lot 3 (promesse : gabarit déterministe + exposé LLM, versionnée, HTML imprimable) + Lot 4 (conditions suspensives : statut/date butoir → Module F, état « prêtes pour l'acte ») | La promesse sort en 1 clic, le suivi des conditions est outillé |
| **S3** ✅ *livré* | Lot 5 : acte définitif **verrouillé** tant que les conditions ne sont pas toutes levées + clauses (GAP, non‑concurrence, séquestre) + **formalités post‑acte → Module F** (enregistrement, BODACC, opposition créanciers, ARS/Ordre, statuts/greffe…) | Chaîne complète jusqu'à l'acte |
| **S4** | Lot 1 API SECIB réelle (si Septeo l'accorde) + RPA fallback | Versement 100 % automatique |

---

## 12. Décisions à trancher avant de coder

1. **SECIB** : version (néo cloud / on‑premise) ? API Septeo disponible ? → conditionne le Lot 1.
2. **Type d'opération prioritaire** : cession de **fonds de commerce** ou cession de **parts de SEL** ? (les deux à terme, mais lequel d'abord pour les gabarits ?)
3. **Type d'avant‑contrat** standard du cabinet : promesse **synallagmatique** (compromis) ou **unilatérale** ?
4. **Séquestre** : CARPA (avocat) ou notaire ? (impacte le gabarit de l'acte).

> ⚠️ Rappel responsabilité : la génération d'actes engage la responsabilité du cabinet. AIOS reste un **assistant de rédaction** ; la table des délais (Module F) et les gabarits doivent être **validés par l'avocat** avant toute mise en production.
