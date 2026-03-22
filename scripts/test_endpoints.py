#!/usr/bin/env python3
"""
Testa os endpoints principais da API (localhost:8000).

Uso:
  python scripts/test_endpoints.py

Requer: API a correr e dados seed (python scripts/seed_test_data.py).
"""

from __future__ import annotations

import sys
import uuid

import httpx

BASE = "http://localhost:8000"


def log(msg: str) -> None:
    print(f"  {msg}")


def main() -> int:
    print("=== Teste dos endpoints Apex Keys API ===\n")

    with httpx.Client(timeout=30.0) as client:
        # 1. Health
        log("GET /health")
        r = client.get(f"{BASE}/health")
        r.raise_for_status()
        log(f"  OK: {r.json()}\n")

        # 2. Login admin
        log("POST /auth/login (admin)")
        r = client.post(
            f"{BASE}/auth/login",
            json={"email": "admin@apexkeys.example.com", "password": "senha12345"},
        )
        r.raise_for_status()
        admin_token = r.json()["access_token"]
        log(f"  Token obtido\n")

        # 3. Login user
        log("POST /auth/login (user)")
        r = client.post(
            f"{BASE}/auth/login",
            json={"email": "user@apexkeys.example.com", "password": "senha12345"},
        )
        r.raise_for_status()
        user_token = r.json()["access_token"]
        log(f"  Token obtido\n")

        # 4. GET /auth/me (user) para obter user_id
        log("GET /auth/me (user)")
        r = client.get(
            f"{BASE}/auth/me",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        r.raise_for_status()
        me = r.json()
        user_id = me["id"]
        log(f"  user_id={user_id}, balance={me['balance']}\n")

        # 5. Admin ajusta saldo do user (entry manual)
        log("POST /admin/users/{id}/adjust-balance (+50.00)")
        r = client.post(
            f"{BASE}/admin/users/{user_id}/adjust-balance",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "amount": "50.00",
                "description": "Crédito inicial para testes",
            },
        )
        r.raise_for_status()
        adj = r.json()
        log(f"  previous={adj['previous_balance']}, new={adj['new_balance']}, amount={adj['amount_adjusted']}\n")

        # 6. User consulta saldo
        log("GET /wallet/balance (user)")
        r = client.get(
            f"{BASE}/wallet/balance",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        r.raise_for_status()
        bal = r.json()
        log(f"  balance={bal['balance']}\n")

        # 7. User lista transações
        log("GET /wallet/transactions (user)")
        r = client.get(
            f"{BASE}/wallet/transactions",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        r.raise_for_status()
        txs = r.json()
        log(f"  {len(txs)} transação(ões): {[(t['type'], t['amount'], t['status']) for t in txs]}\n")

        # 8. Admin cria rifa
        log("POST /admin/raffles")
        r = client.post(
            f"{BASE}/admin/raffles",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "title": "Rifa Teste — Jogo X",
                "image_url": "https://example.com/img.jpg",
                "video_id": "dQw4w9WgXcQ",
                "total_price": "99.99",
                "total_tickets": 10,
            },
        )
        r.raise_for_status()
        raffle = r.json()
        raffle_id = raffle["id"]
        log(f"  raffle_id={raffle_id}, ticket_price={raffle['ticket_price']}, status={raffle['status']}\n")

        # 9. Listar rifas
        log("GET /raffles")
        r = client.get(f"{BASE}/raffles")
        r.raise_for_status()
        raffles = r.json()
        log(f"  {len(raffles)} rifa(s)\n")

        # 10. Mock Pix: user cria intent pendente
        gateway_ref = f"mock-{uuid.uuid4().hex[:12]}"
        log("POST /wallet/mock-pix-intent (user)")
        r = client.post(
            f"{BASE}/wallet/mock-pix-intent",
            headers={"Authorization": f"Bearer {user_token}"},
            json={
                "amount": "25.50",
                "gateway_reference": gateway_ref,
            },
        )
        r.raise_for_status()
        resp = r.json()
        gateway_ref = resp["mock_pix"]["gateway_reference"]
        log(f"  {resp}\n")

        # 11. Webhook aprova o Pix
        log("POST /webhook/mp (status=approved)")
        r = client.post(
            f"{BASE}/webhook/mp",
            json={
                "gateway_reference": gateway_ref,
                "status": "approved",
            },
        )
        r.raise_for_status()
        wh = r.json()
        log(f"  amount_credited={wh['amount_credited']}, new_balance={wh['new_balance']}\n")

        # 12. User consulta saldo novamente
        log("GET /wallet/balance (user) — após Pix")
        r = client.get(
            f"{BASE}/wallet/balance",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        r.raise_for_status()
        bal2 = r.json()
        log(f"  balance={bal2['balance']}\n")

    print("=== Todos os testes passaram ===\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except httpx.HTTPStatusError as e:
        msg = f"\nErro HTTP {e.response.status_code}: {e.response.text}"
        if e.response.status_code == 500 and "auth/login" in str(e.request.url):
            msg += (
                "\n  Dica: confirma bcrypt (pip install bcrypt), "
                "DATABASE_SSL_NO_VERIFY=true no .env (Railway) e que a API arrancou sem erros."
            )
        print(msg, file=sys.stderr)
        sys.exit(1)
    except httpx.ConnectError as e:
        print(f"\nFalha de conexão. A API está a correr em {BASE}?", file=sys.stderr)
        print(f"  {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\nErro: {e}", file=sys.stderr)
        sys.exit(1)
