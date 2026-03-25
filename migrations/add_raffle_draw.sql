-- Sorteio: bilhete vencedor e data (Hall da Fama / rifas finished)
--
-- Aplicar: psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/add_raffle_draw.sql

BEGIN;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'raffles' AND column_name = 'winning_ticket_number'
  ) THEN
    ALTER TABLE raffles ADD COLUMN winning_ticket_number INTEGER NULL;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'raffles' AND column_name = 'drawn_at'
  ) THEN
    ALTER TABLE raffles ADD COLUMN drawn_at TIMESTAMPTZ NULL;
  END IF;
END $$;

COMMIT;
