-- Migration : lien email -> proposition de dossier (deep-link HITL)
-- Idempotent. À exécuter une fois (Supabase > SQL Editor) si la colonne n'existe pas.
ALTER TABLE emails_classifies
    ADD COLUMN IF NOT EXISTS proposition_thread_id VARCHAR;
