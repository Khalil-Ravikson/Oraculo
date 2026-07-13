"""
src/agents/academic_knowledge/memory_summarizer.py
=====================================================
Ex `_summarize()` de `application/workers/worker_memory_manager.py` (Fase 4 do
PLANO_REFATORACAO_SUPERVISOR.md, seção 2.4). Extraído como função reutilizável
fora do worker — comportamento idêntico, só relocado.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


async def summarize(session_id: str) -> dict:
    """Resume o histórico quando excede o budget de tokens."""
    try:
        from src.infrastructure.redis_client import get_redis_text
        from src.infrastructure.settings import settings
        import google.genai as genai
        from google.genai import types

        r   = get_redis_text()
        raw = r.lrange(f"chat:{session_id}", 0, -1)
        if len(raw) < 10:
            return {"status": "ok", "action": "noop", "reason": "historico pequeno"}

        turns = []
        for item in raw:
            d = json.loads(item)
            p = "Aluno" if d["role"] == "user" else "Bot"
            turns.append(f"{p}: {d['content'][:200]}")
        conversa = "\n".join(turns)

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        resp   = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"Resuma esta conversa em 3-5 bullet points preservando fatos importantes:\n{conversa}",
            config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=200),
        )
        summary = (resp.text or "").strip()

        # Substitui histórico pelo resumo
        summary_entry = json.dumps(
            {"role": "assistant", "content": f"[RESUMO ANTERIOR]\n{summary}"},
            ensure_ascii=False
        )
        r.delete(f"chat:{session_id}")
        r.rpush(f"chat:{session_id}", summary_entry)
        r.expire(f"chat:{session_id}", 1800)

        return {"status": "ok", "action": "summarized", "summary": summary}
    except Exception as e:
        return {"status": "error", "error": str(e)[:200]}
