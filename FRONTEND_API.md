# Integração frontend — Apex Keys API

Guia para o time de frontend: **base URL local**, rotas, autenticação, formatos de corpo/resposta e códigos HTTP. O contrato canónico continua a ser **`/docs`** (Swagger) e **`/openapi.json`** no mesmo host.

---

## URL base (desenvolvimento local)

Prefixo oficial em local (com barra final na origem):

```text
http://127.0.0.1:8000/
```

Os caminhos da API **não** levam barra extra antes do primeiro segmento (ex.: `http://127.0.0.1:8000/auth/login`, não `//auth/login`).

Exemplos:

- Documentação interativa: `http://127.0.0.1:8000/docs`
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`
- Health: `http://127.0.0.1:8000/health`
- Login: `POST http://127.0.0.1:8000/auth/login`

Em **produção**, substitua pelo domínio real da API (variável de ambiente no frontend, ex. `VITE_API_URL` / `NEXT_PUBLIC_API_URL`).

---

## Convenções gerais

| Tópico | Detalhe |
|--------|---------|
| **JSON** | Pedidos com corpo: `Content-Type: application/json` |
| **UTF-8** | Textos e JSON em UTF-8 |
| **JWT** | Rotas “usuário” ou “admin”: cabeçalho `Authorization: Bearer <access_token>` |
| **Decimais** | Campos monetários (`balance`, `amount`, `ticket_price`, …) vêm em JSON muitas vezes como **string** (ex.: `"99.99"`) por causa de `Decimal` no backend — tratar como número no UI com parsing seguro |
| **Datas** | ISO 8601 com timezone quando aplicável (ex.: `created_at`) |
| **CORS** | Origens permitidas vêm de `CORS_ORIGINS` no servidor; em local, inclua `http://localhost:3000` ou a origem do teu dev server |

### Erros

- **`4xx` / `5xx`:** em geral `{ "detail": "mensagem" }` ou, em **`422`**, `{ "detail": "...", "errors": [ ... ] }` (validação Pydantic).
- **`500`:** mensagem genérica ao cliente; detalhes só nos logs do servidor.

### Papéis

- **`is_admin`** em `GET /auth/me` indica se o utilizador é administrador (útil para mostrar menus). **As rotas `/admin/*` validam de novo na base de dados** — não basta “inventar” admin no cliente.

---

## Mapa rápido de endpoints

| Método | Caminho | Auth | Descrição |
|--------|---------|------|-----------|
| `GET` | `/health` | — | Liveness (`{ "status": "ok" }`) |
| `POST` | `/auth/signup` | — | Registo de utilizador |
| `POST` | `/auth/login` | — | Login; devolve JWT |
| `GET` | `/auth/me` | Utilizador | Perfil + saldo + `is_admin` |
| `GET` | `/wallet/balance` | Utilizador | Saldo atual |
| `GET` | `/wallet/transactions` | Utilizador | Até 200 movimentos (mais recentes primeiro) |
| `POST` | `/wallet/mock-pix-intent` | Utilizador | Cria depósito Pix **mock** pendente + dados de QR fictícios |
| `GET` | `/raffles` | — | Lista rifas; query opcional `?status=active\|sold_out\|finished\|canceled` |
| `POST` | `/buy-ticket` | Utilizador | Compra um número de bilhete (saldo debitado na hora) |
| `POST` | `/admin/raffles` | **Admin** | Cria rifa (preço por bilhete calculado no servidor) |
| `POST` | `/admin/raffles/{raffle_id}/cancel` | **Admin** | Cancela rifa ativa e estorna bilhetes pagos |
| `POST` | `/admin/users/{user_id}/adjust-balance` | **Admin** | Crédito ou débito manual de saldo |
| `POST` | `/webhook/mp` | — | Mock de webhook (normalmente **backend**; ver nota abaixo) |
| `POST` | `/igdb/game` | — | Metadados de jogo a partir da URL pública do IGDB |

---

## Autenticação (`/auth`)

### `POST /auth/signup` — registo

**Corpo**

| Campo | Tipo | Regras |
|-------|------|--------|
| `full_name` | string | 1–255 caracteres |
| `email` | string | E-mail válido |
| `password` | string | 8–128 caracteres |
| `whatsapp` | string | 10–20 dígitos, opcional `+` no início |

**Resposta `201` — utilizador público** (`UserPublic`): `id`, `full_name`, `email`, `whatsapp`, `is_admin`, `balance`, `created_at`.

**Conflitos:** `409` se e-mail ou WhatsApp já existirem.

### `POST /auth/login` — sessão

**Corpo:** `email`, `password`.

**Resposta `200` — `TokenResponse`**

| Campo | Tipo |
|-------|------|
| `access_token` | string (JWT) |
| `token_type` | `"bearer"` |

Guardar `access_token` e enviar em `Authorization: Bearer ...` nas rotas protegidas.

### `GET /auth/me` — perfil

**Resposta `200` — `UserPublic`** (mesmos campos que após signup). Útil para dashboard, saldo e saber se o utilizador é admin.

---

## Carteira (`/wallet`)

### `GET /wallet/balance`

**Resposta `200`:** `{ "balance": "<decimal-as-string>" }`

### `GET /wallet/transactions`

Lista ordenada do mais recente para o mais antigo (máx. 200).

**Cada item (`TransactionOut`):** `id`, `amount`, `type`, `status`, `gateway_reference`, `description`, `created_at`.

**`type`:** `pix_deposit` \| `purchase` \| `refund` \| `admin_adjustment`  
**`status`:** `pending` \| `completed` \| `failed`

### `POST /wallet/mock-pix-intent` — apenas desenvolvimento / testes

Simula a criação de um Pix pendente. Gera uma linha em `transactions` com `type=pix_deposit`, `status=pending` e o `gateway_reference` que enviares (deve ser **único** por tentativa).

**Corpo (`PixDepositCreate`)**

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `amount` | string/number | Valor > 0 (créditos na mesma unidade do saldo) |
| `gateway_reference` | string | 1–255 caracteres, único globalmente na tabela |

**Resposta `201`:** objeto com `message` e `mock_pix` (inclui `gateway_reference`, `amount_brl`, `emv_payload` fictício, etc.).

**Conflito:** `409` se `gateway_reference` já existir.

---

## Rifas e compra (sem prefixo `/wallet`)

### `GET /raffles`

**Query opcional:** `status` = `active` | `sold_out` | `finished` | `canceled` (case-insensitive no servidor).

**Resposta `200`:** lista de `RafflePublic`: `id`, `title`, `image_url`, `video_id` (ex.: ID YouTube), `total_price`, `total_tickets`, `ticket_price`, `status`, `created_at`.

### `POST /buy-ticket`

**Corpo (`TicketPurchaseRequest`)**

| Campo | Tipo |
|-------|------|
| `raffle_id` | UUID |
| `ticket_number` | inteiro ≥ 1 |

**Resposta `200` — `TicketPurchaseResponse`:** `ticket_id`, `raffle_id`, `ticket_number`, `amount_charged`, `new_balance`.

**Erros frequentes:** `404` rifa inexistente; `400` rifa não ativa ou número fora da faixa; `402` saldo insuficiente; `409` número já vendido ou corrida de concorrência.

---

## Admin (`/admin`) — JWT de utilizador **admin**

Todas exigem `Authorization: Bearer <token>` de um utilizador com `is_admin=true` na base de dados.

### `POST /admin/raffles`

**Corpo (`AdminRaffleCreate`)**

| Campo | Tipo |
|-------|------|
| `title` | string (obrigatório) |
| `image_url` | string \| null |
| `video_id` | string \| null (ID do vídeo YouTube) |
| `total_price` | > 0 |
| `total_tickets` | inteiro > 0 |

O servidor calcula `ticket_price` = `total_price / total_tickets` arredondado a **2 casas** (half-up).

**Resposta `201`:** `RafflePublic`.

### `POST /admin/raffles/{raffle_id}/cancel`

Só rifas em estado `active`. Estorna bilhetes pagos e marca a rifa como `canceled`.

**Resposta `200` — `RaffleCancelResponse`:** `raffle_id`, `status: "canceled"`, `refunds_issued` (quantidade de bilhetes estornados).

### `POST /admin/users/{user_id}/adjust-balance`

**Corpo (`AdminWalletAdjust`)**

| Campo | Tipo |
|-------|------|
| `amount` | positivo = crédito, negativo = débito |
| `description` | string opcional (máx. 500) |

**Resposta `200` — `AdminWalletAdjustResponse`:** `user_id`, `previous_balance`, `new_balance`, `amount_adjusted`.

**Erro:** `400` se o saldo resultante ficaria negativo.

---

## Webhook mock — `POST /webhook/mp`

Confirma um `pix_deposit` **pendente** identificado por `gateway_reference`. Em produção real isto seria chamado pelo **gateway** (Mercado Pago, etc.), não pelo browser.

**Corpo (`MercadoPagoWebhookPayload`)**

| Campo | Tipo | Notas |
|-------|------|--------|
| `gateway_reference` | string | Igual ao usado em `mock-pix-intent` |
| `status` | string | Por defeito `approved`; também `pending`, `rejected`, `cancelled` (normalizado para minúsculas) |

**Resposta `200` — `WebhookProcessResponse`:** `transaction_id`, `user_id`, `amount_credited`, `new_balance`.

**Fluxo típico em dev:** (1) utilizador chama `mock-pix-intent`; (2) o **mesmo** `gateway_reference` devolvido/confirmado é enviado para `/webhook/mp` com `status: approved` — pode ser um pequeno serviço local ou o próprio app de testes, não necessariamente a SPA.

**Erros:** `404` referência desconhecida; `409` transação já marcada como falha; reenvio idempotente quando já `completed` (devolve saldo actual).

---

## IGDB — `POST /igdb/game` (sem login Apex)

**Autenticação Apex Keys:** nenhuma.

O utilizador cola a **URL completa** da ficha no IGDB. O servidor valida domínio e caminho (`/games/<slug>`) para mitigar **SSRF**.

**Corpo — `IgdbGameUrlRequest`**

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `url` | string | Ex.: `https://www.igdb.com/games/resident-evil-requiem` |

**Resposta `200` — `IgdbGameInfoResponse`**

| Campo | Tipo |
|-------|------|
| `slug` | string |
| `name` | string \| null |
| `title` | string \| null |
| `summary` | string \| null |
| `image_url` | string \| null |
| `youtube_url` | string \| null |
| `igdb_url` | string |
| `igdb_game_id` | string \| null |
| `genres` | string[] |
| `series` | string[] |
| `game_modes` | string[] |
| `player_perspectives` | string[] |

**Erros:** `400` URL inválida; `404` jogo / dados não extraíveis; `503` bloqueio Cloudflare; `502` rede.

**Exemplo**

```http
POST http://127.0.0.1:8000/igdb/game
Content-Type: application/json

{
  "url": "https://www.igdb.com/games/resident-evil-requiem"
}
```

---

## Recursos no repositório

- [`README.md`](README.md) — visão geral, stack, deploy, scripts de BD.
- **OpenAPI** em `http://127.0.0.1:8000/openapi.json` quando a API está a correr.

---

*Última actualização alinhada à API com SQLAlchemy, carteira, rifas, admin (ajuste de saldo), mock Pix/webhook e scrape IGDB.*
