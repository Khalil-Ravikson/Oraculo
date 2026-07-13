"""
src/agents/sigaa/auth_flow.py
================================
Fluxo HITL de autenticação SIGAA (CPF/senha), extraído de
`application/chain/cognitive_os.py` na Fase 3 do
PLANO_REFATORACAO_SUPERVISOR.md (seção 2.2 ponto 3 e seção 2.3).

Esqueleto mínimo do futuro `SigaaAgent` (Fase 5): por ora são funções livres,
não uma classe `BaseAgent` ainda — `agents/sigaa/service.py` (Fase 5) é quem
vai orquestrar isso junto com o scraping. Zero mudança de comportamento nesta
fase, só relocação + uso das primitivas de `capabilities/persistence/redis_state.py`
em vez de chamadas Redis cruas espalhadas.

NOTA DE ESCOPO: `handle_hitl_continuation()` também cobre o fallback legado de
confirmação SIM/NÃO (usado por outras ações além de SIGAA, ex. download de
mídia) porque ambos compartilham o mesmo registro `hitl:session:{id}` e a
mesma árvore de decisão no código original — separá-los agora arriscaria
introduzir um bug sutil de precedência sem ganho claro nesta fase. Se no
futuro esse fallback genérico crescer, é candidato a virar um módulo próprio
fora de `agents/sigaa/`.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any

from src.application.runtime.dispatcher import OSResult
from src.capabilities.persistence import redis_state

logger = logging.getLogger(__name__)

_FRIENDLY_NAMES = {
    "sigaa_notas": "Consultar Minhas Notas",
    "sigaa_indice": "Consultar Índice Acadêmico (CR)",
    "sigaa_historico": "Emitir Histórico Escolar",
    "sigaa_estrutura": "Consultar Estrutura Curricular",
    "sigaa_turmas": "Consultar Turmas do Semestre",
    "sigaa_calendario": "Consultar Calendário Acadêmico",
    "sigaa_extensao": "Realizar Inscrição em Evento de Extensão",
    "sigaa_biblioteca": "Consultar Acervo da Biblioteca",
    "sigaa_processos": "Consultar Processos Seletivos",
}


async def handle_hitl_continuation(
    message: str, session_id: str, user_context: dict, r: Any
) -> OSResult | None:
    """
    Verifica se existe uma sessão HITL pendente (`hitl:session:{session_id}`)
    e, se sim, avança o fluxo (coleta de CPF/senha, ou confirmação SIM/NÃO).
    Retorna None se não há sessão HITL pendente — o chamador deve prosseguir
    com o roteamento normal.
    """
    hitl_state = await redis_state.get_hitl_session(r, session_id)
    if not hitl_state:
        return None

    try:
        action = hitl_state.get("action")
        msg_clean = message.strip()

        if action == "sigaa_collect_cpf":
            cpf = re.sub(r"\D", "", msg_clean)
            if len(cpf) != 11:
                # IMPORTANTE: NÃO deletar o estado HITL aqui.
                return OSResult(
                    answer="❌ **CPF Inválido!**\nO CPF deve conter exatamente 11 dígitos numéricos. Por favor, informe seu CPF novamente:",
                    plan_id=hitl_state.get("event", {}).get("plan_id", "sigaa_auth"),
                    rota="SIGAA",
                    cache_hit=False,
                    total_ms=10,
                    status="hitl_pending"
                )
            # Avança para coletar senha
            hitl_state["action"] = "sigaa_collect_password"
            hitl_state["cpf"] = cpf
            await redis_state.set_hitl_session(r, session_id, hitl_state)
            return OSResult(
                answer="🔐 **CPF recebido!**\nAgora, por favor, envie sua **senha do SIGAA** para iniciarmos o acesso (sua senha é transmitida de forma segura e não será salva persistentemente):",
                plan_id=hitl_state.get("event", {}).get("plan_id", "sigaa_auth"),
                rota="SIGAA",
                cache_hit=False,
                total_ms=10,
                status="hitl_pending"
            )

        elif action == "sigaa_collect_password":
            senha = msg_clean
            cpf = hitl_state.get("cpf")
            target_action = hitl_state.get("target_action")
            event = hitl_state.get("event", {})

            # Anexa credenciais ao event de forma temporária usando um token
            event["login"] = cpf
            auth_token = str(uuid.uuid4())
            await redis_state.set_auth_token(r, auth_token, {"senha": senha})
            event["auth_token"] = auth_token
            event["hitl_confirmed"] = True

            await redis_state.delete_hitl_session(r, session_id)

            from src.application.workers.registry import _REGISTRY, _autodiscover_workers
            from src.application.tasks.process_message_task import enviar_resposta_whatsapp_task
            from celery import chain

            _autodiscover_workers()
            fn = _REGISTRY.get(target_action)
            if fn:
                delivery_ctx = {
                    "plan_id": event.get("plan_id", "hitl_fast_path"),
                    "chat_id": user_context.get("chat_id") or session_id,
                    "sender_jid": session_id,
                    "route": "SIGAA",
                    "query": event.get("query", ""),
                }
                workflow = chain(
                    fn.s(event),
                    enviar_resposta_whatsapp_task.s(delivery_ctx)
                )
                workflow.apply_async()

                desc = hitl_state.get("description", target_action)
                return OSResult(
                    answer=f"🚀 **Autenticação em andamento!**\nIniciando acesso seguro ao SIGAA para a operação **{desc}**. Você receberá os resultados em breve por aqui.",
                    plan_id=event.get("plan_id", "hitl_fast_path"),
                    rota="SIGAA",
                    cache_hit=True,
                    total_ms=10,
                    status="ok"
                )
            else:
                from src.application.workers.registry import dispatch
                dispatch(target_action, event)
                return OSResult(
                    answer=f"✅ Ação '{target_action}' iniciada.",
                    plan_id=event.get("plan_id", "hitl_fast_path"),
                    rota="SIGAA",
                    cache_hit=True,
                    total_ms=10,
                    status="ok"
                )

        # Fallback para confirmações SIM/NAO legadas (ex: baixar mídia)
        msg_lower = msg_clean.lower()
        if msg_lower in ("sim", "s", "yes", "y", "confirmo", "ok"):
            await redis_state.delete_hitl_session(r, session_id)
            action = hitl_state.get("action")
            worker_name = hitl_state.get("worker_name")
            event = hitl_state.get("event", {})

            from src.application.workers.registry import _REGISTRY, _autodiscover_workers
            from src.application.tasks.process_message_task import enviar_resposta_whatsapp_task
            from celery import chain

            _autodiscover_workers()
            fn = _REGISTRY.get(worker_name)
            if fn:
                delivery_ctx = {
                    "plan_id": event.get("plan_id", "hitl_fast_path"),
                    "chat_id": user_context.get("chat_id") or session_id,
                    "sender_jid": session_id,
                    "route": "SIGAA" if (action and (action.startswith("sigaa_") or "sigaa" in worker_name)) else "MEDIA_DOWNLOAD",
                    "query": event.get("query", ""),
                }
                workflow = chain(
                    fn.s(event),
                    enviar_resposta_whatsapp_task.s(delivery_ctx)
                )
                workflow.apply_async()

                desc = hitl_state.get("description", action)
                return OSResult(
                    answer=f"✅ Ação **{desc}** confirmada! Enviada para processamento no servidor Celery.",
                    plan_id=event.get("plan_id", "hitl_fast_path"),
                    rota="SIGAA" if (action and (action.startswith("sigaa_") or "sigaa" in worker_name)) else "MEDIA_DOWNLOAD",
                    cache_hit=True,
                    total_ms=10,
                    status="ok"
                )
        elif msg_lower in ("nao", "não", "n", "no", "cancela", "cancelar"):
            await redis_state.delete_hitl_session(r, session_id)
            return OSResult(
                answer="❌ Ação cancelada.",
                plan_id="hitl_fast_path", rota="HITL", cache_hit=True, total_ms=10, status="ok"
            )
        else:
            return OSResult(
                answer="⚠️ Não entendi. Responda *SIM* para confirmar ou *NÃO* para cancelar.",
                plan_id="hitl_fast_path", rota="HITL", cache_hit=True, total_ms=10, status="hitl_pending"
            )
    except Exception as e:
        logger.error("Erro no parse do HITL state: %s", e)
        # Não deletamos a sessão em caso de erro para permitir nova tentativa do usuário
        return None


async def start_or_continue_sigaa(
    decision, message: str, session_id: str, user_context: dict, r: Any, t0: float
) -> OSResult:
    """
    Fast-path SIGAA: se já há sessão ativa no SIGAA, despacha a tarefa
    imediatamente; caso contrário, inicia a coleta de CPF (HITL).
    """
    from src.application.use_cases.sigaa_use_cases import SIGAAUseCase

    uc = SIGAAUseCase()
    fluxo = uc.detectar_fluxo(message)
    worker = fluxo["worker"] if fluxo else "sigaa_biblioteca"
    args = fluxo["args"] if fluxo else {}

    # Se já está confirmado via HITL, prossegue normalmente
    if args.get("hitl_confirmed"):
        args.pop("hitl_confirmed", None)
    else:
        op_desc = _FRIENDLY_NAMES.get(worker, "Acessar o Portal SIGAA")

        # Se o usuário já possui cookies de sessão válidos no Redis, dispacha a tarefa imediatamente
        if await redis_state.has_sigaa_session(r, session_id):
            from src.application.workers.registry import _autodiscover_workers, _REGISTRY
            from src.application.tasks.process_message_task import enviar_resposta_whatsapp_task
            from celery import chain

            _autodiscover_workers()
            fn = _REGISTRY.get(worker)
            if fn:
                event = {
                    "plan_id": f"fast_{worker}_{int(time.time())}",
                    "session_id": session_id,
                    "step_id": "s1",
                    "query": message,
                    **args
                }
                delivery_ctx = {
                    "plan_id": event["plan_id"],
                    "chat_id": user_context.get("chat_id") or session_id,
                    "sender_jid": session_id,
                    "route": "SIGAA",
                    "query": message,
                }
                workflow = chain(
                    fn.s(event),
                    enviar_resposta_whatsapp_task.s(delivery_ctx)
                )
                workflow.apply_async()

                ms = int((time.monotonic() - t0) * 1000)
                return OSResult(
                    answer="🔄 **Acessando dados no SIGAA...**\nUtilizando sua sessão ativa existente. Buscando informações, aguarde um instante...",
                    plan_id=event["plan_id"],
                    rota="SIGAA",
                    cache_hit=True,
                    total_ms=ms,
                    status="ok"
                )

        # Caso contrário, inicia a coleta de CPF
        hitl_state = {
            "action": "sigaa_collect_cpf",
            "target_action": worker,
            "description": op_desc,
            "event": {
                "plan_id": f"fast_{worker}_{int(time.time())}",
                "session_id": session_id,
                "step_id": "s1",
                "query": message,
                **args
            }
        }
        await redis_state.set_hitl_session(r, session_id, hitl_state)

        ms = int((time.monotonic() - t0) * 1000)
        return OSResult(
            answer=f"⚠️ **Autenticação Requerida**\n\nPara executar a operação **{op_desc}**, preciso que você se autentique no SIGAA.\n\nPor favor, informe seu **CPF** (apenas números, sem pontos ou traços):",
            plan_id=hitl_state["event"]["plan_id"],
            rota=decision.rota,
            cache_hit=False,
            total_ms=ms,
            status="hitl_pending",
            action_buttons=[]
        )

    # hitl_confirmed=True chegou até aqui: cai pro Planner normal (retorna None
    # para o dispatcher prosseguir com o fluxo padrão de Planner+despacho).
    return None
