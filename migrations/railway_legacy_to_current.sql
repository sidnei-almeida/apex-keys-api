-- =============================================================================
-- Migração Railway: esquema legado Apex Keys → esquema atual (SQLAlchemy)
--
-- Legado típico:
--   users: name, hashed_password, role, wallet_balance
--   raffles: total_numbers, price_per_number, status (open|closed|canceled), winner_ticket_id
--
-- Alvo: ver schema.sql na raiz do repositório.
--
-- Como aplicar (substitua pela URL pública TCP da Railway, com sslmode=require):
--   psql "$DATABASE_PUBLIC_URL" -v ON_ERROR_STOP=1 -f migrations/railway_legacy_to_current.sql
--
-- Base nova / vazia: use só schema.sql (ou deixe a app fazer create_all).
-- Faça backup antes (Railway → Postgres → Snapshots / dump).
-- =============================================================================

BEGIN;

-- --------------------------------------------------------------------------- users
DO $u$
BEGIN
  IF to_regclass('public.users') IS NULL THEN
    RAISE NOTICE 'Tabela public.users não existe — crie com schema.sql ou create_all.';
    RETURN;
  END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'name'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'full_name'
  ) THEN
    EXECUTE 'ALTER TABLE users RENAME COLUMN name TO full_name';
  END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'hashed_password'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'password_hash'
  ) THEN
    EXECUTE 'ALTER TABLE users RENAME COLUMN hashed_password TO password_hash';
  END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'wallet_balance'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'balance'
  ) THEN
    EXECUTE 'ALTER TABLE users RENAME COLUMN wallet_balance TO balance';
  END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE table_schema = 'public' AND table_name = 'users' AND constraint_name = 'users_role_check'
  ) THEN
    EXECUTE 'ALTER TABLE users DROP CONSTRAINT users_role_check';
  END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'role'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'is_admin'
  ) THEN
    EXECUTE 'ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT FALSE';
    EXECUTE $q$UPDATE users SET is_admin = (role = 'admin')$q$;
    EXECUTE 'ALTER TABLE users DROP COLUMN role';
  END IF;

  -- Tipos / precisão alinhados ao modelo
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'balance'
  ) THEN
    EXECUTE 'ALTER TABLE users ALTER COLUMN balance TYPE NUMERIC(12, 2) USING balance::NUMERIC(12, 2)';
  END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'created_at'
  ) THEN
    BEGIN
      EXECUTE $q$
        ALTER TABLE users
        ALTER COLUMN created_at TYPE TIMESTAMPTZ
        USING created_at AT TIME ZONE 'UTC'
      $q$;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'users.created_at: conversão para timestamptz ignorada (%).', SQLERRM;
    END;
  END IF;
END $u$;

-- --------------------------------------------------------------------------- raffles
DO $r$
BEGIN
  IF to_regclass('public.raffles') IS NULL THEN
    RAISE NOTICE 'Tabela public.raffles não existe.';
    RETURN;
  END IF;

  -- Só remove o CHECK antigo (open/closed/canceled) quando ainda existe coluna legada.
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'raffles' AND column_name = 'total_numbers'
  ) AND EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE table_schema = 'public' AND table_name = 'raffles' AND constraint_name = 'raffles_status_check'
  ) THEN
    EXECUTE 'ALTER TABLE raffles DROP CONSTRAINT raffles_status_check';
  END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'raffles' AND column_name = 'total_numbers'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'raffles' AND column_name = 'total_tickets'
  ) THEN
    EXECUTE 'ALTER TABLE raffles RENAME COLUMN total_numbers TO total_tickets';
  END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'raffles' AND column_name = 'price_per_number'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'raffles' AND column_name = 'ticket_price'
  ) THEN
    EXECUTE 'ALTER TABLE raffles RENAME COLUMN price_per_number TO ticket_price';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'raffles' AND column_name = 'total_price'
  ) THEN
    EXECUTE 'ALTER TABLE raffles ADD COLUMN total_price NUMERIC(12, 2)';
    EXECUTE $q$
      UPDATE raffles
      SET total_price = (total_tickets::NUMERIC * ticket_price::NUMERIC)::NUMERIC(12, 2)
      WHERE total_price IS NULL
    $q$;
    EXECUTE 'ALTER TABLE raffles ALTER COLUMN total_price SET NOT NULL';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'raffles' AND column_name = 'image_url'
  ) THEN
    EXECUTE 'ALTER TABLE raffles ADD COLUMN image_url VARCHAR(1024)';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'raffles' AND column_name = 'video_id'
  ) THEN
    EXECUTE 'ALTER TABLE raffles ADD COLUMN video_id VARCHAR(64)';
  END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'raffles' AND column_name = 'status'
  ) THEN
    EXECUTE $q$
      UPDATE raffles SET status = CASE status
        WHEN 'open' THEN 'active'
        WHEN 'closed' THEN 'finished'
        WHEN 'canceled' THEN 'canceled'
        ELSE status
      END
    $q$;
  END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'raffles' AND column_name = 'winner_ticket_id'
  ) THEN
    EXECUTE 'ALTER TABLE raffles DROP COLUMN winner_ticket_id';
  END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'raffles' AND column_name = 'ticket_price'
  ) THEN
    EXECUTE 'ALTER TABLE raffles ALTER COLUMN ticket_price TYPE NUMERIC(12, 2) USING ticket_price::NUMERIC(12, 2)';
  END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'raffles' AND column_name = 'created_at'
  ) THEN
    BEGIN
      EXECUTE $q$
        ALTER TABLE raffles
        ALTER COLUMN created_at TYPE TIMESTAMPTZ
        USING created_at AT TIME ZONE 'UTC'
      $q$;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'raffles.created_at: conversão para timestamptz ignorada (%).', SQLERRM;
    END;
  END IF;

  EXECUTE $q$
    ALTER TABLE raffles
    ADD CONSTRAINT raffles_status_check
    CHECK (status IN ('active', 'sold_out', 'finished', 'canceled'))
    NOT VALID
  $q$;
EXCEPTION
  WHEN duplicate_object THEN
    NULL;
END $r$;

-- Valida linhas existentes (idempotente se o CHECK já estiver validado)
DO $v$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_constraint c
    JOIN pg_class t ON c.conrelid = t.oid
    JOIN pg_namespace n ON t.relnamespace = n.oid
    WHERE n.nspname = 'public' AND t.relname = 'raffles' AND c.conname = 'raffles_status_check'
      AND NOT c.convalidated
  ) THEN
    EXECUTE 'ALTER TABLE raffles VALIDATE CONSTRAINT raffles_status_check';
  END IF;
END $v$;

COMMIT;
