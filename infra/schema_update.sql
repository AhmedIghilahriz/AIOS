-- =====================================================================
-- AIOS Legal — schema_update.sql  (Ajout 1 de l'audit)
-- IDEMPOTENT : peut être ré-exécuté sans risque.
--
-- Les tables sont normalement créées automatiquement au démarrage
-- (SQLAlchemy : Base.metadata.create_all). Ce fichier :
--   1) garantit l'extension pgvector ;
--   2) ajoute les COLONNES audit (urgence / conflit) si absentes ;
--   3) crée des INDEX pour les requêtes des nouvelles routes (fil dossier,
--      propositions, veille) ;
--   4) sert de DOCUMENTATION des tables principales (cf. CLAUDE.md §11).
-- À appliquer dans l'éditeur SQL Supabase si la base n'est pas à jour.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- ── Colonnes ajoutées au fil de l'eau (idempotent) ───────────────────
ALTER TABLE IF EXISTS emails_classifies
    ADD COLUMN IF NOT EXISTS proposition_thread_id varchar;

-- Champs métier optionnels stockés dans dossiers.metadonnees (JSON) :
--   partie_adverse (str) / parties_adverses (list)  → Cas 10 conflit d'intérêts
--   honoraires_ht (float) / type_honoraires (str)   → Cas 14 facturation jalon
--   inactif (bool)                                   → Cas 13 radar d'inactivité
-- (pas de DDL : metadonnees est un JSON déjà présent sur la table dossiers)

-- ── Index de performance pour les routes d'audit ─────────────────────
-- Cas 5/6 : fil chronologique d'un dossier + résolution dossier d'un email
CREATE INDEX IF NOT EXISTS idx_emails_classifies_dossier
    ON emails_classifies (dossier_id, date_reception);
-- Triage : retrouver un email déjà importé (anti-doublon)
CREATE INDEX IF NOT EXISTS idx_emails_classifies_msgid
    ON emails_classifies (message_id_externe);
-- Cas 8 : deadlines d'un dossier
CREATE INDEX IF NOT EXISTS idx_deadlines_dossier
    ON deadlines (dossier_id, date_echeance);
-- Documents (checklist) d'un dossier
CREATE INDEX IF NOT EXISTS idx_documents_dossier
    ON documents (dossier_id, statut);
-- Propositions HITL en attente
CREATE INDEX IF NOT EXISTS idx_propositions_statut
    ON propositions_dossier (statut, created_at);
-- Veille : dédoublonnage (titre + url) et tri récent
CREATE INDEX IF NOT EXISTS idx_veille_recent
    ON veille_alertes (created_at DESC);

-- ── Recherche sémantique pgvector (si non créés ailleurs) ────────────
-- IVFFlat nécessite des données ; en dev on peut s'en passer (séquentiel).
-- CREATE INDEX IF NOT EXISTS idx_dossiers_embedding
--     ON dossiers USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- =====================================================================
-- RÉFÉRENCE — colonnes clés des tables principales (cf. CLAUDE.md §11)
-- ---------------------------------------------------------------------
-- dossiers(id, reference UNIQUE, titre, specialite, status, priorite,
--          description, metadonnees JSON, client_id, avocat_id, cabinet_id,
--          embedding vector(1536), created_at, updated_at)
-- propositions_dossier(id=thread_id, avocat_id, cabinet_id, proposition JSON,
--          message, statut[EN_ATTENTE|CREE|REJETE], dossier_id, created_at, resolved_at)
-- emails_classifies(id, integration_id, message_id_externe UNIQUE, expediteur,
--          destinataires JSON, sujet, corps_extrait, date_reception, categorie,
--          sous_categorie, priorite, resume_ia, action_suggeree, dossier_id,
--          dossier_detecte_auto, proposition_thread_id, traite, created_at)
-- veille_alertes(id, titre, source, url, date_publication, impact, resume,
--          mots_cles JSON, lu, created_at)
-- fiches_preparation(id, dossier_id, version, type_rdv, contenu, created_at)
-- regles_juridiques(id, scope[cabinet|avocat], scope_id, module, payload JSON,
--          updated_at, UNIQUE(scope, scope_id, module))
-- deadlines(id, titre, description, date_echeance, type_delai, alertes_envoyees,
--          acquitte, dossier_id, created_at)
-- =====================================================================
