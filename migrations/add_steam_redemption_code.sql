-- Código Steam guardado na criação da rifa; enviado ao vencedor por notificação in-app após o sorteio.
ALTER TABLE raffles ADD COLUMN IF NOT EXISTS steam_redemption_code VARCHAR(512);
