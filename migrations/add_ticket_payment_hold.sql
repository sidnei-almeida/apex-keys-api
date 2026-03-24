-- Reservas de números (pending_payment) + ligação a pagamento rifa (MP / manual)
BEGIN;

ALTER TABLE tickets ADD COLUMN IF NOT EXISTS payment_hold_id UUID NULL;
CREATE INDEX IF NOT EXISTS ix_tickets_payment_hold_id ON tickets (payment_hold_id);

ALTER TABLE transactions ADD COLUMN IF NOT EXISTS payment_hold_id UUID NULL;
CREATE INDEX IF NOT EXISTS ix_transactions_payment_hold_id ON transactions (payment_hold_id);

COMMIT;
