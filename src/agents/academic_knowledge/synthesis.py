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

Sprint 2 (Fase 8): a leitura do prompt customizado deixou de ser
`r.get("admin:system_prompt")` cru — passa por
`capabilities/persistence/prompt_config.py::obter_prompt_ativo`, que resolve
em cascata Postgres (`agent_prompts`, versionado) → Redis legado
(`admin:system_prompt`, mantido como fallback de transição) → hardcoded
(`SYSTEM_SYNTHESIS`). `admin:gemini_blocked` continua lido cru — é kill
switch, não prompt, fora de escopo desta fase.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from src.agents.academic_knowledge.prompts import SYSTEM_SYNTHESIS
from src.infrastructure.observability import pricing

logger = logging.getLogger(__name__)


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
        r = None
        try:
            from src.infrastructure.redis_client import get_redis_text
            r = get_redis_text()
            if r.get("admin:gemini_blocked") == "1":
                return SynthesisResult(answer="🔧 Sistema em manutenção. Tente em instantes!")
        except Exception:
            pass

        try:
            from src.capabilities.persistence.prompt_config import obter_prompt_ativo
            from src.infrastructure.database.session import AsyncSessionLocal

            async with AsyncSessionLocal() as session:
                system = await obter_prompt_ativo(
                    session, "academic_knowledge", fallback=SYSTEM_SYNTHESIS, redis=r,
                )
        except Exception as exc:
            logger.warning("⚠️  [SYNTHESIS] Falha ao resolver prompt ativo, usando hardcoded: %s", exc)

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
            from src.infrastructure.adapters.llm_factory import get_llm_provider

            provider = get_llm_provider(agente="academic_knowledge", rota=plan_ctx.get("rota", ""))
            resposta = await provider.gerar_resposta_async(
                prompt=prompt,
                system_instruction=system,
                temperatura=0.2,
                max_tokens=max_tokens,
                # plan_ctx não carrega um identificador de usuário próprio
                # hoje (achado: nenhum caller popula "user_id") — session_id
                # como fallback dá pelo menos atribuição por sessão em vez
                # de deixar a coluna sempre vazia.
                user_id=str(plan_ctx.get("user_id") or plan_ctx.get("session_id", "")),
                rota=plan_ctx.get("rota", ""),
            )

            tokens_in, tokens_out = resposta.input_tokens, resposta.output_tokens
            session_id = plan_ctx.get("session_id")
            if session_id:
                # Mantido em paralelo à telemetria persistente acima — é o
                # cache curto (TTL 1h) que o simulador de avaliação do /hub
                # (`eval_api.py`) consome, propósito diferente (ver
                # analise_custo_real_llm.md §4).
                from src.infrastructure.redis_client import registrar_tokens_redis
                registrar_tokens_redis(session_id, tokens_in, tokens_out)

            custo = pricing.calcular_custo_usd(provider.provider_name, provider.model, tokens_in, tokens_out)
            answer = (resposta.conteudo or "").strip()
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
