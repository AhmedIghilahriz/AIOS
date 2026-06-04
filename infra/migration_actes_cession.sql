-- ╔══════════════════════════════════════════════════════════════════════╗
-- ║  AIOS — Migration Module L : table actes_cession (promesse / acte)    ║
-- ║  Idempotent. À appliquer si la base Supabase n'est pas recréée par    ║
-- ║  Base.metadata.create_all() au démarrage du backend.                 ║
-- ╚══════════════════════════════════════════════════════════════════════╝

CREATE TABLE IF NOT EXISTS actes_cession (
    id          text PRIMARY KEY,
    dossier_id  text NOT NULL REFERENCES dossiers(id),
    type        text DEFAULT 'promesse',     -- promesse | acte
    sous_type   text,                          -- cession_fonds | cession_parts | cession_titres
    version     integer DEFAULT 1,
    contenu     text,                          -- Markdown
    statut      text DEFAULT 'BROUILLON',     -- BROUILLON | VALIDE
    created_at  timestamp DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_actes_cession_dossier
    ON actes_cession (dossier_id, version DESC);
