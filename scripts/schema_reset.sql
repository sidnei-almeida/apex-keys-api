-- Reset manual (psql) — mesma ordem de dependências que drop_all do SQLAlchemy.
-- Uso: psql "$DATABASE_URL" -f scripts/schema_reset.sql
-- Depois: python scripts/apply_schema.py   OU   arrancar a API (init_db create_all)

DROP TABLE IF EXISTS transactions CASCADE;
DROP TABLE IF EXISTS tickets CASCADE;
DROP TABLE IF EXISTS raffles CASCADE;
DROP TABLE IF EXISTS users CASCADE;
