-- Referência alinhada a app/models.py (fonte canónica em runtime).
-- A API cria tabelas com SQLAlchemy create_all em init_db; mantém este ficheiro para
-- revisão manual, Neon/Railway ou ferramentas que esperem SQL estático.
-- Neon: extensão uuid opcional; SQLAlchemy usa uuid.UUID em Python.

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY,
    full_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    whatsapp VARCHAR(20) NOT NULL UNIQUE,
    pix_key VARCHAR(140),
    avatar_url VARCHAR(1024),
    balance NUMERIC(12, 2) NOT NULL DEFAULT 0,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_users_email ON users (email);

CREATE TABLE IF NOT EXISTS raffles (
    id UUID PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    image_url VARCHAR(1024),
    video_id VARCHAR(64),
    total_price NUMERIC(12, 2) NOT NULL,
    total_tickets INTEGER NOT NULL,
    ticket_price NUMERIC(12, 2) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    featured_tier VARCHAR(20) DEFAULT 'none',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tickets (
    id UUID PRIMARY KEY,
    raffle_id UUID NOT NULL REFERENCES raffles (id),
    user_id UUID NOT NULL REFERENCES users (id),
    ticket_number INTEGER NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'paid',
    payment_hold_id UUID,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_ticket_raffle_number UNIQUE (raffle_id, ticket_number)
);

CREATE INDEX IF NOT EXISTS ix_tickets_raffle_id ON tickets (raffle_id);
CREATE INDEX IF NOT EXISTS ix_tickets_user_id ON tickets (user_id);
CREATE INDEX IF NOT EXISTS ix_tickets_payment_hold_id ON tickets (payment_hold_id);

CREATE TABLE IF NOT EXISTS transactions (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users (id),
    amount NUMERIC(12, 2) NOT NULL,
    type VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    gateway_reference VARCHAR(255),
    description TEXT,
    payment_hold_id UUID,
    raffle_checkout_snapshot JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_transactions_user_id ON transactions (user_id);
CREATE INDEX IF NOT EXISTS ix_transactions_payment_hold_id ON transactions (payment_hold_id);

CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users (id),
    type VARCHAR(50) NOT NULL,
    title VARCHAR(255) NOT NULL,
    body TEXT NOT NULL,
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_notifications_user_id ON notifications (user_id);
