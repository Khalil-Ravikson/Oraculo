"""
LLMOrchestrator — Slow Path com Structured Output (Gemini).
Chamado apenas quando o Fast Path (! @ $) não bate.
"""
from __future__ import annotations
import logging
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

ACTIONS = ["reply_direct", "call_rag", "call_sigaa", "check_status", "call_media"]


class OrchestratorDecision(BaseModel):
    action: str = Field(description=f"Uma de: {ACTIONS}")
    reasoning: str = Field(description="Motivo da decisão em até 60 chars")
    route_hint: str = Field(default="GERAL",
        description="Sub-rota opcional: CALENDARIO, EDITAL, CONTATOS, WIKI, GERAL")


_SYSTEM = """Você é o orquestrador do Oráculo UEMA.
Analise a mensagem e decida a ação correta:

- reply_direct: saudação, agradecimento, pergunta sobre você mesmo
- call_rag: dúvida sobre documentos, calendário, editais, contatos, wiki
- call_sigaa: notas, histórico, turmas, CR, IRA, estrutura curricular
- check_status: usuário pergunta sobre andamento, solicita o resultado de uma tarefa/requisição anterior, ou faz referência à 'requisição anterior'.
- call_media: usuário pede para baixar um vídeo, áudio, criar sticker, ou processar mídia

Você é uma API. RETORNE EXCLUSIVAMENTE UM JSON VÁLIDO obedecendo ao schema exigido. NÃO RETORNE MARKDOWN, NEM TEXTO EXPLICATIVO."""

_client = None
def _get_client():
    global _client
    if _client is None:
        from src.infrastructure.settings import settings
        import google.genai as genai
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client


async def orchestrate(
    message: str,
    history_summary: str = "",
    task_history: dict | None = None,
    operational_memory: dict | None = None,
    user_context: dict | None = None,
    session_id: str = "",
) -> OrchestratorDecision:
    from src.infrastructure.settings import settings
    import google.genai as genai
    from google.genai import types

    ctx_parts = []
    if history_summary:
        ctx_parts.append(f"[HISTÓRICO RECENTE]\n{history_summary[-800:]}")
    if task_history and task_history.get("last_worker"):
        ctx_parts.append(
            f"[ÚLTIMA TAREFA]\nWorker: {task_history['last_worker']}\n"
            f"Resultado: {task_history.get('last_result', '')[:200]}\n"
            f"(DICA: Se a pergunta for sobre o resultado acima, retorne a ação 'check_status')"
        )
    if operational_memory and operational_memory.get("last_action"):
        ctx_parts.append(
            f"[MEMÓRIA OPERACIONAL]\nÚltima ação: {operational_memory['last_action']}\n"
            f"Se o usuário estiver apenas reagindo a uma informação prévia, considere 'reply_direct' ou 'check_status'."
        )

    prompt = "\n\n".join(ctx_parts + [f"Mensagem: \"{message[:300]}\""])

    try:
        client = _get_client()
        response = await client.aio.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM,
                temperature=0.0,
                max_output_tokens=120,
                response_mime_type="application/json",
                response_schema=OrchestratorDecision,
            ),
        )
        
        usage = response.usage_metadata
        if usage and session_id:
            from src.infrastructure.redis_client import registrar_tokens_redis
            registrar_tokens_redis(session_id,
                                   usage.prompt_token_count or 0,
                                   usage.candidates_token_count or 0)
        
        # 1. Extrair e limpar o texto (Remove blocos markdown ```json ... ```)
        raw_text = response.text or "{}"
        import re
        import json
        clean_text = re.sub(r'^```json\s*|\s*```$', '', raw_text.strip(), flags=re.IGNORECASE|re.MULTILINE)
        
        # 2. Parse seguro
        data = json.loads(clean_text)
        decision = OrchestratorDecision(**data)
        
        if decision.action not in ACTIONS:
            decision.action = "call_rag"
            
        logger.info("🎯 [ORCHESTRATOR] action=%s route=%s | '%s'",
                    decision.action, decision.route_hint, message[:40])
        return decision
        
    except json.JSONDecodeError as e:
        logger.error(f"❌ [ORCHESTRATOR] JSON Inválido: '{raw_text}' | Erro: {e}")
        return OrchestratorDecision(action="call_rag", reasoning="fallback_json", route_hint="GERAL")
    except Exception as e:
        logger.warning("⚠️  [ORCHESTRATOR] falhou, fallback call_rag: %s", e)
        return OrchestratorDecision(action="call_rag", reasoning="fallback", route_hint="GERAL")
