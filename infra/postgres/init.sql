-- Runs once, on first container start, before any Alembic migration.
-- Extensions must exist before migrations reference vector/tsvector features.

CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector: embeddings
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- trigram: fuzzy title matching
CREATE EXTENSION IF NOT EXISTS btree_gist;  -- composite (video_id, range) indexes
