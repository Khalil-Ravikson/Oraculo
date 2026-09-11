"""
src/application/tasks/beat_checkpoint_gc.py
===========================================
Higiene dos checkpoints do LangGraph (item B10 do plano de recuperação).

O problema: o `thread_id` é fixo por sessão (`entrypoint._thread_config`), e o
`AsyncRedisSaver` mantém o checkpoint indefinidamente — inclusive depois de
chegar em `__end__`. Toda pessoa que já falou com o bot deixa estado em Redis
para sempre. Não é urgente (os funis saem do v1), mas é crescimento sem teto
numa instância que também guarda o índice do RAG, e memória cheia no Redis
Stack é o tipo de incidente que derruba o bot inteiro.

**Esta task nunca apaga uma chave.** Ela só coloca um TTL nas chaves de
checkpoint que ainda não têm um, e deixa o próprio Redis expirar. Duas razões:

1. `DEL` num checkpoint de uma conversa em andamento cortaria o usuário no
   meio de um passo. Um TTL generoso não faz isso — a chave só morre depois
   de a janela inteira passar sem escrita.
2. Um `SCAN` seguido de `DEL` num prefixo é exatamente a forma do incidente
   já registrado na ADR 0007 (um "limpar cache" que apagou os índices
   RediSearch e os chunks do RAG, que não se reconstroem sozinhos).

**Desligada por padrão** (`CHECKPOINT_TTL_HORAS=0`). Ligar é decisão de
operação, com um valor folgado o bastante para nenhuma conversa real ser
atingida.
"""
from __future__ import annotations

import logging

from src.infrastructure.celery_app import celery_app

logger = logging.getLogger(__name__)

# Prefixos que o `langgraph-checkpoint-redis` usa. Deliberadamente explícitos
# em vez de um `checkpoint*` genérico: um glob largo demais é o que transforma
# uma limpeza em incidente.
_PREFIXOS = ("checkpoint:", "checkpoint_blob:", "checkpoint_write:")

# Lote do SCAN. Pequeno de propósito: esta task divide o Redis com o caminho
# quente do RAG, e não há pressa nenhuma em terminar.
_SCAN_COUNT = 200


@celery_app.task(
    name="beat_checkpoint_gc",
    bind=True,
    queue="default",
)
def beat_checkpoint_gc(self) -> dict:
    """Aplica TTL nos checkpoints que ainda não têm um.

    Devolve um resumo para o log do beat: quantas chaves foram vistas e em
    quantas o TTL foi aplicado. Idempotente — rodar duas vezes seguidas não
    muda nada, porque a segunda passada não encontra chave sem TTL."""
    from src.infrastructure.settings import settings

    horas = int(getattr(settings, "CHECKPOINT_TTL_HORAS", 0) or 0)
    if horas <= 0:
        logger.debug("🧹 [CHECKPOINT_GC] desligado (CHECKPOINT_TTL_HORAS=0)")
        return {"status": "desligado", "vistas": 0, "marcadas": 0}

    ttl = horas * 3600
    vistas = 0
    marcadas = 0

    try:
        from src.infrastructure.redis_client import get_redis_text

        r = get_redis_text()
        for prefixo in _PREFIXOS:
            cursor = 0
            while True:
                cursor, chaves = r.scan(cursor=cursor, match=f"{prefixo}*", count=_SCAN_COUNT)
                for chave in chaves:
                    vistas += 1
                    # -1 = existe e não tem TTL. -2 = já não existe (expirou
                    # entre o SCAN e agora). Só o primeiro caso nos interessa.
                    if r.ttl(chave) == -1:
                        r.expire(chave, ttl)
                        marcadas += 1
                if cursor == 0:
                    break
    except Exception:  # noqa: BLE001 — higiene não pode derrubar o beat
        logger.exception("⚠️  [CHECKPOINT_GC] falhou")
        return {"status": "erro", "vistas": vistas, "marcadas": marcadas}

    logger.info(
        "🧹 [CHECKPOINT_GC] %d chaves vistas, %d com TTL de %dh aplicado",
        vistas, marcadas, horas,
    )
    return {"status": "ok", "vistas": vistas, "marcadas": marcadas}
