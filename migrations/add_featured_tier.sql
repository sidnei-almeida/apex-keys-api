-- Adiciona coluna featured_tier à tabela raffles
-- featured = hero no topo (várias rifas permitidas; ver ordenação em GET /raffles), carousel = carrossel, none = só em /rifas
--
-- Aplicar: psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/add_featured_tier.sql

BEGIN;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'raffles' AND column_name = 'featured_tier'
  ) THEN
    ALTER TABLE raffles ADD COLUMN featured_tier VARCHAR(20) DEFAULT 'none';
    UPDATE raffles SET featured_tier = 'none' WHERE featured_tier IS NULL;
  END IF;
END $$;

COMMIT;
