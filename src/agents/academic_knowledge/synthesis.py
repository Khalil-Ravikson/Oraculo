"""
src/agents/academic_knowledge/synthesis.py
=============================================
Síntese final via LLM — consolidação de DUAS implementações paralelas que
existiam antes da Fase 4 do PLANO_REFATORACAO_SUPERVISOR.md (seção 2.4):

1. `application/workers/worker_synthesis.py::_sintetizar_async` — a que
   REALMENTE roda em produção (worker Celery `synthesis`, autodescoberto).
   Prompt mais completo (contexto_tarefa_anterior, datetime, histórico),
   client `google.genai` direto.
2. `infrastructure/services/synthesis_service.py::SynthesisService` — service
   "puro" bem desenhado (retorna `SynthesisResult` com custo/tokens/latência,
   checagem de overrides administrativos via Redis: `admin:gemini_blocked` e
   `admin:system_prompt`), mas usado só por código órfão já deletado na
   Fase 1 (`application/pipeline/workers.py`) — ou seja, o "kill switch" e o
   override de prompt do painel admin NÃO tinham efeito nenhum no tráfego
   real do WhatsApp antes desta fase.

MUDANÇA DE COMPORTAMENTO DELIBERADA (não é só relocação): esta classe junta
o prompt/contexto mais rico do worker vivo com as checagens administrativas
do service dormente. Resultado prático: `admin:gemini_blocked` e
`admin:system_prompt`, configuráveis pelo hub admin, passam a valer de fato
para as respostas do WhatsApp. Se isso não for desejado, é fácil reverter
removendo o bloco "overrides administrativos" abaixo.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from src.agents.academic_knowledge.prompts import SYSTEM_SYNTHESIS

logger = logging.getLogger(__name__)

# Custo Gemini 2.5 Flash (USD por 1M tokens)
_CUSTO_INPUT = 0.075
_CUSTO_OUTPUT = 0.30

_client = None
def _get_client():
    global _client
    if _client is None:
        from src.infrastructure.settings import settings
        import google.genai as genai
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client


@dataclass
class SynthesisResult:
    answer: str
    tokens_in: int = 0
    tokens_out: int = 0
    custo_usd: float = 0.0
    latencia_ms: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


class SynthesisService:
    """
    Gera a resposta final com Gemini a partir de chunks já recuperados.
    Agnóstico à origem da query (RAG clássico ou fallback SIGAA/task_history).
    """

    async def sintetizar(
        self,
        chunks: list[dict],
        plan_ctx: dict,
        max_tokens: int = 512,
    ) -> SynthesisResult:
        t0 = time.monotonic()

        # ── Overrides administrativos (kill switch / prompt override) ────────
        system = SYSTEM_SYNTHESIS
        try:
            from src.infrastructure.redis_client import get_redis_text
            r = get_redis_text()
            if r.get("admin:gemini_blocked") == "1":
                return SynthesisResult(answer="🔧 Sistema em manutenção. Tente em instantes!")

            sp_redis = r.get("admin:system_prompt")
            if sp_redis:
                system = sp_redis if isinstance(sp_redis, str) else sp_redis.decode()
        except Exception:
            pass

        prompt = self._montar_prompt(chunks, plan_ctx)

        # Contexto da última tarefa (Layer 3) injetado na system instruction,
        # igual ao worker original — mantém a prioridade relativa desse
        # contexto na chamada ao LLM.
        task_ctx = plan_ctx.get("task_history", {})
        if task_ctx.get("last_worker"):
            system += (
                f"\n\n<contexto_tarefa_anterior>\n"
                f"Worker: {task_ctx['last_worker']}\n"
                f"Resultado: {task_ctx.get('last_result', '')[:300]}\n"
                f"</contexto_tarefa_anterior>"
            )

        try:
            from src.infrastructure.settings import settings
            from google.genai import types

            client = _get_client()
            response = await client.aio.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=0.2,
                    max_output_tokens=max_tokens,
                ),
            )

            usage = response.usage_metadata
            tokens_in = tokens_out = 0
            if usage:
                tokens_in = usage.prompt_token_count or 0
                tokens_out = usage.candidates_token_count or 0
                session_id = plan_ctx.get("session_id")
                if session_id:
                    from src.infrastructure.redis_client import registrar_tokens_redis
                    registrar_tokens_redis(session_id, tokens_in, tokens_out)

            custo = (
                tokens_in  / 1_000_000 * _CUSTO_INPUT +
                tokens_out / 1_000_000 * _CUSTO_OUTPUT
            )
            answer = (response.text or "").strip()
            ms = int((time.monotonic() - t0) * 1000)

            logger.info(
                "🧠 [SYNTHESIS] %d chars | %d tokens | $%.5f | %dms",
                len(answer), tokens_in + tokens_out, custo, ms,
            )

            return SynthesisResult(
                answer=answer,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                custo_usd=round(custo, 6),
                latencia_ms=ms,
            )

        except Exception as e:
            ms = int((time.monotonic() - t0) * 1000)
            logger.exception("❌ [SYNTHESIS] Gemini falhou: %s", e)
            return SynthesisResult(
                answer="Tive um problema técnico. Tente novamente. 🙏",
                latencia_ms=ms,
                error=str(e)[:200],
            )

    def _montar_prompt(self, chunks: list[dict], plan_ctx: dict) -> str:
        from datetime import datetime

        query      = plan_ctx.get("query", "")
        user_ctx   = plan_ctx.get("user_context", {})
        historico  = plan_ctx.get("history", "")
        fatos      = plan_ctx.get("fatos", [])

        contexto_rag = ""
        for i, chunk in enumerate(chunks, 1):
            source  = chunk.get("label") or chunk.get("source", "")
            content = chunk.get("content", "").strip()
            if content:
                contexto_rag += f"\n[{i}. {source}]\n{content}\n"

        now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
        parts = [f"<datetime>{now_str}</datetime>"]

        if historico:
            parts.append(f"<historico_conversa>\n{historico[-1500:]}\n</historico_conversa>")

        nome  = user_ctx.get("nome", "")
        curso = user_ctx.get("curso", "")
        if nome or curso:
            parts.append(f"<contexto_aluno>Aluno: {nome}"
                         + (f" | Curso: {curso}" if curso else "") + "</contexto_aluno>")

        if fatos:
            parts.append("<perfil>\n" + "\n".join(f"- {f}" for f in fatos[:3]) + "\n</perfil>")

        parts.append(f"<contexto_rag>\n{contexto_rag or 'Nenhuma informação encontrada.'}\n</contexto_rag>")
        parts.append(f"<pergunta>{query}</pergunta>")

        return "\n\n".join(parts)
