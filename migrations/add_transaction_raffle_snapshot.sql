-- Snapshot da rifa/números em transações raffle_payment (auditoria após cancelar)
BEGIN;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS raffle_checkout_snapshot JSONB NULL;
COMMIT;
