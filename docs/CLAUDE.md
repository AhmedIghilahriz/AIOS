# CLAUDE.md — Contexte projet AIOS Legal

> Document de référence pour toute session d'assistance IA sur ce dépôt.
> (Pour qu'il soit chargé automatiquement par Claude Code, une copie peut être placée à la racine `c:\dev\aios\CLAUDE.md`.)

## 1. Le produit
**AIOS Legal** — assistant IA de gestion de dossiers pour un **cabinet d'avocats français spécialisé en droit de la pharmacie (officine)** : tri des emails, création de dossiers, collecte de pièces, qualification, fiches de préparation, délais, facturation, due diligence officine, contentieux, veille réglementaire.

## 2. Contraintes NON négociables
- **Outils gratuits / tier gratuit uniquement.** Toute dépendance payante doit être justifiée et une alternative gratuite préférée.
- **Pas de Docker.**
- **Windows** (PowerShell ; bash dispo). Machine à **disque/RAM très limités** → voir §9 (pièges).
- **Aucune clé en dur** : tout via `.env` (placeholders dans `.env.example`). **Ne jamais committer `.env`** (secrets réels, gitignored).
- **Déontologie** : les calculs juridiques (délais, scores, complétude) sont **déterministes (0 % LLM)** ; le LLM sert **uniquement** à rédiger/résumer/classer. Toute appréciation juridique est préfixée **`[VERIFICATION REQUISE PAR L'AVOCAT]`**.
- **Décisions de sécurité = 0 % LLM** : injection, **urgence/délai** (Cas 8), **conflit d'intérêts** (Cas 10) sont **déterministes** (regex / seuils numériques), jamais confiés au LLM.
- **Données & LLM externe** : avant tout appel à un LLM **externe** (Groq), `core.orchestrateur.sanitiser_prompt` masque NIR / IBAN / carte bancaire / tokens. Pour des données patients, forcer **`USE_LOCAL_LLM=true`** (Ollama, rien ne sort de la machine). Détails en **§13**.

## 3. Stack
- **Backend** : FastAPI (`backend/`), SQLAlchemy, **Supabase PostgreSQL + pgvector**. ~70 routes dans `backend/main.py`.
- **Frontend** : Next.js 14 (App Router, `frontend/app/dashboard/`), Tailwind v3.
- **LLM hybride** via la lib `openai` (`core/orchestrateur.py`) :
  - **Groq** (OpenAI-compatible) : `llama-3.3-70b-versatile` (standard) + `llama-3.1-8b-instant` (`fast=True`, haute fréquence).
  - **Ollama** local (`http://localhost:11434/v1`) si `USE_LOCAL_LLM=true`.
  - Fonctions clés : `llm_chat(prompt, system, max_tokens, fast)`, `_llm_generate` (retry/backoff 429), `scorer_dossier`, `generer_texte_juridique`. Shim de compat `claude`/`CLAUDE_MODEL` conservé.
- **Embeddings** : `fastembed` **local** (`BAAI/bge-small-en-v1.5`, 384‑d **paddé en 1536** — zéro-padding, pas de migration). `EMBEDDING_PROVIDER=local|gemini|openai|ollama`. `get_embedding` (async) / `get_embedding_sync`.
- **Transcription** : Groq Whisper (lib openai) + fallback `faster-whisper`.
- **Emails** : Resend (sortant) + **Gmail OAuth2** (sync `format=full` : corps complet + pièces jointes).
- **Auth** : Supabase (supabase-js côté front, JWT vérifié côté back — `core/auth.py`).
- **Tâches** : Celery + Upstash Redis. **Windows → `--pool=solo` obligatoire.**

## 4. Lancement
```powershell
./start.ps1     # backend uvicorn --reload :8000  +  Celery (--pool=solo)  +  frontend :3000
```
- venv backend : `backend/venv` (python `backend/venv/Scripts/python.exe`).
- Frontend seul : `cd frontend; npm run dev` (le **`next build` de prod échoue** sur cette machine faute de RAM/disque → utiliser `dev`).
- Santé : `http://localhost:8000/api/health` · doc API : `/docs`.

## 5. Modules (A→K) et fichiers
| Module | Rôle | Backend | Frontend |
|---|---|---|---|
| **A** | Documents & classification, **checklist métier** par type de dossier, rapprochement auto pièces↔email | `modules/module_a.py` (`DOCUMENTS_PAR_TYPE`, `_resoudre_type_checklist`, `rapprocher_documents_recus`) | `DocumentsChecklist.tsx` |
| **A.4** | **Triage LangGraph** : anti-injection + **urgence/délai déterministe (Cas 8)** + classification LLM, puis (couche sync) **garde conflit d'intérêts (Cas 10)** + isolation sémantique | `agents/email_triage.py`, `modules/module_email_oauth.py` | détail dans la modale email (`page.tsx`) |
| **B** | Recherche sémantique (pgvector) | `module_b` + `/api/dossiers/recherche` | barre onglet Dossiers |
| **C** | **Qualification** : 3 questions **dynamiques LLM** (fallback `QUESTIONS_PAR_SPECIALITE`), enrichie par le contenu des pièces | `modules/module_c.py` | panneau ⚡ Actions IA |
| **D** | **RDV = planification seule** (date/heure/type) + **Fiche de préparation = document maître** versionné : **Partie 1 Synthèse des éléments connus** (faits/montants/clauses extraits) + **Partie 2 Checklist d'entretien** (questions stratégiques par spécialité, **remplissage dynamique** : ☑ obtenu / ☐ à poser) ; HTML imprimable ; **détection RDV auto** depuis emails | `modules/module_d.py` (`generer_fiche_preparation`, `generer_et_versionner`, `construire_html_fiche`, `detecter_et_creer_rdv`) | `RendezVousPanel.tsx`, `FichesPreparation.tsx` |
| **E** | Transcription audio → compte rendu | `modules/module_e.py` | `TranscriptionUpload.tsx` |
| **F** | Délais légaux + alertes J‑30/14/7/1 (Celery) | `modules/module_f.py` (`DELAIS_LEGAUX`, `creer_deadline`) | `DeadlinesTimeline.tsx` |
| **G** | Factures + relances impayés (Resend) | `modules/module_g.py` (`gerer_relances_impayes`) | `FacturationPanel.tsx` |
| **H** | Cession officine : due diligence, valorisation, suivi ARS (4 mois) | `modules/module_h.py` | `PharmacieDDPanel.tsx` |
| **I** | Contentieux général : recours par juridiction + délais | `modules/module_i.py` | `ContentieuxPanel.tsx` |
| **J** | Contentieux pharmaciens : clause non‑concurrence (cession/travail), recours ARS/CPAM/Ordre | `modules/module_j.py` | `ContentieuxPharmaPanel.tsx` |
| **K** | **Veille réglementaire** : flux RSS **réels** filtrés sur les dossiers actifs + résumé LLM | `modules/module_k.py` | `VeillePanel.tsx` (onglet 🔎 Veille) |
| **L** | **Cession officine — chaîne acte** : (S1) extraction **structurée** des paramètres (`FicheCession`) depuis pièces + transcription appel (déterministe montants/n°, LLM qualification, HITL) + versement **SECIB** (connecteur, fallback « paquet de transfert ») ; (S2) **suivi conditions suspensives** (statut/date butoir → Module F, état « prêtes pour l'acte ») + **génération promesse** (gabarit déterministe + exposé LLM, versionnée, HTML imprimable) ; (S3) **acte définitif** VERROUILLÉ tant que les conditions ne sont pas toutes levées + clauses (GAP, non‑concurrence, séquestre) + **formalités post‑acte → Module F** ; **extraction par RAG HYBRIDE SINGLE-PASS** (chunks `document_chunks` + retrieval vectoriel **pgvector** ∪ lexical ILIKE → 1 seul appel LLM extrayant tous les champs, **citations {pièce,page}** via n° d'extrait, anti-hallucination null ; embeddings **locaux** `EMBEDDING_MODEL` e5 multilingue par défaut + préfixes query:/passage:) — re-indexer via `extraire-fiche?reindex=true` après changement d'embedder | `core/cession_schema.py`, `modules/module_l_cession.py`, `modules/module_l_secib.py`, `modules/module_l_actes.py` ; routes `/api/cession/*` | `CessionPanel.tsx` (modale dossier) — cf. `docs/CDC_cession_officine.md` |

**Email/HITL** : `modules/module_email_oauth.py` (sync Gmail, isolation sémantique au triage via embeddings, `EMAIL_DOSSIER_MODE=propose|auto`) ; création de dossier avec validation humaine **`interrupt()`** dans `agents/dossier_creation.py` (checkpointer `SqliteSaver` → `aios_graph.db`).

**Chemin complet d'un email entrant** (déterministe sauf le nœud `classification`) :
```
Email Gmail → sync OAuth (format=full : corps + pièces jointes)
  → garde_injection (0 % LLM) → urgence/délai déterministe (0 % LLM, Cas 8)
  → classification LLM (Groq fast) → fusion (l'urgence prime)
  → résolution dossier : référence détectée + ISOLATION sémantique (cosinus ≥ SEUIL_ISOLATION_DOSSIER)
  → si nouveau prospect : GARDE CONFLIT D'INTÉRÊTS (0 % LLM, Cas 10) → quarantaine si conflit
     sinon EMAIL_DOSSIER_MODE=propose → PROPOSITION (interrupt) | =auto → création directe
  → si dossier résolu : pièces jointes extraites + checklist (Cas 2),
     deadline si délai détecté (Cas 8), RDV auto si date proposée (Item 7)
```

## 6. Briques transverses
- **Service d'Extraction Unique** : `core/extraction.py` — `extraire_document(chemin, filename, mime) → {texte, metadonnees}` (PDF→PyMuPDF, scan/image→Tesseract, txt). **100 % backend (RGPD)**. `contexte_documents(db, dossier_id)` agrège le texte des pièces pour Qualification/Fiche/Réponses.
- **Règles configurables** : `core/regles.py` — résolution **defaults (code) ← cabinet ← avocat**, table `regles_juridiques`. API `GET/PUT /api/regles/{I|J}`. Extensible (F, H…).
- **Réponse client assistée** : `/api/dossiers/{id}/generer-reponse` — analyse l'**intention** + l'**état du dossier**, choisit un **scénario** (répondre / demander pièces / accuser réception / proposer RDV), salutation « Bonjour {prénom} » ; envoi via **Gmail OAuth** (`/api/emails/envoyer-reponse`).

## 7. Conventions de code
- Backend : `from core...`, `from modules...` (lancer depuis `backend/`).
- Déterministe vs LLM : voir §2. Parsing JSON LLM **tolérant** (`_extraire_json`) + **fallback** (jamais de 500 sur sortie LLM non‑JSON).
- Tout hook de synchro/LLM est **gardé par try/except** pour ne jamais casser la boucle de sync.
- Frontend : état partagé d'API + jeton dans `frontend/lib/api.ts` ; Supabase client null‑safe dans `frontend/lib/supabaseClient.ts`. Liens fichiers en markdown `[texte](chemin)`.

## 8. Base de données / migrations
- Tables ajoutées au fil de l'eau : `propositions_dossier`, `veille_alertes`, `regles_juridiques`, `fiches_preparation` ; colonne `emails_classifies.proposition_thread_id`.
- SQL idempotents dans `infra/*.sql` (à appliquer si base Supabase vierge). Les modèles peuvent aussi créer une table via `Model.__table__.create(engine, checkfirst=True)`.

## 9. Pièges connus (IMPORTANT)
- **Disque saturé** = cause n°1 des bugs : `--reload` ne redémarre plus (backend reste sur **l'ancien code** → 404 sur routes récentes), écritures Supabase qui échouent, Chrome `FILE_ERROR_NO_SPACE`. **Après toute série d'édits, relancer proprement le backend** :
  ```powershell
  Get-Job | Stop-Job; Get-Job | Remove-Job -Force
  Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
  ./start.ps1
  ```
  Vérifier le nombre de routes via `/openapi.json` (doit refléter le code courant).
- « **Serveur injoignable** » / « **CORS** » dans la console = en réalité **backend down/redémarrage** (le CORS est correctement configuré : `allow_origin_regex` localhost:*).
- Celery sous Windows : **`--pool=solo`**. Si **Celery beat** ne planifie rien : vérifier que le worker est lancé avec `--beat` (ou un `celery beat` séparé) et que `REDIS_URL` est joignable (Upstash `rediss://` → `ssl_cert_reqs=CERT_NONE` ajouté automatiquement par `core/celery_app.py`).
- **`aios_graph.db` corrompu** (checkpointer SQLite des `interrupt()`) → le supprimer et relancer : les validations HITL **en attente sont perdues**, mais les propositions restent tracées dans la table `propositions_dossier` (statut `EN_ATTENTE`) et peuvent être re-soumises.

## 10. Vérification rapide après changement
```powershell
# Backend importe sans erreur (compte les routes)
& "C:\dev\aios\backend\venv\Scripts\python.exe" -c "import sys; sys.path.insert(0, r'C:\dev\aios\backend'); import main; print(len(main.app.routes), 'routes')"
# Type-check frontend (léger, pas de build)
node "C:\dev\aios\frontend\node_modules\typescript\bin\tsc" -p "C:\dev\aios\frontend\tsconfig.json" --noEmit
```
Tests : `backend/tests/` (pytest). Scénario E2E détaillé : `docs/TESTING.md`.

## 11. Schéma des tables principales
SQL idempotent de mise à jour : `infra/schema_update.sql` (extension pgvector, colonnes audit, index). Tables aussi créées au démarrage (`Base.metadata.create_all`).

| Table | Colonnes clés |
|---|---|
| `dossiers` | id, reference (UNIQUE), titre, specialite, status, priorite, description, **metadonnees JSON**, client_id, avocat_id, cabinet_id, embedding vector(1536), created_at, updated_at |
| `propositions_dossier` | id (= thread_id HITL), avocat_id, cabinet_id, proposition JSON, message, statut (EN_ATTENTE\|CREE\|REJETE), dossier_id, created_at, resolved_at |
| `emails_classifies` | id, integration_id, message_id_externe (UNIQUE), expediteur, destinataires JSON, sujet, corps_extrait, date_reception, categorie, sous_categorie, priorite, resume_ia, action_suggeree, dossier_id, dossier_detecte_auto, **proposition_thread_id**, traite |
| `documents` | id, nom, type_doc, statut (attendu\|recu\|valide\|refuse), chemin_stockage, mime_type, ocr_contenu, dossier_id, recu_at |
| `deadlines` | id, titre, description, date_echeance, type_delai, alertes_envoyees JSON, acquitte, dossier_id |
| `veille_alertes` | id, titre, source, url, date_publication, impact (CRITIQUE\|ELEVE\|MOYEN), resume, mots_cles JSON, lu |
| `fiches_preparation` | id, dossier_id, version, type_rdv, contenu, created_at |
| `actes_cession` | id, dossier_id, type (promesse\|acte), sous_type, version, contenu (Markdown), statut, created_at — Module L (`infra/migration_actes_cession.sql`) |
| `document_chunks` | id, document_id, dossier_id, chunk_index, page, contenu, embedding vector(1536), created_at — **RAG Module L** (`infra/migration_document_chunks.sql`) ; indexé à l'upload/ingestion email |
| `regles_juridiques` | id, scope (cabinet\|avocat), scope_id, module (I\|J…), payload JSON, updated_at — UNIQUE(scope, scope_id, module) |

**Champs métier portés par `dossiers.metadonnees` (JSON, pas de DDL)** : `partie_adverse` / `parties_adverses` (Cas 10 conflit), `honoraires_ht` / `type_honoraires` (Cas 14 facture jalon), `inactif` (Cas 13 radar), `type_dossier` (checklist Module A), `pharmacie.ars` (Module H), `fiche_cession` (Module L — paramètres + conditions suspensives), `secib` (Module L — trace du dernier versement).

## 12. Comportement des agents LangGraph

### `agents/email_triage.py` — triage (sortie : dict compatible sync)
- **État** : `expediteur, sujet, corps, dossiers_actifs, security_flags, stop, urgence_forcee, urgence_delai_jours, urgence_motif, categorie_forcee, classification, resultat`.
- **Nœuds** (ordre) :
  1. `garde_injection` — **déterministe (regex)**, STOP + classe `spam` si injection. 0 % LLM.
  2. `urgence_deterministe` — **déterministe** : domaines critiques + mots `MOTS_CRITIQUES`/`MOTS_DELAI_RECOURS`, extraction du délai en jours (`_extraire_delai_jours`). 0 % LLM. (**Cas 8**)
  3. `classification_llm` — **seul appel LLM** (Groq `fast`), JSON tolérant + fallback `{}`.
  4. `fusion_decision` — l'**urgence déterministe prime** ; propage `urgence_delai_jours`/`urgence_motif`.
- La **détection de conflit** (Cas 10) et l'**isolation sémantique** vivent dans la couche sync (`module_email_oauth.py`) car elles nécessitent la base (pgvector / `metadonnees.partie_adverse`).

### `agents/dossier_creation.py` — création HITL
- **État** : `expediteur, sujet, resume_ia, categorie, avocat_id, cabinet_id, proposition, decision, dossier_id, statut`.
- **Nœuds** : `preparer` (proposition, sans DB) → `validation` (**`interrupt()`** : pause persistée par `SqliteSaver`) → conditionnel `creer` (écrit client + dossier) | `rejeter` (aucune écriture). Reprise via `Command(resume="valider"|"rejeter")` sur le même `thread_id`.

## 13. Politique données & LLM externe
- **`core.orchestrateur.sanitiser_prompt(texte)`** masque, **avant tout appel à Groq** (mode en ligne), : **NIR** (`[12]…`), **IBAN**, **carte bancaire** (16 chiffres), **tokens/JWT** (`eyJ…`, `sk-…`, `gh[op]_…`). Appliqué systématiquement dans `_llm_generate` quand le mode actif est Groq.
- `CHAMPS_INTERDITS_LLM_EXTERNE` (référentiel) : `numero_secu, date_naissance, donnees_bancaires, mot_de_passe, token, refresh_token`.
- **Mode local** (Ollama, `USE_LOCAL_LLM=true`) : aucune donnée ne quitte la machine → pas de masquage requis. **À privilégier pour tout contexte contenant des données patients.**
- L'**extraction** (`core/extraction.py`) reste **100 % backend** (PyMuPDF/Tesseract) — aucune OCR cloud par défaut (RGPD).
