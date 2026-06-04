-- AIOS — Règles juridiques configurables (defaults ← cabinet ← avocat).
-- Additif et idempotent : ne stocke QUE les surcharges (payload).
-- Tant que cette table est absente, le code retombe sur les valeurs par défaut.

CREATE TABLE IF NOT EXISTS regles_juridiques (
    id          varchar PRIMARY KEY,
    scope       varchar NOT NULL,                 -- 'cabinet' | 'avocat'
    scope_id    varchar NOT NULL,                 -- cabinet_id ou avocat_id
    module      varchar NOT NULL,                 -- 'I' | 'J' | ... (extensible)
    payload     jsonb   DEFAULT '{}'::jsonb,      -- override partiel des règles
    updated_at  timestamp DEFAULT now(),
    CONSTRAINT uq_regle_scope_module UNIQUE (scope, scope_id, module)
);

CREATE INDEX IF NOT EXISTS ix_regles_scope ON regles_juridiques (scope, scope_id, module);
