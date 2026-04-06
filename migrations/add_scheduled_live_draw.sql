-- Sorteio ao vivo: quando a rifa esgota (100% pagos), agenda draw automático +10 min.
ALTER TABLE raffles
  ADD COLUMN IF NOT EXISTS scheduled_live_draw_at TIMESTAMPTZ NULL;

COMMENT ON COLUMN raffles.scheduled_live_draw_at IS 'UTC: após esgotar bilhetes pagos, hora em que o sorteio aleatório pode executar (anúncio + countdown no site).';
