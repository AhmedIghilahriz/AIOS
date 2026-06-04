-- ╔══════════════════════════════════════════════════════════════════════╗
-- ║  AIOS — Setup Supabase                                               ║
-- ║  Coller ce SQL dans : Supabase Dashboard > SQL Editor > New query    ║
-- ╚══════════════════════════════════════════════════════════════════════╝

-- ─── 1. Extensions (vous avez déjà exécuté celles-ci) ────────────────
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ─── 2. Les tables sont créées AUTOMATIQUEMENT par SQLAlchemy ─────────
--  Ne pas créer les tables manuellement.
--  Au premier démarrage du backend (python main.py), SQLAlchemy exécute
--  Base.metadata.create_all() et crée toutes les tables.

-- ─── 3. Index de performance (à exécuter APRÈS le premier démarrage) ──
--  Attendre d'avoir >500 lignes avant de créer les index vectoriels.

-- Index vectoriels (recherche sémantique rapide)
CREATE INDEX IF NOT EXISTS idx_dossiers_vec
    ON dossiers USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS idx_documents_vec
    ON documents USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS idx_clients_vec
    ON clients USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Index texte pour la recherche hybride (pg_trgm)
CREATE INDEX IF NOT EXISTS idx_dossiers_titre_trgm
    ON dossiers USING gin (titre gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_clients_nom_trgm
    ON clients USING gin (nom gin_trgm_ops);

-- ─── 4. Row Level Security (RLS) — Sécurité multi-cabinet ─────────────
--  Chaque cabinet ne voit que ses propres données.
--  À activer après avoir créé les tables (step 2).

ALTER TABLE dossiers  ENABLE ROW LEVEL SECURITY;
ALTER TABLE clients   ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE factures  ENABLE ROW LEVEL SECURITY;
ALTER TABLE deadlines ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_integrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE emails_classifies  ENABLE ROW LEVEL SECURITY;

-- Politique : le backend (service_role) voit tout (pas de restriction)
-- Les clients web (anon/authenticated) sont limités à leur cabinet_id
CREATE POLICY "service_role_bypass" ON dossiers
    USING (auth.role() = 'service_role');

CREATE POLICY "service_role_bypass" ON clients
    USING (auth.role() = 'service_role');

CREATE POLICY "service_role_bypass" ON email_integrations
    USING (auth.role() = 'service_role');

CREATE POLICY "service_role_bypass" ON emails_classifies
    USING (auth.role() = 'service_role');

-- ─── 5. Bucket Supabase Storage (pour les fichiers uploadés) ──────────
--  Exécuter dans : Supabase Dashboard > Storage > New bucket
--  Ou via SQL :

INSERT INTO storage.buckets (id, name, public)
VALUES ('documents-cabinet', 'documents-cabinet', false)
ON CONFLICT DO NOTHING;

-- Politique storage : seul le service_role peut lire/écrire
CREATE POLICY "service_role_storage" ON storage.objects
    USING (auth.role() = 'service_role');

-- ─── 6. Variables d'environnement à mettre dans .env ──────────────────
-- (Supabase Dashboard > Project Settings > Database)
--
-- DATABASE_URL=postgresql://postgres.oxvjniviwbyzmsqdrbvr:[PASSWORD]@aws-0-eu-central-1.pooler.supabase.com:6543/postgres?sslmode=require
--
-- (Supabase Dashboard > Project Settings > API)
-- SUPABASE_URL=https://oxvjniviwbyzmsqdrbvr.supabase.co
-- SUPABASE_SERVICE_KEY=eyJ...  (service_role key)
-- SUPABASE_ANON_KEY=eyJ...     (anon key)
--
-- ─── 7. Ajouter les credentials OAuth2 Google dans .env ──────────────
-- (Google Cloud Console > APIs & Services > Credentials)
-- GOOGLE_CLIENT_ID=xxxx.apps.googleusercontent.com
-- GOOGLE_CLIENT_SECRET=GOCSPX-xxxx
-- BACKEND_URL=http://localhost:8000
-- (Ajouter http://localhost:8000/api/email/callback/google dans Redirect URIs)
