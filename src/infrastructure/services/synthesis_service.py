"""
src/infrastructure/services/synthesis_service.py
--------------------------------------------------
SERVICE PURO de síntese — sem Celery.
workers/worker_synthesis.py apenas instancia este service.

HITL:
  NÃO há lógica de "ambiguity threshold" aqui.
  O LLM decide quando pedir esclarecimento baseado no contexto recuperado.
  HITL de escrita (CRUD/GLPI) permanece via Redis hitl:{session_id}.

SYSTEM PROMPT:
  Instrui o LLM a pedir esclarecimento apenas quando o contexto for
  genuinamente insuficiente — não como bloqueio preventivo.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_SYSTEM_SYNTHESIS = """Você é o Oráculo, assistente oficial da UEMA via WhatsApp.

PROTOCOLO DE RESPOSTA:
1. Responda SOMENTE com base em <contexto_rag>.
2. Se o contexto contiver a informação → responda diretamente, sem rodeios.
3. Se o contexto for GENUINAMENTE insuficiente para a pergunta → diga:
   "Não encontrei essa informação específica. Você pode detalhar mais ou consulte uema.br."
4. NUNCA invente datas, números, emails ou nomes de setores.

QUANDO PEDIR ESCLARECIMENTO (decisão sua, não automática):
- Apenas se a pergunta for ambígua E o contexto trouxer informações de múltiplas
  áreas sem clareza de qual o usuário quer. Exemplo: "qual o prazo?" sem contexto
  sobre qual prazo. Nesse caso, pergunte: "Prazo para qual atividade?"
- NÃO peça esclarecimento se você já tem a resposta no contexto.

FORMATO WHATSAPP:
- *negrito* para datas, prazos e setores importantes
- • para listas
- Máximo 3 parágrafos
- Assine _Oráculo UEMA_ quando relevante"""


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
    Service de síntese LLM. Injetável e testável.
    Agnóstico à origem da query — recebe chunks já recuperados.
    """

    # Custo Gemini 2.5 Flash (USD por 1M tokens)
    _CUSTO_INPUT  = 0.075
    _CUSTO_OUTPUT = 0.30

    async def sintetizar(
        self,
        query: str,
        chunks: list[dict],
        user_context: dict | None = None,
        historico: str = "",
        fatos: list[str] | None = None,
        max_tokens: int = 600,
        system_prompt: str | None = None,
    ) -> SynthesisResult:
        """
        Gera resposta final com Gemini.

        Args:
          query:        pergunta original do usuário
          chunks:       lista de chunks recuperados (com "content" e "source")
          user_context: dict com nome, curso, role do usuário
          historico:    histórico recente da conversa (texto)
          fatos:        fatos de longo prazo do usuário
          max_tokens:   limite de tokens de saída
          system_prompt: sobrescreve o padrão (para admin commands)
        """
        t0 = time.monotonic()

        # Verifica bloqueio admin
        try:
            from src.infrastructure.redis_client import get_redis_text
            r = get_redis_text()
            if r.get("admin:gemini_blocked") == "1":
                return SynthesisResult(answer="🔧 Sistema em manutenção. Tente em instantes!")

            # System prompt sobrescrito pelo admin?
            sp_redis = r.get("admin:system_prompt")
            if sp_redis and not system_prompt:
                system_prompt = sp_redis if isinstance(sp_redis, str) else sp_redis.decode()
        except Exception:
            pass

        system = system_prompt or _SYSTEM_SYNTHESIS

        # Monta contexto RAG
        contexto_rag = self._formatar_chunks(chunks)

        # Monta prompt
        prompt = self._montar_prompt(
            query=query,
            contexto_rag=contexto_rag,
            user_context=user_context or {},
            historico=historico,
            fatos=fatos or [],
        )

        try:
            from src.infrastructure.settings import settings
            from langchain_google_genai import ChatGoogleGenerativeAI
            from langchain_core.messages import HumanMessage, SystemMessage

            llm = ChatGoogleGenerativeAI(
                model=settings.GEMINI_MODEL,
                temperature=0.2,
                google_api_key=settings.GEMINI_API_KEY,
                max_output_tokens=max_tokens,
            )

            messages = [SystemMessage(content=system), HumanMessage(content=prompt)]
            response = await llm.ainvoke(messages)

            usage = getattr(response, "usage_metadata", None) or {}
            tokens_in  = usage.get("input_tokens", 0)
            tokens_out = usage.get("output_tokens", 0)
            custo = (
                tokens_in  / 1_000_000 * self._CUSTO_INPUT +
                tokens_out / 1_000_000 * self._CUSTO_OUTPUT
            )

            ms = int((time.monotonic() - t0) * 1000)
            answer = (response.content or "").strip()

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

    def _formatar_chunks(self, chunks: list[dict], max_chars: int = 2500) -> str:
        blocos: list[str] = []
        total = 0
        for i, chunk in enumerate(chunks[:6], 1):
            content = chunk.get("content", "").strip()
            source  = chunk.get("label") or chunk.get("source", "Documento")
            if not content:
                continue
            bloco = f"[{i}. {source}]\n{content}"
            if total + len(bloco) > max_chars:
                break
            blocos.append(bloco)
            total += len(bloco)
        return "\n\n".join(blocos) or "Nenhuma informação encontrada."

    def _montar_prompt(
        self,
        query: str,
        contexto_rag: str,
        user_context: dict,
        historico: str,
        fatos: list[str],
    ) -> str:
        partes = []
        nome  = user_context.get("nome", "")
        curso = user_context.get("curso", "")

        if nome or curso:
            partes.append(
                f"<contexto_aluno>Aluno: {nome}"
                + (f" | Curso: {curso}" if curso else "")
                + "</contexto_aluno>"
            )
        if fatos:
            partes.append("<perfil_aluno>\n" + "\n".join(f"- {f}" for f in fatos[:3]) + "\n</perfil_aluno>")
        if historico:
            partes.append(f"<historico>\n{historico[-400:]}\n</historico>")

        partes.append(f"<contexto_rag>\n{contexto_rag}\n</contexto_rag>")
        partes.append(f"<pergunta>{query}</pergunta>")
        return "\n\n".join(partes)