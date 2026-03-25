-- Executar uma vez em bases PostgreSQL já existentes (create_all não acrescenta colunas).
ALTER TABLE raffles ADD COLUMN IF NOT EXISTS summary TEXT;
ALTER TABLE raffles ADD COLUMN IF NOT EXISTS genres JSONB;
ALTER TABLE raffles ADD COLUMN IF NOT EXISTS series JSONB;
ALTER TABLE raffles ADD COLUMN IF NOT EXISTS game_modes JSONB;
ALTER TABLE raffles ADD COLUMN IF NOT EXISTS player_perspectives JSONB;
ALTER TABLE raffles ADD COLUMN IF NOT EXISTS igdb_url VARCHAR(1024);
ALTER TABLE raffles ADD COLUMN IF NOT EXISTS igdb_game_id VARCHAR(64);
