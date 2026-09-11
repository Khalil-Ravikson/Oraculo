"""Motor de menu do v1 — o bot de menu (ver docs/ESTADO_ATUAL.md §1).

O menu é dado (`spec.py` + `menus/default.json`), a posição do usuário vive
no Redis (`state.py`) e a decisão é uma função pura (`resolver.py`). Nenhuma
chamada de LLM em nenhum dos três.
"""
