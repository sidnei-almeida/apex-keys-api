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

- **`is_admin`** em `GET /auth/me` indica se o utilizador é administrador (útil para mostrar menus). **As rotas `/api/v1/admin/*` validam de novo na base de dados** (`get_current_admin`) — não basta “inventar” admin no cliente.

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
| `GET` | `/api/v1/admin/raffles/{raffle_id}` | **Admin** | Uma rifa (formulário de edição) |
| `PUT` | `/api/v1/admin/raffles/{raffle_id}` | **Admin** | Actualização parcial (`RaffleUpdate`); recalcula `ticket_price` se preço ou quantidade forem enviados |
| `POST` | `/api/v1/admin/raffles` | **Admin** | Cria rifa (preço por bilhete calculado no servidor) |
| `POST` | `/api/v1/admin/raffles/{raffle_id}/cancel` | **Admin** | Cancela rifa ativa e estorna bilhetes pagos |
| `DELETE` | `/api/v1/admin/raffles/{raffle_id}` | **Admin** | Apaga rifa e bilhetes na BD; **409** se existirem bilhetes pagos e a rifa não estiver `canceled` (cancelar antes) |
| `PATCH` | `/api/v1/admin/raffles/{raffle_id}/image` | **Admin** | Actualiza só `image_url` (URL da capa); corpo: `{ "image_url": "..." }` |
| `PATCH` | `/api/v1/admin/raffles/{raffle_id}/video` | **Admin** | Actualiza só `video_id`; corpo: `{ "youtube_url": "..." }` — aceita URL ou ID, grava o ID |
| `POST` | `/api/v1/admin/users/{user_id}/adjust-balance` | **Admin** | Crédito ou débito manual de saldo |
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

## Admin (`/api/v1/admin`) — JWT de utilizador **admin**

Todas exigem `Authorization: Bearer <token>` de um utilizador com `is_admin=true` na base de dados.

### `GET /api/v1/admin/raffles/{raffle_id}`

**Resposta `200`:** `RafflePublic` (mesmo formato que na listagem pública).

**Erro:** `404` se o id não existir.

### `PUT /api/v1/admin/raffles/{raffle_id}`

**Corpo (`RaffleUpdate`)** — todos os campos opcionais; envia só o que queres alterar.

| Campo | Tipo | Notas |
|-------|------|--------|
| `title` | string \| omitido | 1–255 caracteres se presente |
| `image_url` | string \| null \| omitido | |
| `video_id` | string \| null \| omitido | ID YouTube |
| `total_price` | decimal > 0 \| omitido | Se presente (sozinho ou com `total_tickets`), participa no recálculo |
| `total_tickets` | inteiro > 0 \| omitido | Idem |

**Recálculo de `ticket_price`:** se o JSON incluir **`total_price` e/ou `total_tickets`**, o servidor usa os valores finais (mesclados com os actuais) e define  
`ticket_price = round_half_up(total_price / total_tickets, 2 casas)` (Decimal, mesma função que na criação).

**Restrições:** `400` se a rifa estiver **cancelada** e o pedido tentar alterar preço ou quantidade; `400` se `total_tickets` ficar **inferior** ao maior número de bilhete já vendido (`paid`).

**Resposta `200`:** `RafflePublic` actualizado. Corpo `{}` não altera nada (no-op).

### `POST /api/v1/admin/raffles`

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

### `POST /api/v1/admin/raffles/{raffle_id}/cancel`

Só rifas em estado `active`. Estorna bilhetes pagos e marca a rifa como `canceled`.

**Resposta `200` — `RaffleCancelResponse`:** `raffle_id`, `status: "canceled"`, `refunds_issued` (quantidade de bilhetes estornados).

### `DELETE /api/v1/admin/raffles/{raffle_id}`

Remove a rifa e **todos** os registos de bilhetes (`tickets`) dessa rifa.

**Regra:** se existirem bilhetes com `status` pago (`paid`) e a rifa **não** estiver `canceled`, a API responde **409** — é necessário chamar antes `POST .../cancel` para estornar compradores. Rifas sem vendas ou já `canceled` podem ser apagadas.

**Resposta `200` — `RaffleDeleteResponse`:** `raffle_id`, `tickets_removed` (quantos bilhetes foram apagados).

### `PATCH /api/v1/admin/raffles/{raffle_id}/image`

Actualiza só o campo `image_url` da rifa (URL da capa, ex.: 1080p).

**Corpo (`RaffleImagePatch`):** `{ "image_url": "https://..." }` ou `{ "image_url": null }` para limpar.

**Resposta `200`:** `RafflePublic`.

### `PATCH /api/v1/admin/raffles/{raffle_id}/video`

Actualiza só o campo `video_id` da rifa. Aceita URL completa do YouTube (watch?v=, youtu.be/, embed/) ou só o ID; grava o video_id (11 chars) na BD.

**Corpo (`RaffleVideoPatch`):** `{ "youtube_url": "https://www.youtube.com/watch?v=xxx" }` ou `{ "youtube_url": null }` para limpar.

**Resposta `200`:** `RafflePublic`. **Erro `400`:** URL/ID do YouTube inválido.

### `POST /api/v1/admin/users/{user_id}/adjust-balance`

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
