# Migrações PostgreSQL (Railway / Neon)

## Base nova

Não precisa deste ficheiro. Use `schema.sql` na raiz ou deixe a API correr com `create_all` no arranque.

## Railway — atualizar de esquema legado

1. No painel Railway, abra o serviço **Postgres** → **Variables** ou **Connect** e copie a **URL pública** (TCP proxy), no formato `postgresql://user:pass@host:port/railway`.
2. Garanta `?sslmode=require` no fim da URL se o cliente pedir SSL.
3. A partir da máquina local (com `psql` instalado):

```bash
export DATABASE_PUBLIC_URL='postgresql://...?sslmode=require'
psql "$DATABASE_PUBLIC_URL" -v ON_ERROR_STOP=1 -f migrations/railway_legacy_to_current.sql
```

4. Faça **deploy** da versão da API que usa SQLAlchemy / novo modelo.

O script `railway_legacy_to_current.sql` é **idempotente** para a maior parte dos passos: pode voltar a correr após corrigir dados, mas **faça backup** (snapshot Railway ou `pg_dump`) antes da primeira aplicação em produção.

### O que o script altera

| Área | Alteração |
|------|-----------|
| `users` | `name`→`full_name`, `hashed_password`→`password_hash`, `wallet_balance`→`balance`, `role`→`is_admin` |
| `raffles` | `total_numbers`→`total_tickets`, `price_per_number`→`ticket_price`, adiciona `total_price`, `image_url`, `video_id`, mapeia `status`, remove `winner_ticket_id`, novo `CHECK` em status |
| Tipos | `balance`, `ticket_price`, `total_price` em `NUMERIC(12,2)`; `created_at` para `timestamptz` quando aplicável |

Se a sua base nunca teve o esquema legado (apenas tabelas criadas pela app recente), **não** precisa deste script.
