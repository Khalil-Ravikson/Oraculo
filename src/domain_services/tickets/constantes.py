"""
src/domain_services/tickets/constantes.py
================================
Constantes de domínio do chamado de suporte, compartilhadas.

Extraídas de `ticket_flow.py` em 2026-09-09 (item A8d do plano de
recuperação). Aquele módulo era a máquina de estados do funil antigo,
chamada pelo `dispatcher.py` — o dispatcher foi deletado pela ADR 0008 e o
funil virou uma sequência de nós travados do grafo
(`orchestration/nodes.py`, `ticket_ask_*`), um `interrupt()` por nó.

Do módulo antigo, a única coisa que os nós ainda importavam era esta lista.
Ela vive aqui para que o resto — 400 linhas de funil que ninguém executa —
pudesse sair sem levar junto um dado de negócio real.

O funil de chamado está **fora do v1** (bot de menu): quem precisa de
atendimento humano usa a opção "falar com um atendente". Ver
`docs/ESTADO_ATUAL.md`.
"""
from __future__ import annotations

# Categorias oferecidas ao usuário na abertura de um chamado. A ordem é a
# ordem de exibição, e o `id` é o que ele digita — mudar um `id` invalida
# rascunhos em andamento no Redis.
SEED_CATEGORIAS: list[dict] = [
    {"id": 1, "nome": "Rede e Conectividade (Wi-Fi, cabo, VPN)"},
    {"id": 2, "nome": "Hardware (computador, periférico, impressora)"},
    {"id": 3, "nome": "Software e Sistemas (SIGAA, e-mail institucional)"},
    {"id": 4, "nome": "Acesso e Conta (senha, permissão, login)"},
    {"id": 5, "nome": "Telefonia"},
    {"id": 6, "nome": "Infraestrutura predial (elétrica, mobiliário)"},
    {"id": 7, "nome": "Outros"},
]

CATEGORIA_POR_ID: dict[int, str] = {c["id"]: c["nome"] for c in SEED_CATEGORIAS}
