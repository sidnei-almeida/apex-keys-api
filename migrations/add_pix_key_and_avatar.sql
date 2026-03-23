-- Adiciona colunas pix_key e avatar_url à tabela users.
-- Execute contra a base existente antes de atualizar a API.
-- Ex.: psql $DATABASE_URL -f migrations/add_pix_key_and_avatar.sql

ALTER TABLE users ADD COLUMN IF NOT EXISTS pix_key VARCHAR(140);
ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(1024);
