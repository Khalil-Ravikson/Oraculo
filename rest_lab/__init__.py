"""
rest_lab/
===========
Laboratório de estudo de API REST (branch/worktree `research/rest-mcp-estudos`,
Oraculo-rest-mcp). Objetivo: aprender a fazer um "agente" trabalhar de fato
com GET/POST/PUT/DELETE contra APIs públicas reais (não mockadas), como
prova de capacidade técnica para o Oráculo (CETIC/UEMA) — não é feature de
produto e não tem integração com `src/` do núcleo.

Escopo desta rodada: roteamento determinístico (regex -> tool), sem LLM
decidindo a chamada — ver `router.py`. Teste só via CLI local (`run_test.py`),
sem WhatsApp/Celery/dispatcher.

APIs usadas (ver `clients.py`):
  - JSONPlaceholder (https://jsonplaceholder.typicode.com) — CRUD fake,
    POST/PUT/DELETE não persistem de verdade no servidor (respondem 200/201
    com eco do payload, mas não gravam nada — comportamento documentado da
    própria API, não é bug daqui).
  - DummyJSON (https://dummyjson.com) — listas maiores com paginação real
    (produtos), útil pra testar formatação de mensagem longa.
  - httpbin (https://httpbin.org) — debug puro de request/response HTTP
    (status codes, echo de headers/params).
"""
