-- AIOS — Module D : fiches de préparation versionnées et persistées.
-- Additif et idempotent. Une ligne par version ; la plus récente = version max.

CREATE TABLE IF NOT EXISTS fiches_preparation (
    id          varchar PRIMARY KEY,
    dossier_id  varchar NOT NULL REFERENCES dossiers(id),
    version     integer DEFAULT 1,
    type_rdv    varchar,
    contenu     text,
    created_at  timestamp DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_fiches_dossier ON fiches_preparation (dossier_id, version DESC);
