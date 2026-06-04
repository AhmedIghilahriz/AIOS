-- ╔══════════════════════════════════════════════════════════════════════╗
-- ║  AIOS — Migration Module L (RAG) : table document_chunks             ║
-- ║  Stocke les segments vectorisés des documents pour le retrieval ciblé.║
-- ║  Idempotent. (Aussi créée au démarrage par Base.metadata.create_all.) ║
-- ╚══════════════════════════════════════════════════════════════════════╝

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS document_chunks (
    id          text PRIMARY KEY,
    document_id text REFERENCES documents(id),
    dossier_id  text REFERENCES dossiers(id),
    chunk_index integer DEFAULT 0,
    page        integer,                 -- n° de page 1-indexé (NULL si inconnu)
    contenu     text,
    embedding   vector(1536),
    created_at  timestamp DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chunks_dossier ON document_chunks (dossier_id);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON document_chunks (document_id);

-- Index vectoriel pour la recherche de similarité (cosinus).
-- À (re)créer de préférence APRÈS avoir chargé quelques centaines de lignes (cf. supabase_setup.sql).
CREATE INDEX IF NOT EXISTS idx_chunks_vec
    ON document_chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
